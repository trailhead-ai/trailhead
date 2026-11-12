"""Resolve the camp groups a drive may start a workspace in.

A hub maps to a camp launch account (its hub config); a group is a candidate when it
is launched on that account and binds the requested vault. ``camp groups --json`` is
the only source of group facts.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from trailhead.ranger.config import DEFAULT_LAUNCH_ACCOUNT, expand_home, read_hub_config

VAULT_NAME_PATTERN = r"[a-z0-9][a-z0-9-]*"
_VAULT_NAME = re.compile(VAULT_NAME_PATTERN)
_CAMP_TIMEOUT_SECONDS = 60


class DriveGroupsError(ValueError):
    """The groups cannot be resolved; nothing is guessed."""


def _camp_groups(env: dict[str, str]) -> list[dict]:
    camp = shutil.which("camp", path=env.get("PATH", ""))
    if camp is None:
        raise DriveGroupsError("camp is not on PATH; cannot list groups")
    try:
        proc = subprocess.run(
            [camp, "groups", "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=_CAMP_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DriveGroupsError(f"camp groups --json could not run: {exc}") from exc
    if proc.returncode != 0:
        reason = (proc.stderr.strip().splitlines() or ["no output"])[0]
        raise DriveGroupsError(f"camp groups --json failed (exit {proc.returncode}): {reason}")
    unparseable = DriveGroupsError("camp groups --json answered something unparseable")
    try:
        groups = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise unparseable from exc
    if not isinstance(groups, list) or not all(_well_formed(g) for g in groups):
        raise unparseable
    return groups


def _well_formed(group: object) -> bool:
    return (
        isinstance(group, dict)
        and isinstance(group.get("name"), str)
        and (group.get("account") is None or isinstance(group["account"], str))
        and isinstance(group.get("vaults"), list)
        and all(isinstance(v, str) for v in group["vaults"])
    )


def drive_groups(account: str, vault: str, *, env: dict[str, str]) -> list[dict]:
    """The groups on *account*'s launch account that bind *vault*."""
    if not _VAULT_NAME.fullmatch(vault):
        raise DriveGroupsError(f"vault name {vault!r} must match {VAULT_NAME_PATTERN}")
    launch = read_hub_config(account, env=env).launch_account
    groups = _camp_groups(env)
    home = env.get("HOME", "")

    def on_launch_account(group: dict) -> bool:
        if launch == DEFAULT_LAUNCH_ACCOUNT:
            return group["account"] is None
        return group["account"] is not None and expand_home(group["account"], home) == expand_home(launch, home)

    return [g for g in groups if on_launch_account(g) and vault in g["vaults"]]
