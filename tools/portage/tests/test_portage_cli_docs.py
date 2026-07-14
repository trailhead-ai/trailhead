"""The agent/skill docs invoke the real `portage` CLI, not the retired scripts.

Two guards on the call-site migration:
  - No portage agent or skill doc still shells a ``python3 …/<script>.py``
    invocation — the thin ``scripts/*.py`` are gone, migrated to the CLI.
  - Every ``portage <subcommand>`` invocation the docs spell out maps to a real
    subcommand registered on the dispatch parser — a doc naming a command the
    CLI doesn't expose is drift this catches.
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
# A retired thin-script shell-out: `python3 …/<name>.py`.
_LEGACY_SCRIPT = re.compile(r"python3\s+\S*\.py")


def _subcommands() -> set[str]:
    parser = dispatch.build_parser()
    action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    return set(action.choices)


def test_no_doc_shells_a_legacy_python_script():
    offenders = {
        doc.name: _LEGACY_SCRIPT.findall(doc.read_text())
        for doc in _DOCS
        if _LEGACY_SCRIPT.search(doc.read_text())
    }
    assert offenders == {}, (
        f"these docs still invoke a retired thin script instead of the portage CLI: {offenders}"
    )


def test_every_documented_portage_invocation_is_a_real_subcommand():
    valid = _subcommands()
    bad: dict[str, list[str]] = {}
    for doc in _DOCS:
        used = set(_INVOCATION.findall(doc.read_text()))
        unknown = sorted(used - valid)
        if unknown:
            bad[doc.name] = unknown
    assert bad == {}, f"docs reference unregistered portage subcommands: {bad} (valid: {sorted(valid)})"


def test_docs_actually_reference_the_migrated_cli():
    """At least the core lifecycle commands appear as real `portage <cmd>` lines,
    proving the migration landed rather than the scan passing vacuously."""
    referenced: set[str] = set()
    for doc in _DOCS:
        referenced.update(_INVOCATION.findall(doc.read_text()))
    for expected in ("detect-repos", "merge", "wait-for-actionable"):
        assert expected in referenced, (
            f"expected the docs to invoke `portage {expected}` after migration; "
            f"found only {sorted(referenced)}"
        )
