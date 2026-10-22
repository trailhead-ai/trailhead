"""Shared import helper for the portage CLI test modules.

The portage package lives at ``plugins/portage/portage/`` and its dispatch
lazy-imports the plugin-root-level bare ``_bootstrap`` module. Neither is on
``sys.path`` by default when pytest collects ``tools/portage/tests/``, so this
helper prepends the plugin root — making ``import portage.cli.dispatch`` and
``import _bootstrap`` both resolve.

Named uniquely (not ``conftest.py``) for the same reason the retired
``_script_loader.py`` was: this directory carries no ``__init__.py``, so a
second bare ``conftest`` module here would collide with
``tools/lore/tests/conftest.py`` under pytest's default (prepend) import mode.
A uniquely-named plain module imported directly by the tests sidesteps that.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "portage"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


@pytest.fixture(autouse=True)
def contained_tempdir(tmp_path, monkeypatch):
    """Keep every ``tempfile`` default (the eval-gate state dir) under the test's own tmp_path.

    Eval-gate test modules activate it with
    ``from _portage_cli import contained_tempdir  # noqa: F401``.
    """
    scratch = tmp_path / "system-tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
