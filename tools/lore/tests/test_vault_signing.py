"""Host-key signing — every commit lore creates or replays in a vault signs
with this host's key when one is present, with no agent, tty, or passphrase.

Every subprocess-touching test here pins a global gitconfig that would FAIL
to sign (``commit.gpgsign=true`` with a ``gpg.program`` that exits non-zero,
standing in for a personal key needing a passphrase), unsets
``SSH_AUTH_SOCK``, and closes stdin — so a pass proves lore's own override
won, not that nothing was configured to get in the way (lesson
``2026-05-28-test-fixtures-gpg-signing-no-pinentry``).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from conftest import load_script, make_bare_remote, make_git_vault, run_cli


# ── harness ──────────────────────────────────────────────────────────────


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def _head(vault: Path) -> str:
    return _git(vault, "rev-parse", "HEAD").stdout.strip()


def _write_hostile_global_gitconfig(home: Path) -> None:
    """A global config that WOULD fail to sign — the fixture every test uses.

    ``commit.gpgsign=true`` plus a ``gpg.program`` that exits 1 is what stands
    in for a personal key that needs a passphrase gpg-agent has no cache for;
    a ``gpg.ssh.program`` that also exits 1 stands in for an adopter's SSH
    signing helper (e.g. 1Password's ``op-ssh-sign``) that would hijack SSH
    signing the same way if lore's own override left that key unset. A test
    that passes against this fixture proves lore's override won on BOTH
    axes, rather than proving nothing was configured to interfere at all.
    """
    home.mkdir(parents=True, exist_ok=True)
    bad_gpg_program = home / "bad-gpg-program"
    bad_gpg_program.write_text("#!/bin/sh\nexit 1\n")
    bad_gpg_program.chmod(0o755)
    bad_gpg_ssh_program = home / "bad-gpg-ssh-program"
    bad_gpg_ssh_program.write_text("#!/bin/sh\nexit 1\n")
    bad_gpg_ssh_program.chmod(0o755)
    (home / ".gitconfig").write_text(
        "[user]\n\tname = Nobody\n\temail = nobody@example.invalid\n"
        "[commit]\n\tgpgsign = true\n"
        "[gpg]\n\tformat = openpgp\n"
        f"\tprogram = {bad_gpg_program}\n"
        "[gpg \"ssh\"]\n"
        f"\tprogram = {bad_gpg_ssh_program}\n"
    )


def _generate_key(tmp_path: Path, name: str, *, passphrase: str = "") -> Path:
    key_path = tmp_path / name
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", passphrase, "-f", str(key_path), "-C", name],
        check=True, capture_output=True, timeout=15,
    )
    return key_path


def _enable_host_key(state_dir: Path, home: Path, key_path: Path, *, principal="host") -> None:
    """Write the signing config + allowed-signers file this host reads.

    Located via the real ``signing.signing_dir`` resolver rather than a
    hand-rolled path, so this fixture tracks the module's actual resolution
    instead of a guess at it.
    """
    signing = load_script("lore.vault.signing")
    d = signing.signing_dir(env={"XDG_STATE_HOME": str(state_dir), "HOME": str(home)})
    d.mkdir(parents=True, exist_ok=True)
    (d / signing.CONFIG_FILENAME).write_text(
        json.dumps({signing.KEY_PATH_FIELD: str(key_path)})
    )
    pubkey = Path(f"{key_path}.pub").read_text().strip()
    (d / signing.ALLOWED_SIGNERS_FILENAME).write_text(f"{principal} {pubkey}\n")


def _allowed_signers_path(state: Path, home: Path) -> Path:
    signing = load_script("lore.vault.signing")
    d = signing.signing_dir(env={"XDG_STATE_HOME": str(state), "HOME": str(home)})
    return d / signing.ALLOWED_SIGNERS_FILENAME


def _verify_good(vault: Path, allowed_signers: Path, rev: str = "HEAD") -> str:
    """Return git's ``%G?`` for *rev*, checked against *allowed_signers* directly
    — independent of whatever environment produced the commit.

    Also pins ``gpg.ssh.program`` back to plain ``ssh-keygen`` for this
    verification call: the hostile global gitconfig every test installs
    breaks that same key so a REAL signing call cannot silently fall through
    to it (see ``_write_hostile_global_gitconfig``), and this helper's own
    verification would otherwise inherit that same poison and misreport a
    genuinely good signature as unverifiable.
    """
    result = subprocess.run(
        ["git", "-C", str(vault),
         "-c", f"gpg.ssh.allowedSignersFile={allowed_signers}",
         "-c", "gpg.ssh.program=ssh-keygen",
         "log", "-1", "--pretty=%G?", rev],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


@pytest.fixture(autouse=True)
def _no_agent_no_tty(monkeypatch):
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)


def _isolated_home() -> Path:
    """The HOME the autouse ``_isolate_ambient_env`` fixture already pinned."""
    return Path(os.environ["HOME"])


# ── lore sync — the commit site, key present vs. absent ────────────────────


def test_sync_commit_signs_with_the_host_key_when_present(tmp_path):
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    key_path = _generate_key(tmp_path, "host_key")
    _enable_host_key(state, home, key_path)

    r = run_cli(["record", "create", "--kind", "task", "--title", "T"],
                vault=vault, state_dir=state, stdin_text="body\n")
    assert r.returncode == 0, r.stderr

    r = run_cli(["sync"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr

    allowed = _allowed_signers_path(state, home)
    assert _verify_good(vault, allowed) == "G"


def test_sync_commit_signs_with_a_key_named_outside_the_signing_directory(tmp_path):
    """The key is read from wherever the configuration points — an absolute
    path elsewhere on disk signs and verifies exactly like one inside lore's
    own signing directory."""
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    elsewhere = tmp_path / "elsewhere-keys"
    elsewhere.mkdir()
    key_path = _generate_key(elsewhere, "adopted_key")
    _enable_host_key(state, home, key_path)

    r = run_cli(["record", "create", "--kind", "task", "--title", "T"],
                vault=vault, state_dir=state, stdin_text="body\n")
    assert r.returncode == 0, r.stderr

    r = run_cli(["sync"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr

    allowed = _allowed_signers_path(state, home)
    assert _verify_good(vault, allowed) == "G"


def test_sync_commit_fails_exactly_as_today_with_no_host_key(tmp_path):
    """This is the branch that flips on the key's presence: with no host key,
    the adopter's own (hostile) config is honoured unchanged, and the commit
    fails with the existing ``git commit failed`` error."""
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    # The vault fixture's own local `commit.gpgsign=false` would otherwise beat
    # the hostile GLOBAL config above (local outranks global) and mask the
    # very failure this test exists to pin — an adopter's *own* signing setup
    # (here, set directly on the vault) with no host key present.
    bad_gpg_program = tmp_path / "bad-local-gpg-program"
    bad_gpg_program.write_text("#!/bin/sh\nexit 1\n")
    bad_gpg_program.chmod(0o755)
    for key, val in (("commit.gpgsign", "true"), ("gpg.format", "openpgp"),
                     ("gpg.program", str(bad_gpg_program))):
        _git(vault, "config", key, val)

    r = run_cli(["record", "create", "--kind", "task", "--title", "T"],
                vault=vault, state_dir=state, stdin_text="body\n")
    assert r.returncode == 0, r.stderr

    r = run_cli(["sync"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert "git commit failed" in r.stderr, r.stderr


# ── pull rebase replay ──────────────────────────────────────────────────


def test_pull_rebase_replay_signs_with_the_host_key(tmp_path):
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    key_path = _generate_key(tmp_path, "host_key")
    _enable_host_key(state, home, key_path)

    remote = make_bare_remote(tmp_path / "remote.git")
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(vault, "remote", "add", "origin", str(remote))
    _git(vault, "push", "-u", "origin", branch)

    other = tmp_path / "device-b"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    for k, v in (("user.email", "b@e.st"), ("user.name", "B"), ("commit.gpgsign", "false")):
        _git(other, "config", k, v)
    (other / "theirs.md").write_text("device b\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B commit")
    _git(other, "push", "origin", branch)

    r = run_cli(["record", "create", "--kind", "task", "--title", "T"],
                vault=vault, state_dir=state, stdin_text="body\n")
    assert r.returncode == 0, r.stderr

    r = run_cli(["sync"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert (vault / "theirs.md").exists()

    allowed = _allowed_signers_path(state, home)
    # HEAD is the replayed local commit landed on top of origin's history.
    assert _verify_good(vault, allowed) == "G"


# ── resolver settle ────────────────────────────────────────────────────


def _diverge_on_disjoint_fields(tmp_path: Path, vault: Path, state: Path) -> str:
    """Two devices edit different sidecar fields of the same record — a
    conflict git flags as text (adjacent lines) but the field-wise merge
    settles with no judgment, replaying device A's commit through
    ``_rebase_continue``.

    Leaves *vault* with device A's edit committed locally and device B's
    pushed to ``origin``, unpulled. Returns the branch name.
    """
    r = run_cli(
        ["record", "create", "--kind", "task", "--title", "T"],
        vault=vault, state_dir=state, stdin_text="body\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "seed")
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    remote = make_bare_remote(tmp_path / "remote.git")
    _git(vault, "remote", "add", "origin", str(remote))
    _git(vault, "push", "-u", "origin", branch)

    other = tmp_path / "device-b"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    for k, v in (("user.email", "b@e.st"), ("user.name", "B"), ("commit.gpgsign", "false")):
        _git(other, "config", k, v)
    state_b = tmp_path / "state-b"
    r = run_cli(
        ["record", "update", record_id, "--title", "Remote Title"],
        vault=other, state_dir=state_b, stdin_text="",
    )
    assert r.returncode == 0, r.stderr
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B edit")
    _git(other, "push", "origin", branch)

    r = run_cli(
        ["record", "update", record_id, "--status", "ready"],
        vault=vault, state_dir=state, stdin_text="",
    )
    assert r.returncode == 0, r.stderr
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "device A edit")
    return branch


def test_resolver_settle_signs_its_commit_with_the_host_key(tmp_path):
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    key_path = _generate_key(tmp_path, "host_key")
    _enable_host_key(state, home, key_path)

    branch = _diverge_on_disjoint_fields(tmp_path, vault, state)

    # premise: this really conflicts as text.
    _git(vault, "fetch", "origin")
    conflict_check = _git(vault, "rebase", f"origin/{branch}")
    assert conflict_check.returncode != 0, "fixture must really conflict"
    _git(vault, "rebase", "--abort")

    r = run_cli(["resolve", "default"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert not (vault / ".git" / "rebase-merge").exists()

    allowed = _allowed_signers_path(state, home)
    assert _verify_good(vault, allowed) == "G"


# ── flush ───────────────────────────────────────────────────────────────


def test_flush_commit_signs_with_the_host_key(tmp_path):
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    key_path = _generate_key(tmp_path, "host_key")
    _enable_host_key(state, home, key_path)

    sid = "11111111-2222-4333-8444-555555555555"
    r = run_cli(
        ["session", "candidate", "--session-id", sid, "--kind", "lesson", "--phase", "Build"],
        vault=vault, state_dir=state, stdin_text="note text\n",
        env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""},
    )
    assert r.returncode == 0, r.stderr

    r = run_cli(["flush", "--session-id", sid], vault=vault, state_dir=state,
                env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""})
    assert r.returncode == 0, r.stderr

    allowed = _allowed_signers_path(state, home)
    assert _verify_good(vault, allowed) == "G"


# ── env-override building (unit, on lore.vault.signing) ─────────────────


@pytest.fixture
def signing():
    return load_script("lore.vault.signing")


def _config_env(tmp_path: Path) -> dict:
    return {"XDG_STATE_HOME": str(tmp_path / "state"), "HOME": str(tmp_path / "home")}


def _write_config(signing, cfg_env: dict, content: str) -> Path:
    """Write *content* as the signing configuration; return the signing dir."""
    d = signing.signing_dir(env=cfg_env)
    d.mkdir(parents=True)
    (d / signing.CONFIG_FILENAME).write_text(content)
    return d


def _configure_fake_key(signing, cfg_env: dict, tmp_path: Path) -> "tuple[Path, Path]":
    """Configure a key file that exists on disk; return ``(signing dir, key path)``."""
    key_path = tmp_path / "key"
    key_path.write_text("fake-key\n")
    d = _write_config(signing, cfg_env, json.dumps({signing.KEY_PATH_FIELD: str(key_path)}))
    return d, key_path


def test_no_configuration_leaves_the_environment_exactly_inherited(tmp_path, signing):
    base = {"PATH": "/usr/bin", "SOME_VAR": "x"}
    result = signing.apply_env_overrides(base, env=_config_env(tmp_path))
    assert result == base


def test_a_configured_but_missing_key_file_leaves_the_environment_inherited(tmp_path, signing):
    cfg_env = _config_env(tmp_path)
    _write_config(signing, cfg_env, json.dumps({signing.KEY_PATH_FIELD: str(tmp_path / "nope")}))
    base = {"PATH": "/usr/bin"}
    result = signing.apply_env_overrides(base, env=cfg_env)
    assert result == base


def test_malformed_configuration_leaves_the_environment_inherited(tmp_path, signing):
    cfg_env = _config_env(tmp_path)
    _write_config(signing, cfg_env, "{not json")
    base = {"PATH": "/usr/bin"}
    result = signing.apply_env_overrides(base, env=cfg_env)
    assert result == base


def test_a_usable_key_yields_the_five_signing_overrides(tmp_path, signing):
    cfg_env = _config_env(tmp_path)
    d, key_path = _configure_fake_key(signing, cfg_env, tmp_path)

    result = signing.apply_env_overrides({}, env=cfg_env)

    assert result["GIT_CONFIG_COUNT"] == "5"
    entries = {
        result["GIT_CONFIG_KEY_0"]: result["GIT_CONFIG_VALUE_0"],
        result["GIT_CONFIG_KEY_1"]: result["GIT_CONFIG_VALUE_1"],
        result["GIT_CONFIG_KEY_2"]: result["GIT_CONFIG_VALUE_2"],
        result["GIT_CONFIG_KEY_3"]: result["GIT_CONFIG_VALUE_3"],
        result["GIT_CONFIG_KEY_4"]: result["GIT_CONFIG_VALUE_4"],
    }
    assert entries["gpg.format"] == "ssh"
    assert entries["user.signingkey"] == str(key_path)
    assert entries["commit.gpgsign"] == "true"
    assert entries["gpg.ssh.allowedSignersFile"] == str(d / signing.ALLOWED_SIGNERS_FILENAME)
    assert entries["gpg.ssh.program"] == "ssh-keygen"


def test_an_inherited_unrelated_git_config_entry_is_kept_and_lore_wins(tmp_path, signing):
    """An environment that already carries GIT_CONFIG_COUNT=1 with an unrelated
    key keeps that key in effect; an inherited entry that ALSO sets
    ``user.signingkey`` is overridden because lore's own entries are appended
    after it and git applies later entries last."""
    cfg_env = _config_env(tmp_path)
    _, key_path = _configure_fake_key(signing, cfg_env, tmp_path)

    base = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "user.signingkey",
        "GIT_CONFIG_VALUE_0": "/some/other/key",
    }
    result = signing.apply_env_overrides(base, env=cfg_env)

    assert result["GIT_CONFIG_COUNT"] == "6"
    assert result["GIT_CONFIG_KEY_0"] == "user.signingkey"
    assert result["GIT_CONFIG_VALUE_0"] == "/some/other/key"
    # lore's own user.signingkey lands at a higher index, so git applies it last.
    signingkey_indices = [
        i for i in range(1, 6)
        if result[f"GIT_CONFIG_KEY_{i}"] == "user.signingkey"
    ]
    assert len(signingkey_indices) == 1
    assert result[f"GIT_CONFIG_VALUE_{signingkey_indices[0]}"] == str(key_path)


def test_a_malformed_inherited_count_still_yields_a_usable_override(tmp_path, signing):
    cfg_env = _config_env(tmp_path)
    _configure_fake_key(signing, cfg_env, tmp_path)

    base = {"GIT_CONFIG_COUNT": "abc"}
    result = signing.apply_env_overrides(base, env=cfg_env)

    assert result["GIT_CONFIG_COUNT"] == "5"
    assert result["GIT_CONFIG_KEY_0"] == "gpg.format"


def test_sync_commit_signs_with_the_host_key_despite_an_inherited_git_config_entry(tmp_path):
    """The dict-level assertions above are the mechanism; this proves it holds
    for a REAL commit — an inherited ``GIT_CONFIG_COUNT=1`` naming an unrelated
    ``user.signingkey`` is present in the process's own environment (as it
    would be from a parent that already exported one) when ``lore sync`` runs,
    and the resulting commit still verifies against the HOST key, not the
    inherited one."""
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    key_path = _generate_key(tmp_path, "host_key")
    _enable_host_key(state, home, key_path)

    inherited_env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "user.signingkey",
        "GIT_CONFIG_VALUE_0": "/some/other/key",
    }

    r = run_cli(["record", "create", "--kind", "task", "--title", "T"],
                vault=vault, state_dir=state, stdin_text="body\n", env_extra=inherited_env)
    assert r.returncode == 0, r.stderr

    r = run_cli(["sync"], vault=vault, state_dir=state, env_extra=inherited_env)
    assert r.returncode == 0, r.stderr

    allowed = _allowed_signers_path(state, home)
    assert _verify_good(vault, allowed) == "G"


def test_sync_commit_signs_with_the_host_key_despite_a_malformed_inherited_count(tmp_path):
    """Same real-commit proof for the other malformed-count branch: an
    inherited ``GIT_CONFIG_COUNT=abc`` in the process's own environment does
    not stop ``lore sync`` from producing a commit that verifies against the
    host key."""
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    key_path = _generate_key(tmp_path, "host_key")
    _enable_host_key(state, home, key_path)

    inherited_env = {"GIT_CONFIG_COUNT": "abc"}

    r = run_cli(["record", "create", "--kind", "task", "--title", "T"],
                vault=vault, state_dir=state, stdin_text="body\n", env_extra=inherited_env)
    assert r.returncode == 0, r.stderr

    r = run_cli(["sync"], vault=vault, state_dir=state, env_extra=inherited_env)
    assert r.returncode == 0, r.stderr

    allowed = _allowed_signers_path(state, home)
    assert _verify_good(vault, allowed) == "G"


def test_an_inherited_count_with_a_gap_in_its_indices_is_treated_as_malformed(tmp_path, signing):
    """``GIT_CONFIG_COUNT=2`` naming no ``GIT_CONFIG_KEY_0`` is not a
    trustworthy index base — git itself aborts every config read against
    such a gap (`fatal: unable to parse command-line config`), so trusting it
    would break the very commit lore is trying to make, not just signing."""
    cfg_env = _config_env(tmp_path)
    _configure_fake_key(signing, cfg_env, tmp_path)

    base = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_1": "some.key",
        "GIT_CONFIG_VALUE_1": "x",
    }
    result = signing.apply_env_overrides(base, env=cfg_env)

    assert result["GIT_CONFIG_COUNT"] == "5"
    assert result["GIT_CONFIG_KEY_0"] == "gpg.format"


# ── _rebase_continue: bounded, never raises ─────────────────────────────


def _write_sleeper_true(bin_dir: Path, seconds: float) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "true"
    script.write_text(f"#!/bin/sh\nsleep {seconds}\nexit 0\n")
    script.chmod(0o755)


def test_rebase_continue_returns_within_its_bound_when_git_blocks(tmp_path, monkeypatch):
    """A direct unit test of ``resolve._rebase_continue`` against a real
    conflicted rebase: with ``GIT_EDITOR``'s resolved binary replaced by a
    sleeper past a shortened timeout, the call returns promptly with a
    non-zero rc instead of hanging or raising."""
    resolve_mod = load_script("lore.cli.resolve")

    vault = make_git_vault(tmp_path / "vault", commit=True)
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    base_sha = _head(vault)

    (vault / "note.md").write_text("base\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "base edit")

    _git(vault, "checkout", "-b", "other", base_sha)
    (vault / "note.md").write_text("other\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "other edit")
    _git(vault, "checkout", branch)

    rc = _git(vault, "rebase", "other")
    assert rc.returncode != 0, "fixture must really conflict"
    (vault / "note.md").write_text("resolved\n")
    _git(vault, "add", "-A")

    sleeper_dir = tmp_path / "fakebin"
    _write_sleeper_true(sleeper_dir, seconds=5)
    monkeypatch.setenv("PATH", f"{sleeper_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(resolve_mod, "_REBASE_CONTINUE_TIMEOUT_S", 0.5)

    start = time.monotonic()
    result_rc, still_mid = resolve_mod._rebase_continue(vault)
    elapsed = time.monotonic() - start

    assert result_rc != 0
    assert elapsed < 4.0, f"expected the shortened timeout to fire, took {elapsed:.1f}s"
    _git(vault, "rebase", "--abort")


def test_sweep_aborts_and_marks_failed_when_rebase_continue_times_out(tmp_path):
    """After the sweep path handles a `_rebase_continue` timeout, the vault is
    not mid-rebase, its HEAD is the pre-pull commit, and the failed-vault
    marker names the vault — proved by intercepting exactly the one
    `_rebase_continue` call the sweep's own driving loop makes, so the
    outcome is deterministic rather than raced against real sleep timing
    already covered by the unit test above."""
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    _diverge_on_disjoint_fields(tmp_path, vault, state)
    pre_pull_head = _head(vault)

    resolve_mod = load_script("lore.cli.resolve")
    real_rebase_continue = resolve_mod._rebase_continue
    call_count = {"n": 0}

    def _timing_out_once(vault_arg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return 1, resolve_mod._vault_mid_rebase(vault_arg)
        return real_rebase_continue(vault_arg)

    import unittest.mock as mock
    with mock.patch.object(resolve_mod, "_rebase_continue", side_effect=_timing_out_once):
        r = run_cli(["sync"], vault=vault, state_dir=state)

    assert r.returncode != 0
    assert not (vault / ".git" / "rebase-merge").exists(), "the vault must not be left mid-rebase"
    assert _head(vault) == pre_pull_head, "HEAD must be back at the pre-pull commit"

    resolve_state = load_script("lore.cli.resolve_state")
    os.environ["XDG_STATE_HOME"] = str(state)
    marker = resolve_state.read_failed_marker(vault)
    assert marker is not None
    assert marker["vault"] == vault.name
