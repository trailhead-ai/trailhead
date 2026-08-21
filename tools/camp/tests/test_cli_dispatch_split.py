"""Test contract: the per-command-group CLI split preserved dispatch wiring.

`cli/camp` was split from a ~1173-line monolith into `camp.cli.{dispatch,status,
group,lifecycle,workspace,inject}`, with `dispatch.main` routing verb strings to
handlers now living in sibling modules. camp's dispatch is hand-rolled (not
argparse), so alias resolution and the unknown-command / bare-slug error paths
are more fragile than a declarative parser split. These tests exercise the REAL
`cli/camp` binary end-to-end and assert:

1. Smoke: every verb group (top-level meta-flags + each verb, via the group-aware
   path where relevant) runs without a Python traceback — proving every handler
   module loads and its lazy cross-module imports resolve.
2. Alias resolution survived the split: `rm`→remove (now in lifecycle) and
   `ls`→list (now in workspace) route to their canonical handler, with explicit
   exit-code AND message assertions — not just help-text parity.
3. The unknown-verb / bare-slug and malformed-flag error paths survived, with
   explicit exit-code AND message assertions per surface.

All invocations run from an isolated cwd with CAMP_CONFIG_DIR / CAMP_STATE_DIR
pointed at empty tmp dirs so no real group resolves — fully read-only against the
developer's own camp data.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

_TRACEBACK_MARKER = "Traceback (most recent call last)"


@pytest.fixture()
def isolated_env(tmp_path: Path) -> dict[str, str]:
    """Env pointing camp's config/state at empty tmp dirs (no real group resolves)."""
    cfg = tmp_path / "config"
    state = tmp_path / "state"
    cfg.mkdir()
    state.mkdir()
    return {
        **os.environ,
        "CAMP_CONFIG_DIR": str(cfg),
        "CAMP_STATE_DIR": str(state),
    }


@pytest.fixture()
def stub_group_env(tmp_path: Path) -> dict[str, str]:
    """Env with a parseable stub group 'testgrp' so --group reaches the group-aware
    router (`_dispatch_group_command`) deterministically, independent of cwd."""
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "testgrp.toml").write_text(
        '[group]\nname = "testgrp"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    return {
        **os.environ,
        "CAMP_CONFIG_DIR": str(tmp_path),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
    }


def _run(args: list[str], *, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
    )


# ---------------------------------------------------------------------------
# 1. Smoke: every verb group runs without a Python traceback.
# ---------------------------------------------------------------------------

_SMOKE_INVOCATIONS = [
    ["--help"],
    ["-h"],
    ["help"],
    ["--version"],
    ["version"],
    ["--which"],
    ["status"],
    ["status", "--json"],
    ["list"],
    ["ls"],
    ["group"],
    ["group", "--help"],
    ["new"],
    ["remove"],
    ["rm"],
    ["setup"],
    ["setup", "--status"],
    ["sync"],
    ["rebase"],
    ["pwd"],
    ["activate"],
    ["inject", "--drain"],
    ["restock"],
    ["sweep"],
    ["code"],
    ["fire"],
    ["open"],
    ["break"],
    ["init"],
    ["ai"],
    ["enter"],
    ["kill"],
    ["bogusverb"],
]


@pytest.mark.parametrize("argv", _SMOKE_INVOCATIONS, ids=lambda a: " ".join(a) or "(none)")
def test_verb_smoke_no_traceback(argv, isolated_env, tmp_path) -> None:
    """Every verb group loads and runs — no uncaught Python traceback on any path."""
    result = _run(argv, env=isolated_env, cwd=tmp_path)
    assert _TRACEBACK_MARKER not in result.stderr, (
        f"'camp {' '.join(argv)}' surfaced a raw traceback (handler module or its "
        f"lazy imports broke in the split).\nstderr: {result.stderr}"
    )


def test_help_exits_zero_and_names_command_groups(isolated_env, tmp_path) -> None:
    """Top-level --help exits 0 and lists the major command groups."""
    result = _run(["--help"], env=isolated_env, cwd=tmp_path)
    assert result.returncode == 0, f"--help exited {result.returncode}\n{result.stderr}"
    combined = (result.stdout + result.stderr).lower()
    for verb in ("status", "new", "setup", "activate"):
        assert verb in combined, f"--help omits {verb!r}\n{result.stdout}"


def test_version_routes_through_status_module(isolated_env, tmp_path) -> None:
    """--version (handled by camp.cli.status) prints the version + the cli/camp binary path."""
    result = _run(["--version"], env=isolated_env, cwd=tmp_path)
    assert result.returncode == 0
    assert "camp 0.1.0" in result.stdout
    assert str(_CLI_CAMP) in result.stdout, (
        f"--version must name the cli/camp binary.\nstdout: {result.stdout}"
    )


