"""Group config loader for camp.

Loads TOML group configs from trailhead.paths.config_dir("camp")/groups/<group>.toml.

Schema:
  [group]
  name = "<group-name>"

  [[members]]
  name = "<repo-name>"
  repo_root = "/absolute/path/to/repo"
  bootstrap = ["cmd", "arg1", "arg2"]   # legacy; list for shell=False; optional
  base = "origin/main"                  # branch start-point; optional, default origin/main
  tasks = ["graphify"]                  # optional; names must match [tasks.<name>] below

  [[members.hooks]]
  kind = "dep-install"                  # legacy; keyed activation hook kind; required
  cmd = ["cmd", "arg1", "arg2"]        # list for subprocess shell=False; required

  [tasks.graphify]
  phase = "provision"                   # optional; "provision" (default) | "activate"
  required = false                      # optional; default false
  timeout_seconds = 30                  # optional; positive int; per-task subprocess timeout

  [[tasks.graphify.steps]]
  name = "seed"                         # required; legible label (status/failure reasons)
  cmd = ["rsync", "-a", "{repo_root}/x/", "{worktree}/x/"]  # required; shell=False argv

  [branch]
  pattern = "worktree-{slug}"            # optional; default "worktree-{slug}"

  [dev_env]                              # optional; warn-and-continue (deferred)
  ...

  [[lore_scopes]]                        # optional; repeatable; one entry per scope
  scope = "product"                      # one of: repo, product, suite, team
  name  = "<vault-name>"                 # non-empty; no duplicate scope in one group

Task steps, bootstrap, and hook commands are author-trusted local input. camp
runs them list-mode (subprocess, shell=False). Sharing group configs from
untrusted authors is explicitly out of scope.

Activation hook kinds:
  "dep-install"   Run a dependency installation command in the worktree.

Legacy normalization: at load time, a member's `bootstrap` list normalizes
into an implicit REQUIRED provision-phase task named literally "bootstrap"
(preserving its argv), and `[[members.hooks]]` dep-install entries normalize
into an implicit REQUIRED activate-phase task named literally "dep-install".
The `bootstrap` and `hooks` keys are then DROPPED from the normalized member
dict — every consumer downstream of load_group sees exactly one resolved,
ordered `tasks` list per member (implicit legacy tasks first, then tasks
referenced by name via the member's `tasks` field). A member cannot both
produce an implicit legacy task and separately reference a task of the same
name — that is a name collision, rejected at load.

lore_scopes invariants: scope in {repo, product, suite, team} ("default" is
rejected — it is the unconditional floor in vault_resolve, not a routing target);
name is a non-empty string; no duplicate scope within one group's list.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GroupConfigError(Exception):
    """Raised when a group config file exists but is malformed or missing required fields."""


class GroupConfigNotFound(Exception):
    """Raised when a group config file does not exist.

    The message includes a first-run hint pointing at groups.example/trailhead.toml.
    """


# ---------------------------------------------------------------------------
# Known activation hook kinds
# ---------------------------------------------------------------------------

KNOWN_HOOK_KINDS = frozenset({"dep-install"})

# ---------------------------------------------------------------------------
# [tasks.<name>] — config-driven multi-step member tasks
# ---------------------------------------------------------------------------

# Placeholders task step argv may reference. Distinct from _HARNESS_PLACEHOLDERS
# (harness.cwd's {slug}/{workspace}) — task steps run in a specific
# member's worktree, so they additionally get {repo_root}/{worktree}.
_TASK_PLACEHOLDERS = frozenset({"repo_root", "worktree", "workspace", "slug"})

_TASK_PHASES = frozenset({"provision", "activate"})
_DEFAULT_TASK_PHASE = "provision"

# Legacy bootstrap/hooks normalize into implicit tasks under these literal,
# legible names (they appear in `camp status` output and persisted failure
# reasons) — not internal tokens. Reserved: a member cannot both carry the
# legacy config that produces one of these implicit tasks AND separately
# reference a same-named task via `tasks = [...]` — see the collision check
# in load_group.
_LEGACY_BOOTSTRAP_TASK_NAME = "bootstrap"
_LEGACY_DEP_INSTALL_TASK_NAME = "dep-install"

# ---------------------------------------------------------------------------
# Valid lore routing scopes
# ---------------------------------------------------------------------------

# "default" is omitted: it is the unconditional floor in vault_resolve, not a
# meaningful routing target.  Declaring a binding to default would route to
# whatever vault happens to be the fallback — always wrong.
_VALID_LORE_SCOPES = frozenset({"repo", "product", "suite", "team"})


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_FIRST_RUN_HINT = "copy groups.example/trailhead.toml as a starting point"


def load_group(path: Path) -> dict[str, Any]:
    """Load and validate a group TOML config file.

    Returns a normalized dict:
      {
        "group": {"name": str},
        "members": [{
            "name": str, "repo_root": str, "base": str,
            "tasks": [{"name": str, "phase": "provision"|"activate",
                       "required": bool, "timeout_seconds": int | None,
                       "steps": [{"name": str, "cmd": list[str]}]}],
        }],
        "branch_pattern": str,
        "shared_vaults": [{"name": str, "root": str}],
        "lore_scopes": [{"scope": str, "name": str}],
      }

    Each member's "tasks" is the fully resolved, ordered list — implicit legacy
    tasks (normalized from that member's "bootstrap"/"hooks" TOML fields, if
    any) first, then tasks referenced by name via the member's `tasks = [...]`
    field. The legacy "bootstrap"/"hooks" keys are NOT present on the returned
    member dict — this is the only shape downstream code should read.

    lore_scopes invariants: scope in {repo, product, suite, team}; name is a
    non-empty string; no duplicate scope within one group's list.

    Raises:
        GroupConfigNotFound: If path does not exist.
        GroupConfigError: If the file is malformed or missing required fields.
            The message always names the file and the failing field.
    """
    if not path.is_file():
        raise GroupConfigNotFound(
            f"No group config found at {path!s}; expected a TOML file.\n  {_FIRST_RUN_HINT}"
        )

    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise GroupConfigError(f"{path}: TOML parse error — {e}") from e

    # --- [group] section ---
    group_section = raw.get("group")
    if not isinstance(group_section, dict):
        raise GroupConfigError(f"{path}: missing required [group] section")

    group_name = group_section.get("name")
    if not isinstance(group_name, str) or not group_name.strip():
        raise GroupConfigError(
            f"{path}: field 'group.name' is required and must be a non-empty string"
        )

    # --- [tasks.<name>] section (optional; may be referenced by members below) ---
    task_defs = _parse_tasks(raw.get("tasks"), path)

    # --- [[members]] section ---
    members_raw = raw.get("members")
    if not isinstance(members_raw, list) or len(members_raw) == 0:
        raise GroupConfigError(f"{path}: field 'members' must be a non-empty list of member tables")

    members: list[dict[str, Any]] = []
    for i, m in enumerate(members_raw):
        if not isinstance(m, dict):
            raise GroupConfigError(f"{path}: members[{i}] must be a table")

        member_name = m.get("name")
        if not isinstance(member_name, str) or not member_name.strip():
            raise GroupConfigError(
                f"{path}: members[{i}].name is required and must be a non-empty string"
            )

        repo_root = m.get("repo_root")
        if not isinstance(repo_root, str) or not repo_root.strip():
            raise GroupConfigError(
                f"{path}: members[{i}] ('{member_name}'): field 'repo_root' is required "
                "and must be a non-empty string"
            )

        bootstrap_raw = m.get("bootstrap", [])
        if bootstrap_raw is None:
            bootstrap_raw = []
        if not isinstance(bootstrap_raw, list):
            raise GroupConfigError(
                f"{path}: members[{i}] ('{member_name}'): field 'bootstrap' must be a list "
                "of strings (for subprocess shell=False), not a shell string"
            )
        for j, cmd_part in enumerate(bootstrap_raw):
            if not isinstance(cmd_part, str):
                raise GroupConfigError(
                    f"{path}: members[{i}] ('{member_name}'): "
                    f"bootstrap[{j}] must be a string, got {type(cmd_part).__name__!r}"
                )

        base = m.get("base", "origin/main")
        if not isinstance(base, str) or not base.strip():
            raise GroupConfigError(
                f"{path}: members[{i}] ('{member_name}'): field 'base' must be a "
                "non-empty string (the branch start-point, e.g. 'origin/main')"
            )

        # --- [[members.hooks]] section (optional) ---
        hooks_raw = m.get("hooks", [])
        if hooks_raw is None:
            hooks_raw = []
        if not isinstance(hooks_raw, list):
            raise GroupConfigError(
                f"{path}: members[{i}] ('{member_name}'): field 'hooks' must be a list "
                "of hook tables"
            )
        hooks: list[dict] = []
        for k, hook in enumerate(hooks_raw):
            if not isinstance(hook, dict):
                raise GroupConfigError(
                    f"{path}: members[{i}] ('{member_name}'): hooks[{k}] must be a table"
                )

            hook_kind = hook.get("kind")
            if not isinstance(hook_kind, str) or not hook_kind.strip():
                raise GroupConfigError(
                    f"{path}: members[{i}] ('{member_name}'): hooks[{k}].kind is required "
                    "and must be a non-empty string"
                )
            if hook_kind not in KNOWN_HOOK_KINDS:
                raise GroupConfigError(
                    f"{path}: members[{i}] ('{member_name}'): hooks[{k}].kind "
                    f"{hook_kind!r} is not a known hook kind — "
                    f"supported kinds: {sorted(KNOWN_HOOK_KINDS)}"
                )

            cmd_raw = hook.get("cmd")
            if cmd_raw is None:
                raise GroupConfigError(
                    f"{path}: members[{i}] ('{member_name}'): hooks[{k}] "
                    f"(kind={hook_kind!r}) is missing required field 'cmd'"
                )
            cmd = _validate_string_list_field(
                cmd_raw,
                path=path,
                where=f"members[{i}] ('{member_name}'): hooks[{k}].cmd",
                allow_empty_list=False,
            )

            hooks.append({"kind": hook_kind, "cmd": cmd})

        # --- 'tasks' member field (optional): references into [tasks.<name>] ---
        member_tasks_raw = m.get("tasks", [])
        if member_tasks_raw is None:
            member_tasks_raw = []
        referenced_task_names = _validate_string_list_field(
            member_tasks_raw,
            path=path,
            where=f"members[{i}] ('{member_name}'): field 'tasks'",
            allow_empty_list=True,
        )

        # Legacy bootstrap/hooks normalize into implicit tasks; the 'bootstrap'
        # and 'hooks' keys are dropped from the normalized member dict below —
        # downstream code sees exactly one resolved 'tasks' list per member.
        resolved_tasks = _resolve_member_tasks(
            member_name=member_name,
            task_defs=task_defs,
            referenced_task_names=referenced_task_names,
            bootstrap_raw=bootstrap_raw,
            hooks=hooks,
            path=path,
        )

        members.append(
            {
                "name": member_name,
                "repo_root": repo_root,
                "base": base,
                "tasks": resolved_tasks,
            }
        )

    # --- [branch] section (optional) ---
    branch_section = raw.get("branch") or {}
    branch_pattern = branch_section.get("pattern", "worktree-{slug}")
    if not isinstance(branch_pattern, str):
        raise GroupConfigError(f"{path}: field 'branch.pattern' must be a string")

    # --- [harness] section (optional) — harness profile config ---
    harness = _parse_harness(raw.get("harness"), path)

    # --- [dev_env] section — warn-and-continue (deferred) ---
    if "dev_env" in raw:
        print(
            f"camp: [dev_env] in {path.name!r} is not yet supported — "
            "dev-env capability deferred; ignored.",
            file=sys.stderr,
        )

    # --- [[shared_vaults]] section (optional) ---
    shared_vaults_raw = raw.get("shared_vaults")
    if shared_vaults_raw is None:
        shared_vaults_raw = []
    if not isinstance(shared_vaults_raw, list):
        raise GroupConfigError(f"{path}: field 'shared_vaults' must be a list of tables")

    shared_vaults: list[dict[str, Any]] = []
    for i, sv in enumerate(shared_vaults_raw):
        if not isinstance(sv, dict):
            raise GroupConfigError(f"{path}: shared_vaults[{i}] must be a table")

        sv_name = sv.get("name")
        if not isinstance(sv_name, str) or not sv_name.strip():
            raise GroupConfigError(
                f"{path}: shared_vaults[{i}].name is required and must be a non-empty string"
            )

        sv_root = sv.get("root")
        if not isinstance(sv_root, str) or not sv_root.strip():
            raise GroupConfigError(
                f"{path}: shared_vaults[{i}] ('{sv_name}'): field 'root' is required "
                "and must be a non-empty string"
            )

        shared_vaults.append({"name": sv_name, "root": sv_root})

    # --- [[lore_scopes]] section (optional) ---
    lore_scopes_raw = raw.get("lore_scopes")
    if lore_scopes_raw is None:
        lore_scopes_raw = []
    if not isinstance(lore_scopes_raw, list):
        raise GroupConfigError(f"{path}: field 'lore_scopes' must be a list of tables")

    lore_scopes: list[dict[str, Any]] = []
    seen_scopes: set[str] = set()
    for i, ls in enumerate(lore_scopes_raw):
        if not isinstance(ls, dict):
            raise GroupConfigError(f"{path}: lore_scopes[{i}] must be a table")

        ls_scope = ls.get("scope")
        if not isinstance(ls_scope, str) or not ls_scope.strip():
            raise GroupConfigError(
                f"{path}: lore_scopes[{i}].scope is required and must be a non-empty string"
            )
        # Normalize surrounding whitespace before validating/storing so a padded
        # value (e.g. "product ") is accepted as the scope the author meant rather
        # than rejected as unknown.
        ls_scope = ls_scope.strip()
        if ls_scope not in _VALID_LORE_SCOPES:
            raise GroupConfigError(
                f"{path}: lore_scopes[{i}].scope {ls_scope!r} is not a valid routing scope — "
                f"supported scopes: {sorted(_VALID_LORE_SCOPES)}"
            )
        if ls_scope in seen_scopes:
            raise GroupConfigError(
                f"{path}: lore_scopes[{i}].scope {ls_scope!r} is declared more than once — "
                "each scope may appear at most once per group"
            )
        seen_scopes.add(ls_scope)

        ls_name = ls.get("name")
        if not isinstance(ls_name, str) or not ls_name.strip():
            raise GroupConfigError(
                f"{path}: lore_scopes[{i}].name is required and must be a non-empty string"
            )
        # Store the trimmed name so it matches the elected vault — a padded name
        # ("trailhead ") would otherwise route nowhere despite passing validation.
        ls_name = ls_name.strip()

        lore_scopes.append({"scope": ls_scope, "name": ls_name})

    result: dict[str, Any] = {
        "group": {"name": group_name},
        "members": members,
        "branch_pattern": branch_pattern,
        "shared_vaults": shared_vaults,
        "lore_scopes": lore_scopes,
        "_toml_path": str(path),
    }
    if harness is not None:
        result["harness"] = harness
    return result


# ---------------------------------------------------------------------------
# [harness] profile block
# ---------------------------------------------------------------------------

# Placeholders the launch templates / cwd may reference. Any other {token} is a
# misconfiguration (would KeyError at substitution time) → rejected at load.
_HARNESS_PLACEHOLDERS = frozenset({"slug", "workspace"})

# Mid-session context-injection strategies. "stdout" is the universal
# floor; "claude-hook" opts into the Claude Code PostToolUse → additionalContext
# channel. An unknown value is rejected at load with a legible error.
_INJECT_STRATEGIES = frozenset({"stdout", "claude-hook"})


def _reject_unknown_placeholders(
    value: str,
    *,
    path: Path,
    where: str,
    known: frozenset[str] = _HARNESS_PLACEHOLDERS,
) -> None:
    """Reject any {placeholder} not in the known set so a typo'd template fails
    legibly at load instead of with a KeyError at launch time.

    `known` defaults to the harness-profile placeholder set; task steps pass
    `known=_TASK_PLACEHOLDERS` to validate against their own set instead of a
    parallel re-implementation of this scan.
    """
    import string

    for _, field, _, _ in string.Formatter().parse(value):
        if field is not None and field not in known:
            raise GroupConfigError(
                f"{path}: {where} references unknown placeholder {{{field}}} — "
                f"supported placeholders: {sorted(known)}"
            )


def _validate_string_list_field(
    value: Any,
    *,
    path: Path,
    where: str,
    allow_empty_list: bool,
) -> list[str]:
    """Validate a field that must be a non-empty list of non-blank strings.

    Each token is checked for type (must be str) and stripped-and-rejected if
    empty or whitespace-only.  allow_empty_list=False rejects an empty list (e.g.
    argv templates); True would permit it — currently always False at call sites
    but the flag exists so the helper can be reused without kwargs surgery.
    """
    if not isinstance(value, list) or (not allow_empty_list and len(value) == 0):
        raise GroupConfigError(f"{path}: {where} must be a non-empty list of strings")
    for i, token in enumerate(value):
        if not isinstance(token, str):
            raise GroupConfigError(
                f"{path}: {where}[{i}] must be a string, got {type(token).__name__!r}"
            )
        if not token.strip():
            raise GroupConfigError(
                f"{path}: {where}[{i}] is empty or whitespace-only — "
                "empty tokens mask misconfiguration"
            )
    return list(value)


def _parse_harness(raw: Any, path: Path) -> dict[str, Any] | None:
    """Parse + validate the optional [harness] block. Returns None when absent.

    Every field is OPTIONAL — a [harness] block containing only doc_files (or
    only cwd, or only binary) is valid.  Fields that are absent are simply not
    included in the returned dict; resolve_harness_profile merges per-field against
    _CLAUDE_DEFAULT at resolution time.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise GroupConfigError(f"{path}: [harness] must be a table")

    result: dict[str, Any] = {}

    # `binary` is a single harness binary NAME (e.g. "claude", "codex", or an
    # absolute path). camp does not launch an argv — only the basename is read,
    # by is_claude_launch(), to scope the trust pre-seed. It is a plain string (no
    # {slug}/{workspace} templating).
    if "binary" in raw:
        binary = raw["binary"]
        if not isinstance(binary, str) or not binary.strip():
            raise GroupConfigError(f"{path}: harness.binary must be a non-empty string")
        result["binary"] = binary

    if "cwd" in raw:
        cwd = raw["cwd"]
        if not isinstance(cwd, str) or not cwd.strip():
            raise GroupConfigError(f"{path}: harness.cwd must be a non-empty string")
        _reject_unknown_placeholders(cwd, path=path, where="harness.cwd")
        result["cwd"] = cwd

    doc_files_raw = raw.get("doc_files")
    if doc_files_raw is not None:
        if not isinstance(doc_files_raw, list) or len(doc_files_raw) == 0:
            raise GroupConfigError(
                f"{path}: harness.doc_files must be a non-empty list of strings "
                '(workspace doc filenames, e.g. ["CLAUDE.md"] or ["AGENTS.md"])'
            )
        result["doc_files"] = _validate_string_list_field(
            doc_files_raw, path=path, where="harness.doc_files", allow_empty_list=False
        )

    if "inject" in raw:
        inject = raw["inject"]
        if not isinstance(inject, str) or inject not in _INJECT_STRATEGIES:
            raise GroupConfigError(
                f"{path}: harness.inject {inject!r} is not a known injection "
                f"strategy — supported strategies: {sorted(_INJECT_STRATEGIES)}"
            )
        result["inject"] = inject

    if "pretrust" in raw:
        pretrust = raw["pretrust"]
        if not isinstance(pretrust, bool):
            raise GroupConfigError(
                f"{path}: harness.pretrust must be a boolean (true/false), got "
                f"{type(pretrust).__name__!r}"
            )
        result["pretrust"] = pretrust

    return result


