"""A failed on-acquire recovery surfaces as a named ``lore:`` message from any
lock-taking command, not only ``record`` and ``sync``."""

from __future__ import annotations

import os
import subprocess

import pytest

from conftest import make_vault as _make_vault, run_cli as _run
from test_record_pair_write import _create, _crash_amend, _journals, _paths

SID = "6f1c1c64-3b5e-4b8e-9a53-2f6e0d2a7c11"


def _dirty_session(vault, state):
    r = _run(
        ["session", "candidate", "--session-id", SID, "--kind", "spec", "--phase", "Plan"],
        vault=vault, state_dir=state, stdin_text="a candidate\n",
    )
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@pytest.mark.parametrize(
    "args",
    [["flush", "--session-id", SID], ["vault", "reformat"]],
    ids=["flush", "vault-reformat"],
)
def test_lock_taker_with_failed_recovery_exits_with_named_lore_message(tmp_path, args):
    vault, state = _make_vault(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=vault, check=True)
    rid = _create(vault, state)
    _dirty_session(vault, state)
    _crash_amend(vault, state, rid)
    kind_dir = _paths(vault, rid)[0].parent
    kind_dir.chmod(0o555)
    try:
        r = _run(args, vault=vault, state_dir=state)
    finally:
        kind_dir.chmod(0o755)

    assert r.returncode != 0, r.stdout
    assert r.stderr.startswith("lore:"), r.stderr
    assert rid in r.stderr
    assert "Traceback" not in r.stderr
    assert len(_journals(state, vault)) == 1
