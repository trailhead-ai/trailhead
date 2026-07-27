"""Whole-install vault coverage: multi-vault ``lore sync``, drift reporting.

The behavior under test exists because record writes route **by scope** while the
old ``lore sync`` resolved the ``default``-scope vault alone. A product-scope vault
could therefore accumulate every record a session created and never be committed,
while ``lore sync`` still printed "Committed / Pushed to origin". Every test here
uses a config with MORE THAN ONE vault, because a single-vault config cannot tell
"covered every vault" apart from "covered the default one".

Covers:

``lore sync``:
  - commits every configured vault, not just ``default``
  - labels each line with the vault it describes
  - ``--vault <name>`` narrows to one and leaves the others untouched
  - an unknown ``--vault`` name is refused (exit 1) and commits nothing
  - a malformed config is refused (exit 1) rather than degrading to one vault
  - one broken vault does not strand the others: they commit, exit code is 1
  - a clean but unpushed vault is still pushed
  - a clean, in-sync vault is not pushed (no needless round-trip)

``lore status``:
  - flags never-committed / uncommitted / remote-less vaults, per vault
  - reports a fully-synced vault as synced

``lore flush``:
  - names vaults still holding unsynced work after the session commit
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import write_vault_config

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


# ── harness ────────────────────────────────────────────────────────────────


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True
    )


def _make_vault(path: Path, *, commit: bool = True, dirty: bool = True) -> Path:
    """Create a git vault at ``path``; optionally give it a commit and dirt.

    ``commit=False`` reproduces the never-committed vault — the state the product
    vault was actually found in (git-init'd, zero commits, records untracked).
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    for key, val in (("user.email", "t@e.st"), ("user.name", "Test"), ("commit.gpgsign", "false")):
        _git(path, "config", key, val)
    (path / "README.md").write_text("vault\n")
    if commit:
        _git(path, "add", "-A")
        _git(path, "commit", "-m", "init")
    if dirty:
        (path / "record.md").write_text("# a record\n")
    return path


def _make_bare_remote(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    return path


def _wire_remote(vault: Path, remote: Path) -> None:
    """Attach ``remote`` as origin and push, so the vault has a real upstream."""
    _git(vault, "remote", "add", "origin", str(remote))
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(vault, "push", "-u", "origin", branch)


def _commit_count(vault: Path) -> int:
    r = _git(vault, "rev-list", "--count", "HEAD")
    return int(r.stdout.strip()) if r.returncode == 0 else 0


def run_cli(args, *, config_home: Path, state_dir: Path, cwd=None):
    """Run the lore CLI with XDG fenced to tmp so the real vaults are never touched."""
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(config_home)
    env["XDG_STATE_HOME"] = str(state_dir)
    env["HOME"] = str(state_dir / "home")
    env["LORE_EMAIL"] = "tester@example.com"
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
    )


def _three_vaults(tmp_path: Path):
    """Return ``(config_home, state_dir, {name: path})`` for a 3-vault install.

    Mirrors the real shape that exposed the bug: a ``default`` vault plus a
    product-scope and a repo-scope vault, all dirty.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    vaults = {
        "default": _make_vault(tmp_path / "v-default"),
        "trailhead": _make_vault(tmp_path / "v-trailhead"),
        "home-manager": _make_vault(tmp_path / "v-home-manager"),
    }
    write_vault_config(
        config_home,
        [
            ("default", "default", vaults["default"]),
            ("trailhead", "product", vaults["trailhead"]),
            ("home-manager", "repo", vaults["home-manager"]),
        ],
    )
    return config_home, state_dir, vaults


# ── lore sync: covers every vault ──────────────────────────────────────────


def test_sync_commits_every_configured_vault(tmp_path):
    """The core regression: a product/repo vault must not be left uncommitted."""
    config_home, state_dir, vaults = _three_vaults(tmp_path)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    for name, vault in vaults.items():
        assert _commit_count(vault) == 2, f"{name} did not receive the sync commit"
        assert _git(vault, "status", "--porcelain").stdout.strip() == "", (
            f"{name} still has uncommitted changes"
        )


def test_sync_commits_a_never_committed_vault(tmp_path):
    """A vault with ZERO commits (the state the product vault was found in)."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default")
    virgin = _make_vault(tmp_path / "v-virgin", commit=False)
    write_vault_config(
        config_home, [("default", "default", default), ("trailhead", "product", virgin)]
    )

    assert _commit_count(virgin) == 0

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert _commit_count(virgin) == 1


def test_sync_labels_output_with_the_vault_name(tmp_path):
    """Every outcome line names its vault, so a mismatch is visible at the call site."""
    config_home, state_dir, _ = _three_vaults(tmp_path)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    for name in ("default:", "trailhead:", "home-manager:"):
        assert name in r.stdout, f"output does not name {name!r}: {r.stdout!r}"


# ── lore sync --vault ──────────────────────────────────────────────────────


