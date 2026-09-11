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
- A peer named but not declared in hosts.toml -> refusal naming it; a
  declared one passes the same check.
- A peer answering without the group configured -> refusal; a peer missing
  member repo roots -> refusal naming every missing member, not the first;
  every root present -> passes.
- `regenerated` lists a member's declared exclusions and omits a member
  declaring an explicit empty set, which has nothing to rebuild.
- Across the whole closed transport-outcome set, pairwise, each outcome
  yields a distinct indeterminate detail and carries its own outcome back.
- A peer that does not have the group configured answers no account, member
  list or slug state, so the checks reading those fields report no pass.
- A host with no declared name is never told a peer workspace whose ownership
  was never recorded is owned by this host.
- A malformed peer payload fails every payload-dependent check by name,
  carrying the refusal's reason and no transport outcome; a peer that was
  never probed fails them too, lexically distinct from the malformed case;
  and a transport failure, a malformed payload and a never-probed peer stay
  distinguishable from each other.
- The composition writes nothing and starts no process of its own, asserted
  by a byte-identical snapshot of the whole camp state directory across the
  call, by making every `subprocess` spawn entry point raise, and emits no
  user-facing text, asserted on the captured streams.
"""

from __future__ import annotations

import json
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


#: Distinguishes "caller said nothing" from "caller explicitly passed None",
#: which is the never-probed input the composition must answer for.
_UNSET = object()


def _compose(
    *,
    self_name: str | None = "host-a",
    self_account: str | None = "acct-x",
    workspace_manifest_exists: bool = True,
    owner: str | None = None,
    peer_name: str = "peer-b",
    peer_declared: bool = True,
    probe_result: Any = _UNSET,
    members: tuple = (),
    slug: str = "my-slug",
    conversations: tuple | None = (),
):
    preflight = _preflight_module()
    if probe_result is _UNSET:
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


# ---------------------------------------------------------------------------
# The peer-side declaration and group checks, on their failing branches
# ---------------------------------------------------------------------------


def test_peer_not_declared_is_a_refusal_naming_the_peer() -> None:
    """A peer named on the command line but absent from hosts.toml cannot be
    probed, so the check that says so must fail rather than pass by default."""
    preflight = _preflight_module()

    result = _compose(peer_name="ghost-host", peer_declared=False)

    check = _check(result, "the named peer is declared")
    assert check.status is preflight.CheckStatus.FAILED
    assert "ghost-host" in check.detail
    assert result.verdict is preflight.Verdict.NOT_CLEAN


def test_peer_declared_passes_that_same_check() -> None:
    """The other side of the branch: the check's answer depends on the input."""
    preflight = _preflight_module()

    result = _compose(peer_name="peer-b", peer_declared=True)

    check = _check(result, "the named peer is declared")
    assert check.status is preflight.CheckStatus.PASSED
    assert "peer-b" in check.detail


def test_peer_without_the_group_configured_is_a_refusal() -> None:
    """The peer answered, but does not have the group at all. Distinct from a
    peer that has it configured and empty — the probe answers this explicitly
    rather than deriving it from a member count."""
    preflight = _preflight_module()

    result = _compose(probe_result=_probe_answer(group_configured=False))

    check = _check(
        result, "the peer has the group configured with existing member repo roots"
    )
    assert check.status is preflight.CheckStatus.FAILED
    assert "does not have this group configured" in check.detail
    assert result.verdict is preflight.Verdict.NOT_CLEAN


def test_peer_missing_member_repo_roots_names_every_one_not_just_the_first() -> None:
    """Two members missing their repo root on the peer: both are named, so the
    operator fixes both in one trip rather than discovering the second after."""
    preflight = _preflight_module()
    probe = _probe_module()

    result = _compose(
        probe_result=_probe_answer(
            members=(
                probe.MemberRepoStatus(name="repo-a", repo_root_exists=False),
                probe.MemberRepoStatus(name="repo-b", repo_root_exists=True),
                probe.MemberRepoStatus(name="repo-c", repo_root_exists=False),
            )
        )
    )

    check = _check(
        result, "the peer has the group configured with existing member repo roots"
    )
    assert check.status is preflight.CheckStatus.FAILED
    assert "repo-a" in check.detail
    assert "repo-c" in check.detail, "only the first missing member was named"
    assert "repo-b" not in check.detail, "a present member was reported missing"


def test_peer_with_every_member_repo_root_present_passes_that_check() -> None:
    """The passing side of the same branch."""
    preflight = _preflight_module()
    probe = _probe_module()

    result = _compose(
        probe_result=_probe_answer(
            members=(
                probe.MemberRepoStatus(name="repo-a", repo_root_exists=True),
                probe.MemberRepoStatus(name="repo-b", repo_root_exists=True),
            )
        )
    )

    check = _check(
        result, "the peer has the group configured with existing member repo roots"
    )
    assert check.status is preflight.CheckStatus.PASSED


# ---------------------------------------------------------------------------
# What a would-transfer verdict says it would regenerate
# ---------------------------------------------------------------------------


