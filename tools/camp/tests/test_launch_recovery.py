"""Tests for launch/recovery.py — the derived-name rule and the prefix resolver.

Test contract:
- The name rule maps a cwd at, or anywhere under, a configured group's
  ``worktrees/<slug>`` to that slug, and everything else to the cwd's basename:
  an unconfigured group's state dir, an unrelated directory, and a path that
  does not exist at all (which must answer, never raise).
- The rule compares FULLY RESOLVED paths, asserted through a symlink pointing
  into a workspace: the link's own basename must not win.
- With two configured groups, each cwd maps to its own group's slug and a cwd
  matching neither falls back to the basename.
- AGREEMENT: for a group whose ``[harness] cwd`` places the launch directory
  below the workspace, the name the rule derives from the launch directory
  reproduces the tmux session name the launch engine actually minted. Asserted
  against the engine's own output — a restated literal would keep passing while
  the two rules silently diverged, and it is that agreement that makes the tmux
  name a real collision backstop.
- ``is_workspace_root`` is the boolean half of the same test.
- The resolver matches a ref that is a PREFIX of the session id or of the
  derived name, never a substring, and never a harness display name.
- The pool is the UNION keyed by session id: a live session with no transcript
  still appears (``live=True``), and an id in both appears exactly once.
- Outcomes are the closed set: exactly one match resolves, more than one is
  ambiguous (never a guess), the empty ref never resolves, and no match reports
  the pool size so a caller can tell "nothing at all" from "nothing matching".
- Candidate order is the resolver's own — freshest first, age-less last — not
  the order the pools were handed over in.
- A transcript with no readable cwd degrades to an unreadable, uuid-only
  candidate instead of crashing the resolver; a root that no longer exists is
  marked and still returned.
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

import pytest

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


def test_cwd_at_a_workspace_derives_the_slug(tmp_path: Path) -> None:
    from camp.launch.recovery import derive_name_component

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")

    assert derive_name_component(ws, [_group("alpha")], env=_env(state)) == "feat-x"


def test_cwd_deep_under_a_workspace_derives_the_slug(tmp_path: Path) -> None:
    from camp.launch.recovery import derive_name_component

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    deep = ws / "member" / "src" / "pkg"
    deep.mkdir(parents=True)

    assert derive_name_component(deep, [_group("alpha")], env=_env(state)) == "feat-x"


def test_cwd_under_an_unconfigured_groups_worktrees_derives_the_basename(tmp_path: Path) -> None:
    """The state dir alone proves nothing — the group has to be configured."""
    from camp.launch.recovery import derive_name_component

    state = tmp_path / "state"
    ws = _workspace(state, "other", "feat-x")

    assert derive_name_component(ws, [_group("alpha")], env=_env(state)) == "feat-x"
    # ... and it is the basename that answered, not the slug rule: a deeper cwd
    # under the same unconfigured workspace answers with its own basename.
    deep = ws / "member"
    deep.mkdir()
    assert derive_name_component(deep, [_group("alpha")], env=_env(state)) == "member"


def test_unrelated_cwd_derives_the_basename(tmp_path: Path) -> None:
    from camp.launch.recovery import derive_name_component

    state = tmp_path / "state"
    elsewhere = tmp_path / "code" / "some-repo"
    elsewhere.mkdir(parents=True)

    assert derive_name_component(elsewhere, [_group("alpha")], env=_env(state)) == "some-repo"


def test_nonexistent_cwd_derives_the_basename_without_raising(tmp_path: Path) -> None:
    """A torn-down root still has to yield a name — every listing row needs one."""
    from camp.launch.recovery import derive_name_component

    state = tmp_path / "state"
    gone = tmp_path / "torn" / "down" / "gone-repo"

    assert derive_name_component(gone, [_group("alpha")], env=_env(state)) == "gone-repo"


def test_the_worktrees_directory_itself_derives_the_basename(tmp_path: Path) -> None:
    """``worktrees`` is the container, not a workspace — there is no slug there."""
    from camp.launch.recovery import derive_name_component

    state = tmp_path / "state"
    _workspace(state, "alpha", "feat-x")
    container = state / "alpha" / "worktrees"

    assert derive_name_component(container, [_group("alpha")], env=_env(state)) == "worktrees"


def test_a_symlink_into_a_workspace_derives_the_slug_not_the_link_name(tmp_path: Path) -> None:
    from camp.launch.recovery import derive_name_component

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    link = tmp_path / "shortcut"
    link.symlink_to(ws, target_is_directory=True)

    assert derive_name_component(link, [_group("alpha")], env=_env(state)) == "feat-x"


def test_two_configured_groups_each_map_to_their_own_slug(tmp_path: Path) -> None:
    from camp.launch.recovery import derive_name_component

    state = tmp_path / "state"
    alpha_ws = _workspace(state, "alpha", "feat-x")
    beta_ws = _workspace(state, "beta", "bugfix-y")
    neither = tmp_path / "code" / "loose"
    neither.mkdir(parents=True)

    groups = [_group("alpha"), _group("beta")]
    env = _env(state)

    assert derive_name_component(alpha_ws, groups, env=env) == "feat-x"
    assert derive_name_component(beta_ws, groups, env=env) == "bugfix-y"
    assert derive_name_component(neither, groups, env=env) == "loose"


def test_is_workspace_root_answers_the_same_test_as_a_boolean(tmp_path: Path) -> None:
    from camp.launch.recovery import is_workspace_root

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    member = ws / "member"
    member.mkdir()
    elsewhere = tmp_path / "code" / "some-repo"
    elsewhere.mkdir(parents=True)

    groups = [_group("alpha")]
    env = _env(state)

    assert is_workspace_root(ws, groups, env=env) is True
    assert is_workspace_root(member, groups, env=env) is True
    assert is_workspace_root(elsewhere, groups, env=env) is False


# ---------------------------------------------------------------------------
# The agreement pin: the rule and the launch engine must not drift apart
# ---------------------------------------------------------------------------


class _FakeHarness:
    """Minimal stand-in for the harness seam, enough to reach the spawn."""

    name = "fakeharness"

    def session_launch(self, workspace, session_id, *, session_name=None):
        return ["fakeharness", "--sid", session_id]

    def session_launch_env_unset(self):
        return ["FAKE_TOKEN"]

    def session_launch_env_set(self, account, *, env=None):
        return {"FAKE_ACCOUNT_DIR": str((env or {}).get("HOME", "/fake-home"))}

    def session_enumerate(self, workspace=None):
        return None

    def parse_session_list(self, output):
        return []


class _SpawnRecorder:
    """Stands in for the engine's tmux spawn, which reads tmux's exit status."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return type(
            "CompletedProcess", (), {"returncode": 0, "stdout": "", "stderr": ""}
        )()


