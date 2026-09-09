"""Every gate invocation craft's prose spells out is one the gate really accepts.

craft's skills and agents are instructions to run its gate scripts:
``criterion_gate.py`` on a spec body, ``maturity_bars.py`` with a resolved level,
``ledger_gate.py`` with a parent-coverage file. An instruction naming a script
that has moved, or a flag the script has since renamed, reads perfectly well in
the document and fails at the agent's first attempt to run it — mid-review, with
the gate's refusal looking like a content problem rather than a stale recipe.

The documents are the INPUT: invocations are lifted out of fenced blocks and put
to the script they name. Existence is checked against the file the manifest ships,
and every flag is checked against the option set the script's own argparse parser
registers — read from the parser's own ``--help`` output, so a rename moves the
allowlist automatically and there is no second list of flags to maintain here.

Flags are where a rename bites; positional operands are placeholders in the prose
(``<base-sha>``, ``<level>``) and are deliberately not asserted on.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_CRAFT_ROOT = Path(__file__).parent.parent
_SCRIPTS = _CRAFT_ROOT / "plugins" / "craft" / "scripts"
_PLUGIN = _CRAFT_ROOT / "plugins" / "craft"

_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
_CALL = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([a-z_]+\.py)([^\n`]*)")
_FLAG = re.compile(r"(?<!\S)(--[a-z][a-z0-9-]*)")
_OPTION = re.compile(r"(--[a-z][a-z0-9-]*)")


def _shipped_prose() -> list[Path]:
    return sorted([*(_PLUGIN / "skills").rglob("*.md"), *(_PLUGIN / "agents").glob("*.md")])


def _documented_invocations() -> list[tuple[str, str]]:
    """(script filename, argument tail) for every gate call the prose spells out."""
    found: set[tuple[str, str]] = set()
    for document in _shipped_prose():
        text = document.read_text(encoding="utf-8").replace("\\\n", " ")
        for block in _FENCE.findall(text):
            for script, args in _CALL.findall(block):
                found.add((script, " ".join(args.split())))
    return sorted(found)


def _registered_options(script: str) -> set[str] | None:
    """The option strings `script`'s own parser registers, or None if unreachable.

    A gate that fails closed on empty stdin before argparse ever runs cannot be
    asked for its help text. Every such script is documented with no flags at all,
    so there is nothing to check against — the caller skips those rather than
    guessing at an option set.
    """
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS / script), "--help"],
        input="",
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return set(_OPTION.findall(result.stdout))


def test_the_shipped_prose_spells_out_gate_invocations():
    """Non-vacuity guard: an extraction that stops matching would leave the checks
    below iterating over nothing and reporting clean."""
    invocations = _documented_invocations()
    assert len(invocations) >= 10, invocations
    assert any(_FLAG.findall(args) for _, args in invocations), (
        f"no documented invocation carries a flag, so the flag check below proves "
        f"nothing: {invocations}"
    )


@pytest.mark.parametrize(
    "script,args", _documented_invocations(), ids=lambda v: v.replace(" ", "_")[:40] or "bare"
)
def test_every_documented_gate_invocation_names_a_script_that_ships(script: str, args: str):
    assert (_SCRIPTS / script).is_file(), (
        f"craft's prose tells an agent to run `{script} {args}`, but no such script ships"
    )


@pytest.mark.parametrize(
    "script,args", _documented_invocations(), ids=lambda v: v.replace(" ", "_")[:40] or "bare"
)
def test_every_flag_a_documented_invocation_passes_is_one_the_gate_registers(script: str, args: str):
    """A flag the script no longer registers makes the documented command exit on an
    argparse usage error — the recipe is stale, not the content it was pointed at."""
    flags = set(_FLAG.findall(args))
    if not flags:
        pytest.skip("invocation passes no flags")
    registered = _registered_options(script)
    assert registered is not None, (
        f"{script} is documented with flags {sorted(flags)} but does not reach argparse "
        "— its help text is unreachable, so the recipe cannot be checked"
    )
    unknown = sorted(flags - registered)
    assert not unknown, (
        f"craft's prose runs `{script} {args}`, but its parser does not register "
        f"{unknown}. Registered: {sorted(registered)}"
    )
