"""What we actually tell the model.

Two reports against 0.3.2 that were not code bugs but gaps in guidance:

  #14 — Antigravity called tools without a project name, got refused, read the
        error, and retried correctly. A wasted round trip every session,
        because nothing ever told it the name.

  #17 — summary.md accumulated contradicting decisions. `supersedes` has
        existed since 0.1.4 and the summary renderer honours it, but
        AI_INSTRUCTIONS.md mentioned it zero times in 12,503 characters, so
        models never called it. The reporter's own model said as much.

These assert the guidance exists on every surface a client might read. They are
deliberately about content, not formatting.
"""
from __future__ import annotations

import pytest

from projectmem.commands.init import _claude_md_bridge
from projectmem.storage import ai_instructions


def _mcp_instructions() -> str:
    from projectmem import mcp_server

    return mcp_server.mcp.instructions or ""


# ── #14: the agent should not have to learn its project name from an error ──

def test_the_bridge_names_the_project():
    bridge = _claude_md_bridge("checkout-api")
    assert "checkout-api" in bridge
    assert 'project="checkout-api"' in bridge


def test_the_bridge_is_still_valid_without_a_name():
    """Registration is best-effort; a missing name must not break the block."""
    bridge = _claude_md_bridge(None)
    assert "projectmem (MANDATORY)" in bridge
    assert "registered with projectmem as" not in bridge


def test_init_writes_the_registered_name_into_claude_md(tmp_path, monkeypatch):
    from conftest import set_fake_home
    from projectmem.commands.init import run as init_run

    set_fake_home(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("PROJECTMEM_HOME", str(tmp_path / "pm"))
    project = tmp_path / "checkout-api"
    project.mkdir()
    monkeypatch.chdir(project)
    init_run(root=project)

    text = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert "checkout-api" in text, "an agent reading CLAUDE.md cannot learn the name"


# ── #17: retiring a decision has to be discoverable ─────────────────────────

@pytest.mark.parametrize("surface,getter", [
    ("AI_INSTRUCTIONS.md (get_instructions)", ai_instructions),
    ("CLAUDE.md bridge", lambda: _claude_md_bridge("demo")),
    ("MCP instructions= field", _mcp_instructions),
])
def test_supersedes_is_documented_on_every_surface(surface, getter):
    """A mechanism a model cannot discover is a mechanism that does not exist."""
    text = getter().lower()
    assert "supersede" in text, f"{surface} never mentions supersedes"


def test_supersedes_guidance_says_what_it_does_to_the_summary():
    """Knowing the argument exists is not enough — it must say why to use it."""
    text = ai_instructions().lower()
    assert "append-only" in text
    assert "summary.md" in text
    # the actual call shape, in both flavours
    assert "supersedes=" in text or "--supersedes" in text


def test_the_three_surfaces_agree_about_the_session_start_order():
    """There is a standing warning about these drifting apart.

    A past divergence had CLAUDE.md saying "call get_summary first" while the
    MCP field said get_instructions — clients that read both got contradictory
    orders.
    """
    import re

    # AI_INSTRUCTIONS.md is deliberately not in this list: it *is* the content
    # of step 1, so it does not tell you to call step 1 again. Only the two
    # surfaces that advertise the trio can disagree about its order.
    for name, text in (("bridge", _claude_md_bridge("demo")),
                       ("instructions=", _mcp_instructions())):
        # Anchor on the numbered session-start list, not on the first mention
        # anywhere: the prose also cites get_summary when explaining token cost,
        # which says nothing about ordering.
        ordered = re.findall(r"\d\.\s+`?(get_\w+)", text)
        assert ordered[:2] == ["get_instructions", "get_summary"], (
            f"{name} lists the session-start trio as {ordered[:3]}"
        )
