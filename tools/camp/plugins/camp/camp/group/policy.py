"""Reduce a loaded camp group config to its comparable policy projection.

A group config is per-machine, and its raw shape is not directly comparable
across hosts: several fields are filesystem paths (`repo_root`, `launch.roots`,
a shared vault's `root`) that legitimately differ by placement even when two
machines agree about the group's *policy*. This module reduces a config loaded
by `camp.group.config.load_group` down to the fields that ought to agree across
machines, and compares two such projections.

**Included** in the projection: the group name; the branch pattern; the member
name set; per member, its base and its declared, ordered task list; per task,
its phase, required flag, timeout, capability, cleanup, and step command
vectors (step names are cosmetic labels, not policy, and are left out); the
declared `[launch] account`; shared-vault names; lore scopes.

**Excluded**: every filesystem path — a member's `repo_root`, the `[launch]
roots` allowlist, and a shared vault's `root` — plus `_toml_path` (the loader's
own bookkeeping key, itself a path) and the `[harness]` block. `[harness]` is
excluded deliberately, not merely unhandled: its effective `inject` default
depends on whether the block is *present* in the TOML at all, not on its
contents (`camp.launch.profile.resolve_harness_profile`), so two hosts with the
same effective harness policy can carry opposite raw values. There is no field
extraction that makes that comparison meaningful, so the block is left out
entirely.

**Trust boundary.** A task step's `cmd` and `cleanup` tokens are stored by the
loader as unexpanded placeholder templates (`{repo_root}`, `{worktree}`, ...)
and compared as such, so a legitimate placeholder-templated command never leaks
a resolved filesystem path into the comparison. The loader rejects an unknown
`{token}`, but it does not — and this projection cannot — detect or normalize
an author who hardcodes an absolute path directly into a `cmd` or `cleanup`
token instead of using a placeholder. That remains author-trusted local input,
as documented in `camp.group.config`.

A member's raw `tasks` list is never compared, hashed, or copied wholesale: a
legacy-synthesized task (from `bootstrap` or `[[members.hooks]]`) omits the
`cleanup`/`capability` keys entirely, while a modern `[tasks.<name>]` task
always carries both, even when unset — so the raw dicts fail `==` while every
field extracted via `.get()` is identical. Every per-task field below is
extracted key-by-key with `.get()` for exactly that reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Top-level keys `load_group` may return, classified so a group field added
# later and never classified here fails a schema-coverage test loudly instead
# of silently falling through the projection.
INCLUDED_TOP_LEVEL_KEYS = frozenset(
    {"group", "members", "branch_pattern", "shared_vaults", "lore_scopes", "launch"}
)
EXCLUDED_TOP_LEVEL_KEYS = frozenset({"_toml_path", "harness"})


def _project_task(task: dict[str, Any]) -> dict[str, Any]:
    """Project one resolved task dict, field-by-field via `.get()`.

    Never reads or copies the raw dict wholesale — see the module docstring's
    trust-boundary note on why a legacy-synthesized task and a modern
    `[tasks.<name>]` task must be compared by field, not by dict equality.
    """
    return {
        "name": task.get("name"),
        "phase": task.get("phase"),
        "required": task.get("required"),
        "timeout_seconds": task.get("timeout_seconds"),
        "capability": task.get("capability"),
        "cleanup": task.get("cleanup"),
        "steps": [list(step.get("cmd") or []) for step in task.get("steps") or []],
    }


def _project_member(member: dict[str, Any]) -> dict[str, Any]:
    """Project one resolved member dict: base, and its ordered task list.

    Excludes `repo_root` (a filesystem path) and `excluded` (regenerable-state
    paths, not policy) — neither is in the design's compared field list.
    """
    return {
        "base": member.get("base"),
        "tasks": [_project_task(t) for t in member.get("tasks") or []],
    }


def project_group_policy(config: dict[str, Any]) -> dict[str, Any]:
    """Reduce a `load_group`-shaped dict to its comparable policy projection.

    The result is plain `dict`/`list`/`str`/`int`/`bool`/`None` — JSON
    serializable with stdlib `json.dumps`, unmodified, because a caller puts
    it on a wire.
    """
    launch = config.get("launch") or {}
    return {
        "group_name": (config.get("group") or {}).get("name"),
        "branch_pattern": config.get("branch_pattern"),
        "members": {
            member.get("name"): _project_member(member)
            for member in config.get("members") or []
        },
        "account": launch.get("account"),
        "shared_vaults": [sv.get("name") for sv in config.get("shared_vaults") or []],
        "lore_scopes": [
            {"scope": ls.get("scope"), "name": ls.get("name")}
            for ls in config.get("lore_scopes") or []
        ],
    }


@dataclass(frozen=True)
class PolicyComparison:
    """The result of comparing two policy projections.

    `differences` names the dimensions that differ, in a stable order —
    never the values themselves (a step command vector is exactly the kind of
    field an operator could accidentally embed a credential in; naming the
    dimension is enough to point at where to look).
    """

    matches: bool
    differences: tuple[str, ...]


def compare_group_policy(a: dict[str, Any], b: dict[str, Any]) -> PolicyComparison:
    """Compare two policy projections, naming every dimension that differs."""
    differences: list[str] = []

    if a.get("group_name") != b.get("group_name"):
        differences.append("group_name")
    if a.get("branch_pattern") != b.get("branch_pattern"):
        differences.append("branch_pattern")

    members_a: dict[str, Any] = a.get("members") or {}
    members_b: dict[str, Any] = b.get("members") or {}

    for name in sorted(set(members_a) - set(members_b)):
        differences.append(f"member only in a: {name}")
    for name in sorted(set(members_b) - set(members_a)):
        differences.append(f"member only in b: {name}")

    for name in sorted(set(members_a) & set(members_b)):
        member_a = members_a[name]
        member_b = members_b[name]
        if member_a.get("base") != member_b.get("base"):
            differences.append(f"member:{name}.base")

        tasks_a = {t.get("name"): t for t in member_a.get("tasks") or []}
        tasks_b = {t.get("name"): t for t in member_b.get("tasks") or []}
        for task_name in sorted(set(tasks_a) - set(tasks_b)):
            differences.append(f"member:{name}.task only in a: {task_name}")
        for task_name in sorted(set(tasks_b) - set(tasks_a)):
            differences.append(f"member:{name}.task only in b: {task_name}")
        for task_name in sorted(set(tasks_a) & set(tasks_b)):
            if tasks_a[task_name] != tasks_b[task_name]:
                differences.append(f"member:{name}.task:{task_name}")

    if a.get("account") != b.get("account"):
        differences.append("account")
    if a.get("shared_vaults") != b.get("shared_vaults"):
        differences.append("shared_vaults")
    if a.get("lore_scopes") != b.get("lore_scopes"):
        differences.append("lore_scopes")

    return PolicyComparison(matches=not differences, differences=tuple(differences))