def _parse_tasks(raw: Any, path: Path) -> dict[str, dict[str, Any]]:
    """Parse + validate the optional top-level [tasks.<name>] table.

    Returns a dict keyed by task name; each value is a normalized task dict:
      {"name": str, "phase": "provision"|"activate", "required": bool,
       "timeout_seconds": int | None, "steps": [{"name": str, "cmd": [str, ...]}]}

    Placeholder validation in step argv reuses _reject_unknown_placeholders
    against _TASK_PLACEHOLDERS — no parallel implementation.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise GroupConfigError(f"{path}: [tasks] must be a table of named task tables")

    tasks: dict[str, dict[str, Any]] = {}
    for task_name, task_tbl in raw.items():
        if not isinstance(task_tbl, dict):
            raise GroupConfigError(f"{path}: tasks.{task_name} must be a table")

        phase = task_tbl.get("phase", _DEFAULT_TASK_PHASE)
        if not isinstance(phase, str) or phase not in _TASK_PHASES:
            raise GroupConfigError(
                f"{path}: tasks.{task_name}.phase must be one of "
                f"{sorted(_TASK_PHASES)}, got {phase!r}"
            )

        required = task_tbl.get("required", False)
        if not isinstance(required, bool):
            raise GroupConfigError(
                f"{path}: tasks.{task_name}.required must be a boolean (true/false), "
                f"got {type(required).__name__!r}"
            )

        timeout_seconds = task_tbl.get("timeout_seconds")
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
        ):
            raise GroupConfigError(
                f"{path}: tasks.{task_name}.timeout_seconds must be a positive integer, "
                f"got {timeout_seconds!r}"
            )

        steps_raw = task_tbl.get("steps")
        if not isinstance(steps_raw, list) or len(steps_raw) == 0:
            raise GroupConfigError(
                f"{path}: tasks.{task_name}.steps must be a non-empty list of step tables"
            )

        steps: list[dict[str, Any]] = []
        for i, step in enumerate(steps_raw):
            if not isinstance(step, dict):
                raise GroupConfigError(f"{path}: tasks.{task_name}.steps[{i}] must be a table")

            step_name = step.get("name")
            if not isinstance(step_name, str) or not step_name.strip():
                raise GroupConfigError(
                    f"{path}: tasks.{task_name}.steps[{i}].name is required and must "
                    "be a non-empty string"
                )

            cmd_raw = step.get("cmd")
            if cmd_raw is None:
                raise GroupConfigError(
                    f"{path}: tasks.{task_name}.steps[{i}] ('{step_name}') is missing "
                    "required field 'cmd'"
                )
            where = f"tasks.{task_name}.steps[{i}] ('{step_name}').cmd"
            cmd = _validate_string_list_field(
                cmd_raw, path=path, where=where, allow_empty_list=False
            )
            for token in cmd:
                _reject_unknown_placeholders(
                    token, path=path, where=where, known=_TASK_PLACEHOLDERS
                )

            steps.append({"name": step_name, "cmd": cmd})

        tasks[task_name] = {
            "name": task_name,
            "phase": phase,
            "required": required,
            "timeout_seconds": timeout_seconds,
            "steps": steps,
        }

    return tasks


def _resolve_member_tasks(
    *,
    member_name: str,
    task_defs: dict[str, dict[str, Any]],
    referenced_task_names: list[str],
    bootstrap_raw: list[str],
    hooks: list[dict[str, Any]],
    path: Path,
) -> list[dict[str, Any]]:
    """Resolve one member's ordered task list: implicit legacy tasks (from
    bootstrap/hooks normalization) ordered before explicitly referenced group
    tasks. Raises on an unknown reference or a name collision between an
    implicit legacy task and a referenced group task.
    """
    resolved: list[dict[str, Any]] = []

    if bootstrap_raw:
        resolved.append(
            {
                "name": _LEGACY_BOOTSTRAP_TASK_NAME,
                "phase": "provision",
                "required": True,
                "timeout_seconds": None,
                "steps": [
                    {"name": _LEGACY_BOOTSTRAP_TASK_NAME, "cmd": list(bootstrap_raw)}
                ],
            }
        )

    if hooks:
        resolved.append(
            {
                "name": _LEGACY_DEP_INSTALL_TASK_NAME,
                "phase": "activate",
                "required": True,
                "timeout_seconds": None,
                "steps": [
                    {
                        "name": (
                            _LEGACY_DEP_INSTALL_TASK_NAME
                            if len(hooks) == 1
                            else f"{_LEGACY_DEP_INSTALL_TASK_NAME}-{k}"
                        ),
                        "cmd": hook["cmd"],
                    }
                    for k, hook in enumerate(hooks)
                ],
            }
        )

    implicit_names = {t["name"] for t in resolved}

    for j, task_name in enumerate(referenced_task_names):
        if task_name not in task_defs:
            raise GroupConfigError(
                f"{path}: members ('{member_name}'): tasks[{j}] references unknown "
                f"task {task_name!r} — no [tasks.{task_name}] is defined"
            )
        if task_name in implicit_names:
            raise GroupConfigError(
                f"{path}: members ('{member_name}'): tasks[{j}] references "
                f"{task_name!r}, which collides with the implicit legacy task of "
                "the same name — rename the legacy config or the referenced task"
            )
        resolved.append(task_defs[task_name])

    return resolved


def load_all_groups(groups_dir: Path) -> list[dict[str, Any]]:
    """Load all .toml files in groups_dir and return a list of parsed configs.

    Files that fail to parse raise GroupConfigError (caller decides whether to
    surface or skip). Non-.toml files are ignored.

    Args:
        groups_dir: Directory to scan.

    Returns:
        List of parsed group configs (may be empty if directory is empty).
    """
    if not groups_dir.is_dir():
        return []

    configs: list[dict[str, Any]] = []
    for toml_file in sorted(groups_dir.glob("*.toml")):
        configs.append(load_group(toml_file))
    return configs
