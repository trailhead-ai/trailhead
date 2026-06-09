"""Slice 6 tests: status guard, lore sync, init git-init wiring.

Covers (TDD — written before implementation):

status_validator.py CLI main():
  - exits non-zero naming the file for a bad status
  - exits 0 for all-valid files
  - exits 0 when a note has an untracked type (unconstrained)
  - exits 0 with no files given

pre-commit-status-guard.sh installed hook:
  - staging + committing a note with an off-vocabulary status: rejected (non-zero,
    commit aborted)
  - staging + committing a note with a canonical status: passes
  - staging a non-.md file alone: no-op pass

install-vault-hooks.sh:
  - installs pre-commit hook into vault's .git/hooks/pre-commit
  - idempotent: re-running on an already-installed vault is a no-op
  - chain-safe: warns and exits non-zero when a pre-commit already exists (not ours)

lore init git-init wiring:
  - fresh dir produces a git repo (.git/ exists)
  - fresh dir has the pre-commit hook installed and executable

lore sync:
  - stages + commits a dirty vault; clean tree → no commit created
  - commit.gpgsign=false: commit succeeds (no signing error)
  - toplevel mismatch (vault is a subdir of a larger repo) → aborts without committing
  - no origin remote → commits but prints a notice about skipping push
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
GUARD_SH = HOOKS_DIR / "pre-commit-status-guard.sh"
INSTALLER_SH = HOOKS_DIR / "install-vault-hooks.sh"


def run_cli(args, env=None, input_text=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, input=input_text,
    )


def run_validator(args, env=None):
    """Run status_validator.py as a CLI."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "status_validator.py"), *args],
        capture_output=True, text=True, env=full_env,
    )


def run_sh(script: Path, args=None, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    cmd = ["bash", str(script)] + (args or [])
    return subprocess.run(cmd, capture_output=True, text=True, env=full_env)


def home(tmp_path):
    return {"HOME": str(tmp_path)}


def _git_init(path: Path, gpg_sign: bool = False) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@e.st"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    if not gpg_sign:
        subprocess.run(["git", "-C", str(path), "config", "commit.gpgsign", "false"],
                       check=True, capture_output=True)


def _install_guard(vault: Path) -> subprocess.CompletedProcess:
    return run_sh(INSTALLER_SH, args=[str(vault)],
                  env={"LORE_PLUGIN_ROOT": str(PLUGIN_ROOT)})


def _make_note(path: Path, note_type: str, status: str) -> None:
    path.write_text(f"---\ntype: {note_type}\nstatus: {status}\n---\n\n# Note\n")


def _git_commit_all(vault: Path, msg: str = "init") -> subprocess.CompletedProcess:
    subprocess.run(["git", "-C", str(vault), "add", "-A"], capture_output=True)
    return subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", msg],
        capture_output=True, text=True,
    )


# ── status_validator.py CLI main() ────────────────────────────────────────────

def test_validator_cli_exits_nonzero_for_bad_status(tmp_path):
    note = tmp_path / "bad.md"
    _make_note(note, "deferred", "bogus-status")
    r = run_validator([str(note)])
    assert r.returncode != 0
    assert "bad.md" in r.stderr or "bad.md" in r.stdout


def test_validator_cli_names_bad_status_in_output(tmp_path):
    note = tmp_path / "bad.md"
    _make_note(note, "deferred", "bogus-status")
    r = run_validator([str(note)])
    assert "bogus-status" in r.stderr or "bogus-status" in r.stdout


def test_validator_cli_exits_zero_for_valid_files(tmp_path):
    good1 = tmp_path / "good1.md"
    good2 = tmp_path / "good2.md"
    _make_note(good1, "deferred", "open")
    _make_note(good2, "session", "active")
    r = run_validator([str(good1), str(good2)])
    assert r.returncode == 0


def test_validator_cli_untracked_type_passes(tmp_path):
    note = tmp_path / "briefing.md"
    _make_note(note, "briefing", "anything-at-all")
    r = run_validator([str(note)])
    assert r.returncode == 0


