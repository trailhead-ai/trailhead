"""Recipe-shape regression test for the levr group's dependency-install task.

levr has no in-repo `groups.example` counterpart (it's a private group config),
so this test reads the chezmoi source template directly — the only home this
recipe lives in. `npm ci` moves from the `bootstrap = [...]` shorthand (which
pins phase="provision" — see camp.group.config's module docstring) to an
explicit `[tasks.dep-install]` block declared `phase = "activate"`, so a timed-
out install no longer blocks workspace creation, and gains a `cleanup` step so
a partial `node_modules` left by a prior timeout does not wedge every retry
behind an `ENOTEMPTY` — the concrete failure this whole plan started from.

The full .tmpl file is not valid TOML on its own (it carries a go-template
comment block ahead of the member list); this test parses only the untemplated
slices it needs, asserting each slice is free of template delimiters before
parsing it rather than assuming so — a moved/renamed section must fail this
test loudly, not silently pass on stale text.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_CHEZMOI_LEVR_TMPL = (
    Path.home() / ".local" / "share" / "chezmoi" / "private_dot_config" / "camp"
    / "groups" / "levr.toml.tmpl"
)

_TASK_NAME = "dep-install"
_DEP_MEMBERS = ("levr-facilitator-mobile", "levr-platform")


def _read_template_text() -> str:
    assert _CHEZMOI_LEVR_TMPL.is_file(), (
        f"chezmoi template not found at {_CHEZMOI_LEVR_TMPL} — this check cannot "
        "run without it; this must fail, not skip."
    )
    return _CHEZMOI_LEVR_TMPL.read_text(encoding="utf-8")


def _member_block(name: str) -> str:
    """Return the raw text of one `[[members]]` block, by member name.

    Not parsed as TOML: `repo_root` legitimately carries go-template syntax
    (`{{ .chezmoi.homeDir }}`), which a plain tomllib parse of this slice
    cannot handle. The block's extent is unambiguous — from its own
    `[[members]]` heading to the next `[[members]]`/`[branch]` heading — so
    plain text containment checks are precise enough for what these tests
    assert (presence/absence of the `bootstrap`/`tasks` keys).
    """
    text = _read_template_text()
    heading_pos = text.index(f'name = "{name}"')
    block_start = text.rindex("[[members]]", 0, heading_pos)
    next_heading = min(
        (
            pos
            for pos in (
                text.find("[[members]]", heading_pos),
                text.find("[branch]", heading_pos),
            )
            if pos != -1
        ),
        default=len(text),
    )
    return text[block_start:next_heading]


def _dep_install_task() -> dict:
    """Parse the `[tasks.dep-install]` block (between `[branch]` and
    `[release]`) as its own TOML fragment."""
    text = _read_template_text()
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
        block = _member_block(name)
        assert 'tasks = ["dep-install"]' in block, (
            f"{name!r} must reference tasks = [\"dep-install\"]"
        )
        assert "bootstrap" not in block, (
            f"{name!r} must not use the bootstrap shorthand — it pins "
            "phase=\"provision\" and cannot express a cleanup or capability"
        )


def test_levr_bridge_member_still_has_no_bootstrap_work() -> None:
    """levr-bridge carries no lockfile and is untouched by this reassignment —
    it keeps its empty bootstrap = [] shorthand."""
    block = _member_block("levr-bridge")

    assert "bootstrap = []" in block
    assert "tasks" not in block
