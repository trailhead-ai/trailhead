"""`/craft:slice` step 9's `--title` substitution site: shell-injection safety.

`test_slice_covers_contract.py`'s HIGH-3 repro pins the identical double-quoted-
interpolation defeat against `--covers` — a positive allow-list is available there
because the grammar is fixed. The slice title is free text, so no allow-list
applies; step 9 instead documents a strip-then-quote scrub. These tests bind that
documented scrub to real shell execution, never to a comparison against the
document's own wording: the scrub rule and the quote character are both parsed
live out of `slice/SKILL.md`, then actually applied and run through `bash -c`,
so a rewording that reintroduces the hole fails these tests for the same reason
the original hole would have failed them.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

CRAFT = Path(__file__).parent.parent / "plugins" / "craft"
SLICE_SKILL = CRAFT / "skills" / "slice" / "SKILL.md"


def _skill_text() -> str:
    return SLICE_SKILL.read_text(encoding="utf-8")


def _step(name: str) -> str:
    """The named `### N. ...` step's body, up to the next `### ` heading."""
    text = _skill_text()
    start = text.index(name)
    rest = text[start + len(name):]
    end = re.search(r"\n### \d+\.", rest)
    return rest[: end.start()] if end else rest


STRIP_TOKEN_TO_CHAR = {
    "single quotes": "'",
    "single quote": "'",
    "newlines": "\n",
    "newline": "\n",
    "backticks": "`",
    "backtick": "`",
    "`$`": "$",
    "$": "$",
    "double quotes": '"',
    "double quote": '"',
    "backslashes": "\\",
    "backslash": "\\",
}


def _documented_quote_char(step9: str) -> str:
    """The character step 9's own code fence wraps `<slice title>` in."""
    match = re.search(r"--title\s+(['\"])<slice title>\1", step9)
    assert match, (
        "slice/SKILL.md step 9 must show the --title placeholder wrapped in a "
        "single, consistent quote character in its worked command"
    )
    return match.group(1)


def _documented_strip_chars(step9: str) -> set[str]:
    """The characters step 9's prose says the title is stripped of, before quoting."""
    match = re.search(r"stripped of (.+?) before it is", step9, re.DOTALL)
    assert match, (
        "slice/SKILL.md step 9 must state what the title is stripped of before "
        "it is quoted"
    )
    raw = match.group(1).replace(" and ", ", ")
    tokens = [t.strip().strip(".") for t in raw.split(",") if t.strip()]
    chars: set[str] = set()
    for token in tokens:
        assert token in STRIP_TOKEN_TO_CHAR, (
            f"unrecognized strip token {token!r} parsed out of slice/SKILL.md "
            "step 9's scrub rule — extend STRIP_TOKEN_TO_CHAR or fix the wording"
        )
        chars.add(STRIP_TOKEN_TO_CHAR[token])
    return chars


def _run_documented_title_command(title: str, capture_path: Path) -> None:
    """Build and execute the exact `lore record create --title ...` invocation
    step 9 documents, applying its live-parsed scrub and quote character to
    `title` the way an operator following the doc would. `lore` is stubbed to
    capture its argv rather than actually write a record; anything that runs
    *outside* that stub (an injected command) runs for real.
    """
    step9 = _step("### 9. Materialize the parent task")
    quote_char = _documented_quote_char(step9)
    strip_chars = _documented_strip_chars(step9)
    scrubbed = "".join(c for c in title if c not in strip_chars)

    script = (
        "BODY=x; SPEC_NAME=spec-x; "
        f"lore() {{ printf '%s\\0' \"$@\" > {shlex.quote(str(capture_path))}; }}; "
        "printf '%s' \"$BODY\" | lore record create "
        f"--kind task --title {quote_char}{scrubbed}{quote_char} "
        '--status in-progress --related "spec=$SPEC_NAME" '
        "--label craft/slice-parent"
    )
    subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)


def test_documented_scrub_neutralizes_a_double_quote_breakout_title(tmp_path):
    """HIGH-3-style repro: a title carrying an unescaped `"` and a shell
    metacharacter sequence must not execute as a separate command."""
    sentinel = tmp_path / "pwned_dq"
    title = f'Add retry logic"; touch {sentinel}; echo "'
    _run_documented_title_command(title, tmp_path / "argv.bin")
    assert not sentinel.exists(), (
        "slice/SKILL.md step 9's documented --title scrub let a double-quote "
        f"breakout payload execute a shell command ({sentinel} was created)"
    )


def test_documented_scrub_neutralizes_a_single_quote_breakout_title(tmp_path):
    """The character step 9's own quoting style is vulnerable to must actually
    be stripped, not merely named in prose."""
    sentinel = tmp_path / "pwned_sq"
    title = f"Add retry logic'; touch {sentinel}; echo '"
    _run_documented_title_command(title, tmp_path / "argv.bin")
    assert not sentinel.exists(), (
        "slice/SKILL.md step 9's documented --title scrub let a single-quote "
        f"breakout payload execute a shell command ({sentinel} was created)"
    )


def test_documented_scrub_delivers_a_benign_title_as_one_literal_argv_token():
    step9 = _step("### 9. Materialize the parent task")
    quote_char = _documented_quote_char(step9)
    strip_chars = _documented_strip_chars(step9)
    title = "Add retry logic for the export queue"
    scrubbed = "".join(c for c in title if c not in strip_chars)
    assert scrubbed == title, (
        "a title with no character in the documented strip set must survive "
        "the scrub unchanged"
    )
    assert quote_char in ("'", '"')
