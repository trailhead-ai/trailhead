"""The operator-facing README is checked against the code it documents.

Two kinds of drift are cheap to introduce and expensive to discover: a
documented TOML spelling the real parser would reject, and a prose description
of the credential deny list that no longer matches what the launch gate
enforces. Both are pinned here against the production parser and the production
deny list rather than against a transcription of them.
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


def test_the_readme_ships_toml_examples() -> None:
    assert _toml_blocks(), "no ```toml blocks found — the extractor has drifted"


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


def test_the_readme_documents_that_declared_accounts_extend_the_deny_list() -> None:
    """Group-declared accounts are added to the floor — the README must say so.

    Without this, an operator reads "fixed in camp's code" and concludes their
    group's `account` is not itself protected as a launch root.
    """
    text = README.read_text()
    assert "account" in text and re.search(
        r"account[^.]{0,400}?(deny|denied|credential)", text, re.IGNORECASE | re.DOTALL
    ), "README does not document that a declared account becomes a deny entry"


def test_the_readme_does_not_promise_that_the_account_is_never_read_as_a_path() -> None:
    """The deny derivation DOES interpret `account` as a path, and contributes no
    entry at all for one that is neither absolute nor `~`-anchored. Prose saying
    camp never validates it as a path reads as deny-list protection an operator
    with a relative value does not have."""
    text = README.read_text()
    assert "does not validate it as a path" not in text
    assert re.search(
        r"relative[^.]{0,200}?(no deny entry|contributes no|no entry)",
        text,
        re.IGNORECASE | re.DOTALL,
    ), "README does not say that a relative account contributes no deny entry"