def test_sync_vault_filter_narrows_to_one(tmp_path):
    config_home, state_dir, vaults = _three_vaults(tmp_path)

    r = run_cli(["sync", "--vault", "trailhead"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    assert _commit_count(vaults["trailhead"]) == 2
    for name in ("default", "home-manager"):
        assert _commit_count(vaults[name]) == 1, f"{name} should have been left alone"


def test_sync_unknown_vault_is_refused_and_commits_nothing(tmp_path):
    """An unknown name must not silently fall back to syncing default."""
    config_home, state_dir, vaults = _three_vaults(tmp_path)

    r = run_cli(["sync", "--vault", "nope"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1
    assert "unknown vault" in r.stderr.lower()
    # The diagnostic lists what IS configured, so the operator can self-correct.
    assert "trailhead" in r.stderr
    for name, vault in vaults.items():
        assert _commit_count(vault) == 1, f"{name} must not have been committed"


def test_sync_vault_filter_accepts_the_unnormalized_name(tmp_path):
    """A repo-scope vault is configured as ``org/repo`` but stored ``org_repo``."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default")
    repo_vault = _make_vault(tmp_path / "v-repo")
    write_vault_config(
        config_home,
        [("default", "default", default), ("trailhead-ai/trailhead", "repo", repo_vault)],
    )

    r = run_cli(
        ["sync", "--vault", "trailhead-ai/trailhead"],
        config_home=config_home,
        state_dir=state_dir,
    )
    assert r.returncode == 0, r.stderr
    assert _commit_count(repo_vault) == 2
    assert _commit_count(default) == 1


# ── lore sync: partial failure ─────────────────────────────────────────────


def test_sync_broken_vault_does_not_strand_the_others(tmp_path):
    """One unusable vault is skipped; the rest still commit; exit code is 1."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default")
    good = _make_vault(tmp_path / "v-good")
    missing = tmp_path / "v-missing"  # never created
    write_vault_config(
        config_home,
        [
            ("default", "default", default),
            ("broken", "product", missing),
            ("good", "repo", good),
        ],
    )

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1, "a hard per-vault failure must surface in the exit code"
    assert "broken" in r.stderr

    # The healthy vaults committed anyway — including `good`, which is ordered
    # AFTER the broken one, so a failure cannot abort the remaining work.
    assert _commit_count(default) == 2
    assert _commit_count(good) == 2


def test_sync_refuses_a_malformed_config(tmp_path):
    """A broken config must abort, not degrade to syncing the floor vault alone."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (config_home / "lore").mkdir(parents=True)
    (config_home / "lore" / "config.json").write_text("{not json")

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1
    assert "partial vault set" in r.stderr.lower()


# ── lore sync: push behavior ───────────────────────────────────────────────


def test_sync_pushes_a_clean_but_unpushed_vault(tmp_path):
    """A clean tree with local-only commits must still reach the remote.

    The pre-fix command returned early on a clean tree, so commits made outside
    ``lore sync`` were never pushed and the vault silently stayed local-only.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)

    # A commit made outside lore sync, leaving a CLEAN tree that is ahead of origin.
    (default / "extra.md").write_text("later\n")
    _git(default, "add", "-A")
    _git(default, "commit", "-m", "out-of-band")
    assert _git(default, "status", "--porcelain").stdout.strip() == ""

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Pushed to origin." in r.stdout

    assert _commit_count(Path(remote)) == _commit_count(default)


def test_sync_skips_push_when_clean_and_in_sync(tmp_path):
    """Nothing to commit and nothing ahead → no push, no round-trip."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    _wire_remote(default, _make_bare_remote(tmp_path / "remote.git"))

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Nothing to commit" in r.stdout
    assert "Pushed to origin." not in r.stdout


# ── lore status: vault drift ───────────────────────────────────────────────


def test_status_flags_a_never_committed_vault(tmp_path):
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default")
    virgin = _make_vault(tmp_path / "v-virgin", commit=False)
    write_vault_config(
        config_home, [("default", "default", default), ("trailhead", "product", virgin)]
    )

    r = run_cli(["status"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "vault trailhead:" in r.stdout
    assert "never committed" in r.stdout
    # And the remedy names the specific vault, not a bare "run lore sync".
    assert "lore sync --vault trailhead" in r.stdout


def test_status_flags_uncommitted_and_remoteless_vaults(tmp_path):
    config_home, state_dir, _ = _three_vaults(tmp_path)

    r = run_cli(["status"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "uncommitted change(s)" in r.stdout
    assert "no origin remote" in r.stdout
    for name in ("default", "trailhead", "home-manager"):
        assert f"vault {name}:" in r.stdout


def test_status_reports_a_fully_synced_vault_as_synced(tmp_path):
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    _wire_remote(default, _make_bare_remote(tmp_path / "remote.git"))
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["status"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "vault default: synced" in r.stdout


def test_status_survives_a_malformed_config(tmp_path):
    """The ruleset section must still report; the vault section degrades to stderr."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (config_home / "lore").mkdir(parents=True)
    (config_home / "lore" / "config.json").write_text("{not json")

    r = run_cli(["status"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "unreadable" in r.stderr.lower()


# ── lore flush: names what its own commit does not cover ───────────────────


def test_flush_names_vaults_still_holding_unsynced_work(tmp_path):
    """`lore flush` commits the session record only — it must say what it left."""
    config_home, state_dir, _ = _three_vaults(tmp_path)

    r = run_cli(["flush"], config_home=config_home, state_dir=state_dir)
    # No session exists here; the notice is about VAULT state, so it fires anyway —
    # a clean/absent session says nothing about whether the vaults are committed.
    assert "run `lore sync`" in r.stdout
    assert "trailhead:" in r.stdout
