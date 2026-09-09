"""Every `portage <subcommand>` the docs spell out is a real subcommand.

The documents are the INPUT: their invocation lines are looked up against the
choices the dispatch parser actually registers, so a doc naming a command the
CLI doesn't expose fails here rather than at the agent's first attempt to run
it.
"""

from __future__ import annotations

import argparse
import re

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage.cli import dispatch

_PLUGIN_ROOT = _portage_cli.PLUGIN_ROOT
_DOCS = [
    *(_PLUGIN_ROOT / "agents").rglob("*.md"),
    *(_PLUGIN_ROOT / "skills").rglob("*.md"),
]

# A CLI invocation line: optional indentation, then `portage <subcommand>`.
_INVOCATION = re.compile(r"(?m)^\s*portage\s+([a-z][a-z0-9-]*)")
def _subcommands() -> set[str]:
    parser = dispatch.build_parser()
    action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    return set(action.choices)


def test_every_documented_portage_invocation_is_a_real_subcommand():
    valid = _subcommands()
    referenced: set[str] = set()
    bad: dict[str, list[str]] = {}
    for doc in _DOCS:
        used = set(_INVOCATION.findall(doc.read_text()))
        referenced |= used
        unknown = sorted(used - valid)
        if unknown:
            bad[doc.name] = unknown
    # The scan has teeth only while it still finds invocations to check.
    assert referenced, f"no `portage <cmd>` invocation found across {len(_DOCS)} docs"
    assert bad == {}, f"docs reference unregistered portage subcommands: {bad} (valid: {sorted(valid)})"
