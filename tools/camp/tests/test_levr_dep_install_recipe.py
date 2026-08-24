"""Recipe-shape regression test for the levr group's dependency-install task.

`npm ci` moves from the `bootstrap = [...]` shorthand (which pins
phase="provision" — see camp.group.config's module docstring) to an explicit
`[tasks.dep-install]` block declared `phase = "activate"`, so a timed-out
install no longer blocks workspace creation, and gains a `cleanup` step so a
partial `node_modules` left by a prior timeout does not wedge every retry
behind an `ENOTEMPTY` — the concrete failure this whole plan started from.

The shape assertions below run everywhere, against the in-repo
`groups.example/levr.toml` — a public mirror of the private chezmoi-managed
`levr.toml.tmpl` (levr has no public repo, so that template is the only "live"
home for this recipe). Only the two-homes agreement check — the one that
would catch the in-repo mirror drifting from the private template — reads the
chezmoi tree directly, and it is opt-in: it SKIPS (never fails) when that tree
is absent, so CI, a fresh clone, or any other machine without the private
chezmoi checkout does not go red on a recipe unrelated to whatever change it
is testing.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_GROUPS_EXAMPLE_DIR = _REPO_ROOT / "tools" / "camp" / "groups.example"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.group.config import load_group  # noqa: E402

_CHEZMOI_LEVR_TMPL = (
    Path.home() / ".local" / "share" / "chezmoi" / "private_dot_config" / "camp"
    / "groups" / "levr.toml.tmpl"
)

_TASK_NAME = "dep-install"
_DEP_MEMBERS = ("levr-facilitator-mobile", "levr-platform")


def _levr_config() -> dict:
    return load_group(_GROUPS_EXAMPLE_DIR / "levr.toml")


def _member(name: str) -> dict:
    cfg = _levr_config()
    return next(m for m in cfg["members"] if m["name"] == name)


def _dep_install_task() -> dict:
    """The resolved dep-install task, from the in-repo example config's
    levr-facilitator-mobile member (both dep members reference the same
    group-level [tasks.dep-install] block, so either member yields the
    identical resolved task)."""
    member = _member("levr-facilitator-mobile")
    return next(t for t in member["tasks"] if t["name"] == _TASK_NAME)


def test_dep_install_task_is_activate_phase_with_timeout_and_cleanup() -> None:
    task = _dep_install_task()

    assert task["phase"] == "activate"
    assert isinstance(task["timeout_seconds"], int) and task["timeout_seconds"] > 0
    assert task.get("cleanup"), "a timed-out npm ci must be retryable via cleanup"


def test_dep_install_task_is_required() -> None:
    """The `bootstrap = [...]` shorthand this task replaced pinned
    required=True unconditionally (camp.group.config's module docstring,
    group/config.py:766-772) — declaring `required = false` here silently
    downgrades that invariant. A failed `npm ci` must mark the member's
    work_state 'failed', not 'ready', or an executor gets dispatched into a
    tree with no node_modules."""
    task = _dep_install_task()

    assert task["required"] is True


def test_dep_install_cleanup_removes_partial_node_modules() -> None:
    """The concrete failure this plan started from: a timed-out `npm ci`
    leaves a partial `node_modules` that fails every retry with ENOTEMPTY
    until it's deleted by hand. cleanup must delete it."""
    task = _dep_install_task()

    cleanup = task["cleanup"]
    assert "node_modules" in " ".join(cleanup)
    assert any(tok in ("rm", "-rf") for tok in cleanup)


def test_dep_install_task_declares_a_capability_consequence() -> None:
    task = _dep_install_task()

    assert isinstance(task["capability"], str) and task["capability"].strip()
    lowered = task["capability"].lower()
    assert "install" in lowered or "depend" in lowered


def test_dep_install_step_runs_npm_ci() -> None:
    task = _dep_install_task()

    flat = [tok for step in task["steps"] for tok in step["cmd"]]
    assert "npm" in flat and "ci" in flat


def test_levr_dependency_members_reference_dep_install_task() -> None:
    """levr-facilitator-mobile and levr-platform reference the shared
    dep-install task by name rather than the bootstrap = [...] shorthand,
    which cannot express phase="activate"."""
    for name in _DEP_MEMBERS:
        member = _member(name)
        task_names = [t["name"] for t in member["tasks"]]
        assert task_names == [_TASK_NAME], (
            f"{name!r} must resolve to exactly the dep-install task, got: {task_names}"
        )


def test_levr_bridge_member_still_has_no_bootstrap_work() -> None:
    """levr-bridge carries no lockfile and is untouched by this reassignment —
    it keeps its empty bootstrap = [] shorthand, which resolves to no tasks."""
    member = _member("levr-bridge")

    assert member["tasks"] == []


# ---------------------------------------------------------------------------
# Two-homes agreement: the in-repo example mirrors the private chezmoi
# template. Opt-in — skipped, not failed, when the chezmoi tree is absent.
# ---------------------------------------------------------------------------


def _dep_install_task_from_chezmoi_template() -> dict:
    """Parse the `[tasks.dep-install]` block from the private chezmoi
    template directly, as its own TOML fragment (the surrounding .tmpl file
    is not valid TOML on its own — it carries a go-template comment block
    ahead of the member list)."""
    if not _CHEZMOI_LEVR_TMPL.is_file():
        pytest.skip(
            f"chezmoi tree not present at {_CHEZMOI_LEVR_TMPL} — the two-homes "
            "agreement check is opt-in and only runs on a machine with the "
            "private chezmoi checkout"
        )
    text = _CHEZMOI_LEVR_TMPL.read_text(encoding="utf-8")
    start = text.index(f"[tasks.{_TASK_NAME}]")
    end = text.index("[release]")
    fragment = text[start:end]
    assert "{{" not in fragment and "}}" not in fragment, (
        "the [tasks.dep-install] slice of the chezmoi template now contains "
        "template syntax — the untemplated-slice assumption this check relies "
        "on no longer holds"
    )
    parsed = tomllib.loads(fragment)
    return parsed["tasks"][_TASK_NAME]


def test_example_config_and_chezmoi_template_agree_on_dep_install_shape() -> None:
    example_task = _dep_install_task()
    chezmoi_task = _dep_install_task_from_chezmoi_template()

    assert example_task["phase"] == chezmoi_task.get("phase", "provision")
    assert example_task["required"] == chezmoi_task.get("required", False)
    assert example_task["timeout_seconds"] == chezmoi_task["timeout_seconds"]
    assert example_task["cleanup"] == chezmoi_task["cleanup"]


def test_agreement_check_skips_rather_than_fails_when_chezmoi_tree_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The load-bearing fix: on a machine without the private chezmoi
    checkout (CI, a fresh clone, any other dev box), this check must SKIP —
    not raise an AssertionError that reads as an unrelated red test."""
    monkeypatch.setattr(sys.modules[__name__], "_CHEZMOI_LEVR_TMPL", tmp_path / "absent.toml.tmpl")

    with pytest.raises(pytest.skip.Exception):
        _dep_install_task_from_chezmoi_template()
