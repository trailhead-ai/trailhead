"""``lore signing enable``/``status`` — operator control over unattended
host-key vault-commit signing, and ``lore status``'s per-host signing line.

Every subprocess-touching test pins a global gitconfig that would FAIL to
sign (``commit.gpgsign=true`` with a ``gpg.program`` that exits non-zero,
standing in for a personal key needing a passphrase) so a passing sync commit
proves ``enable``'s key won, not that nothing was configured to interfere
(lesson ``2026-05-28-test-fixtures-gpg-signing-no-pinentry``).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from conftest import CLI_PATH, load_script, make_git_vault, run_cli
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


def test_status_names_a_corrupted_key_that_is_not_a_passphrase_issue(tmp_path):
    """A configured key replaced with data ``ssh-keygen`` cannot load at all
    (not a passphrase-protected key, just garbage) must be diagnosed
    correctly rather than lumped in with the passphrase case — the two point
    an operator at different realities."""
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    key_path = _configured_key_path(state, home)
    key_path.write_text("not a key at all\n")
    key_path.chmod(0o600)

    r = _run_status(vault, state)
    assert r.returncode != 0
    assert "not a private key ssh-keygen can load" in r.stderr, r.stderr
    assert "passphrase" not in r.stderr, r.stderr
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


# ── enable — ssh-keygen failure modes escape as clean `lore:` errors ──────


def test_enable_prints_clean_error_when_ssh_keygen_missing(tmp_path):
    """A fresh `enable` (nothing configured yet) with no ssh-keygen on PATH
    must not let the FileNotFoundError escape as a traceback — it should be
    mapped to the same message `lore signing status` already gives."""
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    r = _run_enable(vault, state, extra_env={"PATH": "/nonexistent-bin-dir"})
    assert r.returncode != 0
    assert "lore: ssh-keygen is not on PATH" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_enable_prints_clean_error_when_ssh_keygen_exits_nonzero(tmp_path):
    """A ``ssh-keygen`` binary on PATH that exits non-zero during generation
    (``CalledProcessError``, `check=True`) must not escape as a traceback."""
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_ssh_keygen = fake_bin / "ssh-keygen"
    fake_ssh_keygen.write_text("#!/bin/sh\nexit 7\n")
    fake_ssh_keygen.chmod(0o755)

    r = _run_enable(vault, state, extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"})
    assert r.returncode != 0
    assert r.stderr.startswith("lore: "), r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_enable_prints_clean_error_when_ssh_keygen_times_out(tmp_path, monkeypatch):
    """A ``ssh-keygen`` that hangs past the module's own bound
    (``TimeoutExpired``) must not escape as a traceback either."""
    import lore.vault.signing as live_signing_mod

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_ssh_keygen = fake_bin / "ssh-keygen"
    fake_ssh_keygen.write_text("#!/bin/sh\nsleep 5\n")
    fake_ssh_keygen.chmod(0o755)

    monkeypatch.setattr(live_signing_mod, "_SSH_KEYGEN_TIMEOUT", 0.2)

    r = _run_enable(vault, state, extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"})
    assert r.returncode != 0
    assert r.stderr.startswith("lore: "), r.stderr
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


def test_lore_status_degrades_the_signing_line_instead_of_crashing_the_report(tmp_path, monkeypatch):
    """A ``describe_status`` call that raises (a ``ssh-keygen`` timeout, here)
    must not take down the rest of `lore status`'s report — the ruleset
    section above it and the vault-drift section below it must still print,
    matching the report's existing "an unreadable config downgrades a
    section to a stderr line" contract."""
    import lore.vault.signing as live_signing_mod

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_ssh_keygen = fake_bin / "ssh-keygen"
    fake_ssh_keygen.write_text("#!/bin/sh\nsleep 5\n")
    fake_ssh_keygen.chmod(0o755)
    monkeypatch.setattr(live_signing_mod, "_SSH_KEYGEN_TIMEOUT", 0.2)

    r = run_cli(
        ["status"], vault=vault, state_dir=state,
        env_extra={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stdout and "Traceback" not in r.stderr, (r.stdout, r.stderr)
    assert "vault" in r.stdout  # the vault-drift section still ran
    assert "signing" in r.stdout or "signing" in r.stderr


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


# ── enable, no argument — recovering from a drifted default-path key ──────
#
# Each of these starts from a state ``lore signing status`` names with the
# remedy "run `lore signing enable`" and proves what a bare re-run actually
# does: reuse a still-good key at the default path, or refuse by name rather
# than generating over/beside an unusable file or hanging on a hidden
# overwrite prompt.


def test_enable_refuses_when_the_generated_key_was_replaced_by_a_passphrase_key(tmp_path):
    """State (a): the configured (generated) key file was replaced in place
    by a passphrase-protected key. Nothing at the default path is usable, so
    a bare `enable` must refuse by name rather than silently generating over
    it — there is no key left to reuse or an argument-free way to replace a
    passphrase key with an empty one."""
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    key_path = _configured_key_path(state, home)
    original_bytes = key_path.read_bytes()
    passphrase_key = _generate_key(tmp_path, "replacement", passphrase="secret123")
    key_path.write_bytes(passphrase_key.read_bytes())
    key_path.chmod(0o600)

    r = _run_enable(vault, state)
    assert r.returncode != 0
    assert "needs a passphrase" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    # refused — the unusable file was not touched or replaced
    assert key_path.read_bytes() != original_bytes  # still the passphrase key we wrote
    assert key_path.read_bytes() == passphrase_key.read_bytes()


def test_enable_reuses_the_default_key_after_an_adopted_key_is_moved_away(tmp_path):
    """State (b): generate, adopt an external key with --key, then that
    external key is moved away. The originally generated key is still sitting
    untouched at the default path and still loads with no passphrase, so a
    bare `enable` reuses it rather than trying (and failing/hanging) to
    generate a new one at an occupied path."""
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    assert _run_enable(vault, state).returncode == 0
    generated_key = _configured_key_path(state, home)
    generated_fp = _fingerprint(generated_key)

    adopt_dir = tmp_path / "adopt"
    adopt_dir.mkdir()
    other_key = _generate_key(adopt_dir, "other")
    assert _run_enable(vault, state, key=other_key).returncode == 0
    assert _configured_key_path(state, home) == other_key

    other_key.rename(tmp_path / "other.moved")
    Path(f"{other_key}.pub").rename(tmp_path / "other.moved.pub")

    r = _run_status(vault, state)
    assert r.returncode != 0
    assert "lore signing enable" in r.stderr, r.stderr

    r = _run_enable(vault, state)
    assert r.returncode == 0, r.stderr
    assert _configured_key_path(state, home) == generated_key
    assert _fingerprint(generated_key) == generated_fp

    r = _run_status(vault, state)
    assert r.returncode == 0, r.stderr


def test_enable_reuses_the_default_key_after_its_configuration_file_is_deleted(tmp_path):
    """State (c): signing.json is deleted but the generated key at the
    default path remains untouched — `enable` rewrites the configuration to
    point at it again rather than generating a second key over it."""
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    assert _run_enable(vault, state).returncode == 0
    generated_key = _configured_key_path(state, home)
    generated_fp = _fingerprint(generated_key)

    signing = _signing_module()
    signing_dir = signing.signing_dir(env={"XDG_STATE_HOME": str(state), "HOME": str(home)})
    (signing_dir / signing.CONFIG_FILENAME).unlink()

    r = _run_status(vault, state)
    assert r.returncode != 0
    assert "lore signing enable" in r.stderr, r.stderr

    r = _run_enable(vault, state)
    assert r.returncode == 0, r.stderr
    assert _configured_key_path(state, home) == generated_key
    assert _fingerprint(generated_key) == generated_fp

    r = _run_status(vault, state)
    assert r.returncode == 0, r.stderr


def test_enable_with_no_arg_refuses_promptly_via_subprocess_when_default_key_is_occupied(tmp_path):
    """The same refusal as state (a), driven through a real subprocess with
    stdin closed — proving `enable` returns a clean refusal rather than
    blocking on ssh-keygen's hidden "Overwrite (y/n)?" prompt, which a
    closed/inherited stdin would otherwise expose as a hang in production
    even though pytest's own captured stdin masks it as an immediate EOF."""
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    key_path = _configured_key_path(state, home)
    passphrase_key = _generate_key(tmp_path, "replacement2", passphrase="secret123")
    key_path.write_bytes(passphrase_key.read_bytes())
    key_path.chmod(0o600)

    r = subprocess.run(
        [__import__("sys").executable, str(CLI_PATH), "signing", "enable"],
        capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL,
        env={**os.environ, "XDG_STATE_HOME": str(state), "HOME": str(home)},
    )
    assert r.returncode != 0
    assert "needs a passphrase" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


# ── enable — the printed gh registration command is shell-neutral ────────


def test_enable_prints_a_gh_command_with_no_process_substitution_for_a_generated_key(tmp_path):
    """The generated key always has an accompanying ``.pub`` file, so the
    printed registration command should name that path directly rather than
    process-substituting the printed public key line — process substitution
    (`<(...)`) is a bash/zsh construct that fails outright in fish."""
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    r = _run_enable(vault, state)
    assert r.returncode == 0, r.stderr
    assert "<(" not in r.stdout, r.stdout
    assert "gh ssh-key add" in r.stdout, r.stdout

    key_path = _configured_key_path(state, _isolated_home())
    assert f"{key_path}.pub" in r.stdout, r.stdout


##  Security fix-pass — S1, M1, M3, M4, M5, M6


def test_enable_with_key_refuses_a_key_inside_a_vault_sites_dir(tmp_path):
    """S1: an adopted key placed inside the configured vault's working tree
    (here, its ``sites/`` free-write zone) gets auto-staged and pushed by
    ``lore sync`` — refuse it and write nothing."""
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    sites_dir = vault / "sites"
    sites_dir.mkdir(parents=True, exist_ok=True)
    key_path = _generate_key(sites_dir, "host-key")

    r = _run_enable(vault, state, key=key_path)
    _assert_refused_and_nothing_written(
        r, state, "is inside vault", "move it outside any configured vault"
    )


def test_status_names_a_configured_key_that_lives_inside_a_vault(tmp_path):
    """S1: ``describe_status`` must flag a configuration that names a key
    inside a vault as failing, even though the operator could only reach
    that state by hand-editing the config (``enable --key`` itself refuses
    it) — a config written any other way must still be caught."""
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    sites_dir = vault / "sites"
    sites_dir.mkdir(parents=True, exist_ok=True)
    key_path = _generate_key(sites_dir, "in-vault-key")

    signing = _signing_module()
    d = signing.signing_dir(env={"XDG_STATE_HOME": str(state), "HOME": str(home)})
    d.mkdir(parents=True, exist_ok=True)
    (d / signing.CONFIG_FILENAME).write_text(
        json.dumps({signing.KEY_PATH_FIELD: str(key_path)})
    )

    r = _run_status(vault, state)
    assert r.returncode != 0
    assert "is inside vault" in r.stderr, r.stderr
    assert "move it outside any configured vault" in r.stderr, r.stderr


def test_enable_shows_generate_key_stderr_as_text_not_bytes(tmp_path):
    """M1: ``_generate_key``'s ``subprocess.run`` must capture text, not
    bytes — a bytes ``stderr`` prints as ``b'boom\\n'`` in the error line."""
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_ssh_keygen = fake_bin / "ssh-keygen"
    fake_ssh_keygen.write_text("#!/bin/sh\necho 'boom' >&2\nexit 7\n")
    fake_ssh_keygen.chmod(0o755)

    r = _run_enable(vault, state, extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"})
    assert r.returncode != 0
    assert "boom" in r.stderr, r.stderr
    assert "b'boom" not in r.stderr, r.stderr


def test_following_the_passphrase_status_remedy_gets_status_to_pass(tmp_path):
    """M3: the remedy printed for a passphrase-protected configured key must
    actually work — bare ``lore signing enable`` refuses in that state, so
    the remedy must name ``--key`` instead."""
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
    assert "--key" in r.stderr, r.stderr

    new_key = _generate_key(tmp_path, "fresh_no_passphrase")
    r2 = _run_enable(vault, state, key=new_key)
    assert r2.returncode == 0, r2.stderr

    r3 = _run_status(vault, state)
    assert r3.returncode == 0, r3.stderr


def test_status_reports_non_utf8_allowed_signers_as_mismatch_not_traceback(tmp_path):
    """M4: a corrupted (non-UTF-8) allowed-signers file must not raise
    ``UnicodeDecodeError`` up through ``lore signing status``."""
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0

    _allowed_signers_path(state, home).write_bytes(b"\xff\xfe\x00bad-bytes")

    r = _run_status(vault, state)
    assert r.returncode != 0
    assert "Traceback" not in r.stderr, r.stderr
    assert "allowed-signers" in r.stderr, r.stderr
    assert "lore signing enable" in r.stderr, r.stderr


def test_lore_status_reports_non_utf8_allowed_signers_without_a_traceback(tmp_path):
    """M4, ``lore status``'s per-host line: same corruption, same guard."""
    home = _isolated_home()
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    assert _run_enable(vault, state).returncode == 0
    _allowed_signers_path(state, home).write_bytes(b"\xff\xfe\x00bad-bytes")

    r = run_cli(["status"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stdout and "Traceback" not in r.stderr


def test_signing_status_guards_a_describe_status_crash(tmp_path, monkeypatch):
    """M4: ``lore signing status`` must guard ``describe_status`` the same
    way ``lore status`` already guards its own call — a timeout or
    permission error becomes a clean ``lore:`` line, not a traceback."""
    import lore.vault.signing as live_signing_mod

    def _boom(env=None):
        raise subprocess.TimeoutExpired(cmd=["ssh-keygen"], timeout=1)

    monkeypatch.setattr(live_signing_mod, "describe_status", _boom)

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    r = _run_status(vault, state)
    assert r.returncode != 0
    assert r.stderr.startswith("lore: "), r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_enable_reports_the_real_error_when_ssh_keygen_is_on_path_but_filenotfound_anyway(tmp_path, monkeypatch):
    """M5: only map ``FileNotFoundError`` to the "ssh-keygen is not on PATH"
    message when ssh-keygen is genuinely missing — otherwise report the real
    error, since ssh-keygen being present rules out that explanation."""
    import lore.vault.signing as live_signing_mod

    def _boom(*, key=None, env=None):
        raise FileNotFoundError(2, "No such file or directory", "/some/other/path")

    monkeypatch.setattr(live_signing_mod, "enable", _boom)

    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"
    r = _run_enable(vault, state)
    assert r.returncode != 0
    assert "ssh-keygen is not on PATH" not in r.stderr, r.stderr
    assert "/some/other/path" in r.stderr, r.stderr


def test_enable_refuses_rather_than_switches_when_the_existing_key_sits_in_an_unreadable_dir(tmp_path):
    """M6: ``Path.is_file()`` can return ``False`` on a ``PermissionError``
    (a directory this process cannot traverse), which must not be mistaken
    for "no key configured" — that would let a bare ``enable`` silently fall
    back to generating or switching to a different key."""
    home = _isolated_home()
    _write_hostile_global_gitconfig(home)
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    restricted_dir = tmp_path / "restricted"
    restricted_dir.mkdir()
    key_path = _generate_key(restricted_dir, "adopted")
    assert _run_enable(vault, state, key=key_path).returncode == 0

    restricted_dir.chmod(0o000)
    try:
        r = _run_enable(vault, state)
        assert r.returncode != 0
        assert "permission" in r.stderr.lower(), r.stderr
    finally:
        restricted_dir.chmod(0o700)


def test_status_names_permission_denied_for_a_configured_key_in_an_unreadable_dir(tmp_path):
    """M6, the ``status`` side of the same fix."""
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    restricted_dir = tmp_path / "restricted"
    restricted_dir.mkdir()
    key_path = _generate_key(restricted_dir, "adopted")
    assert _run_enable(vault, state, key=key_path).returncode == 0

    restricted_dir.chmod(0o000)
    try:
        r = _run_status(vault, state)
        assert r.returncode != 0
        assert "cannot be read" in r.stderr, r.stderr
    finally:
        restricted_dir.chmod(0o700)


def test_enable_prints_a_gh_command_with_no_process_substitution_for_an_adopted_key_with_no_pub_file(tmp_path):
    """An adopted key may have no ``.pub`` file sitting beside it — the
    printed command must still avoid process substitution, and must not
    claim a ``.pub`` path that does not exist."""
    vault = make_git_vault(tmp_path / "vault")
    state = tmp_path / "state"

    adopted_dir = tmp_path / "elsewhere"
    adopted_dir.mkdir()
    key_path = _generate_key(adopted_dir, "adopted")
    Path(f"{key_path}.pub").unlink()

    r = _run_enable(vault, state, key=key_path)
    assert r.returncode == 0, r.stderr
    assert "<(" not in r.stdout, r.stdout
    assert f"{key_path}.pub" not in r.stdout, r.stdout
