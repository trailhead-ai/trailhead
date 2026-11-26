"""The closing line of a run that left the opt-in tiers out.

A default run leaves the opt-in tiers out (see `addopts` in pyproject.toml), so a green default
run does not cover them. The closing line names the tiers a run left out and the one command that
runs everything. The root `conftest.py` imports the hook; the tool configs (`tools/craft`,
`tools/lore`, `tools/camp`), which resolve their own rootdir and never load the root conftest,
load this module with `-p`.
"""

from __future__ import annotations

import os
import re

TIERS = ("chromium", "seam_real")
ALL_TIERS_COMMAND = '.venv/bin/python -m pytest -m "not benchmark"'
# Not a marker: conftest.py leaves the gateway end-to-end files out of collection unless this is set.
GATEWAY_TIER = "gateway-e2e"
GATEWAY_GATE = "TRAILHEAD_GATEWAY_E2E"
# Likewise: the files that start a real Outpost are left out unless this is set.
REAL_OUTPOST_TIER = "real-outpost"
REAL_OUTPOST_GATE = "TRAILHEAD_REAL_OUTPOST"


def deselected_tiers(markexpr: str) -> list[str]:
    return [tier for tier in TIERS if re.search(rf"\bnot\s+{tier}\b", markexpr)]


def pytest_unconfigure(config):
    if hasattr(config, "workerinput"):
        return
    left_out = deselected_tiers(config.getoption("markexpr", default="") or "")
    notes = []
    if os.environ.get(GATEWAY_GATE) != "1":
        left_out.append(GATEWAY_TIER)
        notes.append(f" ({GATEWAY_TIER}: {GATEWAY_GATE}=1, from a host terminal)")
    if os.environ.get(REAL_OUTPOST_GATE) != "1":
        left_out.append(REAL_OUTPOST_TIER)
        notes.append(f" ({REAL_OUTPOST_TIER}: {REAL_OUTPOST_GATE}=1, needs the sibling Outpost checkout)")
    if left_out:
        config.get_terminal_writer().line(
            f"deselected tiers: {', '.join(left_out)} — run them with {ALL_TIERS_COMMAND}{''.join(notes)}"
        )
