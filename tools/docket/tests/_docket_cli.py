"""Shared import helper for the docket test modules.

The docket package lives at ``plugins/docket/docket/``; pytest collecting
``tools/docket/tests/`` does not have the plugin root on ``sys.path``, so this helper
prepends it and ``import docket.cli`` resolves. Tests import it for its side effect
(``from _docket_cli import PLUGIN_ROOT``).

Named uniquely (not ``conftest.py``) because this directory carries no ``__init__.py``, so a
second bare ``conftest`` module would collide with the other tools' under pytest's default
(prepend) import mode.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "docket"
CLI = PLUGIN_ROOT / "cli" / "docket"
WRAPPER = PLUGIN_ROOT / "bin" / "docket"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
