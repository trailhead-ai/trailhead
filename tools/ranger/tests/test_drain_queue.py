"""Tests for ranger.drain.queue — drain queue derivation and classification.

Test contract:
- Shape gate: a runnable task with a parent, or with children, is excluded
  even though `lore task list --runnable` itself would include it.
- Buildable payload: a `**Files:**` line naming a member-repo path is
  buildable; naming only record-id/vault paths is not; no `**Files:**` line
  at all is not.
- Slug collision: a task whose derived slug matches an existing workspace's
  slug, where that workspace's branch is NOT `worktree-<slug>`, is
  `skipped:collision`; a task whose derived slug matches a workspace whose
  branch IS `worktree-<slug>` (its own prior/in-flight attempt) is
  `buildable`, not a collision.
- `derive --json`-shaped output carries `bucket` and `slug` per entry.
- The drain outcome grammar: all four tokens with a mandatory argument
  round-trip; a missing argument or an unrecognized token fails to parse.
- Runner injection + error surfacing for `camp list` mirrors `run_lore`'s.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "ranger" / "plugins" / "ranger"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from ranger.drain import queue as drain_queue  # noqa: E402

_VAULT = "myvault"


def _task_entry(
    name: str,
    *,
    status: str = "ready",
    created_at: str = "2026-01-01T00:00:00Z",
    parent: str | None = None,
    children: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "status": status,
        "created-at": created_at,
        "updated-at": created_at,
        "parent": parent,
        "depends-on": [],
        "children": list(children or []),
    }


def _buildable_body(*, path: str = "tools/ranger/plugins/ranger/ranger/drain/foo.py") -> str:
    return f"# a task\n\n**Delivers:** something.\n\n**Files:** `{path}` (new).\n"


def _not_buildable_body_record_only() -> str:
    return "# a task\n\n**Delivers:** an ADR.\n\n**Files:** `adr/some-decision` (new).\n"


def _not_buildable_body_no_files_line() -> str:
    return "# a task\n\nJust prose, no Files line at all.\n"


def _workspace(slug: str, branch: str) -> dict:
    return {"slug": slug, "branch": branch, "workspace_path": f"/workspaces/{slug}"}


def _make_runner(*, tasks=None, bodies=None, workspaces=None, labels=None, tasks_rc=0, camp_rc=0):
    tasks = tasks if tasks is not None else []
    bodies = bodies if bodies is not None else {}
    workspaces = workspaces if workspaces is not None else []
    labels = labels if labels is not None else {}

    def runner(cmd, **kwargs):
        if cmd[:3] == ["lore", "task", "list"]:
            assert "--runnable" in cmd
            assert cmd[cmd.index("--vault") + 1] == _VAULT
            stdout = json.dumps(tasks) if tasks_rc == 0 else ""
            return subprocess.CompletedProcess(cmd, tasks_rc, stdout=stdout, stderr="err" if tasks_rc else "")
        if cmd[:3] == ["lore", "record", "show"]:
            record_id = cmd[3]
            name = record_id.split("/", 1)[1]
            payload = {
                "record_id": record_id,
                "kind": "task",
                "name": name,
                "sidecar": {"labels": labels.get(name, {})},
                "body": bodies.get(name, ""),
            }
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        if cmd[:2] == ["camp", "list"]:
            stdout = json.dumps(workspaces) if camp_rc == 0 else ""
            return subprocess.CompletedProcess(cmd, camp_rc, stdout=stdout, stderr="camp err" if camp_rc else "")
        raise AssertionError(f"unexpected cmd: {cmd!r}")

    return runner


# ---------------------------------------------------------------------------
# Shape gate
# ---------------------------------------------------------------------------


def test_excludes_runnable_tasks_with_a_parent_or_children():
    tasks = [
        _task_entry("standalone", parent=None, children=[]),
        _task_entry("has-parent", parent="some-parent"),
        _task_entry("has-children", children=["some-child"]),
    ]
    bodies = {"standalone": _buildable_body()}
    runner = _make_runner(tasks=tasks, bodies=bodies)

    result = drain_queue.derive_drain_queue(_VAULT, runner=runner)

    assert [e["name"] for e in result] == ["standalone"]


# ---------------------------------------------------------------------------
# Buildable payload
# ---------------------------------------------------------------------------


def test_files_line_naming_a_member_repo_path_is_buildable():
    tasks = [_task_entry("t1")]
    runner = _make_runner(tasks=tasks, bodies={"t1": _buildable_body()})

    result = drain_queue.derive_drain_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "buildable"


def test_files_line_naming_only_a_record_id_is_not_buildable():
    tasks = [_task_entry("t1")]
    runner = _make_runner(tasks=tasks, bodies={"t1": _not_buildable_body_record_only()})

    result = drain_queue.derive_drain_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "skipped:not-buildable"


def test_body_with_no_files_line_is_not_buildable():
    tasks = [_task_entry("t1")]
    runner = _make_runner(tasks=tasks, bodies={"t1": _not_buildable_body_no_files_line()})

    result = drain_queue.derive_drain_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "skipped:not-buildable"


@pytest.mark.parametrize(
    "token",
    ["task/some-task", "spec/some-spec", "adr/some-decision", "area/some-area"],
)
def test_various_record_kinds_are_not_member_repo_paths(token):
    body = f"# t\n\n**Files:** `{token}` (edit).\n"
    assert drain_queue.is_buildable_payload(body) is False


def test_vault_storage_path_is_not_a_member_repo_path():
    body = "# t\n\n**Files:** `/Users/x/.local/state/lore/vaults/trailhead/task/foo.md` (edit).\n"
    assert drain_queue.is_buildable_payload(body) is False


def test_mixed_files_line_with_one_member_repo_path_is_buildable():
    body = "# t\n\n**Files:** `task/some-task`, `tools/ranger/plugins/ranger/ranger/x.py` (new).\n"
    assert drain_queue.is_buildable_payload(body) is True


def test_bulleted_files_list_under_bare_header_is_buildable():
    body = (
        "# t\n\n**Files:**\n"
        "- `outpost/server/index.ts` (edit)\n"
        "- `outpost/server/api/pr-envelope.ts` (edit)\n"
    )
    assert drain_queue.is_buildable_payload(body) is True


def test_bulleted_files_list_without_backticks_is_buildable():
    body = "# t\n\n**Files:**\n- outpost/server/index.ts (edit)\n- outpost/server/api/x.ts\n"
    assert drain_queue.is_buildable_payload(body) is True


def test_bulleted_files_list_naming_only_record_ids_is_not_buildable():
    body = "# t\n\n**Files:**\n- `task/some-task` (edit)\n- `adr/some-decision`\n"
    assert drain_queue.is_buildable_payload(body) is False


def test_bulleted_list_ends_at_first_non_bullet_line():
    body = (
        "# t\n\n**Files:**\n- `task/some-task` (edit)\n\n"
        "## Test contract\n- `tools/ranger/tests/test_x.py` is not a Files entry\n"
    )
    assert drain_queue.is_buildable_payload(body) is False


def test_inline_unbackticked_comma_separated_paths_are_buildable():
    body = "# t\n\n**Files:** server/index.ts, server/api/pr-envelope.ts (edit).\n"
    assert drain_queue.is_buildable_payload(body) is True


@pytest.mark.parametrize("tail", ["None expected", "none", "n/a", "TBD", "-"])
def test_files_line_with_a_none_marker_is_not_buildable(tail):
    body = f"# t\n\n**Files:** {tail}\n"
    assert drain_queue.is_buildable_payload(body) is False


def test_bare_header_with_no_bullets_is_not_buildable():
    body = "# t\n\n**Files:**\n\nJust prose after the header.\n"
    assert drain_queue.is_buildable_payload(body) is False


# ---------------------------------------------------------------------------
# Slug collision
# ---------------------------------------------------------------------------


def test_intra_queue_same_slug_different_task_second_is_a_collision():
    """Two queued tasks whose names normalize to the same slug: the first (in
    listing/oldest-first order) keeps its own bucket; every later one with the
    same derived slug is a collision naming the first task and the slug —
    camp would re-enter the same workspace for both, silently mixing their
    changes."""
    tasks = [
        _task_entry("Fix Bug", created_at="2026-01-01T00:00:00Z"),
        _task_entry("fix-bug", created_at="2026-01-02T00:00:00Z"),
    ]
    bodies = {"Fix Bug": _buildable_body(), "fix-bug": _buildable_body()}
    runner = _make_runner(tasks=tasks, bodies=bodies)

    result = drain_queue.derive_drain_queue(_VAULT, runner=runner)

    by_name = {e["name"]: e for e in result}
    assert by_name["Fix Bug"]["bucket"] == "buildable"
    assert by_name["fix-bug"]["bucket"] == "skipped:collision"
    assert by_name["fix-bug"]["slug"] == "fix-bug"
    assert by_name["fix-bug"]["collision_with"] == "Fix Bug"


def test_existing_workspace_without_resume_label_is_a_collision():
    """A workspace already exists at the task's slug, but the task record
    carries no `craft/branch` label — there is no proof this workspace is
    this task's own, so it is not safe to reuse."""
    tasks = [_task_entry("fix-bug")]
    slug = drain_queue.derive_slug("fix-bug")
    workspaces = [_workspace(slug, f"worktree-{slug}")]
    runner = _make_runner(
        tasks=tasks, bodies={"fix-bug": _buildable_body()}, workspaces=workspaces, labels={}
    )

    result = drain_queue.derive_drain_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "skipped:collision"
    assert result[0]["slug"] == slug


