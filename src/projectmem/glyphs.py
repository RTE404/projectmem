"""Terminal glyphs that survive a legacy Windows console.

Windows consoles still run code pages — cp1252 on a default en-GB/en-US
install — and `typer.echo` writes through a stream with that encoding. A
box-drawing character has no cp1252 mapping, so writing one raises
`UnicodeEncodeError` and the command dies mid-output. That is how
`pjm init` failed on Windows: it completed every side effect, then crashed
printing a decorative rule (#18).

Two layers here, deliberately redundant:

`configure_stdio()` is the net. It puts the output streams into
``errors="replace"`` so an unencodable character degrades to ``?`` instead
of raising. Nothing below it can crash a command, including glyphs added
later by someone who never reads this module.

`ASCII` and the constants are the manners. When the stream cannot encode
our glyph set we substitute ASCII that was chosen to read well, so the
output looks intentional rather than like the net caught something.

The web dashboard is unaffected: it is written to a file as UTF-8 and read
by a browser, so it keeps its real characters.
"""

from __future__ import annotations

import sys

# Probe character — the rule in `pjm init` that actually broke, and the
# densest glyph we print. Anything that can encode this can encode the rest.
_PROBE = "═"


def _stream_handles_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", None)
    if not encoding:
        return False
    try:
        _PROBE.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def configure_stdio() -> None:
    """Make unencodable output impossible to crash on.

    Called once from the CLI entry point. Idempotent, and silent when the
    stream does not support reconfiguration (a pytest capture object, say).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            # A detached or already-closed stream. Nothing to protect.
            pass

    if ASCII:
        sys.stdout = _AsciiFoldingStream(sys.stdout)
        sys.stderr = _AsciiFoldingStream(sys.stderr)


ASCII = not _stream_handles_unicode()


def _pick(unicode_form: str, ascii_form: str) -> str:
    return ascii_form if ASCII else unicode_form


# Status marks
OK = _pick("✓", "+")
FAIL = _pick("✗", "x")
WARN = _pick("⚠", "!")
RUNNING = _pick("●", "*")
STOPPED = _pick("○", "o")

# Rules. Callers multiply these, so each must stay exactly one column wide.
RULE = _pick("─", "-")
RULE_HEAVY = _pick("━", "-")
RULE_DOUBLE = _pick("═", "=")

# Connectors
ARROW = _pick("→", "->")

# Meter fill. Also one column each — the bar's width is computed, not measured.
BAR_FULL = _pick("█", "#")
BAR_EMPTY = _pick("░", ".")


# ── Punctuation, which fails a different way ─────────────────────────────────
#
# The constants above cover characters cp1252 cannot encode at all. Our prose
# also carries punctuation it CAN encode — an em dash is 0x97, an ellipsis
# 0x85 — so nothing above touches them and `errors="replace"` never fires.
# They still come out wrong: Python writes the cp1252 byte, a terminal set to
# UTF-8 tries to decode 0x97 as a UTF-8 lead byte, finds it invalid, and shows
# U+FFFD. The glyph was encodable and the result is still a black diamond.
#
# These live in ordinary sentences rather than in named constants, so they are
# folded at the stream instead of at each call site.

_FOLD = str.maketrans({
    "—": "--",
    "–": "-",
    "…": "...",
    "·": "*",
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "−": "-",
    " ": " ",  # non-breaking space
})


class _AsciiFoldingStream:
    """Writes ASCII-folded text through to the real stream.

    Delegates everything else, so `click` still sees a stream it recognises —
    `isatty()` in particular decides whether colour is emitted.
    """

    def __init__(self, stream) -> None:
        self._stream = stream

    def write(self, text):
        if isinstance(text, str):
            text = text.translate(_FOLD)
        return self._stream.write(text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def __getattr__(self, name):
        return getattr(self._stream, name)