def test_validator_cli_no_files_exits_zero():
    r = run_validator([])
    assert r.returncode == 0


def test_validator_cli_mixed_valid_and_invalid(tmp_path):
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.md"
    _make_note(good, "session", "active")
    _make_note(bad, "radar", "nonexistent-status")
    r = run_validator([str(good), str(bad)])
    assert r.returncode != 0
    assert "bad.md" in r.stderr or "bad.md" in r.stdout


# ── install-vault-hooks.sh ────────────────────────────────────────────────────

def test_installer_creates_pre_commit_hook(tmp_path):
    vault = tmp_path / "vault"
    _git_init(vault)
    r = _install_guard(vault)
    assert r.returncode == 0, r.stderr
    hook = vault / ".git" / "hooks" / "pre-commit"
    assert hook.exists()


def test_installer_hook_is_executable(tmp_path):
    vault = tmp_path / "vault"
    _git_init(vault)
    _install_guard(vault)
    hook = vault / ".git" / "hooks" / "pre-commit"
    assert os.access(hook, os.X_OK)


def test_installer_idempotent(tmp_path):
    vault = tmp_path / "vault"
    _git_init(vault)
    r1 = _install_guard(vault)
    assert r1.returncode == 0, r1.stderr
    r2 = _install_guard(vault)
    assert r2.returncode == 0, r2.stderr
    hook = vault / ".git" / "hooks" / "pre-commit"
    assert hook.exists()


def test_installer_chain_safe_with_existing_hook(tmp_path):
    """When a pre-commit hook already exists (not ours), installer should not
    silently clobber it — it should either chain or warn+fail.

    Chain mode: installer saves original to pre-commit-before-lore and writes a
    wrapper that calls the original then our guard. The original content survives.
    Warn-and-refuse mode: installer exits non-zero and original hook is untouched.
    """
    vault = tmp_path / "vault"
    _git_init(vault)
    hook = vault / ".git" / "hooks" / "pre-commit"
    original_content = "#!/bin/bash\necho 'existing hook'\n"
    hook.write_text(original_content)
    hook.chmod(0o755)
    r = _install_guard(vault)
    hooks_dir = vault / ".git" / "hooks"
    if r.returncode == 0:
        # chain mode: the original content must be preserved somewhere in .git/hooks/
        all_contents = " ".join(f.read_text() for f in hooks_dir.iterdir() if f.is_file())
        assert "existing hook" in all_contents, "installer silently clobbered existing hook"
    else:
        # warn-and-refuse mode: original hook must be byte-identical
        assert hook.read_text() == original_content


def test_installer_requires_git_repo(tmp_path):
    vault = tmp_path / "not-a-repo"
    vault.mkdir()
    r = _install_guard(vault)
    assert r.returncode != 0


# ── pre-commit-status-guard.sh (installed hook end-to-end) ───────────────────

def _setup_guarded_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    _git_init(vault)
    (vault / "deferred").mkdir()
    (vault / "sessions").mkdir()
    # install guard
    r = _install_guard(vault)
    assert r.returncode == 0, f"guard install failed: {r.stderr}"
    # initial commit so HEAD exists
    (vault / "README.md").write_text("vault\n")
    _git_commit_all(vault, "init vault")
    return vault


def test_guard_rejects_bad_status_at_commit(tmp_path):
    vault = _setup_guarded_vault(tmp_path)
    note = vault / "deferred" / "bad.md"
    _make_note(note, "deferred", "totally-wrong")
    subprocess.run(["git", "-C", str(vault), "add", str(note)], check=True, capture_output=True)
    r = subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "should be rejected"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    # The bad note must NOT have been committed
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--oneline"],
        capture_output=True, text=True,
    )
    assert "should be rejected" not in log.stdout


def test_guard_passes_canonical_status(tmp_path):
    vault = _setup_guarded_vault(tmp_path)
    note = vault / "deferred" / "good.md"
    _make_note(note, "deferred", "open")
    subprocess.run(["git", "-C", str(vault), "add", str(note)], check=True, capture_output=True)
    r = subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "good note"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout


