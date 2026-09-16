"""Test contract: `camp status --stale` — how a workspace is judged idle.

`--stale` annotates each workspace with `idle_days` and a `stale` verdict, taken
from the most recent commit across its member worktrees and compared against a
threshold (`--days`, default 7).

These tests exist because the annotation is about to be shared by two callers —
the standalone path and the group-resolved one — and an annotator that two
callers disagree about is worse than one nobody can reach. Each case varies the
input across the branch that decides, so the shared core cannot be refactored
into a different answer without going red.

Written against the contract rather than the implementation: `_last_commit_epoch`
is injected so the tests turn on elapsed time, not on a real git history.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_DAY = 86400.0


@pytest.fixture()
def at_ages(monkeypatch: pytest.MonkeyPatch):
    """Report each repo path as last committed N days ago, per a {path: days} map.

    A path mapped to None has no commit epoch at all — a worktree git cannot
    answer for, which is a different input from an old one.
    """
    import camp.spine as spine

    now = 1_700_000_000.0
    monkeypatch.setattr(spine.time, "time", lambda: now)

    def _install(ages: dict[str, float | None]):
        def _epoch(wt_path: Path):
            days = ages.get(str(wt_path), 0.0)
            return None if days is None else now - days * _DAY

        monkeypatch.setattr(spine, "_last_commit_epoch", _epoch)
        return spine

    return _install


def _workspace(*paths: str) -> dict:
    return {"slug": "ws", "repos": [{"name": p, "path": p} for p in paths]}


# ---------------------------------------------------------------------------
# The threshold decides, and --days moves it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_days", "threshold", "expected_stale"),
    [
        (0.0, 7, False),
        (6.0, 7, False),
        (7.0, 7, True),
        (8.0, 7, True),
        (7.0, 30, False),
        (31.0, 30, True),
    ],
    ids=["today", "just-under", "exactly-at", "over", "under-raised", "over-raised"],
)
def test_staleness_is_idle_days_against_the_threshold(
    at_ages, age_days: float, threshold: int, expected_stale: bool
) -> None:
    """The `exactly-at` pair is the contract: the comparison is `>=`, so a
    workspace idle for exactly the threshold IS stale. The raised-threshold
    cases show the same age flipping answer when `--days` moves the line."""
    spine = at_ages({"repo-a": age_days})
    worktrees = [_workspace("repo-a")]

    spine._annotate_stale(worktrees, threshold_days=threshold)

    assert worktrees[0]["stale"] is expected_stale
    assert worktrees[0]["idle_days"] == int(age_days)


def test_the_most_recently_touched_member_speaks_for_the_workspace(at_ages) -> None:
    """A workspace is as idle as its LIVELIEST member, not its stalest.

    Work on any one member is work on the workspace, so the maximum epoch wins.
    Taking the minimum would report a workspace as abandoned because one repo in
    it has not changed in a year.
    """
    spine = at_ages({"old": 400.0, "fresh": 1.0, "middling": 30.0})
    worktrees = [_workspace("old", "fresh", "middling")]

    spine._annotate_stale(worktrees, threshold_days=7)

    assert worktrees[0]["idle_days"] == 1
    assert worktrees[0]["stale"] is False


def test_a_workspace_git_cannot_answer_for_reads_as_stale(at_ages) -> None:
    """No commit epoch — an empty or unreadable worktree — is treated as idle
    AT the threshold, so it lands on the stale side rather than being reported
    as freshly active."""
    spine = at_ages({"repo-a": None})
    worktrees = [_workspace("repo-a")]

    spine._annotate_stale(worktrees, threshold_days=7)

    assert worktrees[0]["idle_days"] == 7
    assert worktrees[0]["stale"] is True


def test_every_workspace_is_annotated_independently(at_ages) -> None:
    """Annotating a list must not let one workspace's verdict leak into another's
    — the case a shared accumulator would break."""
    spine = at_ages({"fresh": 0.0, "old": 99.0})
    worktrees = [_workspace("fresh"), _workspace("old")]

    spine._annotate_stale(worktrees, threshold_days=7)

    assert [w["stale"] for w in worktrees] == [False, True]


# ---------------------------------------------------------------------------
# The group-resolved status answers the same question the standalone one does.
# ---------------------------------------------------------------------------


@pytest.fixture()
def group_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real group with two workspaces on disk, so `cmd_status_group` reads
    actual manifests rather than a hand-built dict."""
    from camp.group.manifest import manifest_path_for, workspace_dir, write_central_manifest

    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    env = {"CAMP_CONFIG_DIR": str(tmp_path / "config"), "CAMP_STATE_DIR": str(tmp_path / "state")}

    def _seed(slug: str) -> Path:
        ws = workspace_dir("testgrp", slug, env=env)
        member = ws / "repo_a"
        member.mkdir(parents=True, exist_ok=True)
        write_central_manifest(
            manifest_path_for("testgrp", slug, env=env),
            {
                "schema_version": 1,
                "group": "testgrp",
                "slug": slug,
                "branch": f"worktree-{slug}",
                "members": [
                    {
                        "name": "repo_a",
                        "repo_root": "/nonexistent/repo_a",
                        "worktree_path": str(member),
                        "provision_state": "ready",
                    }
                ],
            },
        )
        return member

    return {"env": env, "seed": _seed, "group": {"group": {"name": "testgrp"}}}