def test_regenerated_lists_declared_exclusions_and_omits_an_empty_declaration() -> None:
    """`regenerated` answers "what would be rebuilt rather than carried", so a
    member declaring an explicit empty excluded set has nothing to rebuild and
    must not appear — the same three-valued distinction the refusal check makes,
    holding in this field too.
    """
    result = _compose(
        members=(
            _member("has-exclusions", ("build/", "node_modules/")),
            _member("declares-nothing", ()),
        )
    )

    names = [m.name for m in result.regenerated]
    assert "has-exclusions" in names
    assert "declares-nothing" not in names, (
        "a member declaring an explicit empty excluded set has nothing to "
        "regenerate and must not be listed as though it did"
    )


# ---------------------------------------------------------------------------
# Every transport outcome stays distinguishable, not just the pair we sampled
# ---------------------------------------------------------------------------


def _every_transport_outcome() -> tuple:
    transport = _transport_module()
    return (
        transport.Unreachable(reason="name or service not known"),
        transport.StoppedResponding(execution_timeout=30.0),
        transport.IdentityUnknown(),
        transport.IdentityChanged(),
        transport.CredentialsRefused(),
        transport.CampNotResolvable(),
        transport.RemoteRefusal(stdout="", stderr="camp: no such group", exit_code=2),
    )


def test_every_transport_outcome_yields_a_distinct_indeterminate_detail() -> None:
    """Pairwise across the whole closed outcome set, not one sampled pair.

    A changed host key and an ordinary timeout must not read alike, and neither
    must any other pair: the operator's next action differs for every one of
    them. Driven through the public composition so the detail asserted on is the
    one a reader would actually see.
    """
    preflight = _preflight_module()

    details: dict[str, str] = {}
    for outcome in _every_transport_outcome():
        result = _compose(probe_result=outcome)
        check = _check(result, "the peer answers")
        assert check.status is preflight.CheckStatus.INDETERMINATE
        assert check.transport_outcome is outcome
        details[type(outcome).__name__] = check.detail

    collisions = [
        (a, b)
        for a in details
        for b in details
        if a < b and details[a] == details[b]
    ]
    assert not collisions, f"transport outcomes read alike: {collisions}"


def test_composition_starts_no_subprocess(monkeypatch: Any) -> None:
    """Purity's other half: the composition runs no process of its own.

    Enforced by making every process-spawning entry point in `subprocess` raise
    — a spawn that wrote nothing to the streams or the state directory would
    slip past both the snapshot and the captured-stream checks.
    """
    import subprocess

    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"compose_preflight spawned a process: {args!r}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _refuse)

    result = _compose(owner="host-a")

    assert result.verdict is _preflight_module().Verdict.WOULD_TRANSFER


# ---------------------------------------------------------------------------
# The peer answered with nothing usable: a malformed payload, or never probed
# ---------------------------------------------------------------------------

#: The four checks that each need a field of the peer's parsed answer, so each
#: owes an answer when there is no parsed answer to read.
_PAYLOAD_DEPENDENT_CHECKS = (
    "the peer's declared name differs from this host's",
    "the peer has the group configured with existing member repo roots",
    "the peer's harness account binding matches this end's",
    "the slug is free on the peer, or present there and owned by this host",
)


def test_malformed_peer_response_fails_every_payload_dependent_check_by_name() -> None:
    """A refused payload means the peer *did* answer, so this is a failure
    rather than indeterminate - nothing about the transport is in doubt.

    Each of the four checks must say so in its own right, and carry the
    refusal's reason, rather than one of them reporting it and the rest
    reading as satisfied.
    """
    preflight = _preflight_module()
    probe = _probe_module()

    result = _compose(probe_result=probe.ProbeRefused(reason="contract version 99"))

    for name in _PAYLOAD_DEPENDENT_CHECKS:
        check = _check(result, name)
        assert check.status is preflight.CheckStatus.FAILED, name
        assert "contract version 99" in check.detail, name
        assert check.transport_outcome is None, (
            f"{name}: a malformed payload is not a transport outcome"
        )
    assert result.verdict is preflight.Verdict.NOT_CLEAN


def test_a_peer_that_was_never_probed_fails_every_payload_dependent_check() -> None:
    """`None` is the never-probed input - the CLI reaches it when the named
    peer is not declared, so there was nobody to ask.

    It must not read as a pass, and must stay lexically distinct from a peer
    that answered with something malformed.
    """
    preflight = _preflight_module()

    result = _compose(probe_result=None)

    answers = _check(result, "the peer answers")
    assert answers.status is preflight.CheckStatus.FAILED
    assert "never probed" in answers.detail

    for name in _PAYLOAD_DEPENDENT_CHECKS:
        check = _check(result, name)
        assert check.status is preflight.CheckStatus.FAILED, name
        assert "never probed" in check.detail, name
        assert "malformed" not in check.detail, name
    assert result.verdict is preflight.Verdict.NOT_CLEAN


