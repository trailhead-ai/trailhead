"""EPHEMERAL assumption probe — delete after use.

Captures: `trailhead doctor` reports registration/install state purely from the
global composed root (TRAILHEAD_STATE_DIR/composed/<harness>), and never looks
at CLAUDE_CONFIG_DIR / TRAILHEAD_CLAUDE_DIR at all. So pointing doctor at a
Claude config dir that does not exist on disk still reports "registered" and
"installed" as long as the global composed-root markers are present.
"""

import os
import subprocess
from pathlib import Path

from trailhead.doctor import run_doctor
from trailhead.tests.test_doctor import _make_tree, _fake_py


def test_doctor_reports_healthy_for_a_nonexistent_claude_config_dir(tmp_path):
    # Global composed root has markers written (as if `trailhead install` ran
    # once, against whichever config dir was active at the time).
    _make_tree(tmp_path, "claude_code", ["lore", "camp"])

    nonexistent_config_dir = tmp_path / "does-not-exist" / ".claude-levr"
    assert not nonexistent_config_dir.exists()

    env = {
        **os.environ,
        "TRAILHEAD_STATE_DIR": str(tmp_path),
        # The seam the harness's own config-dir resolver consults
        # (trailhead/harness/claude_code.py `_claude_dir`), pointed at a dir
        # that has never been created.
        "TRAILHEAD_CLAUDE_DIR": str(nonexistent_config_dir),
    }

    r = run_doctor(env=env, which_runner=lambda n: None, python_version_runner=_fake_py)

    info = r.data["harnesses"]["claude_code"]
    # THE CLAIM: doctor reports this config dir as fully registered/installed
    # even though it has never existed and holds no plugin state whatsoever.
    assert info["registered"] is True
    assert set(info["installed"]) == {"lore", "camp"}