def test_existing_workspace_with_matching_resume_label_is_the_tasks_own_not_a_collision():
    """The task record's own `craft/branch` label names this exact
    `worktree-<slug>` branch — the resume marker execute's ritual writes at
    dispatch — so this is provably this task's own prior attempt."""
    tasks = [_task_entry("fix-bug")]
    slug = drain_queue.derive_slug("fix-bug")
    workspaces = [_workspace(slug, f"worktree-{slug}")]
    runner = _make_runner(
        tasks=tasks,
        bodies={"fix-bug": _buildable_body()},
        workspaces=workspaces,
        labels={"fix-bug": {"craft/branch": f"worktree-{slug}"}},
    )

    result = drain_queue.derive_drain_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "buildable"


def test_existing_workspace_with_label_naming_a_different_branch_is_a_collision():
    """The label is present but names a different branch than this slug's
    expected `worktree-<slug>` — a stale or otherwise-owned label, not proof
    of ownership over the workspace actually sitting at this slug."""
    tasks = [_task_entry("fix-bug")]
    slug = drain_queue.derive_slug("fix-bug")
    workspaces = [_workspace(slug, f"worktree-{slug}")]
    runner = _make_runner(
        tasks=tasks,
        bodies={"fix-bug": _buildable_body()},
        workspaces=workspaces,
        labels={"fix-bug": {"craft/branch": "some-other-branch"}},
    )

    result = drain_queue.derive_drain_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "skipped:collision"
    assert result[0]["slug"] == slug


