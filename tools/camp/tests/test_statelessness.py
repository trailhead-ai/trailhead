"""camp's transfer preflight persists nothing: one snapshot over both its
read-only surfaces.

`camp transfer-probe` and `camp transfer --dry-run` are the transfer
preflight's two read-only surfaces — see `camp.cli.transfer`'s module
docstring. Both read camp's group config and central manifests and write
nothing camp owns, and the guarantee this module holds is not "each command
is clean in isolation" (that is `test_transfer_probe.py` /
`test_transfer_cli.py`'s job) but "the whole preflight surface, walked in one
process-sequence against one state directory, is clean together" — a full
recursive snapshot of `CAMP_STATE_DIR`, taken before the walk against the
same snapshot taken after each step. Byte content is part of the snapshot
deliberately: the state directory holds the group's worktrees and their
manifests, so a flow that rewrote a manifest in place — same paths,
different contents — would be invisible to a listing of names.

Two things keep the walk honest:

- The baseline is taken only once `camp new`'s BACKGROUND provisioner has stopped
  writing. Workspace creation is asynchronous; a snapshot taken while it is still
  moving would report the provisioner's writes as the flow-under-test's.
- The walk asserts what it actually provoked — the probe actually answered
  about a real group and slug, and the dry run actually reached the
  transport and classified a real, unresolvable ssh target — rather than
  either one refusing early for some unrelated reason and "proving"
  statelessness by never reaching the code it exists to guard.

The world — a fake harness and the hermetic fixture — is the one
`test_session_cli.py` builds, imported rather than rebuilt. The point of
this module is that it drives the SAME commands those tests drive; a
second, separately-maintained copy of the scaffolding could drift into
driving something else and the cross-cutting guarantee would quietly stop
covering the real flow.

`camp sessions` and `camp kill` are retired verbs (`LEGACY_REDIRECTS`:
`sessions` -> `list`, `kill` -> `stop`), so this module does not walk them
alongside the transfer preflight: a retired verb's entire body is
`cmd_legacy_redirect`, one `print` to stderr and `sys.exit(1)`, so their
statelessness is structural rather than a property a shared helper could
quietly start violating.

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
)

_camp = _cli._camp
_new_workspace = _cli._new_workspace
_workspace_launch_dir = _cli._workspace_launch_dir

#: Re-bound so pytest resolves the fixture from this module.
cli_env = _cli.cli_env


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


def test_transfer_preflight_writes_nothing_under_the_state_dir(cli_env) -> None:
    """`camp transfer-probe` and `camp transfer --dry-run` — the transfer
    preflight's two read-only surfaces (`camp.cli.transfer`'s module
    docstring) — against one snapshot, success and refusal alike.

    `camp sessions` and `camp kill` are retired verbs (LEGACY_REDIRECTS:
    sessions -> list, kill -> stop) not walked here: a retired verb's entire
    body is `cmd_legacy_redirect` — one print to stderr and `sys.exit(1)` —
    so their statelessness is structural, not a property a guard like this
    one needs to hold.
    """
    tmp_path: Path = cli_env["tmp_path"]
    state_dir: Path = Path(cli_env["state_dir"])
    home = tmp_path / "fakehome"
    (home / ".ssh").mkdir(parents=True)

    # A declared self-name so `camp new` below stamps an owner on the fixture
    # workspace — see the precondition assertion after `workspace`. The
    # declared peer is never actually reached: its ssh name resolves to
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

    _workspace_launch_dir(cli_env, "feat-stateless")

    # Precondition: the workspace this walk actually drives must already carry
    # an owner, read back through the real manifest reader — otherwise the
    # byte-level snapshot below is walking a manifest key it has never seen,
    # and would keep passing even if a future change stopped stamping it.
    manifest = manifest_path_for("mygroup", "feat-stateless", env=cli_env["env"])
    owner_before = owner_of(read_central_manifest(manifest))
    assert owner_before == "statelessness-host", (
        "fixture workspace carries no owner — the guard below would be "
        "walking a manifest key it has never seen"
    )

    hermetic = {"HOME": str(home)}
    baseline = _settled_snapshot(state_dir)

    # `camp transfer` WITHOUT `--dry-run`, once every preflight check has
    # passed, drives `camp.transfer.move.move_workspace` — it force-updates
    # git refs, extracts archives over a peer's worktree, and writes a
    # transfer marker, by design. A byte-identical `CAMP_STATE_DIR` after
    # that path would mean the transfer failed, not that it was clean, so
    # this file cannot hold it to the same assertion the rest of the walk
    # uses without asserting the opposite of what the path is for. Nor is
    # `camp transfer-receive` walked here: it is the peer-side half of that
    # same mutating path, reachable only from a remote `camp transfer`
    # invocation over ssh, never from a single local host's own command
    # line. What each of them writes, and where, is `camp.transfer.move`'s
    # and `camp.transfer.receive`'s own test coverage
    # (`test_transfer_cli.py::TestMoveWorkspaceEndToEnd`,
    # `test_transfer_receive.py`) to hold, not this file's.
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
    owner_after = owner_of(read_central_manifest(manifest))
    assert owner_after == owner_before