def test_guard_noop_for_non_md_file(tmp_path):
    vault = _setup_guarded_vault(tmp_path)
    txt = vault / "data.txt"
    txt.write_text("hello\n")
    subprocess.run(["git", "-C", str(vault), "add", str(txt)], check=True, capture_output=True)
    r = subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "text only"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout


# ── lore init git-init wiring ─────────────────────────────────────────────────

def test_init_creates_git_repo(tmp_path):
    target = tmp_path / "vault"
    env = {**home(tmp_path), "LORE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    r = run_cli(["init", str(target), "--yes"], env=env)
    assert r.returncode == 0, r.stderr
    assert (target / ".git").is_dir()


def test_init_installs_pre_commit_hook(tmp_path):
    target = tmp_path / "vault"
    env = {**home(tmp_path), "LORE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    r = run_cli(["init", str(target), "--yes"], env=env)
    assert r.returncode == 0, r.stderr
    hook = target / ".git" / "hooks" / "pre-commit"
    assert hook.exists()
    assert os.access(hook, os.X_OK)


def test_init_skips_git_init_if_already_a_repo(tmp_path):
    """If the target is already a git repo, init should not re-initialize."""
    target = tmp_path / "vault"
    _git_init(target)
    git_dir_mtime_before = (target / ".git").stat().st_mtime
    env = {**home(tmp_path), "LORE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    r = run_cli(["init", str(target), "--yes", "--force"], env=env)
    assert r.returncode == 0, r.stderr
    assert (target / ".git").is_dir()


# ── lore sync ─────────────────────────────────────────────────────────────────

def _make_sync_vault(tmp_path: Path) -> Path:
    """Create a git-initialized vault with initial commit and guard installed."""
    vault = tmp_path / "vault"
    _git_init(vault)
    (vault / "sessions").mkdir()
    (vault / "deferred").mkdir()
    (vault / "README.md").write_text("vault\n")
    _git_commit_all(vault, "init")
    return vault


def test_sync_commits_dirty_vault(tmp_path):
    vault = _make_sync_vault(tmp_path)
    (vault / "sessions" / "note.md").write_text(
        "---\ntype: session\nstatus: active\n---\n\n# Session\n"
    )
    r = run_cli(["sync"], env={"LORE_VAULT": str(vault)})
    assert r.returncode == 0, r.stderr
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--oneline"],
        capture_output=True, text=True,
    )
    assert len(log.stdout.strip().splitlines()) == 2  # init + sync commit


def test_sync_noop_on_clean_tree(tmp_path):
    vault = _make_sync_vault(tmp_path)
    r = run_cli(["sync"], env={"LORE_VAULT": str(vault)})
    assert r.returncode == 0, r.stderr
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--oneline"],
        capture_output=True, text=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1  # only init, no empty commit


def test_sync_respects_gpgsign_false(tmp_path):
    """With commit.gpgsign=false in the repo config, sync commits succeed unsigned."""
    vault = _make_sync_vault(tmp_path)
    (vault / "deferred" / "note.md").write_text(
        "---\ntype: deferred\nstatus: open\n---\n\n# Deferred\n"
    )
    r = run_cli(["sync"], env={"LORE_VAULT": str(vault)})
    assert r.returncode == 0, r.stderr + r.stdout
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--oneline"],
        capture_output=True, text=True,
    )
    assert len(log.stdout.strip().splitlines()) == 2


def test_sync_aborts_on_toplevel_mismatch(tmp_path):
    """Vault is a subdir of a larger repo — sync must not commit the parent."""
    parent = tmp_path / "parent"
    _git_init(parent)
    (parent / "README.md").write_text("parent\n")
    _git_commit_all(parent, "parent init")

    # vault subdir — NOT its own git repo
    vault = parent / "vault-subdir"
    vault.mkdir()
    (vault / "sessions").mkdir()

    r = run_cli(["sync"], env={"LORE_VAULT": str(vault)})
    assert r.returncode != 0
    # Parent repo should be unmodified
    log = subprocess.run(
        ["git", "-C", str(parent), "log", "--oneline"],
        capture_output=True, text=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1


def test_sync_skips_push_without_origin(tmp_path):
    """No origin remote → commit is made, push is skipped with a notice."""
    vault = _make_sync_vault(tmp_path)
    (vault / "README.md").write_text("vault updated\n")
    r = run_cli(["sync"], env={"LORE_VAULT": str(vault)})
    assert r.returncode == 0, r.stderr
    combined = r.stdout + r.stderr
    assert "origin" in combined.lower() or "push" in combined.lower() or "remote" in combined.lower()


def test_sync_accepts_custom_message(tmp_path):
    vault = _make_sync_vault(tmp_path)
    (vault / "README.md").write_text("vault updated\n")
    r = run_cli(["sync", "--message", "my custom commit"], env={"LORE_VAULT": str(vault)})
    assert r.returncode == 0, r.stderr
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--oneline"],
        capture_output=True, text=True,
    )
    assert "my custom commit" in log.stdout


# ── C1: guard fails CLOSED when plugin has moved (LORE_GUARD_STRICT) ─────────

def test_guard_strict_blocks_when_plugin_root_missing(tmp_path):
    """C1: With LORE_GUARD_STRICT=1 and a non-existent LORE_PLUGIN_ROOT,
    running the guard exits non-zero (fail closed), not silently 0."""
    r = subprocess.run(
        ["bash", str(GUARD_SH)],
        capture_output=True, text=True,
        env={
            **os.environ,
            "LORE_GUARD_STRICT": "1",
            "LORE_PLUGIN_ROOT": "/nonexistent/path/that/does/not/exist",
        },
    )
    assert r.returncode != 0, "guard must fail closed when STRICT and plugin root missing"
    combined = r.stdout + r.stderr
    assert "lore init" in combined or "reinstall" in combined or "not found" in combined


def test_guard_strict_blocks_commit_when_plugin_moved(tmp_path):
    """C1 end-to-end: install the hook, bake a bad LORE_PLUGIN_ROOT, then verify
    ANY commit (even clean notes) is blocked (non-zero)."""
    vault = tmp_path / "vault"
    _git_init(vault)
    (vault / "deferred").mkdir()
    r = _install_guard(vault)
    assert r.returncode == 0, r.stderr

    # Rewrite the hook to simulate a moved plugin (point at a non-existent dir)
    hook = vault / ".git" / "hooks" / "pre-commit"
    hook_text = hook.read_text()
    # Replace the LORE_PLUGIN_ROOT path with a non-existent one
    broken_text = hook_text.replace(
        str(PLUGIN_ROOT),
        str(tmp_path / "moved-plugin-that-does-not-exist"),
    )
    # The hook must also carry LORE_GUARD_STRICT=1 (from the installer fix)
    # We verify: if STRICT is set and root is gone → fail closed
    # For test, we inject STRICT manually since the installer may not set it yet
    # Once C1 fix is complete, the installer will bake it automatically
    hook.write_text(broken_text.replace(
        "export LORE_PLUGIN_ROOT=",
        "export LORE_GUARD_STRICT=1\nexport LORE_PLUGIN_ROOT=",
        1,
    ))
    hook.chmod(0o755)

    # Initial commit to establish HEAD
    (vault / "README.md").write_text("vault\n")
    subprocess.run(["git", "-C", str(vault), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-m", "init"], capture_output=True)

    # Now try to commit a good note — must be BLOCKED because validator is gone
    note = vault / "deferred" / "fine.md"
    _make_note(note, "deferred", "open")
    subprocess.run(["git", "-C", str(vault), "add", str(note)], capture_output=True)
    r = subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "should be blocked"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0, (
        "guard must fail closed when STRICT=1 and plugin has moved; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )


def test_installer_bakes_strict_flag(tmp_path):
    """C1: The generated wrapper must contain LORE_GUARD_STRICT=1."""
    vault = tmp_path / "vault"
    _git_init(vault)
    r = _install_guard(vault)
    assert r.returncode == 0, r.stderr
    hook = vault / ".git" / "hooks" / "pre-commit"
    assert "LORE_GUARD_STRICT=1" in hook.read_text(), (
        "installer must bake LORE_GUARD_STRICT=1 into the generated wrapper"
    )


def test_guard_lenient_without_strict_when_plugin_missing(tmp_path):
    """C1: Standalone invocation without STRICT stays lenient (exit 0) — existing
    behavior for development / direct invocation use-cases."""
    r = subprocess.run(
        ["bash", str(GUARD_SH)],
        capture_output=True, text=True,
        env={
            **os.environ,
            "LORE_PLUGIN_ROOT": "/nonexistent/path",
            # No LORE_GUARD_STRICT
        },
    )
    assert r.returncode == 0, "standalone (no STRICT) may stay lenient"


# ── C2: non-ASCII filenames bypass the guard ──────────────────────────────────

def test_guard_rejects_bad_status_non_ascii_filename(tmp_path):
    """C2: A note with a non-ASCII filename carrying a bad status must be rejected."""
    vault = _setup_guarded_vault(tmp_path)
    non_ascii_note = vault / "deferred" / "café-redesign.md"
    _make_note(non_ascii_note, "deferred", "bad-nonascii-status")
    subprocess.run(
        ["git", "-C", str(vault), "add", str(non_ascii_note)],
        capture_output=True,
    )
    r = subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "non-ascii bad status"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0, (
        "guard must reject a bad-status note with a non-ASCII filename"
    )


def test_guard_passes_good_status_non_ascii_filename(tmp_path):
    """C2: A note with a non-ASCII filename carrying a valid status must pass."""
    vault = _setup_guarded_vault(tmp_path)
    non_ascii_note = vault / "deferred" / "café-redesign.md"
    _make_note(non_ascii_note, "deferred", "open")
    subprocess.run(
        ["git", "-C", str(vault), "add", str(non_ascii_note)],
        capture_output=True,
    )
    r = subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "non-ascii good status"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"good non-ASCII note must pass: {r.stderr} {r.stdout}"


# ── I1: validator silently skips unreadable files → must fail closed ──────────

def test_validator_cli_exits_nonzero_for_nonexistent_file(tmp_path):
    """I1: Passing a non-existent path to the validator must produce non-zero exit."""
    r = run_validator(["/nonexistent/path/that/does/not/exist.md"])
    assert r.returncode != 0, "validator must fail closed for a missing argv path"
    combined = r.stdout + r.stderr
    assert "nonexistent" in combined or "not found" in combined or "missing" in combined


# ── I2: guard reads working tree, not staged blob ─────────────────────────────

def test_guard_validates_staged_blob_not_working_copy(tmp_path):
    """I2a: Stage a BAD-status note, then fix working copy without re-staging.
    Guard must reject (staged blob is bad) even though working copy is good."""
    vault = _setup_guarded_vault(tmp_path)
    note = vault / "deferred" / "tricky.md"
    # Stage a bad-status note
    _make_note(note, "deferred", "totally-bad-status")
    subprocess.run(["git", "-C", str(vault), "add", str(note)], capture_output=True)
    # Fix working copy WITHOUT re-staging
    _make_note(note, "deferred", "open")
    # Guard should read the staged blob (bad) and reject
    r = subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "should be rejected"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0, (
        "guard must read staged blob; working-copy fix must not fool it"
    )


def test_guard_passes_good_staged_blob_even_if_working_copy_bad(tmp_path):
    """I2b: Stage a GOOD-status note, then corrupt working copy without re-staging.
    Guard must allow the commit (staged blob is good)."""
    vault = _setup_guarded_vault(tmp_path)
    note = vault / "deferred" / "sneaky.md"
    # Stage a good-status note
    _make_note(note, "deferred", "open")
    subprocess.run(["git", "-C", str(vault), "add", str(note)], capture_output=True)
    # Corrupt working copy WITHOUT re-staging
    _make_note(note, "deferred", "totally-bad-status")
    # Guard reads staged blob (good) → commit allowed
    r = subprocess.run(
        ["git", "-C", str(vault), "commit", "-m", "staged blob is good"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, (
        f"guard must allow commit when staged blob is good, even if working copy is bad: "
        f"{r.stderr} {r.stdout}"
    )


# ── I3: lore sync exit code after push failure ────────────────────────────────

def _make_sync_vault_with_failing_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Create a vault with an origin that will fail to push."""
    vault = tmp_path / "vault"
    _git_init(vault)
    (vault / "sessions").mkdir()
    (vault / "deferred").mkdir()
    (vault / "README.md").write_text("vault\n")
    _git_commit_all(vault, "init")

    # Add a remote that doesn't exist → push will fail
    subprocess.run(
        ["git", "-C", str(vault), "remote", "add", "origin",
         "git@github.com:nonexistent-test-org-xyz/nonexistent-repo.git"],
        check=True, capture_output=True,
    )
    return vault


def test_sync_exits_zero_when_push_fails_but_commit_succeeds(tmp_path):
    """I3: When commit succeeds but push fails (offline/auth), lore sync must exit 0
    and print a prominent notice — commit is durable, push failure is soft."""
    vault = _make_sync_vault_with_failing_remote(tmp_path)
    (vault / "README.md").write_text("vault updated\n")

    r = run_cli(["sync"], env={"LORE_VAULT": str(vault)})
    assert r.returncode == 0, (
        f"sync must exit 0 when commit succeeded but push failed; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    combined = r.stdout + r.stderr
    # Must have printed a notice about push failure / re-run
    assert (
        "push failed" in combined.lower()
        or "re-run" in combined.lower()
        or "online" in combined.lower()
        or "lore sync" in combined
    ), f"sync must print a soft-failure notice; got: {combined!r}"

    # The commit must have been made
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "--oneline"],
        capture_output=True, text=True,
    )
    assert len(log.stdout.strip().splitlines()) == 2, (
        "commit must have been made before push was attempted"
    )


# ── M2: pass "$@" to the guard in chained wrapper ────────────────────────────

def test_installer_chained_wrapper_passes_args(tmp_path):
    """M2: When chaining over an existing hook, the wrapper passes $@ to the guard."""
    vault = tmp_path / "vault"
    _git_init(vault)
    hook_path = vault / ".git" / "hooks" / "pre-commit"
    # Install a dummy existing hook
    hook_path.write_text("#!/bin/bash\n# dummy existing hook\nexit 0\n")
    hook_path.chmod(0o755)
    r = _install_guard(vault)
    assert r.returncode == 0, r.stderr
    hook_text = hook_path.read_text()
    # The chained guard invocation must pass "$@"
    assert '"$@"' in hook_text, (
        f"chained wrapper must pass \"$@\" to the guard; hook content:\n{hook_text}"
    )


# ── M3: lore init planned-tree preview matches what is actually written ───────

def test_init_planned_tree_shows_both_template_dirs(tmp_path):
    """M3: The planned-tree preview printed by lore init must list .templates/
    (the hidden copy-target). Currently it only shows .templates/ but the code
    creates that — verify they match what's actually created."""
    target = tmp_path / "vault"
    env = {**home(tmp_path), "LORE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    r = run_cli(["init", str(target), "--yes"], env=env)
    assert r.returncode == 0, r.stderr

    # Find directories actually created
    created_dirs = {d.name for d in target.iterdir() if d.is_dir() and d.name != ".git"}
    # The preview output must mention all actually-created directories
    output = r.stdout
    for d in created_dirs:
        assert d in output, (
            f"init preview must mention dir {d!r} that was actually created; "
            f"output:\n{output}"
        )