def test_the_group_status_carries_the_same_stale_annotation(
    group_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--stale` means the same thing whichever path answers it.

    The group-resolved status reads manifests rather than the standalone
    registry, but the QUESTION is identical — how long since anyone touched this
    workspace — so the annotation has to be too, keyed off the same threshold.
    """
    import camp.spine as spine
    from camp.provision.lifecycle import cmd_status_group

    fresh = group_env["seed"]("fresh-ws")
    old = group_env["seed"]("old-ws")

    now = 1_700_000_000.0
    ages = {str(fresh): 1.0, str(old): 99.0}
    monkeypatch.setattr(spine.time, "time", lambda: now)
    monkeypatch.setattr(
        spine, "_last_commit_epoch", lambda p: now - ages.get(str(p), 0.0) * _DAY
    )

    result = cmd_status_group(
        group_env["group"], slug=None, env=group_env["env"], stale_days=7
    )

    by_slug = {w["slug"]: w for w in result["worktrees"]}
    assert by_slug["fresh-ws"]["stale"] is False
    assert by_slug["fresh-ws"]["idle_days"] == 1
    assert by_slug["old-ws"]["stale"] is True
    assert by_slug["old-ws"]["idle_days"] == 99


def test_the_group_status_is_not_annotated_unless_stale_was_asked_for(
    group_env,
) -> None:
    """The annotation costs a `git log` per member, so it is opt-in.

    Its absence is the observable difference — a status that always carried the
    keys would make `--stale` a no-op flag.
    """
    from camp.provision.lifecycle import cmd_status_group

    group_env["seed"]("ws")

    result = cmd_status_group(group_env["group"], slug=None, env=group_env["env"])

    assert "stale" not in result["worktrees"][0]
    assert "idle_days" not in result["worktrees"][0]


def test_the_group_threshold_moves_with_days(
    group_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same workspace, same age, two thresholds, two answers — the group path
    honours `--days` rather than hardcoding the default."""
    import camp.spine as spine
    from camp.provision.lifecycle import cmd_status_group

    member = group_env["seed"]("ws")
    now = 1_700_000_000.0
    monkeypatch.setattr(spine.time, "time", lambda: now)
    monkeypatch.setattr(spine, "_last_commit_epoch", lambda p: now - 10.0 * _DAY)

    assert member.is_dir()
    tight = cmd_status_group(group_env["group"], slug=None, env=group_env["env"], stale_days=7)
    loose = cmd_status_group(group_env["group"], slug=None, env=group_env["env"], stale_days=30)

    assert tight["worktrees"][0]["stale"] is True
    assert loose["worktrees"][0]["stale"] is False


# ---------------------------------------------------------------------------
# The operator-facing surface: the flags, the rendering, and the refusal.
# ---------------------------------------------------------------------------


def _run_status(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    import camp.cli.dispatch as dispatch

    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    try:
        dispatch.main()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


@pytest.fixture()
def aged_group(group_env, monkeypatch: pytest.MonkeyPatch):
    """One group with a fresh workspace and one idle for 99 days."""
    import camp.spine as spine

    (tmp := group_env["env"])["CAMP_CONFIG_DIR"]
    groups_dir = Path(tmp["CAMP_CONFIG_DIR"]) / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n[[members]]\nname = "repo_a"\nrepo_root = "/nonexistent/repo_a"\n'
    )
    fresh = group_env["seed"]("fresh-ws")
    old = group_env["seed"]("old-ws")

    now = 1_700_000_000.0
    ages = {str(fresh): 1.0, str(old): 99.0}
    monkeypatch.setattr(spine.time, "time", lambda: now)
    monkeypatch.setattr(
        spine, "_last_commit_epoch", lambda p: now - ages.get(str(p), 0.0) * _DAY
    )
    return group_env


def test_group_status_stale_is_accepted_and_reported_in_json(
    aged_group, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The flag the group path used to refuse as unknown now answers."""
    import json

    code = _run_status(monkeypatch, ["status", "--group", "testgrp", "--stale", "--json"])
    assert code == 0
    rows = {w["slug"]: w for w in json.loads(capsys.readouterr().out)["worktrees"]}
    assert rows["old-ws"]["stale"] is True
    assert rows["fresh-ws"]["stale"] is False


def test_group_status_marks_the_stale_workspace_in_the_human_table(
    aged_group, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Human output carries the same verdict as `--json`, in the marker shape the
    standalone path already uses."""
    code = _run_status(monkeypatch, ["status", "--group", "testgrp", "--stale"])
    out = capsys.readouterr().out
    assert code == 0
    stale_line = next(line for line in out.splitlines() if "old-ws" in line)
    fresh_line = next(line for line in out.splitlines() if "fresh-ws" in line)
    assert "[STALE 99d]" in stale_line
    assert "STALE" not in fresh_line


def test_group_status_without_stale_shows_no_marker(
    aged_group, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The pair that shows the flag is doing the work: same 99-day workspace,
    no flag, no marker."""
    code = _run_status(monkeypatch, ["status", "--group", "testgrp"])
    assert code == 0
    assert "STALE" not in capsys.readouterr().out


def test_group_status_days_moves_the_threshold(
    aged_group, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """`--days` raised past the idle age flips the same workspace to not-stale."""
    import json

    _run_status(monkeypatch, ["status", "--group", "testgrp", "--stale", "--days", "365", "--json"])
    rows = {w["slug"]: w for w in json.loads(capsys.readouterr().out)["worktrees"]}
    assert rows["old-ws"]["stale"] is False


def test_group_status_days_refuses_a_non_integer_exactly_as_the_standalone_path_does(
    aged_group, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Two paths, one wording — a flag that behaves the same should refuse the
    same, or the operator learns which code answered them rather than what they
    typed wrong."""
    code = _run_status(monkeypatch, ["status", "--group", "testgrp", "--stale", "--days", "abc"])
    assert code == 1
    assert capsys.readouterr().err == (
        "camp status: --days requires an integer argument, got 'abc'\n"
    )


def test_stale_is_refused_for_the_single_workspace_view(
    aged_group, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Naming one workspace leaves nothing to rank.

    The scoped view reports provisioning state for the workspace the operator
    named; idleness is a comparison across the group's workspaces. Accepting the
    flag and quietly dropping it is the failure this refusal exists to prevent.
    """
    code = _run_status(
        monkeypatch, ["status", "--group", "testgrp", "--name", "old-ws", "--stale"]
    )
    assert code == 1
    assert capsys.readouterr().err == (
        "camp status: --stale ranks the group's workspaces by idleness — it has "
        "no meaning for the single-workspace view\n"
    )


def test_the_same_scoped_invocation_without_stale_is_accepted(
    aged_group, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The control: the refusal is about `--stale`, not about naming a
    workspace."""
    code = _run_status(monkeypatch, ["status", "--group", "testgrp", "--name", "old-ws"])
    assert code != 1 or "--stale" not in capsys.readouterr().err


def test_days_is_validated_even_without_stale(
    aged_group, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A malformed `--days` is a typo worth reporting whether or not `--stale`
    came with it — and the standalone path already validates it unconditionally,
    so the two must agree here too."""
    code = _run_status(monkeypatch, ["status", "--group", "testgrp", "--days", "abc"])
    assert code == 1
    assert capsys.readouterr().err == (
        "camp status: --days requires an integer argument, got 'abc'\n"
    )
