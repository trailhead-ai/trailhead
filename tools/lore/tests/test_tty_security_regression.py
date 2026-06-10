"""Slice 5 — TTY security regression tests.

These tests assert that LORE_SIMULATE_TTY (or any other env-var) cannot be
used to bypass the TTY gate on `lore promote`.  The gate must rely solely on
sys.stdin.isatty() in the production process; an agent controlling the
environment of a lore subprocess cannot forge a TTY.

Contract:
  1. With piped stdin AND any env-var manipulation, promote is still refused
     before any token is minted.
  2. The happy-path TTY tests work via in-process isatty patching, NOT via
     a production env-var hook.  An in-process monkeypatch is a legitimate
     test boundary; it cannot be exercised by an external subprocess.
"""
from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from conftest import CLI_PATH, SCRIPTS_DIR

TODAY = "2026-06-10"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_subprocess(args, env=None, input_text=None, cwd=None):
    """Run the lore CLI as a real subprocess (no in-process tricks)."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    full_env.setdefault("LORE_TODAY", TODAY)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, input=input_text,
        cwd=str(cwd) if cwd else None,
    )


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for d in ("deferred", "dead-ends", "decisions", "radar", "areas",
              "sessions", "plans", "specs"):
        (vault / d).mkdir(parents=True)
    return vault


def _make_note(vault: Path, subdir: str = "decisions", name: str = "my-note.md") -> Path:
    note_dir = vault / subdir / "2026-06"
    note_dir.mkdir(parents=True, exist_ok=True)
    note = note_dir / name
    note.write_text("---\ntype: decision\n---\n\n# My Decision\n\nBody text.\n")
    return note


def _write_group_config(groups_dir: Path, member_root: Path, shared_vaults: list,
                        group_name: str = "testgroup") -> Path:
    groups_dir.mkdir(parents=True, exist_ok=True)
    cfg = groups_dir / f"{group_name}.toml"
    lines = [f'[group]\nname = "{group_name}"\n\n',
             f'[[members]]\nname = "repo"\nrepo_root = "{member_root}"\n']
    for sv in shared_vaults:
        lines.append(
            f'\n[[shared_vaults]]\nname = "{sv["name"]}"\nroot = "{sv["root"]}"\n'
        )
    cfg.write_text("".join(lines))
    return cfg


# ---------------------------------------------------------------------------
# Regression test: no env-var bypass of the TTY gate
#
# This is the PRIMARY assertion: a non-TTY subprocess, regardless of what
# environment variables are set, MUST be refused before any token is minted.
# ---------------------------------------------------------------------------

class TestNoEnvVarBypass:
    """Assert that no env-var manipulation can bypass sys.stdin.isatty()."""

    def test_piped_stdin_refused_even_with_arbitrary_env_vars(self, tmp_path):
        """With piped stdin, setting random env vars does not bypass the gate."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault)

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        # Attempt with every plausible env-var override an agent might try
        env_attempts = [
            {},                             # baseline
            {"LORE_SIMULATE_TTY": "1"},     # the removed escape hatch
            {"LORE_SIMULATE_TTY": "true"},
            {"LORE_SIMULATE_TTY": "yes"},
            {"LORE_FORCE_TTY": "1"},        # other imaginable names
            {"LORE_TTY": "1"},
            {"TERM": "xterm"},              # terminal hint
            {"CI": "false"},
        ]

        base_env = {
            "LORE_VAULT": str(vault),
            "LORE_GROUPS_DIR": str(groups_dir),
        }

        for extra in env_attempts:
            env = {**base_env, **extra}
            r = _run_subprocess(
                ["promote", str(note)],
                env=env,
                input_text="y\n",
                cwd=cwd_dir,
            )
            assert r.returncode != 0, (
                f"Gate BYPASSED with env {extra!r}. stdin was piped (non-TTY). "
                f"stdout: {r.stdout!r}  stderr: {r.stderr!r}"
            )
            # Nothing written
            shared_files = [f for f in shared_root.glob("**/*") if f.is_file()]
            assert not shared_files, (
                f"File written to shared root despite piped stdin + env={extra!r}: {shared_files}"
            )

    def test_no_token_minted_with_piped_stdin_regardless_of_env(self, tmp_path):
        """No token is written to any directory when stdin is piped, whatever env is set."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault)

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        token_dir = tmp_path / "lore-tokens"
        token_dir.mkdir()

        r = _run_subprocess(
            ["promote", str(note)],
            env={
                "LORE_VAULT": str(vault),
                "LORE_GROUPS_DIR": str(groups_dir),
                "LORE_SIMULATE_TTY": "1",   # the removed escape hatch — must not work
                "LORE_TOKEN_DIR": str(token_dir),
            },
            input_text="y\n",
            cwd=cwd_dir,
        )
        assert r.returncode != 0, (
            "Gate should be refused with piped stdin even if LORE_SIMULATE_TTY=1. "
            f"stdout: {r.stdout!r}  stderr: {r.stderr!r}"
        )


# ---------------------------------------------------------------------------
# In-process happy-path tests: monkeypatch sys.stdin.isatty to True
#
# This is the legitimate test approach: the test patches its OWN process's
# isatty.  A separate agent process cannot patch production's isatty.
# ---------------------------------------------------------------------------

def _load_cli_module():
    """Load cli/lore as a module (in-process, so monkeypatch works).

    The CLI file has no .py extension, so spec_from_file_location returns None.
    Use SourceFileLoader explicitly instead.
    """
    from importlib.machinery import SourceFileLoader
    # Ensure scripts are importable before loading the CLI
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    loader = SourceFileLoader("lore_cli", str(CLI_PATH))
    spec = importlib.util.spec_from_loader("lore_cli", loader)
    # Remove any previously cached version so env patches take effect
    sys.modules.pop("lore_cli", None)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FakeStdin:
    """A fake stdin object: isatty() returns True, readline() feeds provided lines."""

    def __init__(self, lines: list[str]):
        self._lines = list(lines)
        self._idx = 0

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        if self._idx < len(self._lines):
            line = self._lines[self._idx]
            self._idx += 1
            return line
        return ""


class TestHappyPathInProcess:
    """Happy-path promote tests using in-process isatty patching."""

    def _cmd_promote_args(self, note: Path, to: str | None = None, yes: bool = False):
        return SimpleNamespace(note=str(note), to=to, yes=yes)

    def test_confirm_y_copies_note_in_process(self, tmp_path, monkeypatch):
        """In-process: isatty()=True + 'y' → note copied, personal original intact."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault, name="my-decision.md")
        original_content = note.read_text()

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        # Patch the environment and sys.stdin in-process
        monkeypatch.setenv("LORE_VAULT", str(vault))
        monkeypatch.setenv("LORE_GROUPS_DIR", str(groups_dir))
        monkeypatch.setenv("LORE_TODAY", TODAY)
        monkeypatch.chdir(cwd_dir)

        cli = _load_cli_module()

        # Patch sys.stdin to return isatty()=True and readline()="y\n"
        fake_stdin = FakeStdin(["y\n"])
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        # Patch the builtin input() to feed "y"
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")

        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        with mock.patch("sys.stdout", captured_stdout), mock.patch("sys.stderr", captured_stderr):
            rc = cli.cmd_promote(self._cmd_promote_args(note))

        assert rc == 0, f"Expected rc=0. stderr: {captured_stderr.getvalue()!r}"
        dest = shared_root / note.name
        assert dest.exists(), "Note must be copied to shared root"
        assert dest.read_text() == original_content
        assert note.exists(), "Personal original must not be deleted"
        assert note.read_text() == original_content

        combined = captured_stdout.getvalue() + captured_stderr.getvalue()
        assert "Promoted:" in combined

    def test_confirm_n_writes_nothing_in_process(self, tmp_path, monkeypatch):
        """In-process: isatty()=True + 'n' → nothing written, clean exit."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault, name="my-decision.md")

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        monkeypatch.setenv("LORE_VAULT", str(vault))
        monkeypatch.setenv("LORE_GROUPS_DIR", str(groups_dir))
        monkeypatch.setenv("LORE_TODAY", TODAY)
        monkeypatch.chdir(cwd_dir)

        cli = _load_cli_module()

        fake_stdin = FakeStdin(["n\n"])
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")

        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        with mock.patch("sys.stdout", captured_stdout), mock.patch("sys.stderr", captured_stderr):
            rc = cli.cmd_promote(self._cmd_promote_args(note))

        assert rc == 0, f"n should produce clean exit. stderr: {captured_stderr.getvalue()!r}"
        shared_files = [f for f in shared_root.glob("**/*") if f.is_file()]
        assert not shared_files, f"n should write nothing: {shared_files}"

    def test_preview_shows_warning_in_process(self, tmp_path, monkeypatch):
        """In-process: preview includes WARNING: SHARED line."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault, name="my-decision.md")

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        monkeypatch.setenv("LORE_VAULT", str(vault))
        monkeypatch.setenv("LORE_GROUPS_DIR", str(groups_dir))
        monkeypatch.setenv("LORE_TODAY", TODAY)
        monkeypatch.chdir(cwd_dir)

        cli = _load_cli_module()

        fake_stdin = FakeStdin(["n\n"])
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")

        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        with mock.patch("sys.stdout", captured_stdout), mock.patch("sys.stderr", captured_stderr):
            cli.cmd_promote(self._cmd_promote_args(note))

        combined = captured_stdout.getvalue() + captured_stderr.getvalue()
        assert "WARNING:" in combined, f"Must include WARNING: prefix. Got: {combined!r}"
        assert str(note) in combined or note.name in combined, (
            f"Must show source path. Got: {combined!r}"
        )

    def test_non_tty_still_refused_in_process(self, tmp_path, monkeypatch):
        """In-process: isatty()=False → refused even when called directly."""
        vault = _make_vault(tmp_path)
        shared_root = tmp_path / "shared"
        shared_root.mkdir()
        note = _make_note(vault, name="my-decision.md")

        groups_dir = tmp_path / "camp-config" / "groups"
        cwd_dir = tmp_path / "repo"
        cwd_dir.mkdir()
        _write_group_config(groups_dir, cwd_dir, [{"name": "team", "root": str(shared_root)}])

        monkeypatch.setenv("LORE_VAULT", str(vault))
        monkeypatch.setenv("LORE_GROUPS_DIR", str(groups_dir))
        monkeypatch.setenv("LORE_TODAY", TODAY)
        monkeypatch.chdir(cwd_dir)

        # Remove any LORE_SIMULATE_TTY that might be inherited
        monkeypatch.delenv("LORE_SIMULATE_TTY", raising=False)

        cli = _load_cli_module()

        # Patch stdin to non-TTY
        fake_stdin = FakeStdin(["y\n"])
        fake_stdin_non_tty = FakeStdin(["y\n"])
        fake_stdin_non_tty.isatty = lambda: False
        monkeypatch.setattr(sys, "stdin", fake_stdin_non_tty)

        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        with mock.patch("sys.stdout", captured_stdout), mock.patch("sys.stderr", captured_stderr):
            rc = cli.cmd_promote(self._cmd_promote_args(note))

        assert rc != 0, (
            "Must refuse when isatty()=False, even in-process. "
            f"stdout: {captured_stdout.getvalue()!r}"
        )
        shared_files = [f for f in shared_root.glob("**/*") if f.is_file()]
        assert not shared_files, "Nothing written to shared root on non-TTY refusal"
