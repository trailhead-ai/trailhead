"""Tests for launch/recovery.py — the derived-name rule and the addressable pool.

Test contract:
- The name rule maps a cwd at, or anywhere under, a configured group's
  ``worktrees/<slug>`` to that slug, and everything else to the cwd's basename:
  an unconfigured group's state dir, an unrelated directory, and a path that
  does not exist at all (which must answer, never raise).
- The rule compares FULLY RESOLVED paths, asserted through a symlink pointing
  into a workspace: the link's own basename must not win.
- With two configured groups, each cwd maps to its own group's slug and a cwd
  matching neither falls back to the basename.
- A candidate's derived name is folded the same way the name rule itself is,
  through :func:`session_candidates` — the pool's own name never diverges
  from what a plain derivation would produce.
- recovery.py stays a pure data-to-data module: asserted over its AST, so a
  later change cannot quietly move rendering, exits, or CLI imports into it.
  The scan reads syntax, so it catches the spellings someone reaches for by
  habit, not a determined evasion — a module resolved from a computed string
  still gets through, and no AST rule closes that.

Every path comes from ``tmp_path`` and every group state dir from an injected
``CAMP_STATE_DIR``, so no test reads or touches the operator's real state.
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)

_UUID_A = "aaaaaaaa-1111-4111-8111-111111111111"
_UUID_B = "bbbbbbbb-2222-4222-8222-222222222222"


def _env(state_root: Path) -> dict[str, str]:
    """A hermetic environment whose camp state lives entirely under tmp_path."""
    return {"CAMP_STATE_DIR": str(state_root), "HOME": str(state_root.parent / "home")}


def _group(name: str, **extra: Any) -> dict[str, Any]:
    return {"group": {"name": name}, **extra}


def _workspace(state_root: Path, group: str, slug: str) -> Path:
    """Create and return ``<state>/<group>/worktrees/<slug>``."""
    ws = state_root / group / "worktrees" / slug
    ws.mkdir(parents=True)
    return ws


def _transcript(session_id: str, cwd: Path | None, *, age_seconds: float = 60.0):
    from trailhead.harness.base import SessionTranscript

    return SessionTranscript(
        session_id=session_id,
        cwd=cwd,
        modified_at=_NOW - timedelta(seconds=age_seconds),
    )


def _record(session_id: str, cwd: Path, *, name: str | None = None):
    from trailhead.harness.base import SessionRecord

    return SessionRecord(
        session_id=session_id,
        cwd=cwd,
        kind="agent",
        controllable=True,
        name=name,
        pid=None,
        started_at=None,
    )


# ---------------------------------------------------------------------------
# The name rule
# ---------------------------------------------------------------------------


def _derived(cwd: Path, groups: list, env: dict[str, str]) -> str:
    """The name component `session_candidates` derives for a session rooted
    at *cwd* — the rule's one production caller (via `_build_candidate`),
    now that a launch names its own session from a known slug instead of
    resolving one back out of a directory."""
    from camp.launch.recovery import session_candidates

    result = session_candidates(
        transcripts=[_transcript(_UUID_A, cwd)],
        live_records=[],
        groups=groups,
        env=env,
        now=_NOW,
    )
    assert len(result) == 1
    name = result[0].derived_name
    prefix, suffix = "camp-", f"-{_UUID_A[:8]}"
    assert name.startswith(prefix) and name.endswith(suffix), name
    return name[len(prefix) : -len(suffix)]


def test_cwd_at_a_workspace_derives_the_slug(tmp_path: Path) -> None:
    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")

    assert _derived(ws, [_group("alpha")], _env(state)) == "feat-x"


def test_cwd_at_a_member_derives_the_slug_and_the_member(tmp_path: Path) -> None:
    """A session rooted at a member names the member it owns, not just the workspace.

    This is what tells two workers in one multi-repo workspace apart: rooted at
    their own member, they differ by that member's name rather than by hash.
    """
    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    member = ws / "member"
    member.mkdir()

    assert _derived(member, [_group("alpha")], _env(state)) == "feat-x-member"


def test_cwd_deep_under_a_member_stops_at_the_member(tmp_path: Path) -> None:
    """The rule caps one level below the workspace.

    A session rooted deeper still belongs to the member it is inside, and the
    name says so without growing a segment per directory — a tmux name has to
    stay something an operator can type.
    """
    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    deep = ws / "member" / "src" / "pkg"
    deep.mkdir(parents=True)

    assert _derived(deep, [_group("alpha")], _env(state)) == "feat-x-member"


def test_cwd_at_the_workspace_itself_derives_the_slug_alone(tmp_path: Path) -> None:
    """The workspace root has no member to name, and gains no suffix for one."""
    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")

    assert _derived(ws, [_group("alpha")], _env(state)) == "feat-x"


def test_member_segment_is_folded_like_every_other_component(tmp_path: Path) -> None:
    """A member directory carrying a tmux target separator is folded, not passed through."""
    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    member = ws / "trailhead-ai.github.io"
    member.mkdir()

    assert _derived(member, [_group("alpha")], _env(state)) == "feat-x-trailhead-ai-github-io"


def test_cwd_under_an_unconfigured_groups_worktrees_derives_the_basename(tmp_path: Path) -> None:
    """The state dir alone proves nothing — the group has to be configured."""
    state = tmp_path / "state"
    ws = _workspace(state, "other", "feat-x")

    assert _derived(ws, [_group("alpha")], _env(state)) == "feat-x"
    # ... and it is the basename that answered, not the slug rule: a deeper cwd
    # under the same unconfigured workspace answers with its own basename.
    deep = ws / "member"
    deep.mkdir()
    assert _derived(deep, [_group("alpha")], _env(state)) == "member"


def test_unrelated_cwd_derives_the_basename(tmp_path: Path) -> None:
    state = tmp_path / "state"
    elsewhere = tmp_path / "code" / "some-repo"
    elsewhere.mkdir(parents=True)

    assert _derived(elsewhere, [_group("alpha")], _env(state)) == "some-repo"


def test_nonexistent_cwd_derives_the_basename_without_raising(tmp_path: Path) -> None:
    """A torn-down root still has to yield a name — every listing row needs one."""
    state = tmp_path / "state"
    gone = tmp_path / "torn" / "down" / "gone-repo"

    assert _derived(gone, [_group("alpha")], _env(state)) == "gone-repo"


def test_the_worktrees_directory_itself_derives_the_basename(tmp_path: Path) -> None:
    """``worktrees`` is the container, not a workspace — there is no slug there."""
    state = tmp_path / "state"
    _workspace(state, "alpha", "feat-x")
    container = state / "alpha" / "worktrees"

    assert _derived(container, [_group("alpha")], _env(state)) == "worktrees"


def test_a_symlink_into_a_workspace_derives_the_slug_not_the_link_name(tmp_path: Path) -> None:
    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    link = tmp_path / "shortcut"
    link.symlink_to(ws, target_is_directory=True)

    assert _derived(link, [_group("alpha")], _env(state)) == "feat-x"


def test_two_configured_groups_each_map_to_their_own_slug(tmp_path: Path) -> None:
    state = tmp_path / "state"
    alpha_ws = _workspace(state, "alpha", "feat-x")
    beta_ws = _workspace(state, "beta", "bugfix-y")
    neither = tmp_path / "code" / "loose"
    neither.mkdir(parents=True)

    groups = [_group("alpha"), _group("beta")]
    env = _env(state)

    assert _derived(alpha_ws, groups, env) == "feat-x"
    assert _derived(beta_ws, groups, env) == "bugfix-y"
    assert _derived(neither, groups, env) == "loose"


# ---------------------------------------------------------------------------
# The module boundary
# ---------------------------------------------------------------------------


#: Module roots a pure data-to-data module has no business importing: the two
#: that carry a process or a terminal, the two that parse or format argv, the
#: one that emits, and the one that would let any of the others in by name.
_FORBIDDEN_IMPORT_ROOTS = {
    "sys",
    "subprocess",
    "argparse",
    "shutil",
    "logging",
    "importlib",
}

#: Builtins that render or terminate. Matched as bare NAMES, so `emit = print`
#: is caught as surely as `print(...)`. `__import__` is here rather than above
#: because it defeats the import scan by spelling a module as a string.
_FORBIDDEN_NAMES = {"print", "exit", "quit", "input", "breakpoint", "__import__"}

#: Attributes that terminate the process — `sys.exit`, `os._exit`.
_FORBIDDEN_ATTRS = {"exit", "_exit"}


def _import_offenders(tree: ast.Module) -> list[str]:
    """Every import in *tree* a pure camp-core module may not make."""
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FORBIDDEN_IMPORT_ROOTS:
                    offenders.append(f"import {alias.name}")
                elif alias.name.startswith("camp.cli"):
                    offenders.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            parts = module.split(".") if module else []
            spelled = f"from {'.' * node.level}{module} import ..."
            if parts and parts[0] in _FORBIDDEN_IMPORT_ROOTS:
                offenders.append(spelled)
            # An absolute `camp.cli...`, or a relative hop into the cli package
            # from anywhere inside camp (`from ..cli import ...`).
            elif module.startswith("camp.cli") or (node.level and "cli" in parts):
                offenders.append(spelled)
            # `from camp import cli` names the package as an imported symbol
            # rather than in the module path, so the checks above never see it.
            # It is the spelling most likely to be reached for by accident.
            elif any(alias.name == "cli" for alias in node.names):
                offenders.append(spelled)
    return offenders


def _render_or_exit_offenders(tree: ast.Module) -> list[str]:
    """Every reference in *tree* to a builtin that prints or terminates."""
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            offenders.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
            offenders.append(f".{node.attr}")
    return offenders


def _recovery_ast() -> ast.Module:
    import camp.launch.recovery as recovery

    return ast.parse(Path(recovery.__file__).read_text(encoding="utf-8"))


class TestModuleBoundary:
    """recovery.py is pure data-to-data: no process, no terminal, no CLI import.

    The offender lists are the checkers' whole subject; a fixture module that
    imports a forbidden root, or references a rendering/exiting builtin, must
    be named, while a clean module of the same shape must not."""

    def test_a_forbidden_import_is_named_and_a_clean_one_is_not(self):
        dirty = ast.parse("import sys\n")
        clean = ast.parse("import os\n")

        assert _import_offenders(dirty) == ["import sys"]
        assert _import_offenders(clean) == []

    def test_reaching_into_the_cli_package_is_named_an_offender(self):
        dirty = ast.parse("from camp.cli import group\n")
        clean = ast.parse("from camp.launch import recovery\n")

        assert _import_offenders(dirty) != _import_offenders(clean)
        assert _import_offenders(clean) == []

    def test_a_render_or_exit_reference_is_named_and_a_clean_one_is_not(self):
        dirty = ast.parse("print('x')\n")
        clean = ast.parse("value = 'x'\n")

        assert _render_or_exit_offenders(dirty) == ["print"]
        assert _render_or_exit_offenders(clean) == []

    def test_sys_exit_attribute_access_is_named_an_offender(self):
        dirty = ast.parse("import sys\nsys.exit(1)\n")
        clean = ast.parse("import sys\nsys.argv\n")

        assert _render_or_exit_offenders(dirty) == [".exit"]
        assert _render_or_exit_offenders(clean) == []

    def test_the_real_recovery_module_imports_and_renders_nothing_forbidden(self):  # inert-gate: allow module, no input
        tree = _recovery_ast()

        assert _import_offenders(tree) == []
        assert _render_or_exit_offenders(tree) == []


# ---------------------------------------------------------------------------
# Name components tmux can address
# ---------------------------------------------------------------------------


class TestNameComponentIsAddressable:
    """A session name is only useful if tmux will accept it back.

    tmux reads ``:`` as the session/window separator and ``.`` as the
    window/pane separator when resolving a target, so a name carrying either is
    created without complaint and then cannot be named again — ``kill-session
    -t`` reports "can't find pane", and the ``=`` exact-match prefix does not
    rescue it. Directory basenames carry dots routinely, so the launch that
    prints an attach handle has to fold them first.
    """

    def test_separators_are_folded(self) -> None:
        from camp.launch.recovery import sanitize_name_component

        assert sanitize_name_component("my.project") == "my-project"
        assert sanitize_name_component("a:b") == "a-b"
        assert sanitize_name_component("v1.2.3") == "v1-2-3"

    def test_ordinary_names_are_untouched(self) -> None:
        from camp.launch.recovery import sanitize_name_component

        for name in ("feat-x", "session_resume", "abc123", "UPPER-lower_9"):
            assert sanitize_name_component(name) == name

    def test_a_name_that_folds_away_entirely_still_yields_a_component(self) -> None:
        """A name is never built with an empty middle."""
        from camp.launch.recovery import sanitize_name_component

        for name in ("...", "", "---", ":::"):
            assert sanitize_name_component(name) == "dir"

    def test_a_group_camp_cannot_resolve_a_state_dir_for_is_skipped(
        self, tmp_path: Path
    ) -> None:
        """One malformed sibling config must not take the whole answer down.

        A group name outside the charset camp confines state directories to is
        loadable but unresolvable, and the rule's contract is to answer. Skipping
        it costs the listing nothing but that group's workspaces; raising costs
        the operator every other session on the machine.
        """
        state = tmp_path / "state"
        target = tmp_path / "code" / "proj"
        target.mkdir(parents=True)
        groups = [_group("../escape"), _group("alpha")]

        assert _derived(target, groups, _env(state)) == "proj"

    def test_a_symlinked_worktrees_container_names_nothing(self, tmp_path: Path) -> None:
        """A link in the container's place cannot make the world a workspace.

        Every camp-managed answer is measured against this container, so a
        symlink standing in for it and pointed at a root would have the rule
        report an arbitrary directory as living in some group's workspace.
        """
        state = tmp_path / "state"
        (state / "alpha").mkdir(parents=True)
        (state / "alpha" / "worktrees").symlink_to(tmp_path)
        target = tmp_path / "code" / "proj"
        target.mkdir(parents=True)

        assert _derived(target, [_group("alpha")], _env(state)) == "proj"

    def test_a_candidates_derived_name_is_folded_too(self, tmp_path: Path) -> None:
        """The listing's name and the engine's tmux name are the same string.

        A candidate whose name carries a character tmux reads as a target
        separator can be offered for recovery and then never addressed again.
        """
        from camp.launch.recovery import session_candidates

        state = tmp_path / "state"
        dotted = tmp_path / "code" / "my.project"
        dotted.mkdir(parents=True)

        result = session_candidates(
            transcripts=[_transcript(_UUID_A, dotted)],
            live_records=[],
            groups=[_group("alpha")],
            env=_env(state),
            now=_NOW,
        )

        assert len(result) == 1
        assert result[0].derived_name == f"camp-my-project-{_UUID_A[:8]}"

    def test_a_dotted_workspace_slug_is_folded_too(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        ws = _workspace(state, "alpha", "feat.x")

        assert _derived(ws, [_group("alpha")], _env(state)) == "feat-x"
