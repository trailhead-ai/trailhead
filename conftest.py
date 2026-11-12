"""Root pytest hooks: the closing line naming the opt-in tiers a run left out, and the opt-in gateway file."""

import os

from tier_closing_line import pytest_unconfigure  # noqa: F401

# The gateway end-to-end file needs a real Outpost, the Claude Code sandbox and the operator's
# dotfiles. Unless TRAILHEAD_GATEWAY_E2E=1 it is not collected, so a default run does not count
# its tests as skips against the suite's skipped-count ceiling.
_OPT_IN_FILES = {"test_gateway_e2e_andromeda.py": "TRAILHEAD_GATEWAY_E2E"}


def pytest_ignore_collect(collection_path, config):
    gate = _OPT_IN_FILES.get(collection_path.name)
    return gate is not None and os.environ.get(gate) != "1"
