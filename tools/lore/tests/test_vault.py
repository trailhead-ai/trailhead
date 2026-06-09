"""Slice 2 tests: config resolver (vault.py).

resolve_vault(): $LORE_VAULT (expanded+resolved) or ~/lore resolved. Never raises.
resolve_user(): $LORE_USER → git config user.name → "you", sanitized.
"""

import os
import subprocess
from pathlib import Path
from unittest import mock

from conftest import load_script


def test_resolve_vault_uses_env_when_set(tmp_path):
    target = tmp_path / "my-vault"
    target.mkdir()
    with mock.patch.dict(os.environ, {"LORE_VAULT": str(target)}, clear=False):
        v = load_script("vault")
        result = v.resolve_vault()
    assert result == str(target.resolve())


def test_resolve_vault_expands_tilde(tmp_path):
    with mock.patch.dict(os.environ, {"LORE_VAULT": "~/some-vault"}, clear=False):
        v = load_script("vault")
        result = v.resolve_vault()
    assert result == str((Path.home() / "some-vault").resolve())


def test_resolve_vault_defaults_to_home_lore_when_unset():
    env = {k: val for k, val in os.environ.items() if k != "LORE_VAULT"}
    with mock.patch.dict(os.environ, env, clear=True):
        v = load_script("vault")
        result = v.resolve_vault()
    assert result == str((Path.home() / "lore").resolve())


def test_resolve_vault_never_raises_on_empty_env():
    with mock.patch.dict(os.environ, {"LORE_VAULT": ""}, clear=False):
        v = load_script("vault")
        result = v.resolve_vault()
    assert result == str((Path.home() / "lore").resolve())


def test_resolve_user_honors_lore_user_env():
    with mock.patch.dict(os.environ, {"LORE_USER": "ada"}, clear=False):
        v = load_script("vault")
        assert v.resolve_user() == "ada"


def test_resolve_user_sanitizes_lore_user():
    with mock.patch.dict(os.environ, {"LORE_USER": "  ada\nlovelace\t "}, clear=False):
        v = load_script("vault")
        # newlines and control chars stripped; surrounding whitespace trimmed
        assert v.resolve_user() == "adalovelace"


def test_resolve_user_falls_back_to_git_config():
    env = {k: val for k, val in os.environ.items() if k != "LORE_USER"}
    with mock.patch.dict(os.environ, env, clear=True):
        v = load_script("vault")
        with mock.patch.object(
            subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, stdout="Grace Hopper\n", stderr=""),
        ):
            assert v.resolve_user() == "Grace Hopper"


def test_resolve_user_falls_back_to_literal_you():
    env = {k: val for k, val in os.environ.items() if k != "LORE_USER"}
    with mock.patch.dict(os.environ, env, clear=True):
        v = load_script("vault")
        with mock.patch.object(
            subprocess, "run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="not set"),
        ):
            assert v.resolve_user() == "you"


def test_resolve_user_git_failure_falls_back_to_you():
    env = {k: val for k, val in os.environ.items() if k != "LORE_USER"}
    with mock.patch.dict(os.environ, env, clear=True):
        v = load_script("vault")
        with mock.patch.object(subprocess, "run", side_effect=OSError("no git")):
            assert v.resolve_user() == "you"


# ---------------------------------------------------------------------------
# find_session_note: worktree-scoped filtering (I1)
# ---------------------------------------------------------------------------

def _make_session_note(sessions_dir: Path, stem: str, worktree: str) -> Path:
    """Write a minimal session note with worktree frontmatter."""
    p = sessions_dir / f"{stem}-{worktree}.md"
    p.write_text(
        f"---\ntype: session\nworktree: {worktree}\nstatus: active\n---\n\n# Session: {worktree}\n"
    )
    return p


def test_find_session_note_no_worktree_returns_newest_overall(tmp_path):
    """Without worktree_name, returns the newest note across all worktrees."""
    vault = tmp_path / "vault"
    sd = vault / "sessions"
    sd.mkdir(parents=True)
    _make_session_note(sd, "2026-06-01-1000", "alpha")
    beta = _make_session_note(sd, "2026-06-02-1000", "beta")

    v = load_script("vault")
    result = v.find_session_note(vault)
    assert result == beta


def test_find_session_note_scoped_to_alpha_ignores_newer_beta(tmp_path):
    """When worktree_name='alpha' and beta's note is newer, return alpha's note."""
    vault = tmp_path / "vault"
    sd = vault / "sessions"
    sd.mkdir(parents=True)
    alpha = _make_session_note(sd, "2026-06-01-1000", "alpha")
    _make_session_note(sd, "2026-06-02-1000", "beta")  # newer but different worktree

    v = load_script("vault")
    result = v.find_session_note(vault, worktree_name="alpha")
    assert result == alpha


def test_find_session_note_scoped_no_note_returns_none(tmp_path):
    """When worktree_name has no note, return None (no-session fallback)."""
    vault = tmp_path / "vault"
    sd = vault / "sessions"
    sd.mkdir(parents=True)
    _make_session_note(sd, "2026-06-02-1000", "beta")

    v = load_script("vault")
    result = v.find_session_note(vault, worktree_name="alpha")
    assert result is None


def test_find_session_note_no_sessions_dir_returns_none(tmp_path):
    """Missing sessions/ dir returns None regardless of worktree_name."""
    vault = tmp_path / "vault"
    vault.mkdir()

    v = load_script("vault")
    assert v.find_session_note(vault) is None
    assert v.find_session_note(vault, worktree_name="alpha") is None


def test_find_session_note_no_collision_with_super_prefix(tmp_path):
    """worktree_name='foo' must NOT match '…-super-foo.md' (M1 consistency)."""
    vault = tmp_path / "vault"
    sd = vault / "sessions"
    sd.mkdir(parents=True)
    _make_session_note(sd, "2026-06-02-1000", "super-foo")
    foo = _make_session_note(sd, "2026-06-01-1000", "foo")

    v = load_script("vault")
    result = v.find_session_note(vault, worktree_name="foo")
    assert result == foo
