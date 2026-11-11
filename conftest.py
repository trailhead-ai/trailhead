"""Root pytest hooks.

A default run leaves the opt-in tiers out (see `addopts` in pyproject.toml), so a green default
run does not cover them. The closing line names the tiers a run left out and the one command that
runs everything.
"""

from __future__ import annotations

import re

TIERS = ("chromium", "seam_real")
ALL_TIERS_COMMAND = '.venv/bin/python -m pytest -m "not benchmark"'


def deselected_tiers(markexpr: str) -> list[str]:
    return [tier for tier in TIERS if re.search(rf"\bnot\s+{tier}\b", markexpr)]


def pytest_unconfigure(config):
    if hasattr(config, "workerinput"):
        return
    left_out = deselected_tiers(config.getoption("markexpr", default="") or "")
    if left_out:
        config.get_terminal_writer().line(
            f"deselected tiers: {', '.join(left_out)} — run them with {ALL_TIERS_COMMAND}"
        )
