"""The load convention over the four real plugin trees: the one wiring check that the shipped skills and agents
are in the fixed wording. Every other test of the rules builds its own tree; this one runs ``check_plugin`` over
what is checked in and fails on any fault, naming file and line."""

from __future__ import annotations

from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from portage.eval_gate.convention import check_plugin

TOOLS = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("plugin", ["craft", "lore", "portage", "outpost"])
def test_the_shipped_plugin_tree_has_no_load_convention_faults(plugin):  # inert-gate: allow seam smoke, one per root
    root = TOOLS / plugin / "plugins" / plugin
    assert root.is_dir()
    assert check_plugin(root) == []