def test_which_routes_through_status_module(isolated_env, tmp_path) -> None:
    """--which (handled by camp.cli.status) prints the cli/camp binary path, exit 0."""
    result = _run(["--which"], env=isolated_env, cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == str(_CLI_CAMP)


# ---------------------------------------------------------------------------
# 2. Alias resolution survived the split (exit code + message), group-aware path.
# ---------------------------------------------------------------------------


def test_alias_rm_routes_to_remove_handler(stub_group_env, tmp_path) -> None:
    """`rm` canonicalizes to `remove` and reaches `_cmd_remove_group_cli`
    (camp.cli.lifecycle): with no resolvable slug it emits the remove handler's
    own slug error and exits non-zero — NOT a bare-slug/unknown-verb error."""
    result = _run(["rm", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert result.returncode != 0, (
        f"`rm` (→remove) with no slug must exit non-zero.\nstderr: {result.stderr}"
    )
    assert "camp remove:" in result.stderr, (
        f"`rm` must route to the remove handler (message names 'camp remove').\n"
        f"stderr: {result.stderr}"
    )


def test_alias_rm_matches_canonical_remove(stub_group_env, tmp_path) -> None:
    """`rm` and `remove` produce byte-identical exit/stderr via the group-aware path."""
    alias = _run(["rm", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    canonical = _run(["remove", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert (alias.returncode, alias.stdout, alias.stderr) == (
        canonical.returncode,
        canonical.stdout,
        canonical.stderr,
    )


def test_alias_ls_routes_to_list_handler(stub_group_env, tmp_path) -> None:
    """`ls` canonicalizes to `list` and reaches `_cmd_ls_group_cli`
    (camp.cli.workspace): an empty group lists nothing and exits 0."""
    result = _run(["ls", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert result.returncode == 0, (
        f"`ls` (→list) on an empty group must exit 0.\nstderr: {result.stderr}"
    )


def test_alias_ls_matches_canonical_list(stub_group_env, tmp_path) -> None:
    """`ls` and `list` produce byte-identical exit/stdout via the group-aware path."""
    alias = _run(["ls", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    canonical = _run(["list", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert (alias.returncode, alias.stdout, alias.stderr) == (
        canonical.returncode,
        canonical.stdout,
        canonical.stderr,
    )


# ---------------------------------------------------------------------------
# 3. Unknown-verb / malformed-input error paths survived (exit code + message).
# ---------------------------------------------------------------------------


def test_group_aware_unknown_verb_is_bare_slug_error(stub_group_env, tmp_path) -> None:
    """An unknown token on the group-aware path (`_dispatch_group_command`) hits the
    bare-slug error and exits non-zero — no traceback, no silent no-op."""
    result = _run(["bogusverb", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert result.returncode != 0
    assert "bare slug dispatch is no longer supported" in result.stderr, (
        f"unknown group-aware verb must emit the bare-slug error.\nstderr: {result.stderr}"
    )
    assert "camp new bogusverb" in result.stderr


def test_no_group_unknown_verb_exits_nonzero_with_message(isolated_env, tmp_path) -> None:
    """An unknown top-level token with no group resolved falls through to spine and
    still exits non-zero with a message (not a traceback)."""
    result = _run(["bogusverb"], env=isolated_env, cwd=tmp_path)
    assert result.returncode != 0
    assert result.stderr.strip() != ""
    assert _TRACEBACK_MARKER not in result.stderr


def test_group_unknown_flag_errors(isolated_env, tmp_path) -> None:
    """An unknown flag to `group` is rejected by camp.cli.group's arg parser."""
    result = _run(["group", "testgrp", "--bogus-flag"], env=isolated_env, cwd=tmp_path)
    assert result.returncode != 0
    assert "unknown flag" in result.stderr
    assert "--bogus-flag" in result.stderr


def test_group_malformed_member_errors(isolated_env, tmp_path) -> None:
    """A malformed --member (no '=') is rejected by camp.cli.group's parser."""
    result = _run(
        ["group", "testgrp", "--member", "noequals"], env=isolated_env, cwd=tmp_path
    )
    assert result.returncode != 0
    assert "malformed --member" in result.stderr


def test_disabled_verb_group_path_exits_nonzero_with_message(stub_group_env, tmp_path) -> None:
    """A disabled verb on the group-aware path emits the disabled message + non-zero."""
    result = _run(["restock", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "temporarily" in combined or "stabiliz" in combined


def test_legacy_redirect_group_path_names_replacement(stub_group_env, tmp_path) -> None:
    """A legacy verb on the group-aware path redirects to its canonical name."""
    result = _run(["ai", "--group", "testgrp"], env=stub_group_env, cwd=tmp_path)
    assert result.returncode != 0
    assert "camp new" in result.stderr, (
        f"legacy 'ai' must name its replacement 'camp new'.\nstderr: {result.stderr}"
    )


def test_kill_is_routed_without_resolving_a_group(isolated_env, tmp_path) -> None:
    """`camp kill` is ref-addressed, so it must answer from a cwd where no group
    resolves — the situation it exists to be usable in. Reaching the needs-a-group
    refusal or the bare-slug error would mean it never got routed at all."""
    result = _run(["kill", "some-ref"], env=isolated_env, cwd=tmp_path)

    combined = result.stdout + result.stderr
    assert _TRACEBACK_MARKER not in combined
    assert "camp kill: " in combined
    assert "--group" not in combined