def test_no_matching_workspace_slug_is_not_a_collision():
    tasks = [_task_entry("t1")]
    workspaces = [_workspace("some-other-slug", "worktree-some-other-slug")]
    runner = _make_runner(tasks=tasks, bodies={"t1": _buildable_body()}, workspaces=workspaces)

    result = drain_queue.derive_drain_queue(_VAULT, runner=runner)

    assert result[0]["bucket"] == "buildable"


def test_slug_derivation_matches_camps_own_normalization_shape():
    assert drain_queue.derive_slug("Fix Bug!!") == "fix-bug"
    assert drain_queue.derive_slug("  leading-trailing  ") == "leading-trailing"
    assert drain_queue.derive_slug("already-slug-like") == "already-slug-like"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_ordering_is_oldest_first_by_created_at():
    tasks = [
        _task_entry("zeta", created_at="2026-01-02T00:00:00Z"),
        _task_entry("alpha", created_at="2026-01-01T00:00:00Z"),
    ]
    bodies = {n: _buildable_body() for n in ("zeta", "alpha")}
    runner = _make_runner(tasks=tasks, bodies=bodies)

    result = drain_queue.derive_drain_queue(_VAULT, runner=runner)

    assert [e["name"] for e in result] == ["alpha", "zeta"]


# ---------------------------------------------------------------------------
# Runner injection + error surfacing
# ---------------------------------------------------------------------------


