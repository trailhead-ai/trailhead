"""Every TOML the README documents is run through the code that consumes it.

A documented spelling the real parser would reject, and a backticked deny entry
the launch gate does not enforce, are both cheap to introduce and expensive to
discover. Each documented block is fed to the production parser, and each named
entry checked against the production deny list — the README's examples are the
inputs, not the assertions.

What the README *says* about the deny list is not checked here: the behaviours
that prose describes — a declared account extending the list, a relative one
contributing nothing — are pinned by execution in ``test_launch_eligibility``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from camp.group.config import _parse_launch
from camp.launch.eligibility import CREDENTIAL_DENY_ENTRIES

README = Path(__file__).resolve().parents[1] / "README.md"

_TOML_BLOCK = re.compile(r"```toml\n(.*?)```", re.DOTALL)


def _toml_blocks() -> list[str]:
    return _TOML_BLOCK.findall(README.read_text())


@pytest.mark.parametrize("block", _toml_blocks())
def test_every_documented_toml_block_is_valid_toml(block: str) -> None:
    tomllib.loads(block)


def test_every_documented_launch_block_parses_under_the_real_parser() -> None:
    """A documented `[launch]` spelling the production parser rejects is a lie."""
    seen = 0
    for block in _toml_blocks():
        raw = tomllib.loads(block).get("launch")
        if raw is None:
            continue
        seen += 1
        _parse_launch(raw, README)
    assert seen, "no documented [launch] block found — the README lost its example"


def test_the_documented_account_key_is_the_spelling_the_parser_accepts() -> None:
    accounts = [
        tomllib.loads(b)["launch"]["account"]
        for b in _toml_blocks()
        if "account" in tomllib.loads(b).get("launch", {})
    ]
    assert accounts, "the README no longer documents the account key"
    for value in accounts:
        parsed = _parse_launch({"account": value}, README)
        assert parsed is not None and parsed["account"] == value


def _deny_paragraph() -> str:
    """The prose block that enumerates the fixed deny entries."""
    text = README.read_text()
    start = text.index("**A credential deny list overrides the allowlist")
    end = text.index("\n\n", text.index("fixed\nin camp's code", start))
    return text[start:end]


def test_every_deny_entry_the_readme_names_is_really_in_the_floor() -> None:
    """The prose must not advertise protection the code does not provide.

    Scoped to the deny-list paragraph and applied to EVERY home-relative path
    it backticks, so adding an unenforced entry to the prose fails here rather
    than reading as a promise camp does not keep.
    """
    named = re.findall(r"`(~[^`]*)`", _deny_paragraph())
    assert named, "deny paragraph names no entries — the extractor has drifted"
    missing = sorted({n for n in named if n not in CREDENTIAL_DENY_ENTRIES})
    assert not missing, f"README names deny entries the code does not enforce: {missing}"


