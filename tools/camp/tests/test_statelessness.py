"""camp's launch surface persists nothing: one snapshot over every new flow.

`camp launch --dir`, `camp launch --resume`, `camp sessions --recoverable`, and
`camp kill` are stateless by contract — they read the harness's own transcript
store and camp's group config, and they write nothing camp owns. Every session
they can name already exists somewhere else, so there is no camp-side record to
keep and none to go stale.

`camp kill` is the sharpest case, and the reason this guard covers it. Stopping
a session is exactly the moment a design would be tempted to leave a marker
behind — a "parked" flag set on the stop and cleared on the resume — and the
whole recoverability story depends on there being no such marker: a stopped
session stays addressable by the same ref because the transcript the harness
already keeps is the only record, and a flag camp wrote could go stale against
it. Nothing is set on a stop and nothing is cleared on a resume, so the walk
below drives a full stop-then-resume round trip and holds it to the same
byte-level snapshot as every other flow.

The per-flow tests in `test_session_cli.py` each assert that for the one command
they drive. That is not the same guarantee. A per-flow assertion passes as long
as *that* command is clean, and stays passing while a sibling flow — or a helper
they share — starts writing. This module closes that gap the only way it can be
closed: it walks the WHOLE union of new flows, success and refusal alike, in one
process-sequence against one state directory, and compares a full recursive
snapshot of `CAMP_STATE_DIR` — every path, every symlink target, and every byte
of every file — taken before the walk against the same snapshot taken after each
step. A snapshot over the union cannot be satisfied by any one flow behaving.

Byte content is part of the snapshot deliberately. The state directory holds the
group's worktrees and their manifests, so a flow that rewrote a manifest in place
— same paths, different contents — would be invisible to a listing of names.

Two things keep the walk honest:

- The baseline is taken only once `camp new`'s BACKGROUND provisioner has stopped
  writing. Workspace creation is asynchronous; a snapshot taken while it is still
  moving would report the provisioner's writes as the flow-under-test's.
- The walk asserts what it actually provoked — that it drove a launch that
  happened (exit 0), a launch that was refused (exit 1), and an ambiguous
  reference (exit 2). A walk in which every step failed early for some unrelated
  reason would otherwise "prove" statelessness by never reaching the code. The
  stop flows are held to the same rule, and two of them need more than an exit
  code to satisfy it: a stop that reclaimed memory and a stop that found the
  session already down are both successes, so the outcome line is what says
  which of the two early-return branches actually ran.

The world — a fake harness, a tmux stand-in, and the hermetic transcript store —
is the one `test_session_cli.py` builds, imported rather than rebuilt. The point
of this module is that it drives the SAME commands those tests drive; a second,
separately-maintained copy of the scaffolding could drift into driving something
else and the cross-cutting guarantee would quietly stop covering the real flows.
HOME is redirected for every command here, so a launch that pre-seeds harness
trust writes into a temporary home rather than the developer's own.

A workspace that arrived by transfer looks, to `camp launch --resume` and
`camp sessions --recoverable`, like any other workspace whose manifest names a
foreign owner — ownership never moves on arrival, so its central manifest keeps
recording the sending host as owner even once the workspace is fully usable
here. The walk below seeds exactly that: a workspace this host provisioned
itself but whose manifest owner is overwritten to a name that is not this
host's own, then resumes, stops, and re-resumes a session rooted in it, and
lists it under `--recoverable`, the same way the rest of this file drives the
non-transfer flows.

`camp transfer-probe` and `camp transfer --dry-run` are the transfer
preflight's two read-only surfaces — see `camp.cli.transfer`'s module
docstring — and are walked here too, under the same blanket snapshot equality,
but scored separately from the launch/sessions/kill walk's own exit-code
bookkeeping below: their exit codes come from a disjoint vocabulary (see that
module's EXIT CODES table) and folding them into the `{0, 1, 2}` closed-set
check further down would make that check assert something it was never meant
to.

**The mutating move path is deliberately excluded, not silently omitted.**
`camp transfer` WITHOUT `--dry-run`, once every preflight check has passed,
drives `camp.transfer.move.move_workspace` — it force-updates git refs,
extracts archives over a peer's worktree, and writes a transfer marker, by
design. A byte-identical `CAMP_STATE_DIR` after that path would mean the
transfer failed, not that it was clean, so this file cannot hold it to the
same assertion the rest of the walk uses without asserting the opposite of
what the path is for. Nor is `camp transfer-receive` walked here: it is the
peer-side half of that same mutating path, reachable only from a remote
`camp transfer` invocation over ssh, never from a single local host's own
command line. What each of them writes, and where, is `camp.transfer.move`'s
and `camp.transfer.receive`'s own test coverage
(`test_transfer_cli.py::TestMoveWorkspaceEndToEnd`, `test_transfer_receive.py`)
to hold, not this file's.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

#: The CLI-surface module, loaded BY PATH under a name of its own.
#:
#: Not `import test_session_cli`: `tools/lore/tests/` ships a module of that
#: exact name, and which of the two a bare import resolves to depends on the
#: collection order of whatever suite happens to be running. Addressing the file
#: directly makes the reuse unambiguous under any invocation, from this one test
#: file to the whole repo.
_SOURCE = Path(__file__).resolve().parent / "test_session_cli.py"
_spec = importlib.util.spec_from_file_location("camp_tests_session_cli", _SOURCE)
assert _spec and _spec.loader, _SOURCE
_cli = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _cli
_spec.loader.exec_module(_cli)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.cli.transfer import EXIT_PEER_UNREACHABLE  # noqa: E402
from camp.group.manifest import (  # noqa: E402
    manifest_path_for,
    owner_of,
    read_central_manifest,
    write_central_manifest,
)

from ._helpers import init_git_repo  # noqa: E402

_camp = _cli._camp
_new_workspace = _cli._new_workspace
_register_live = _cli._register_live
_seed_transcript = _cli._seed_transcript
_set_harness_binary = _cli._set_harness_binary
_set_launch_roots = _cli._set_launch_roots
_workspace_launch_dir = _cli._workspace_launch_dir
#: Re-bound so pytest resolves the fixture from this module.
cli_env = _cli.cli_env

#: Distinct, well-formed session ids — one per flow that addresses a session, so
#: no flow inherits the liveness a previous flow's successful resume created.
#: The `feat-amb-*` pair shares a workspace-name prefix, which is what makes the
#: ambiguity flow ambiguous.
_ID_WORKSPACE = "11111111-1111-4111-8111-111111111111"
_ID_ROOTED = "22222222-2222-4222-8222-222222222222"
_ID_AMB_ONE = "33333333-3333-4333-8333-333333333333"
_ID_AMB_TWO = "44444444-4444-4444-8444-444444444444"
_ID_LIVE = "55555555-5555-4555-8555-555555555555"
_ID_GONE = "66666666-6666-4666-8666-666666666666"
_ID_INELIGIBLE = "77777777-7777-4777-8777-777777777777"
_ID_UNREADABLE = "88888888-8888-4888-8888-888888888888"
#: A conversation whose recorded root already sits under a workspace this
#: host provisioned itself — standing in for one that arrived by transfer,
#: whose recorded root the receiving host rewrites to its own workspace
#: directory on arrival (see `camp.transfer.receive`'s `conversations` phase).
_ID_ARRIVED = "99999999-9999-4999-8999-999999999999"


def _snapshot(root: Path) -> dict[str, tuple[str, object]]:
    """Every path under *root*, with its kind and its full content.

    Directories carry no content, symlinks carry their target unresolved (so a
    retarget is a change even when the destination reads the same), and regular
    files carry their bytes.

    The scan itself isn't atomic against a concurrently-mutating tree (a
    background provisioner can remove a path between the walk and the read), so
    a transient `FileNotFoundError` retries the whole scan rather than escaping
    as if it were suite state.
    """
    while True:
        try:
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
        except FileNotFoundError:
            continue


def _settled_snapshot(root: Path, *, quiet_for: float = 0.3, timeout: float = 30.0) -> dict:
    """`_snapshot` once nothing under *root* has changed for *quiet_for*.

    `camp new` provisions in the background, so the tree is still moving when it
    returns. Comparing against a moving baseline would attribute the
    provisioner's writes to whichever flow happened to run next.
    """
    import time

    deadline = time.monotonic() + timeout
    previous = _snapshot(root)
    while time.monotonic() < deadline:
        time.sleep(quiet_for)
        current = _snapshot(root)
        if current == previous:
            return current
        previous = current
    return previous


def _diff(before: dict, after: dict) -> str:
    """The paths that appeared, vanished, or changed content — for the failure."""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(key for key in set(before) & set(after) if before[key] != after[key])
    return f"added={added} removed={removed} changed={changed}"


def test_no_new_launch_flow_writes_anything_under_the_state_dir(cli_env) -> None:
    """The whole surface, success and refusal alike, against one snapshot."""
    tmp_path: Path = cli_env["tmp_path"]
    state_dir: Path = Path(cli_env["state_dir"])
    home = tmp_path / "fakehome"
    (home / ".ssh").mkdir(parents=True)

    # A declared self-name so `camp new` below stamps an owner on the fixture's
    # live workspace — see the precondition assertion after `live_workspace`.
    # The declared peer is never actually reached: its ssh name resolves to
    # nothing, so `camp transfer --dry-run` below hits a real, fast DNS
    # failure rather than a fake transport this file would have to maintain.
    Path(cli_env["config_dir"]).mkdir(parents=True, exist_ok=True)
    (Path(cli_env["config_dir"]) / "hosts.toml").write_text(
        'self_name = "statelessness-host"\n'
        "\n"
        "[hosts.unreachable-peer]\n"
        'ssh = "camp-statelessness-guard-no-such-host"\n',
        encoding="utf-8",
    )

    # A third group, harnessed like mygroup but with no [launch] roots at all —
    # the "directory rooting is off by default" refusal needs a group that never
    # turned it on, and the allowlist below can only be authored once per group.
    third_repo = tmp_path / "repo_c"
    init_git_repo(third_repo, origin=True)
    result = _camp(cli_env, "group", "nolaunch", "--member", f"member={third_repo}")
    assert result.returncode == 0, result.stderr
    _set_harness_binary(cli_env["config_dir"], "nolaunch", "fakeharness")

    roots = tmp_path / "roots"
    rooted = roots / "projectx"
    rooted.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    a_file = roots / "notes.txt"
    a_file.write_text("not a directory\n", encoding="utf-8")
    # "~" expands from the injected HOME below, so the credential-store flow can
    # be allowlisted at the top level and still be refused on the deny rule.
    _set_launch_roots(cli_env, roots, "~")

    workspace = _workspace_launch_dir(cli_env, "feat-stateless")
    amb_one = _workspace_launch_dir(cli_env, "feat-amb-one")
    amb_two = _workspace_launch_dir(cli_env, "feat-amb-two")
    live_workspace = Path(_new_workspace(cli_env, "feat-live")).resolve()
    arrived_workspace = Path(_new_workspace(cli_env, "feat-arrived")).resolve()

    # This host provisioned `feat-arrived` itself, but a workspace that
    # arrived by transfer never gets its ownership moved to the receiving
    # host — the manifest keeps naming the sender. Overwriting it to a name
    # that is not this host's own is what makes the fixture stand in for an
    # arrived workspace rather than an ordinary one.
    arrived_manifest = manifest_path_for("mygroup", "feat-arrived", env=cli_env["env"])
    write_central_manifest(arrived_manifest, {"owner": "sender-host"}, allow_owner_change=True)

    # Precondition: the workspace this walk actually drives must already carry
    # an owner, read back through the real manifest reader — otherwise the
    # byte-level snapshot below is walking a manifest key it has never seen,
    # and would keep passing even if a future change stopped stamping it.
    live_manifest = manifest_path_for("mygroup", "feat-live", env=cli_env["env"])
    owner_before = owner_of(read_central_manifest(live_manifest))
    assert owner_before == "statelessness-host", (
        "fixture workspace carries no owner — the guard below would be "
        "walking a manifest key it has never seen"
    )

    _seed_transcript(cli_env, _ID_WORKSPACE, workspace)
    _seed_transcript(cli_env, _ID_ROOTED, rooted)
    _seed_transcript(cli_env, _ID_AMB_ONE, amb_one, age_seconds=30.0)
    _seed_transcript(cli_env, _ID_AMB_TWO, amb_two, age_seconds=90.0)
    _seed_transcript(cli_env, _ID_LIVE, live_workspace)
    _seed_transcript(cli_env, _ID_GONE, roots / "torn-down")
    _seed_transcript(cli_env, _ID_INELIGIBLE, elsewhere)
    _seed_transcript(cli_env, _ID_UNREADABLE, None)
    _seed_transcript(cli_env, _ID_ARRIVED, arrived_workspace)
    _register_live(cli_env, _ID_LIVE, live_workspace)

    hermetic = {"HOME": str(home)}
    flows: list[tuple[str, list[str], Path | None]] = [
        # --- camp launch --dir: the success and every refusal ---
        ("--dir success", ["launch", "--dir", str(rooted), "--group", "mygroup"], None),
        ("--dir with a slug", ["launch", "--dir", str(rooted), "feat-stateless", "--group", "mygroup"], None),
        ("--dir with --resume", ["launch", "--dir", str(rooted), "--resume", _ID_WORKSPACE], None),
        ("--dir without --group", ["launch", "--dir", str(rooted)], tmp_path),
        ("--dir with no value", ["launch", "--dir=", "--group", "mygroup"], None),
        ("--dir that does not exist", ["launch", "--dir", str(roots / "gone"), "--group", "mygroup"], None),
        ("--dir naming a file", ["launch", "--dir", str(a_file), "--group", "mygroup"], None),
        ("--dir outside the allowlist", ["launch", "--dir", str(elsewhere), "--group", "mygroup"], None),
        ("--dir with no allowlist", ["launch", "--dir", str(rooted), "--group", "nolaunch"], None),
        ("--dir at a credential store", ["launch", "--dir", str(home / ".ssh"), "--group", "mygroup"], None),
        # --- camp launch --resume: the successes and every refusal ---
        ("--resume a workspace session", ["launch", "--resume", _ID_WORKSPACE], tmp_path),
        ("--resume an allowlisted root", ["launch", "--resume", _ID_ROOTED, "--group", "mygroup"], tmp_path),
        ("--resume an ambiguous ref", ["launch", "--resume", "camp-feat-amb-"], tmp_path),
        ("--resume a live session", ["launch", "--resume", _ID_LIVE], tmp_path),
        ("--resume a vanished root", ["launch", "--resume", _ID_GONE, "--group", "mygroup"], tmp_path),
        ("--resume an ineligible root", ["launch", "--resume", _ID_INELIGIBLE, "--group", "mygroup"], tmp_path),
        ("--resume without a group", ["launch", "--resume", _ID_INELIGIBLE], tmp_path),
        ("--resume an unreadable transcript", ["launch", "--resume", _ID_UNREADABLE], tmp_path),
        ("--resume matching nothing", ["launch", "--resume", "nothing-matches-this"], tmp_path),
        ("--resume with no value", ["launch", "--resume"], tmp_path),
        ("--resume a transferred workspace session", ["launch", "--resume", _ID_ARRIVED], tmp_path),
        # --- camp sessions: every scope and every degradation ---
        ("--recoverable everywhere", ["sessions", "--recoverable", "--group", "mygroup"], None),
        ("--recoverable in a workspace", ["sessions", "--recoverable", "feat-stateless", "--group", "mygroup"], None),
        ("--recoverable in a transferred workspace",
         ["sessions", "--recoverable", "feat-arrived", "--group", "mygroup"], None),
        ("--recoverable under a directory",
         ["sessions", "--recoverable", "--dir", str(roots), "--group", "mygroup"], None),
        ("--recoverable under a vanished directory",
         ["sessions", "--recoverable", "--dir", str(roots / "torn-down"), "--group", "mygroup"], None),
        ("--recoverable --json", ["sessions", "--recoverable", "--json", "--group", "mygroup"], None),
        ("--recoverable --all", ["sessions", "--recoverable", "--all", "--group", "mygroup"], None),
        ("--recoverable --limit", ["sessions", "--recoverable", "--limit", "1", "--group", "mygroup"], None),
        ("--recoverable with an unusable --limit",
         ["sessions", "--recoverable", "--limit", "0", "--group", "mygroup"], None),
        ("--recoverable with --limit and --all",
         ["sessions", "--recoverable", "--limit", "1", "--all", "--group", "mygroup"], None),
        ("--limit without --recoverable", ["sessions", "--limit", "1", "--group", "mygroup"], None),
        ("live listing under a directory", ["sessions", "--dir", str(roots), "--group", "mygroup"], None),
        # --- camp kill: every outcome, ending on a park/resume round trip ---
        # Ordered deliberately, and after the resume successes above: the stop
        # needs a pane camp itself launched, and the already-down branch needs
        # the name that stop just released.
        ("kill a session camp launched", ["kill", _ID_ROOTED], tmp_path),
        ("kill a session already down", ["kill", _ID_ROOTED], tmp_path),
        ("kill a live session owning no tmux session", ["kill", _ID_LIVE], tmp_path),
        ("kill an ambiguous ref", ["kill", "camp-feat-amb-"], tmp_path),
        ("resume the stopped session", ["launch", "--resume", _ID_ROOTED, "--group", "mygroup"], tmp_path),
        # The same park/resume round trip, on the transferred workspace's own
        # session — the sharpest case for a foreign-owned workspace exactly as
        # it is for an ordinary one: nothing may be set on the stop or cleared
        # on the resume just because the manifest names another host as owner.
        ("kill the transferred workspace's session", ["kill", _ID_ARRIVED], tmp_path),
        ("resume the transferred workspace after stop", ["launch", "--resume", _ID_ARRIVED], tmp_path),
    ]

    baseline = _settled_snapshot(state_dir)
    codes: dict[str, int] = {}
    errors: dict[str, str] = {}

    for label, argv, cwd in flows:
        result = _camp(cli_env, *argv, extra_env=hermetic, cwd=cwd)
        codes[label] = result.returncode
        errors[label] = result.stderr
        assert _snapshot(state_dir) == baseline, (
            f"{label} wrote under CAMP_STATE_DIR: {_diff(baseline, _snapshot(state_dir))}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    # Degradations, which need their own environment rather than their own argv.
    degradations: list[tuple[str, list[str], dict[str, str]]] = [
        ("--recoverable with an undeterminable live set",
         ["sessions", "--recoverable", "--group", "mygroup"], {"CAMP_FAKE_ENUMERATE": "none"}),
        ("--recoverable on a harness that keeps no transcripts",
         ["sessions", "--recoverable", "--group", "mygroup"], {"CAMP_FAKE_TRANSCRIPTS": "none"}),
        ("--resume on a harness that keeps no transcripts",
         ["launch", "--resume", _ID_WORKSPACE], {"CAMP_FAKE_TRANSCRIPTS": "none"}),
        ("--resume on a harness that cannot re-enter",
         ["launch", "--resume", _ID_ROOTED, "--group", "mygroup"], {"CAMP_FAKE_RESUME": "none"}),
        ("--resume that never confirms",
         ["launch", "--resume", _ID_AMB_ONE], {"CAMP_FAKE_TMUX_NO_REGISTER": "1"}),
    ]
    for label, argv, extra in degradations:
        result = _camp(cli_env, *argv, extra_env={**hermetic, **extra}, cwd=tmp_path)
        codes[label] = result.returncode
        errors[label] = result.stderr
        assert _snapshot(state_dir) == baseline, (
            f"{label} wrote under CAMP_STATE_DIR: {_diff(baseline, _snapshot(state_dir))}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    # Non-vacuity: the walk reached real code on all three outcomes rather than
    # bouncing off argument parsing everywhere and proving nothing.
    assert codes["--dir success"] == 0, codes
    assert codes["--resume a workspace session"] == 0, codes
    assert codes["--resume an allowlisted root"] == 0, codes
    assert codes["--resume an ambiguous ref"] == 2, codes
    assert set(codes.values()) == {0, 1, 2}, codes

    # The stop flows, same rule. Two of them share exit 0, so for those the
    # exit code alone cannot say which branch ran and the outcome line is what
    # separates a stop that reclaimed memory from one that found nothing to do.
    assert codes["kill a session camp launched"] == 0, errors["kill a session camp launched"]
    assert "stopped session" in errors["kill a session camp launched"]
    assert codes["kill a session already down"] == 0, errors["kill a session already down"]
    assert "already down" in errors["kill a session already down"]
    assert codes["kill a live session owning no tmux session"] == 1, codes
    assert codes["kill an ambiguous ref"] == 2, codes
    assert codes["resume the stopped session"] == 0, errors["resume the stopped session"]
    refusals = [label for label, code in codes.items() if code == 1]
    assert len(refusals) >= 15, refusals

    # The transferred-workspace round trip, same rule: each step actually ran
    # rather than bouncing off argument parsing.
    assert codes["--resume a transferred workspace session"] == 0, errors[
        "--resume a transferred workspace session"
    ]
    assert codes["kill the transferred workspace's session"] == 0, errors[
        "kill the transferred workspace's session"
    ]
    assert "stopped session" in errors["kill the transferred workspace's session"]
    assert codes["resume the transferred workspace after stop"] == 0, errors[
        "resume the transferred workspace after stop"
    ]
    assert codes["--recoverable in a transferred workspace"] == 0, errors[
        "--recoverable in a transferred workspace"
    ]

    # --- camp transfer-probe and camp transfer --dry-run: the transfer
    # preflight's two read-only surfaces. Walked under the same blanket
    # snapshot equality as every flow above, but scored in their own dicts:
    # their exit codes come from a vocabulary disjoint from the launch/
    # sessions/kill walk's {0, 1, 2} (see `camp.cli.transfer`'s EXIT CODES
    # table), so folding them into `codes` would corrupt the closed-set
    # assertion above rather than extend it. The mutating move path —
    # `camp transfer` without `--dry-run` — is deliberately excluded; see the
    # module docstring for why.
    transfer_flows: list[tuple[str, list[str]]] = [
        ("transfer-probe", ["transfer-probe", "--group", "mygroup", "--slug", "feat-stateless"]),
        (
            "transfer --dry-run against an unreachable peer",
            [
                "transfer", "feat-stateless", "--to", "unreachable-peer",
                "--group", "mygroup", "--dry-run",
            ],
        ),
    ]
    transfer_results: dict[str, subprocess.CompletedProcess] = {}
    for label, argv in transfer_flows:
        result = _camp(cli_env, *argv, extra_env=hermetic, cwd=tmp_path)
        transfer_results[label] = result
        assert _snapshot(state_dir) == baseline, (
            f"{label} wrote under CAMP_STATE_DIR: {_diff(baseline, _snapshot(state_dir))}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    # Non-vacuity: the probe actually answered about this host's own group
    # and slug, rather than refusing before it ever built an answer.
    probe_result = transfer_results["transfer-probe"]
    assert probe_result.returncode == 0, probe_result.stderr
    probe_payload = json.loads(probe_result.stdout)
    assert probe_payload["group_configured"] is True, probe_payload
    assert probe_payload["workspace_exists"] is True, probe_payload
    assert probe_payload["workspace_owner"] == "statelessness-host", probe_payload

    # Non-vacuity: the dry run actually reached the transport and classified
    # a real, unresolvable ssh target — not a local argument-parsing refusal
    # that never touched the peer-reachability check at all.
    dry_run_result = transfer_results["transfer --dry-run against an unreachable peer"]
    assert dry_run_result.returncode == EXIT_PEER_UNREACHABLE, dry_run_result.stderr
    assert "the peer answers" in dry_run_result.stdout
    assert "INDETERMINATE" in dry_run_result.stdout

    # The owner value itself, read directly — not just implied by the snapshot
    # equality above — so a flow that rewrote it to a same-length value would
    # still be caught.
    owner_after = owner_of(read_central_manifest(live_manifest))
    assert owner_after == owner_before