def test_derived_name_reproduces_the_launch_engines_tmux_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The rule, applied to a launch directory, rebuilds the engine's own name.

    The engine picks the launch directory (here below the workspace, via
    ``[harness] cwd``) and mints the tmux name; this test derives the name
    component back out of that directory and requires the two to compose to the
    identical string. Nothing here is restated by hand, so the assertion fails
    the moment either side changes without the other.
    """
    import camp.launch.session as session
    from camp.group.manifest import workspace_dir
    from camp.launch.recovery import derive_name_component

    state = tmp_path / "state"
    env = _env(state)
    group = _group(
        "agreegroup",
        harness={"cwd": "{workspace}/member", "pretrust": False},
    )
    slug = "resume-me"

    ws = workspace_dir("agreegroup", slug, env=env)
    (ws / "member").mkdir(parents=True)

    recorder = _SpawnRecorder()
    monkeypatch.setattr(session, "harness_for", lambda g: _FakeHarness())
    monkeypatch.setattr(session.shutil, "which", lambda binary: "/usr/bin/tmux")
    monkeypatch.setattr(session.subprocess, "run", recorder)

    launched = session.launch_session(group, slug, env=env)

    component = derive_name_component(launched.launch_dir, [group], env=env)
    assert launched.tmux_name == f"camp-{component}-{launched.session_id[:8]}"
    assert launched.tmux_name in recorder.calls[0]


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def _resolve(ref, *, transcripts=(), live_records=(), groups=None, env=None, state=None):
    from camp.launch.recovery import resolve_session_ref

    return resolve_session_ref(
        ref,
        transcripts=list(transcripts),
        live_records=list(live_records),
        groups=list(groups if groups is not None else []),
        env=env if env is not None else _env(state if state is not None else Path("/nonexistent")),
        now=_NOW,
    )


def test_exact_session_id_resolves_to_one_candidate(tmp_path: Path) -> None:
    from camp.launch.recovery import Resolved

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    result = _resolve(
        _UUID_A,
        transcripts=[_transcript(_UUID_A, ws), _transcript(_UUID_B, ws)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, Resolved)
    assert result.candidate.session_id == _UUID_A
    assert result.candidate.derived_name == f"camp-feat-x-{_UUID_A[:8]}"
    assert result.candidate.root == ws
    assert result.candidate.live is False
    assert result.candidate.root_missing is False
    assert result.candidate.unreadable is False
    assert result.candidate.age_seconds == pytest.approx(60.0)


def test_an_eight_character_uuid_prefix_resolves(tmp_path: Path) -> None:
    from camp.launch.recovery import Resolved

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    result = _resolve(
        _UUID_A[:8],
        transcripts=[_transcript(_UUID_A, ws), _transcript(_UUID_B, ws)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, Resolved)
    assert result.candidate.session_id == _UUID_A


def test_a_derived_name_prefix_resolves(tmp_path: Path) -> None:
    from camp.launch.recovery import Resolved

    state = tmp_path / "state"
    alpha_ws = _workspace(state, "alpha", "feat-x")
    other_ws = _workspace(state, "alpha", "bugfix-y")
    result = _resolve(
        "camp-feat-x-",
        transcripts=[_transcript(_UUID_A, alpha_ws), _transcript(_UUID_B, other_ws)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, Resolved)
    assert result.candidate.session_id == _UUID_A


def test_a_bare_slug_fragment_is_not_a_prefix_and_does_not_match(tmp_path: Path) -> None:
    """Matching is prefix-only: the slug sits mid-name, so naming it matches nothing."""
    from camp.launch.recovery import NoMatch

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    result = _resolve(
        "feat-x",
        transcripts=[_transcript(_UUID_A, ws)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, NoMatch)
    assert result.pool_size == 1


def test_a_harness_display_name_never_matches(tmp_path: Path) -> None:
    """The harness's own name for a session is not an address camp accepts."""
    from camp.launch.recovery import NoMatch

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    result = _resolve(
        "friendly-display-name",
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws, name="friendly-display-name")],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, NoMatch)