def test_the_three_unusable_peer_answers_stay_distinguishable_from_each_other() -> None:
    """A transport failure, a malformed payload and a never-probed peer are
    three different operator problems - retry, a version mismatch, and a
    missing host declaration - so the checks must not collapse them.
    """
    preflight = _preflight_module()
    probe = _probe_module()
    transport = _transport_module()

    details = {
        "transport": _compose(probe_result=transport.IdentityChanged()),
        "malformed": _compose(probe_result=probe.ProbeRefused(reason="bad shape")),
        "never": _compose(probe_result=None),
    }

    for name in _PAYLOAD_DEPENDENT_CHECKS:
        rendered = {k: _check(r, name).detail for k, r in details.items()}
        assert len(set(rendered.values())) == 3, f"{name}: {rendered}"

    # Only the transport case is indeterminate; the other two are failures the
    # peer itself produced.
    assert (
        _check(details["transport"], _PAYLOAD_DEPENDENT_CHECKS[0]).status
        is preflight.CheckStatus.INDETERMINATE
    )
    for key in ("malformed", "never"):
        assert (
            _check(details[key], _PAYLOAD_DEPENDENT_CHECKS[0]).status
            is preflight.CheckStatus.FAILED
        )


# ---------------------------------------------------------------------------
# Fields the peer never answered must not be reported as observations
# ---------------------------------------------------------------------------


def _wire_answer(**overrides) -> Any:
    """A ProbeAnswer built by running the real parser over a real wire payload.

    Hand-built ProbeAnswer fixtures can express field combinations the
    answering side never emits and the parser never produces — an unconfigured
    group carrying a populated member list, say. Routing through
    `parse_probe_response` keeps these cases honest about what can actually
    arrive.
    """
    probe = _probe_module()
    payload = {
        "contract_version": probe.PROBE_CONTRACT_VERSION,
        "self_name": "peer-b",
        "group_configured": False,
        "members": None,
        "account": None,
        "workspace_exists": None,
        "workspace_owner": None,
    }
    payload.update(overrides)
    answer = probe.parse_probe_response(json.dumps(payload))
    assert isinstance(answer, probe.ProbeAnswer), answer
    return answer


def test_an_unconfigured_peer_group_leaves_its_dependent_checks_unsatisfied() -> None:
    """When the peer lacks the group, it answers no account, no member list and
    no slug state — so the checks reading those fields have observed nothing.

    Reporting them as passes would tell the operator the account binding
    matches and the slug is free on a host that never looked, and check 9's
    comparison is the overwrite authorization a later slice inherits.
    """
    preflight = _preflight_module()

    result = _compose(probe_result=_wire_answer())

    for name in (
        "the peer's harness account binding matches this end's",
        "the slug is free on the peer, or present there and owned by this host",
    ):
        check = _check(result, name)
        assert check.status is not preflight.CheckStatus.PASSED, (
            f"{name}: reported a pass from a field the peer never answered — "
            f"{check.detail!r}"
        )
        assert "cannot evaluate" in check.detail, (
            f"{name}: must say it could not evaluate, not merely restate check "
            f"7's own failure — {check.detail!r}"
        )
        assert "this group configured" in check.detail, f"{name}: {check.detail!r}"
    assert result.verdict is preflight.Verdict.NOT_CLEAN


def test_a_nameless_host_is_never_told_the_peer_workspace_is_its_own() -> None:
    """With no declared name here and no owner recorded on the peer, both
    values are None and an equality test reads as a match.

    A host that cannot name itself cannot own anything, so the slug check must
    not claim it does.
    """
    preflight = _preflight_module()

    result = _compose(
        self_name=None,
        probe_result=_wire_answer(
            group_configured=True,
            members=[],
            account=None,
            workspace_exists=True,
            workspace_owner=None,
        ),
    )

    check = _check(
        result, "the slug is free on the peer, or present there and owned by this host"
    )
    assert check.status is not preflight.CheckStatus.PASSED, check.detail
    assert "owned by this host" not in check.detail, (
        f"claimed ownership from a None-to-None comparison: {check.detail!r}"
    )


def test_a_peer_workspace_with_no_recorded_owner_says_so_in_its_own_words() -> None:
    """A slug present on the peer whose ownership was never recorded there is
    not this host's to overwrite, and is not the same condition as ownership
    never having been recorded *here*.

    Both readings must stay lexically distinct, because the remedy differs:
    one is fixed on this machine and the other on the far one.
    """
    preflight = _preflight_module()

    result = _compose(
        self_name="host-a",
        owner="host-a",
        probe_result=_wire_answer(
            group_configured=True,
            members=[],
            account=None,
            workspace_exists=True,
            workspace_owner=None,
        ),
    )

    peer_check = _check(
        result, "the slug is free on the peer, or present there and owned by this host"
    )
    local_check = _check(result, "this host owns it, or it was never recorded")

    assert peer_check.status is preflight.CheckStatus.FAILED, peer_check.detail
    assert "never recorded" in peer_check.detail
    assert "peer" in peer_check.detail, (
        f"must say where the unrecorded ownership is: {peer_check.detail!r}"
    )
    assert peer_check.detail != local_check.detail
    assert result.verdict is preflight.Verdict.NOT_CLEAN
