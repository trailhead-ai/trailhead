"""``lore signing enable``/``status`` — operator control over unattended
host-key vault-commit signing, and ``lore status``'s per-host signing line.

Every subprocess-touching test pins a global gitconfig that would FAIL to
sign (``commit.gpgsign=true`` with a ``gpg.program`` that exits non-zero,
standing in for a personal key needing a passphrase) so a passing sync commit
proves ``enable``'s key won, not that nothing was configured to interfere
(lesson ``2026-05-28-test-fixtures-gpg-signing-no-pinentry``).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from conftest import load_script, make_git_vault, run_cli
from test_vault_signing import (
    _allowed_signers_path,
    _generate_key,
    _isolated_home,
    _verify_good,
    _write_hostile_global_gitconfig,
)


# ── harness ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_agent_no_tty(monkeypatch):
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)


def _run_enable(vault, state, *, key=None, extra_env=None):
    args = ["signing", "enable"]
    if key is not None:
        args += ["--key", str(key)]
    return run_cli(args, vault=vault, state_dir=state, env_extra=extra_env)


def _run_status(vault, state, *, extra_env=None):
    return run_cli(["signing", "status"], vault=vault, state_dir=state, env_extra=extra_env)


def _signing_module():
    return load_script("lore.vault.signing")


def _configured_key_path(state: Path, home: Path):
    signing = _signing_module()
    return signing.load_key_path(env={"XDG_STATE_HOME": str(state), "HOME": str(home)})


def _fingerprint(key_path: Path) -> str:
    result = subprocess.run(["ssh-keygen", "-lf", str(key_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# ── enable, no argument ───────────────────────────────────────────────────


def test_enable_generates_a_key_and_a_sync_commit_verifies_against_it(tmp_path):
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    r = _run_enable(vault, state)
    assert r.returncode == 0, r.stderr

    signing = _signing_module()
    signing_env = {"XDG_STATE_HOME": str(state), "HOME": str(home)}
    signing_dir = signing.signing_dir(env=signing_env)
    key_path = signing.load_key_path(env=signing_env)

    assert key_path is not None and key_path.is_file()
    assert stat.S_IMODE(signing_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    loaded = subprocess.run(
        ["ssh-keygen", "-y", "-P", "", "-f", str(key_path)], capture_output=True, text=True
    )
    assert loaded.returncode == 0, loaded.stderr

    r = run_cli(["record", "create", "--kind", "task", "--title", "T"],
                vault=vault, state_dir=state, stdin_text="body\n")
    assert r.returncode == 0, r.stderr
    r = run_cli(["sync"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr

    allowed = _allowed_signers_path(state, home)
    assert _verify_good(vault, allowed) == "G"


def test_enable_run_twice_leaves_the_keys_fingerprint_unchanged(tmp_path):
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    r = _run_enable(vault, state)
    assert r.returncode == 0, r.stderr
    key_path = _configured_key_path(state, home)
    fp1 = _fingerprint(key_path)

    r = _run_enable(vault, state)
    assert r.returncode == 0, r.stderr
    key_path_again = _configured_key_path(state, home)
    fp2 = _fingerprint(key_path_again)

    assert fp1 == fp2


# ── status ────────────────────────────────────────────────────────────────


def test_status_exits_zero_after_enable(tmp_path):
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    r = _run_status(vault, state)
    assert r.returncode == 0, r.stderr


def test_status_names_missing_key_and_remedy(tmp_path):
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    r = _run_status(vault, state)
    assert r.returncode != 0
    assert "lore signing enable" in r.stderr, r.stderr


def test_status_names_a_passphrase_protected_replacement_key(tmp_path):
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    key_path = _configured_key_path(state, home)
    passphrase_key = _generate_key(tmp_path, "replacement", passphrase="secret123")
    key_path.write_bytes(passphrase_key.read_bytes())
    key_path.chmod(0o600)

    r = _run_status(vault, state)
    assert r.returncode != 0
    assert "passphrase" in r.stderr, r.stderr
    assert "lore signing enable" in r.stderr, r.stderr


def test_status_names_a_widened_key_mode(tmp_path):
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    key_path = _configured_key_path(state, home)
    key_path.chmod(0o644)

    r = _run_status(vault, state)
    assert r.returncode != 0
    assert f"chmod 600 {key_path}" in r.stderr, r.stderr


def test_status_names_a_deleted_allowed_signers_file(tmp_path):
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    _allowed_signers_path(state, home).unlink()

    r = _run_status(vault, state)
    assert r.returncode != 0
    assert "allowed-signers" in r.stderr, r.stderr
    assert "lore signing enable" in r.stderr, r.stderr


def test_status_names_an_allowed_signers_file_naming_a_different_key(tmp_path):
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    other_key = _generate_key(tmp_path, "unrelated")
    other_pub = Path(f"{other_key}.pub").read_text().strip()
    _allowed_signers_path(state, home).write_text(f"someone@example.com {other_pub}\n")

    r = _run_status(vault, state)
    assert r.returncode != 0
    assert "allowed-signers" in r.stderr, r.stderr
    assert "lore signing enable" in r.stderr, r.stderr


def test_status_names_missing_ssh_keygen_on_path(tmp_path):
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    r = _run_status(vault, state, extra_env={"PATH": "/nonexistent-bin-dir"})
    assert r.returncode != 0
    assert "lore: ssh-keygen is not on PATH" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_status_names_a_configured_key_since_deleted(tmp_path):
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    key_path = _configured_key_path(state, home)
    key_path.unlink()

    r = _run_status(vault, state)
    assert r.returncode != 0
    assert str(key_path) in r.stderr, r.stderr
    assert "lore signing enable" in r.stderr, r.stderr


# ── lore status — one line per host ─────────────────────────────────────


def test_lore_status_shows_signing_passing_after_enable(tmp_path):
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0
    key_path = _configured_key_path(state, home)
    fingerprint = _fingerprint(key_path)

    r = run_cli(["status"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert "signing" in r.stdout
    assert fingerprint in r.stdout
    assert "lore signing enable" not in r.stdout


def test_lore_status_shows_signing_failing_with_remedy_when_key_absent(tmp_path):
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    r = run_cli(["status"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert "signing" in r.stdout
    assert "lore signing enable" in r.stdout


# ── enable --key ──────────────────────────────────────────────────────────


def test_enable_with_key_adopts_an_existing_unencrypted_key_in_place(tmp_path):
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    adopted_dir = tmp_path / "elsewhere"
    adopted_dir.mkdir()
    key_path = _generate_key(adopted_dir, "adopted")
    original_bytes = key_path.read_bytes()
    original_mode = stat.S_IMODE(key_path.stat().st_mode)

    r = _run_enable(vault, state, key=key_path)
    assert r.returncode == 0, r.stderr

    configured = _configured_key_path(state, home)
    assert configured == key_path
    assert key_path.read_bytes() == original_bytes
    assert stat.S_IMODE(key_path.stat().st_mode) == original_mode

    r = run_cli(["record", "create", "--kind", "task", "--title", "T"],
                vault=vault, state_dir=state, stdin_text="body\n")
    assert r.returncode == 0, r.stderr
    r = run_cli(["sync"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr

    allowed = _allowed_signers_path(state, home)
    assert _verify_good(vault, allowed) == "G"


def _assert_refused_and_nothing_written(r, state: Path, reason_substr: str, remedy_substr: str):
    assert r.returncode != 0
    assert reason_substr in r.stderr, r.stderr
    assert remedy_substr in r.stderr, r.stderr
    signing = _signing_module()
    d = signing.signing_dir(env={"XDG_STATE_HOME": str(state), "HOME": os.environ["HOME"]})
    assert not (d / signing.CONFIG_FILENAME).exists()
    assert not (d / signing.ALLOWED_SIGNERS_FILENAME).exists()


def test_enable_with_key_refuses_a_nonexistent_path(tmp_path):
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    missing = tmp_path / "nope" / "no-such-key"

    r = _run_enable(vault, state, key=missing)
    _assert_refused_and_nothing_written(
        r, state, "does not exist", "run `lore signing enable` with no argument"
    )


def test_enable_with_key_refuses_a_public_key_file(tmp_path):
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    key_path = _generate_key(tmp_path, "somekey")
    pub_path = Path(f"{key_path}.pub")
    pub_path.chmod(0o600)

    r = _run_enable(vault, state, key=pub_path)
    _assert_refused_and_nothing_written(
        r, state, "not a private key", "point --key at a regular SSH private key file"
    )


def test_enable_with_key_refuses_a_passphrase_protected_key(tmp_path):
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    key_path = _generate_key(tmp_path, "passkey", passphrase="hunter2")

    r = _run_enable(vault, state, key=key_path)
    _assert_refused_and_nothing_written(
        r, state, "needs a passphrase", "run `lore signing enable` with no argument"
    )


def test_enable_with_key_refuses_a_key_at_mode_0644(tmp_path):
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    key_path = _generate_key(tmp_path, "widekey")
    key_path.chmod(0o644)

    r = _run_enable(vault, state, key=key_path)
    _assert_refused_and_nothing_written(
        r, state, "readable by others", f"chmod 600 {key_path}"
    )


def test_enable_with_key_switches_from_a_generated_key_and_no_arg_keeps_it(tmp_path):
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    assert _run_enable(vault, state).returncode == 0
    generated_key = _configured_key_path(state, home)
    generated_fp = _fingerprint(generated_key)

    named_dir = tmp_path / "named"
    named_dir.mkdir()
    named_key = _generate_key(named_dir, "named")
    named_fp = _fingerprint(named_key)
    assert named_fp != generated_fp

    r = _run_enable(vault, state, key=named_key)
    assert r.returncode == 0, r.stderr
    assert _configured_key_path(state, home) == named_key
    assert generated_key.exists()  # previously generated key left on disk

    r = run_cli(["record", "create", "--kind", "task", "--title", "T"],
                vault=vault, state_dir=state, stdin_text="body\n")
    assert r.returncode == 0, r.stderr
    r = run_cli(["sync"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr

    allowed = _allowed_signers_path(state, home)
    assert _verify_good(vault, allowed) == "G"
    # The allowed-signers file lore just wrote names the NAMED key's public
    # half, not the generated key's — the commit verifies against it only
    # because it actually signed with the named key.
    assert Path(f"{named_key}.pub").read_text().strip() in allowed.read_text(encoding="utf-8")

    r = _run_enable(vault, state)
    assert r.returncode == 0, r.stderr
    assert _configured_key_path(state, home) == named_key
