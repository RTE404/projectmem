"""#18 — a decorative character killed the command on a Windows console.

`pjm init` on Windows created `.projectmem/`, the hooks, `CLAUDE.md` and
`AGENTS.md`, then raised `UnicodeEncodeError` printing a box-drawing rule and
exited 1. Every side effect had already landed, so the command both succeeded
and reported failure — the worst combination for a script that checks the exit
code.

The cause is not Windows-specific in principle: any stream whose encoding
cannot represent a glyph we print will raise. cp1252 is simply the default on
a stock Windows install.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest


SRC = str(Path(__file__).resolve().parent.parent / "src")


def _cp1252_env(tmp_path: Path) -> dict:
    import os
    return {
        **os.environ,
        "PYTHONIOENCODING": "cp1252",
        "PYTHONPATH": SRC,
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "PROJECTMEM_HOME": str(tmp_path / "pm"),
    }


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=repo, check=True,
    )
    return repo


def _run(args: list[str], repo: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c",
         "from projectmem.cli import main; import sys;"
         " sys.argv=['pjm']+sys.argv[1:]; main()", *args],
        cwd=repo, env=env, capture_output=True, text=True,
        # The child writes cp1252 bytes by design — that is the whole point.
        # Reading them back as utf-8 fails on the em-dash (0x97), which is
        # the test harness being wrong, not the program.
        encoding="cp1252",
    )


def test_init_survives_a_cp1252_console(tmp_path):
    """The reported failure, exactly: exit 1 after the work was done."""
    repo = _repo(tmp_path)
    result = _run(["init"], repo, _cp1252_env(tmp_path))

    assert "UnicodeEncodeError" not in result.stderr, result.stderr
    assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr}"
    assert (repo / ".projectmem").is_dir()


def test_init_prints_the_mcp_block_on_cp1252(tmp_path):
    """Surviving is not enough — the output after the rule must still arrive.

    The crash happened mid-render, so a fix that swallowed the exception
    would pass the test above while losing everything printed afterwards.
    """
    repo = _repo(tmp_path)
    result = _run(["init"], repo, _cp1252_env(tmp_path))

    assert "Client config file locations" in result.stdout
    assert "Next:" in result.stdout, "output after the rule was lost"


@pytest.mark.parametrize("command", [["score"], ["show"], ["doctor"]])
def test_commands_survive_a_cp1252_console(tmp_path, command):
    """init was where it surfaced; it was never only init.

    score, show and doctor all print rules and status marks.
    """
    repo = _repo(tmp_path)
    env = _cp1252_env(tmp_path)
    _run(["init"], repo, env)
    result = _run(command, repo, env)

    assert "UnicodeEncodeError" not in result.stderr, result.stderr
    assert result.returncode == 0, f"exit {result.returncode}\n{result.stderr}"


def test_no_replacement_characters_leak_into_cp1252_output(tmp_path):
    """The net catches; the ASCII fallbacks are what make it presentable.

    Without them every rule renders as a row of '?' — technically working,
    visibly broken. This asserts the fallbacks are actually reached, which
    the exit code alone cannot tell us.
    """
    repo = _repo(tmp_path)
    result = _run(["init"], repo, _cp1252_env(tmp_path))

    assert "?" not in result.stdout, "glyphs fell through to errors='replace'"
    assert "=" * 20 in result.stdout, "the ASCII rule never rendered"


def test_configure_stdio_tolerates_a_stream_it_cannot_reconfigure(tmp_path):
    """pytest's capture object and a detached stream both lack reconfigure()."""
    from projectmem.glyphs import configure_stdio

    original = sys.stdout
    try:
        sys.stdout = io.StringIO()  # no reconfigure()
        configure_stdio()  # must not raise
    finally:
        sys.stdout = original


def test_cp1252_output_contains_no_byte_that_renders_as_a_black_diamond(tmp_path):
    """The second Windows report: characters that encode but still look wrong.

    An em dash IS cp1252-encodable (0x97), so the glyph constants never
    substitute it and errors="replace" never fires. Python writes 0x97, a
    terminal set to UTF-8 reads it as a lead byte, finds it invalid, and
    shows U+FFFD. Encodable and still broken.

    The guarantee is stronger than "no exception": on a stream that cannot
    carry Unicode we emit no non-ASCII byte at all, so no decoder on the
    other end has anything to misread.
    """
    repo = _repo(tmp_path)
    env = _cp1252_env(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c",
         "from projectmem.cli import main; import sys;"
         " sys.argv=['pjm','init']; main()"],
        cwd=repo, env=env, capture_output=True,  # bytes, not text
    )

    high = [b for b in result.stdout if b > 127]
    assert not high, f"{len(high)} non-ASCII bytes on a cp1252 stream"
    result.stdout.decode("utf-8")  # raises if any byte could render as U+FFFD


def test_utf8_output_keeps_its_real_characters(tmp_path):
    """The fallbacks must not follow us onto a capable terminal."""
    repo = _repo(tmp_path)
    import os
    env = {**_cp1252_env(tmp_path), "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-c",
         "from projectmem.cli import main; import sys;"
         " sys.argv=['pjm','init']; main()"],
        cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8",
    )

    assert "═" in result.stdout, "the real rule was replaced on a UTF-8 stream"
    assert "—" in result.stdout, "em dashes were folded on a UTF-8 stream"


def test_folding_stream_still_looks_like_a_stream():
    """click asks isatty() to decide on colour; a naive wrapper breaks that."""
    from projectmem.glyphs import _AsciiFoldingStream

    wrapped = _AsciiFoldingStream(sys.__stdout__)
    assert hasattr(wrapped, "isatty")
    assert wrapped.isatty() == sys.__stdout__.isatty()
    assert wrapped.encoding == sys.__stdout__.encoding
