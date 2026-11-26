"""Root pytest hooks: the closing line naming the opt-in tiers a run left out, and the opt-in files."""

import os

from tier_closing_line import pytest_unconfigure  # noqa: F401

# The gateway end-to-end file needs a real Outpost, the Claude Code sandbox and the operator's
# dotfiles. Unless TRAILHEAD_GATEWAY_E2E=1 it is not collected, so a default run does not count
# its tests as skips against the suite's skipped-count ceiling. The files that start a real Outpost
# need only the sibling Outpost checkout (a camp workspace has one, GitHub CI does not), so they have
# their own switch, TRAILHEAD_REAL_OUTPOST=1.
_OPT_IN_FILES = {
    "test_gateway_e2e_andromeda.py": "TRAILHEAD_GATEWAY_E2E",
    "test_gateway_e2e_outpost_reader.py": "TRAILHEAD_GATEWAY_E2E",
    "test_gateway_command_real_outpost.py": "TRAILHEAD_REAL_OUTPOST",
    "test_outpost_open_real_outpost.py": "TRAILHEAD_REAL_OUTPOST",
    "test_gateway_e2e_credential_scan_real_outpost.py": "TRAILHEAD_REAL_OUTPOST",
}


def pytest_ignore_collect(collection_path, config):
    gate = _OPT_IN_FILES.get(collection_path.name)
    if gate is not None and os.environ.get(gate) != "1":
        return True
    return None  # not False: False would stop pytest's own ignore rules (--ignore, norecursedirs) from running