def test_lore_task_list_failure_raises_named_error():
    runner = _make_runner(tasks_rc=1)

    with pytest.raises(drain_queue.QueueDeriveError, match="lore task list"):
        drain_queue.derive_drain_queue(_VAULT, runner=runner)


def test_camp_list_failure_raises_named_error():
    tasks = [_task_entry("t1")]
    runner = _make_runner(tasks=tasks, bodies={"t1": _buildable_body()}, camp_rc=1)

    with pytest.raises(drain_queue.QueueDeriveError, match="camp list"):
        drain_queue.derive_drain_queue(_VAULT, runner=runner)


def test_absent_camp_cli_raises_a_named_error_with_remediation(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(drain_queue.QueueDeriveError) as exc:
        drain_queue.run_camp(["list", "--json"], runner=None)

    assert "camp CLI not found on PATH" in str(exc.value)
    assert "install camp or adjust PATH" in str(exc.value)


# ---------------------------------------------------------------------------
# Slug normalization delegates to camp.spine, and shared default runner
# ---------------------------------------------------------------------------


def test_derive_slug_delegates_to_camp_spine_normalize_slug(monkeypatch):
    """`derive_slug` is not its own regex — it calls camp's own normalizer.
    Monkeypatch that call to a distinguishable stub and confirm the drain
    module reflects it, proving there is no local reimplementation left."""
    import camp.spine as camp_spine

    monkeypatch.setattr(
        camp_spine, "normalize_slug", lambda raw: ("stubbed-slug-value", True)
    )

    assert drain_queue.derive_slug("Fix Bug!!") == "stubbed-slug-value"


def test_derive_slug_raises_named_error_when_camp_bootstrap_fails(monkeypatch):
    """When camp is not importable, `derive_slug` raises `QueueDeriveError`
    naming the same remediation `ranger.sweep.preflight` already uses,
    rather than letting a raw ImportError escape."""

    def boom():
        raise drain_queue.QueueDeriveError(
            "camp is not importable, so no slug can be normalized (no module "
            "named 'camp'); install camp first: trailhead install --plugin camp"
        )

    monkeypatch.setattr(drain_queue, "_import_camp_spine", boom)

    with pytest.raises(drain_queue.QueueDeriveError) as exc:
        drain_queue.derive_slug("Fix Bug!!")

    assert "install camp first: trailhead install --plugin camp" in str(exc.value)


def test_run_camps_default_runner_is_sweep_queues_shared_default_runner():
    """`run_camp`'s no-runner-injected path resolves to
    `ranger.sweep.queue.default_runner` — proves the duplicate
    `_default_runner` copy is gone rather than merely renamed in place."""
    from ranger.sweep import queue as sweep_queue

    assert drain_queue.default_runner is sweep_queue.default_runner


def test_run_camp_picks_up_a_patched_sweep_default_runner(monkeypatch):
    """Patching `ranger.sweep.queue.default_runner` and re-binding the name
    in the drain module is what `run_camp`'s no-runner path actually calls —
    confirms it is the shared object, not a frozen local copy."""
    captured = {}

    def fake_default_runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(drain_queue, "default_runner", fake_default_runner)

    drain_queue.run_camp(["list", "--json"], runner=None)

    assert captured["cmd"] == ["camp", "list", "--json"]


# The drain outcome grammar lives in `ranger.drain.report`, and so do its
# tests (`test_drain_report.py`) — this module only derives the queue.
