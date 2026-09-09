"""The outcome grammar `monitor.md` documents is the grammar the parser accepts.

`monitor` writes one line to the caller's `outcome_file`; `portage.monitor_outcome`
is what reads it back. Those two are only a contract while the tokens the agent
is told to write are the tokens the parser recognizes — a doc that grows a fifth
state, or renames one, produces a line the caller silently reads as a failure.

The document is the INPUT here: the grammar lines are lifted out of monitor.md,
expanded to concrete values, and fed to the real parser. Nothing below asserts
that a sentence appears in a document.
"""

from __future__ import annotations

import re

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage.monitor_outcome import MONITOR_OUTCOME_TOKENS, parse_monitor_outcome

_MONITOR_MD = _portage_cli.PLUGIN_ROOT / "agents" / "monitor.md"

# A grammar entry as the doc writes it — one bullet of the outcome-line list:
# a backticked all-caps token, optionally carrying a `<reason>` placeholder,
# then the em-dash gloss. Anchored to the bullet so the doc's other backticked
# all-caps spans (the BLOCKED config-error report, for one) are not swept in.
_GRAMMAR = re.compile(r"(?m)^- `([A-Z]+)(?: (<[a-z_]+>))?` \u2014")


def _documented_lines() -> list[tuple[str, str]]:
    """Every documented outcome line, as (token, the line an agent would write)."""
    entries: list[tuple[str, str]] = []
    for token, placeholder in _GRAMMAR.findall(_MONITOR_MD.read_text(encoding="utf-8")):
        line = f"{token} because-something-happened" if placeholder else token
        entries.append((token, line))
    return entries


def test_the_documented_grammar_covers_exactly_the_parsers_tokens() -> None:
    """A state documented but unparsed — or parsed but undocumented — is drift."""
    assert {token for token, _ in _documented_lines()} == set(MONITOR_OUTCOME_TOKENS)


@pytest.mark.parametrize(
    "token,line", _documented_lines(), ids=[line for _, line in _documented_lines()]
)
def test_every_documented_outcome_line_parses_to_the_state_it_names(
    token: str, line: str
) -> None:
    parsed, argument = parse_monitor_outcome(line)
    assert parsed == token, (
        f"monitor.md documents the agent writing {line!r}, but the parser reads it "
        f"as {parsed!r} — a caller polling the outcome file would misread it"
    )
    assert argument == line[len(token) :].strip()
