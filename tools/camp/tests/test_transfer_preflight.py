"""Tests for transfer/preflight.py — the preflight composition as pure data.

Test contract:
- This host owns it, peer ready, every excluded set declared -> a verdict of
  "would transfer", listing the conversations that would cross and the trees
  that would be regenerated.
- A workspace owned by the peer -> a refusal naming the owning host and the
  remedy that actually exists in this slice, naming no flag this slice does
  not ship.
- A workspace with no recorded owner -> proceeds, and says explicitly that
  ownership was never recorded - lexically distinct from "this host has no
  declared name" and from "a member never declared an excluded set".
- A member declaring no excluded set -> refusal naming that member.
- Two members declaring no excluded set -> both named, not just the first.
- A member declaring an explicit empty excluded set -> not a refusal.
- An unreachable peer -> every peer-dependent check reports indeterminate,
  the verdict is not clean, and no check reports a pass it could not have
  observed.
- Each distinct transport outcome produces an indeterminate check carrying
  that outcome's own identity: a changed host key and a connection timeout
  are distinguishable in the data, not merely both indeterminate.
- A peer whose declared name equals this host's -> refusal naming the
  collision.
- A peer whose account binding differs -> refusal naming both values.
- A slug already present on the peer owned by a third name -> refusal; owned
  by this host -> not a refusal.
- A run in which several checks fail reports every one of them, not the
  first.
- A live conversation rooted in the workspace is reported as live and does
  not by itself make the verdict unclean, since nothing moves on this path.
- The composition writes nothing and starts no process of its own, asserted
  by a byte-identical snapshot of the whole camp state directory across the
  call, and emits no user-facing text, asserted on the captured streams.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _preflight_module():
    import importlib

    return importlib.import_module("camp.transfer.preflight")


def _probe_module():
    import importlib

    return importlib.import_module("camp.transfer.probe")


def _transport_module():
    import importlib

    return importlib.import_module("camp.host.transport")


def _member(name: str, excluded: tuple[str, ...] | None) -> Any:
    preflight = _preflight_module()
    return preflight.MemberDeclaration(name=name, excluded=excluded)


def _probe_answer(
    *,
    self_name: str | None = "peer-b",
    group_configured: bool = True,
    members: tuple | None = None,
    account: str | None = "acct-x",
    workspace_exists: bool = False,
    workspace_owner: str | None = None,
) -> Any:
    probe = _probe_module()
    if members is None:
        members = (probe.MemberRepoStatus(name="repo-a", repo_root_exists=True),)
    return probe.ProbeAnswer(
        self_name=self_name,
        group_configured=group_configured,
        members=members,
        account=account,
        workspace_exists=workspace_exists,
        workspace_owner=workspace_owner,
        contract_version=probe.PROBE_CONTRACT_VERSION,
    )


def _compose(
    *,
    self_name: str | None = "host-a",
    self_account: str | None = "acct-x",
    workspace_manifest_exists: bool = True,
    owner: str | None = None,
    peer_name: str = "peer-b",
    peer_declared: bool = True,
    probe_result: Any = None,
    members: tuple = (),
    slug: str = "my-slug",
    conversations: tuple | None = (),
):
    preflight = _preflight_module()
    if probe_result is None:
        probe_result = _probe_answer()
    if not members:
        members = (_member("repo-a", ("build/",)),)
    return preflight.compose_preflight(
        self_name=self_name,
        self_account=self_account,
        workspace_manifest_exists=workspace_manifest_exists,
        owner=owner,
        peer_name=peer_name,
        peer_declared=peer_declared,
        probe_result=probe_result,
        members=members,
        slug=slug,
        conversations=conversations,
    )


def _check(result: Any, name: str) -> Any:
    matches = [c for c in result.checks if c.name == name]
    assert len(matches) == 1, f"expected exactly one check named {name!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# The clean path
# ---------------------------------------------------------------------------


def test_owned_here_peer_ready_every_excluded_declared_yields_would_transfer():
    preflight = _preflight_module()
    conversation = object()
    result = _compose(
        owner="host-a",
        members=(_member("repo-a", ("build/",)),),
        conversations=(conversation,),
    )
    assert result.verdict is preflight.Verdict.WOULD_TRANSFER
    assert result.conversations == (conversation,)
    assert result.regenerated == (_member("repo-a", ("build/",)),)


def test_owner_none_also_yields_would_transfer_when_everything_else_is_clean():
    preflight = _preflight_module()
    result = _compose(owner=None)
    assert result.verdict is preflight.Verdict.WOULD_TRANSFER


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_workspace_owned_by_peer_is_a_refusal_naming_owner_and_no_unshipped_flag():
    preflight = _preflight_module()
    result = _compose(owner="peer-b", self_name="host-a")
    check = _check(result, "this host owns it, or it was never recorded")
    assert check.status is preflight.CheckStatus.FAILED
    assert "peer-b" in check.detail
    assert "--move" not in check.detail
    assert "--force" not in check.detail
    assert result.verdict is preflight.Verdict.NOT_CLEAN


def test_no_recorded_owner_proceeds_with_lexically_distinct_wording():
    preflight = _preflight_module()
    result = _compose(owner=None, self_name=None, members=(_member("repo-a", None),))
    ownership_check = _check(result, "this host owns it, or it was never recorded")
    name_check = _check(result, "this host has declared a name")
    excluded_check = _check(result, "every member declares an excluded set")

    assert ownership_check.status is preflight.CheckStatus.PASSED
    assert ownership_check.detail == "ownership was never recorded"
    assert name_check.detail != ownership_check.detail
    assert excluded_check.detail != ownership_check.detail
    assert "ownership" not in name_check.detail
    assert "ownership" not in excluded_check.detail


# ---------------------------------------------------------------------------
# Excluded-set declarations
# ---------------------------------------------------------------------------


def test_one_member_with_no_excluded_set_is_named_in_the_refusal():
    preflight = _preflight_module()
    result = _compose(
        members=(
            _member("repo-a", ("build/",)),
            _member("repo-b", None),
        )
    )
    check = _check(result, "every member declares an excluded set")
    assert check.status is preflight.CheckStatus.FAILED
    assert "repo-b" in check.detail
    assert result.verdict is preflight.Verdict.NOT_CLEAN


def test_two_members_with_no_excluded_set_are_both_named():
    preflight = _preflight_module()
    result = _compose(
        members=(
            _member("repo-a", None),
            _member("repo-b", None),
        )
    )
    check = _check(result, "every member declares an excluded set")
    assert check.status is preflight.CheckStatus.FAILED
    assert "repo-a" in check.detail
    assert "repo-b" in check.detail


def test_explicit_empty_excluded_set_is_not_a_refusal():
    preflight = _preflight_module()
    result = _compose(
        owner="host-a",
        members=(_member("repo-a", ()),),
    )
    check = _check(result, "every member declares an excluded set")
    assert check.status is preflight.CheckStatus.PASSED
    assert result.verdict is preflight.Verdict.WOULD_TRANSFER


# ---------------------------------------------------------------------------
# Peer unreachable -> indeterminate cascade
# ---------------------------------------------------------------------------

_PEER_DEPENDENT_CHECK_NAMES = (
    "the peer answers",
    "the peer's declared name differs from this host's",
    "the peer has the group configured with existing member repo roots",
    "the peer's harness account binding matches this end's",
    "the slug is free on the peer, or present there and owned by this host",
)


def test_unreachable_peer_makes_every_peer_dependent_check_indeterminate():
    preflight = _preflight_module()
    transport = _transport_module()
    outcome = transport.Unreachable(reason="Connection timed out")
    result = _compose(owner="host-a", probe_result=outcome)

    for name in _PEER_DEPENDENT_CHECK_NAMES:
        check = _check(result, name)
        assert check.status is preflight.CheckStatus.INDETERMINATE, name
        assert check.status is not preflight.CheckStatus.PASSED
        assert check.transport_outcome is outcome

    assert result.verdict is preflight.Verdict.NOT_CLEAN


def test_distinct_transport_outcomes_produce_distinguishable_indeterminate_checks():
    preflight = _preflight_module()
    transport = _transport_module()

    unreachable = transport.Unreachable(reason="Connection timed out")
    identity_changed = transport.IdentityChanged()

    result_unreachable = _compose(probe_result=unreachable)
    result_changed = _compose(probe_result=identity_changed)

    check_unreachable = _check(result_unreachable, "the peer answers")
    check_changed = _check(result_changed, "the peer answers")

    assert check_unreachable.status is preflight.CheckStatus.INDETERMINATE
    assert check_changed.status is preflight.CheckStatus.INDETERMINATE
    assert check_unreachable.detail != check_changed.detail
    assert type(check_unreachable.transport_outcome) is transport.Unreachable
    assert type(check_changed.transport_outcome) is transport.IdentityChanged


# ---------------------------------------------------------------------------
# Self-name collision
# ---------------------------------------------------------------------------


def test_self_name_collision_is_a_refusal_naming_the_collision():
    preflight = _preflight_module()
    probe = _probe_module()
    collision = probe.SelfNameCollision(peer_self_name="host-a")
    result = _compose(self_name="host-a", probe_result=collision)

    check = _check(result, "the peer's declared name differs from this host's")
    assert check.status is preflight.CheckStatus.FAILED
    assert "host-a" in check.detail
    assert result.verdict is preflight.Verdict.NOT_CLEAN


# ---------------------------------------------------------------------------
# Account binding
# ---------------------------------------------------------------------------


def test_account_binding_mismatch_is_a_refusal_naming_both_values():
    preflight = _preflight_module()
    result = _compose(
        self_account="acct-mine",
        probe_result=_probe_answer(account="acct-theirs"),
    )
    check = _check(result, "the peer's harness account binding matches this end's")
    assert check.status is preflight.CheckStatus.FAILED
    assert "acct-mine" in check.detail
    assert "acct-theirs" in check.detail


def test_account_binding_match_is_not_a_refusal():
    preflight = _preflight_module()
    result = _compose(
        owner="host-a",
        self_account="acct-x",
        probe_result=_probe_answer(account="acct-x"),
    )
    check = _check(result, "the peer's harness account binding matches this end's")
    assert check.status is preflight.CheckStatus.PASSED


# ---------------------------------------------------------------------------
# Slug conflict on the peer
# ---------------------------------------------------------------------------


def test_slug_present_on_peer_owned_by_third_name_is_a_refusal():
    preflight = _preflight_module()
    result = _compose(
        self_name="host-a",
        probe_result=_probe_answer(workspace_exists=True, workspace_owner="host-c"),
    )
    check = _check(
        result, "the slug is free on the peer, or present there and owned by this host"
    )
    assert check.status is preflight.CheckStatus.FAILED
    assert "host-c" in check.detail


def test_slug_present_on_peer_owned_by_this_host_is_not_a_refusal():
    preflight = _preflight_module()
    result = _compose(
        owner="host-a",
        self_name="host-a",
        probe_result=_probe_answer(workspace_exists=True, workspace_owner="host-a"),
    )
    check = _check(
        result, "the slug is free on the peer, or present there and owned by this host"
    )
    assert check.status is preflight.CheckStatus.PASSED
    assert result.verdict is preflight.Verdict.WOULD_TRANSFER


# ---------------------------------------------------------------------------
# Several failures at once
# ---------------------------------------------------------------------------


def test_a_run_with_several_failures_reports_every_one():
    preflight = _preflight_module()
    result = _compose(
        self_name=None,
        owner="peer-b",
        members=(_member("repo-a", None),),
    )
    name_check = _check(result, "this host has declared a name")
    ownership_check = _check(result, "this host owns it, or it was never recorded")
    excluded_check = _check(result, "every member declares an excluded set")

    assert name_check.status is preflight.CheckStatus.FAILED
    assert ownership_check.status is preflight.CheckStatus.FAILED
    assert excluded_check.status is preflight.CheckStatus.FAILED
    assert result.verdict is preflight.Verdict.NOT_CLEAN


# ---------------------------------------------------------------------------
# Live conversations don't dirty the verdict
# ---------------------------------------------------------------------------


class _FakeConversation:
    def __init__(self, live: bool) -> None:
        self.live = live


def test_live_conversation_is_reported_but_does_not_unclean_the_verdict():
    preflight = _preflight_module()
    live_convo = _FakeConversation(live=True)
    result = _compose(owner="host-a", conversations=(live_convo,))

    assert live_convo in result.conversations
    assert live_convo.live is True
    assert result.verdict is preflight.Verdict.WOULD_TRANSFER


def test_unenumerable_conversations_fail_that_check_and_uncleans_the_verdict():
    preflight = _preflight_module()
    result = _compose(owner="host-a", conversations=None)
    check = _check(result, "the conversations rooted here are enumerated")
    assert check.status is preflight.CheckStatus.FAILED
    assert result.verdict is preflight.Verdict.NOT_CLEAN


# ---------------------------------------------------------------------------
# Purity: no write, no process
# ---------------------------------------------------------------------------


def _snapshot(root: Path) -> dict[str, tuple[str, object]]:
    import os

    snapshot: dict[str, tuple[str, object]] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            snapshot[key] = ("symlink", os.readlink(path))
        elif path.is_dir():
            snapshot[key] = ("dir", None)
        else:
            snapshot[key] = ("file", path.read_bytes())
    return snapshot


def test_composition_writes_nothing_to_camp_state(
    tmp_path: Path, monkeypatch: Any
) -> None:
    state_root = tmp_path / "camp-state"
    state_root.mkdir()
    (state_root / "marker").write_text("untouched")
    # compose_preflight takes no env/path argument of its own — this points a
    # generic CAMP_STATE_DIR at the snapshot root only so a hypothetical write
    # keyed off ambient state would land somewhere this snapshot would catch.
    monkeypatch.setenv("CAMP_STATE_DIR", str(state_root))
    before = _snapshot(state_root)

    _compose(owner="host-a")

    after = _snapshot(state_root)
    assert before == after


def test_composition_emits_no_user_facing_text_of_its_own(capsys: Any) -> None:
    """Rendering belongs to the CLI layer, so the composition returns data and
    writes nothing for a human to read.

    Exercised on a run that fails several checks, because that is exactly where
    a convenience print gets added: the refusal wording is carried back in the
    returned checks, not emitted here.
    """
    preflight = _preflight_module()

    result = _compose(owner="peer-b", members=(_member("repo-a", None),))
    captured = capsys.readouterr()

    assert result.verdict is preflight.Verdict.NOT_CLEAN
    failed = [c for c in result.checks if c.status is preflight.CheckStatus.FAILED]
    assert len(failed) >= 2, "expected a multi-failure run to exercise this"
    assert [c.detail for c in failed if c.detail], "refusals must carry their wording as data"
    assert captured.out == ""
    assert captured.err == ""