def test_a_live_record_without_a_transcript_is_still_a_candidate(tmp_path: Path) -> None:
    from camp.launch.recovery import Resolved

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    result = _resolve(
        _UUID_B,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_B, ws)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, Resolved)
    assert result.candidate.session_id == _UUID_B
    assert result.candidate.live is True
    assert result.candidate.root == ws
    assert result.candidate.derived_name == f"camp-feat-x-{_UUID_B[:8]}"
    assert result.candidate.age_seconds is None


def test_an_id_in_both_pools_appears_exactly_once_and_is_live(tmp_path: Path) -> None:
    from camp.launch.recovery import Resolved

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    result = _resolve(
        "",
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("alpha")],
        state=state,
    )

    # The empty ref matches everything, so the whole pool comes back — and the
    # pool is one candidate, not two.
    assert not isinstance(result, Resolved)
    assert [c.session_id for c in result.candidates] == [_UUID_A]
    assert result.candidates[0].live is True
    assert result.candidates[0].age_seconds == pytest.approx(60.0)


def test_a_ref_matching_two_candidates_is_ambiguous_never_a_guess(tmp_path: Path) -> None:
    from camp.launch.recovery import Ambiguous

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    shared = "cccccccc"
    first = f"{shared}-1111-4111-8111-111111111111"
    second = f"{shared}-2222-4222-8222-222222222222"
    result = _resolve(
        shared,
        transcripts=[_transcript(first, ws), _transcript(second, ws)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, Ambiguous)
    assert {c.session_id for c in result.candidates} == {first, second}


def test_the_empty_ref_against_a_non_empty_pool_is_ambiguous(tmp_path: Path) -> None:
    """An empty prefix matches everything, so it addresses nothing in particular."""
    from camp.launch.recovery import Ambiguous

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    result = _resolve(
        "",
        transcripts=[_transcript(_UUID_A, ws), _transcript(_UUID_B, ws)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, Ambiguous)
    assert len(result.candidates) == 2


def test_the_empty_ref_never_resolves_even_against_a_single_candidate(tmp_path: Path) -> None:
    """An absent address is never silently completed into the only session."""
    from camp.launch.recovery import Ambiguous

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    result = _resolve(
        "",
        transcripts=[_transcript(_UUID_A, ws)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, Ambiguous)
    assert len(result.candidates) == 1


def test_ambiguous_candidates_come_back_freshest_first(tmp_path: Path) -> None:
    """Candidate order is the resolver's, not the input's — a caller renders it.

    The inputs are handed over in exactly the wrong order, so a resolver that
    echoed its input, or ordered by whatever a dict happened to yield, fails.
    """
    from camp.launch.recovery import Ambiguous

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    stale = "dddddddd-1111-4111-8111-111111111111"
    fresh = "dddddddd-2222-4222-8222-222222222222"
    live_only = "dddddddd-3333-4333-8333-333333333333"

    result = _resolve(
        "dddddddd",
        transcripts=[
            _transcript(stale, ws, age_seconds=3600.0),
            _transcript(fresh, ws, age_seconds=10.0),
        ],
        live_records=[_record(live_only, ws)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, Ambiguous)
    # Freshest transcript first; the candidate with no transcript — and so no
    # age to compare — sorts last rather than jumping the queue.
    assert [c.session_id for c in result.candidates] == [fresh, stale, live_only]


def test_no_match_against_a_non_empty_pool_reports_the_pool_size(tmp_path: Path) -> None:
    from camp.launch.recovery import NoMatch

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    result = _resolve(
        "zzzzzzzz",
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_B, ws)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, NoMatch)
    assert result.pool_size == 2


def test_no_match_against_an_empty_pool_reports_zero(tmp_path: Path) -> None:
    """Zero is the distinguishable case a caller renders differently."""
    from camp.launch.recovery import NoMatch

    state = tmp_path / "state"
    result = _resolve("zzzzzzzz", groups=[_group("alpha")], state=state)

    assert isinstance(result, NoMatch)
    assert result.pool_size == 0


def test_a_transcript_without_a_cwd_degrades_to_an_unreadable_candidate(tmp_path: Path) -> None:
    from camp.launch.recovery import Resolved

    state = tmp_path / "state"
    result = _resolve(
        _UUID_A,
        transcripts=[_transcript(_UUID_A, None)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, Resolved)
    assert result.candidate.unreadable is True
    assert result.candidate.root is None
    assert result.candidate.root_missing is False
    assert result.candidate.derived_name == f"camp-{_UUID_A[:8]}"


def test_an_unreadable_candidate_does_not_disturb_the_rest_of_the_pool(tmp_path: Path) -> None:
    from camp.launch.recovery import Resolved

    state = tmp_path / "state"
    ws = _workspace(state, "alpha", "feat-x")
    result = _resolve(
        _UUID_B,
        transcripts=[_transcript(_UUID_A, None), _transcript(_UUID_B, ws)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, Resolved)
    assert result.candidate.derived_name == f"camp-feat-x-{_UUID_B[:8]}"


def test_a_root_that_no_longer_exists_is_marked_and_still_returned(tmp_path: Path) -> None:
    from camp.launch.recovery import Resolved

    state = tmp_path / "state"
    gone = tmp_path / "torn" / "down" / "gone-repo"
    result = _resolve(
        _UUID_A,
        transcripts=[_transcript(_UUID_A, gone)],
        groups=[_group("alpha")],
        state=state,
    )

    assert isinstance(result, Resolved)
    assert result.candidate.root_missing is True
    assert result.candidate.unreadable is False
    assert result.candidate.root == gone
    assert result.candidate.derived_name == f"camp-gone-repo-{_UUID_A[:8]}"


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


def test_recovery_imports_nothing_from_the_cli_layer_or_process_machinery() -> None:
    """The module answers questions; it never speaks to a terminal or a process."""
    offenders = _import_offenders(_recovery_ast())

    assert offenders == [], f"recovery.py must stay pure, found: {offenders}"


def test_recovery_never_prints_or_exits() -> None:
    """Rendering, exit codes, and refusal wording belong to the CLI layer."""
    offenders = _render_or_exit_offenders(_recovery_ast())

    assert offenders == [], f"recovery.py must not print or exit, found: {offenders}"


def test_the_purity_checks_catch_every_violation_they_claim_to(tmp_path: Path) -> None:
    """The two checks above are only worth having if they fail on a violation.

    Runs the SAME checkers over a module that commits each violation on purpose,
    so a checker that silently stopped matching is caught here rather than years
    later by whatever it failed to prevent.
    """
    violator = tmp_path / "violator.py"
    violator.write_text(
        "import sys\n"
        "import subprocess\n"
        "import logging\n"
        "import importlib\n"
        "from camp.cli.session import render\n"
        "from ..cli import helper\n"
        "from camp import cli\n"
        "from argparse import ArgumentParser\n"
        "emit = print\n"
        "def go():\n"
        "    emit('hi')\n"
        "    __import__('camp.cli.session')\n"
        "    sys.exit(2)\n",
        encoding="utf-8",
    )
    tree = ast.parse(violator.read_text(encoding="utf-8"))

    assert _import_offenders(tree) == [
        "import sys",
        "import subprocess",
        "import logging",
        "import importlib",
        "from camp.cli.session import ...",
        "from ..cli import ...",
        "from camp import ...",
        "from argparse import ...",
    ]
    assert _render_or_exit_offenders(tree) == ["print", "__import__", ".exit"]


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

    def test_the_derived_name_is_folded(self, tmp_path: Path) -> None:
        """The rule's own output is folded, so all three callers inherit it."""
        from camp.launch.recovery import derive_name_component

        state = tmp_path / "state"
        dotted = tmp_path / "code" / "my.project"
        dotted.mkdir(parents=True)

        assert derive_name_component(dotted, [_group("alpha")], env=_env(state)) == "my-project"

    def test_a_group_camp_cannot_resolve_a_state_dir_for_is_skipped(
        self, tmp_path: Path
    ) -> None:
        """One malformed sibling config must not take the whole answer down.

        A group name outside the charset camp confines state directories to is
        loadable but unresolvable, and the rule's contract is to answer. Skipping
        it costs the listing nothing but that group's workspaces; raising costs
        the operator every other session on the machine.
        """
        from camp.launch.recovery import derive_name_component

        state = tmp_path / "state"
        target = tmp_path / "code" / "proj"
        target.mkdir(parents=True)
        groups = [_group("../escape"), _group("alpha")]

        assert derive_name_component(target, groups, env=_env(state)) == "proj"

    def test_a_symlinked_worktrees_container_names_nothing(self, tmp_path: Path) -> None:
        """A link in the container's place cannot make the world a workspace.

        Every camp-managed answer is measured against this container, so a
        symlink standing in for it and pointed at a root would have the rule
        report an arbitrary directory as living in some group's workspace — and
        the launch gate waives its allowlist for exactly those.
        """
        from camp.launch.recovery import derive_name_component, is_workspace_root

        state = tmp_path / "state"
        (state / "alpha").mkdir(parents=True)
        (state / "alpha" / "worktrees").symlink_to(tmp_path)
        target = tmp_path / "code" / "proj"
        target.mkdir(parents=True)

        assert derive_name_component(target, [_group("alpha")], env=_env(state)) == "proj"
        assert is_workspace_root(target, [_group("alpha")], env=_env(state)) is False

    def test_a_candidates_derived_name_is_folded_too(self, tmp_path: Path) -> None:
        """The listing's name and the engine's tmux name are the same string.

        A candidate whose name carries a character tmux reads as a target
        separator can be offered for recovery and then never addressed again.
        """
        from camp.launch.recovery import Resolved, resolve_session_ref

        state = tmp_path / "state"
        dotted = tmp_path / "code" / "my.project"
        dotted.mkdir(parents=True)

        result = resolve_session_ref(
            _UUID_A,
            transcripts=[_transcript(_UUID_A, dotted)],
            live_records=[],
            groups=[_group("alpha")],
            env=_env(state),
        )

        assert isinstance(result, Resolved)
        assert result.candidate.derived_name == f"camp-my-project-{_UUID_A[:8]}"

    def test_a_dotted_workspace_slug_is_folded_too(self, tmp_path: Path) -> None:
        from camp.launch.recovery import derive_name_component

        state = tmp_path / "state"
        ws = _workspace(state, "alpha", "feat.x")

        assert derive_name_component(ws, [_group("alpha")], env=_env(state)) == "feat-x"


# ---------------------------------------------------------------------------
# The recoverable row builder
# ---------------------------------------------------------------------------


class TestRecoverableCandidates:
    """dead = enumerated − live, as a pure function of the two pools.

    The subtraction is keyed by session id and nothing else. Both pools must
    already be scoped the same way by whoever gathered them — this function
    cannot tell a pool that was scoped from one that was not, which is exactly
    why it does no scoping of its own.
    """

    def test_a_live_session_is_subtracted_from_the_enumerated_pool(
        self, tmp_path: Path
    ) -> None:
        from camp.launch.recovery import recoverable_candidates

        state = tmp_path / "state"
        root = tmp_path / "proj"
        root.mkdir()

        result = recoverable_candidates(
            transcripts=[_transcript(_UUID_A, root), _transcript(_UUID_B, root)],
            live_records=[_record(_UUID_B, root)],
            groups=[],
            env=_env(state),
            now=_NOW,
        )

        assert [c.session_id for c in result] == [_UUID_A]
        assert result[0].live is False

    def test_a_live_session_with_no_transcript_is_not_a_dead_row(
        self, tmp_path: Path
    ) -> None:
        """The pool is the ENUMERATED transcripts; live records only subtract."""
        from camp.launch.recovery import recoverable_candidates

        state = tmp_path / "state"
        root = tmp_path / "proj"
        root.mkdir()

        result = recoverable_candidates(
            transcripts=[],
            live_records=[_record(_UUID_A, root)],
            groups=[],
            env=_env(state),
            now=_NOW,
        )

        assert result == ()

    def test_rows_are_newest_first_with_a_uuid_tiebreak(self, tmp_path: Path) -> None:
        from camp.launch.recovery import recoverable_candidates

        state = tmp_path / "state"
        root = tmp_path / "proj"
        root.mkdir()
        tied_b = _transcript(_UUID_B, root, age_seconds=300.0)
        tied_a = _transcript(_UUID_A, root, age_seconds=300.0)
        newest = _transcript("00000000-0000-4000-8000-000000000000", root, age_seconds=10.0)

        result = recoverable_candidates(
            transcripts=[tied_b, tied_a, newest],
            live_records=[],
            groups=[],
            env=_env(state),
            now=_NOW,
        )

        assert [c.session_id for c in result] == [newest.session_id, _UUID_A, _UUID_B]

    def test_a_session_id_seen_twice_yields_one_row(self, tmp_path: Path) -> None:
        """Two harnesses reporting the same store must not double a row."""
        from camp.launch.recovery import recoverable_candidates

        state = tmp_path / "state"
        root = tmp_path / "proj"
        root.mkdir()

        result = recoverable_candidates(
            transcripts=[_transcript(_UUID_A, root), _transcript(_UUID_A, root)],
            live_records=[],
            groups=[],
            env=_env(state),
            now=_NOW,
        )

        assert [c.session_id for c in result] == [_UUID_A]

    def test_a_torn_down_root_is_marked_and_an_unreadable_one_is_kept(
        self, tmp_path: Path
    ) -> None:
        """Neither is ever hidden — a row the operator cannot resume is still news."""
        from camp.launch.recovery import recoverable_candidates

        state = tmp_path / "state"
        gone = tmp_path / "torn-down"

        result = recoverable_candidates(
            transcripts=[
                _transcript(_UUID_A, gone, age_seconds=10.0),
                _transcript(_UUID_B, None, age_seconds=20.0),
            ],
            live_records=[],
            groups=[],
            env=_env(state),
            now=_NOW,
        )

        assert [c.session_id for c in result] == [_UUID_A, _UUID_B]
        assert (result[0].root_missing, result[0].unreadable) == (True, False)
        assert (result[1].root_missing, result[1].unreadable) == (False, True)
        assert result[1].root is None
        assert result[1].derived_name == f"camp-{_UUID_B[:8]}"

    def test_the_workspace_name_rule_applies_to_a_row(self, tmp_path: Path) -> None:
        """A row's name is the same name a launch into that directory would mint."""
        from camp.launch.recovery import recoverable_candidates

        state = tmp_path / "state"
        ws = _workspace(state, "alpha", "feat-x")

        result = recoverable_candidates(
            transcripts=[_transcript(_UUID_A, ws / "member")],
            live_records=[],
            groups=[_group("alpha")],
            env=_env(state),
            now=_NOW,
        )

        assert result[0].derived_name == f"camp-feat-x-{_UUID_A[:8]}"
