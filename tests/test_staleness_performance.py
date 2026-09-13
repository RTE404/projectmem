"""Staleness detection: same answers, without one git process per event.

`precheck_file` took 26 seconds on a 1,200-event project — 1,201 git
subprocesses to answer a question about a single file. Three faults compounded:

  - it computed staleness for the whole event log, then kept one file's
  - the memo key was (file, timestamp); real events carry distinct timestamps,
    so it never hit
  - nothing bounded the git walk

These tests pin the behaviour that must not change while that got fixed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from projectmem.models import Event
from projectmem.staleness import (
    STALE_COMMIT_THRESHOLD,
    commit_times,
    commits_touching_since,
    find_stale_events,
)


def _git(repo: Path, *args, when: str | None = None):
    env = None
    if when:
        import os
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True, env=env)


@pytest.fixture
def repo(tmp_path):
    """A repo where f.txt is edited across known dates, including via a merge."""
    r = tmp_path / "r"
    (r / "src").mkdir(parents=True)
    _git(r.parent, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")

    (r / "src" / "f.py").write_text("v0\n")
    (r / "src" / "other.py").write_text("x\n")
    _git(r, "add", "-A"); _git(r, "commit", "-qm", "init", when="2026-01-01T10:00:00")
    for i, month in enumerate(("03", "04", "05", "06"), start=1):
        (r / "src" / "f.py").write_text(f"v{i}\n")
        _git(r, "add", "-A"); _git(r, "commit", "-qm", f"edit {i}",
                                   when=f"2026-{month}-01T10:00:00")
    return r


def _event(eid, ts, loc, etype="decision"):
    return Event(id=eid, type=etype, summary=f"claim {eid}", timestamp=ts, location=loc)


def test_counts_match_a_per_event_git_log(repo):
    """The batched count must equal what one `git log --since` per event gave."""
    for cutoff in ("2026-01-15T00:00:00Z", "2026-03-15T00:00:00Z",
                   "2026-05-15T00:00:00Z", "2026-12-01T00:00:00Z"):
        direct = subprocess.run(
            ["git", "log", f"--since={cutoff}", "--oneline", "--", "src/f.py"],
            cwd=repo, capture_output=True, text=True)
        expected = sum(1 for l in direct.stdout.splitlines() if l.strip())
        assert commits_touching_since("src/f.py", cutoff, repo) == expected


def test_one_git_call_per_file_not_per_event(repo, monkeypatch):
    """The regression. Distinct timestamps used to defeat the memo entirely."""
    events = [_event(f"evt_{i:03d}", f"2026-01-0{i % 9 + 1}T10:00:0{i % 9}Z", "src/f.py")
              for i in range(40)]

    calls = {"n": 0}
    real = subprocess.run

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(subprocess, "run", counting)
    find_stale_events(events, repo)

    assert calls["n"] == 1, f"40 events citing one file took {calls['n']} git calls"


def test_only_files_restricts_the_work_and_the_answer(repo):
    """precheck asks about the files being committed, not the whole log."""
    events = [_event("a", "2026-01-15T10:00:00Z", "src/f.py"),
              _event("b", "2026-01-15T10:00:00Z", "src/other.py")]

    everything = find_stale_events(events, repo)
    scoped = find_stale_events(events, repo, only_files={"src/f.py"})

    assert {x["file"] for x in scoped} == {"src/f.py"}
    assert scoped == [x for x in everything if x["file"] == "src/f.py"]


def test_a_deleted_file_flags_without_asking_git(repo, monkeypatch):
    """A missing file is the strongest staleness signal and needs no history."""
    events = [_event("gone", "2026-01-15T10:00:00Z", "src/deleted.py")]

    def explode(*a, **k):
        raise AssertionError("git should not run for a file that does not exist")

    monkeypatch.setattr(subprocess, "run", explode)
    flagged = find_stale_events(events, repo)

    assert [(x["file"], x["commits_since"]) for x in flagged] == [("src/deleted.py", -1)]


def test_git_failure_reads_as_cannot_judge_not_stale(tmp_path):
    """Outside a repo there is no history — that must never mean 'stale'."""
    plain = tmp_path / "not-a-repo"
    (plain / "src").mkdir(parents=True)
    (plain / "src" / "f.py").write_text("x")

    assert commit_times("src/f.py", plain) is None
    assert commits_touching_since("src/f.py", "2026-01-01T00:00:00Z", plain) is None
    assert find_stale_events([_event("a", "2026-01-01T00:00:00Z", "src/f.py")], plain) == []


def test_threshold_is_still_respected(repo):
    """Below the threshold nothing is flagged; at or above it, it is."""
    old = _event("old", "2026-01-15T10:00:00Z", "src/f.py")   # 4 commits after
    recent = _event("recent", "2026-05-15T10:00:00Z", "src/f.py")  # 1 commit after

    flagged = {x["event"].id: x["commits_since"]
               for x in find_stale_events([old, recent], repo)}

    assert flagged["old"] >= STALE_COMMIT_THRESHOLD
    assert "recent" not in flagged


def test_superseded_events_are_still_skipped(repo):
    """Already retired is already handled; flagging it again is noise."""
    old = _event("old", "2026-01-15T10:00:00Z", "src/f.py")
    newer = Event(id="new", type="decision", summary="replaces it",
                  timestamp="2026-07-01T10:00:00Z", location="src/f.py",
                  supersedes="old")

    ids = {x["event"].id for x in find_stale_events([old, newer], repo)}
    assert "old" not in ids
