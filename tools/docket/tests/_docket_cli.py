"""Shared import helper and test helpers for the docket test modules.

The docket package lives at ``plugins/docket/docket/``; pytest collecting
``tools/docket/tests/`` does not have the plugin root on ``sys.path``, so this helper
prepends it and ``import docket.cli`` resolves. Tests import it for that side effect
(``from _docket_cli import PLUGIN_ROOT``) and for the helpers below: the configured vaults a
scripted lore lists, and the install-then-run of commands through ``docket.cli.main``.

Named uniquely (not ``conftest.py``) because this directory carries no ``__init__.py``, so a
second bare ``conftest`` module would collide with the other tools' under pytest's default
(prepend) import mode.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "docket"
CLI = PLUGIN_ROOT / "cli" / "docket"
WRAPPER = PLUGIN_ROOT / "bin" / "docket"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

VAULTS = {"proj": "default", "other": "product", "team": "repo"}


def completed(argv, rc=0, out="", err=""):
    return subprocess.CompletedProcess(list(argv), rc, out, err)


def vault_ls(argv, vault_dir):
    """`lore vault ls --json` listing VAULTS: the default one at `vault_dir`, the others below it."""
    rows = [{"name": n, "scope": s, "path": str(vault_dir if s == "default" else vault_dir / n)}
            for n, s in VAULTS.items()]
    return completed(argv, out=json.dumps(rows))


def install(monkeypatch, commands):
    """Register a name -> Command table in `docket.cli.COMMANDS` for the length of the test."""
    from docket import cli

    for name, command in commands.items():
        monkeypatch.setitem(cli.COMMANDS, name, command)


def run_cli(capsys, argv):
    """`docket.cli.main(argv)`: its exit code, its stdout parsed as JSON (None when empty) and its stderr."""
    from docket import cli

    capsys.readouterr()
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if captured.out.strip() else None), captured.err
