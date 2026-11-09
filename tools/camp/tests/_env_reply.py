"""Builders for a Docker inventory reply, shared by the tests that feed `parse_inventory`."""

from __future__ import annotations

import json

from camp.env.contract import INVENTORY_HEADER


def record(*fields: object) -> str:
    return "|".join(json.dumps(f, ensure_ascii=False) for f in fields)


def reply(*lines: str) -> str:
    return "\n".join([INVENTORY_HEADER, *lines]) + "\n"
