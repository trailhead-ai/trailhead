"""KU1 assumption-prover (EPHEMERAL — delete after Slice 0 is recorded).

Proves that a PreToolUse hook script can:
  (a) deny a Write to a path under a guarded vault root (exit 2 path)
  (b) allow a Write to a path outside the vault (exit 0, no JSON decision)
  (c) deny a Write whose tool_input.file_path resolves (via os.path.realpath)
      to the REAL target of a symlinked vault — proving symlink canonicalization
      at execution time, not install time.

The guard script is written inline here and exercised via subprocess so we're
testing the exact mechanism Claude Code will invoke:
  - stdin = PreToolUse JSON payload (tool_name + tool_input.file_path)
  - stdout = JSON with hookSpecificOutput OR empty
  - exit code: 2 = deny (immediate block, JSON on stdout is ignored by Claude Code)
               0 = allow / defer

The "deny via exit code 2" path is used because it is the simpler, unconditional
block contract: exit 2 always blocks, with stderr shown to Claude as the reason.
The "deny via JSON hookSpecificOutput.permissionDecision=deny + exit 0" path is
also proven in test_deny_via_json_stdout for completeness (KU1b).

No production code is modified. The guard script written to a tmp dir is the
prototype for hooks/<guard-hook>.py in Slice 3.

Clean-up: delete this file (tools/lore/tests/test_ku1_pretooluse_guard_assumption.py)
after Slice 0 is recorded in the plan.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# The vault-guard hook script prototype (written to tmp dir, not plugin tree)
# ---------------------------------------------------------------------------

GUARD_SCRIPT = textwrap.dedent("""\
    #!/usr/bin/env python3
    \"\"\"Vault write-protection PreToolUse guard (prototype for KU1 assumption prover).

    Reads a PreToolUse JSON payload from stdin. Resolves the write target
    (tool_input.file_path) and the vault root(s) to their real paths via
    os.path.realpath (symlink-transparent). Denies with exit 2 if the real
    target is under any guarded real vault root.

    Deny mechanism: exit code 2 (stderr carries human-readable reason).
    Claude Code docs: "exit 2 blocks the tool call; stderr is shown to Claude."
    JSON on stdout is ignored when exit code is 2.

    Environment:
      LORE_VAULT_GUARD_ROOT  — colon-separated list of vault roots to guard.
    \"\"\"
    import json
    import os
    import sys
    from pathlib import Path

    def _real(p: str) -> str:
        return os.path.realpath(p)

    payload = json.load(sys.stdin)
    file_path = payload.get("tool_input", {}).get("file_path", "")
    if not file_path:
        sys.exit(0)  # No file_path — not our concern, defer.

    real_target = _real(file_path)

    guard_roots_env = os.environ.get("LORE_VAULT_GUARD_ROOT", "")
    guard_roots = [r for r in guard_roots_env.split(":") if r]

    for raw_root in guard_roots:
        real_root = _real(raw_root)
        # Guard: target is under real_root if real_target == real_root or
        # real_target starts with real_root + os.sep.
        if real_target == real_root or real_target.startswith(real_root + os.sep):
            print(
                f"LORE VAULT GUARD: write to {file_path!r} "
                f"(resolved: {real_target!r}) is under guarded vault root "
                f"{raw_root!r} (resolved: {real_root!r}). Denied.",
                file=sys.stderr,
            )
            sys.exit(2)  # Deny: exit code 2 blocks the tool call.

    sys.exit(0)  # Allow: not under any guarded root.
""")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def guard_script(tmp_path: Path) -> Path:
    """Write the guard script to a tmp dir and make it executable."""
    script = tmp_path / "vault_guard.py"
    script.write_text(GUARD_SCRIPT)
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return script


def _make_payload(file_path: str) -> str:
    """Return a minimal PreToolUse JSON payload string for a Write to file_path."""
    return json.dumps({
        "session_id": "test-session",
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {
            "file_path": file_path,
            "file_text": "# test content",
        },
    })


def _run_guard(script: Path, file_path: str, guard_roots: list[str]) -> subprocess.CompletedProcess:
    """Invoke guard_script with the given file_path and guard_roots env."""
    env = dict(os.environ)
    env["LORE_VAULT_GUARD_ROOT"] = ":".join(guard_roots)
    return subprocess.run(
        [sys.executable, str(script)],
        input=_make_payload(file_path),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# KU1 (a)+(b): Write/Edit matcher and deny mechanism — exit 2
# ---------------------------------------------------------------------------


class TestVaultGuardDenyAllow:
    """KU1 (a)+(b): guard denies writes under vault root; allows writes outside."""

    def test_write_inside_vault_is_denied(
        self, guard_script: Path, tmp_path: Path
    ) -> None:
        """A Write to a path under the guarded vault root is denied (exit 2)."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        target = str(vault / "records" / "some-note.md")

        result = _run_guard(guard_script, target, [str(vault)])

        assert result.returncode == 2, (
            f"Expected exit 2 (deny) for write inside vault, got {result.returncode}.\n"
            f"stderr: {result.stderr!r}\nstdout: {result.stdout!r}"
        )
        assert "LORE VAULT GUARD" in result.stderr, (
            f"Expected guard message on stderr, got: {result.stderr!r}"
        )

    def test_write_outside_vault_is_allowed(
        self, guard_script: Path, tmp_path: Path
    ) -> None:
        """A Write to a path outside the guarded vault root is allowed (exit 0)."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        outside = tmp_path / "project" / "src" / "main.py"
        outside.parent.mkdir(parents=True, exist_ok=True)

        result = _run_guard(guard_script, str(outside), [str(vault)])

        assert result.returncode == 0, (
            f"Expected exit 0 (allow) for write outside vault, got {result.returncode}.\n"
            f"stderr: {result.stderr!r}"
        )

    def test_write_to_vault_root_itself_is_denied(
        self, guard_script: Path, tmp_path: Path
    ) -> None:
        """A Write targeting the vault root directory itself is denied."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)

        result = _run_guard(guard_script, str(vault), [str(vault)])

        assert result.returncode == 2, (
            f"Expected exit 2 for write to vault root itself, got {result.returncode}."
        )

    def test_no_file_path_in_payload_is_allowed(
        self, guard_script: Path, tmp_path: Path
    ) -> None:
        """A payload with no file_path exits 0 (not our concern — defer)."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        # Send a Bash payload (no file_path) to ensure the guard doesn't crash
        payload = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /tmp/x"},
        })
        env = dict(os.environ)
        env["LORE_VAULT_GUARD_ROOT"] = str(vault)
        result = subprocess.run(
            [sys.executable, str(guard_script)],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            f"Payload with no file_path must exit 0 (defer), got {result.returncode}."
        )


# ---------------------------------------------------------------------------
# KU1 (c): symlink real-target resolution — execution-time canonicalization
# ---------------------------------------------------------------------------


class TestSymlinkRealTargetGuard:
    """KU1 (c): guard catches writes to the REAL target of a symlinked vault."""

    def test_write_to_real_target_of_symlinked_vault_is_denied(
        self, guard_script: Path, tmp_path: Path
    ) -> None:
        """Write to the REAL target of a symlinked vault is denied.

        The guard root is configured as the SYMLINK path (the canonical vault location).
        The write target is the REAL path that the symlink points to.
        os.path.realpath on both sides resolves them to the same tree — so the
        write is correctly denied even though the path doesn't start with the
        symlink path string.

        This is the exact scenario Slice 3 must handle: user has
        lore init --vault /real/dir/my-vault, so vaults/default is a symlink
        to /real/dir/my-vault. Claude writes to /real/dir/my-vault/note.md
        (bypassing the symlink path). The guard must catch it.
        """
        real_dir = tmp_path / "real-vault"
        real_dir.mkdir(parents=True)
        sym_dir = tmp_path / "vaults" / "default"
        sym_dir.parent.mkdir(parents=True, exist_ok=True)
        sym_dir.symlink_to(real_dir)

        # The write target uses the REAL path (not through the symlink)
        real_target = str(real_dir / "records" / "note.md")

        # The guard root is the SYMLINK path (as lore init would configure it)
        result = _run_guard(guard_script, real_target, [str(sym_dir)])

        assert result.returncode == 2, (
            f"Expected exit 2 (deny) for write to real target of symlinked vault.\n"
            f"guard_root (symlink): {sym_dir}\n"
            f"write target (real):  {real_target}\n"
            f"realpath(symlink):    {os.path.realpath(sym_dir)}\n"
            f"realpath(target):     {os.path.realpath(real_target)}\n"
            f"returncode: {result.returncode}\n"
            f"stderr: {result.stderr!r}"
        )

    def test_write_to_real_target_after_symlink_retarget_is_denied(
        self, guard_script: Path, tmp_path: Path
    ) -> None:
        """After retargeting the symlink to a NEW real dir, writes to the new real dir
        are still denied (proves execution-time resolution, not install-time snapshot).

        Slice 3 spec requirement (council Reliability): resolution happens at hook
        EXECUTION TIME so a symlink retargeted after lore init is always covered.
        """
        # Initial target
        real_dir_1 = tmp_path / "real-vault-v1"
        real_dir_1.mkdir(parents=True)
        # New target (simulates user retargeting the vault symlink post-install)
        real_dir_2 = tmp_path / "real-vault-v2"
        real_dir_2.mkdir(parents=True)

        sym_dir = tmp_path / "vaults" / "default"
        sym_dir.parent.mkdir(parents=True, exist_ok=True)
        sym_dir.symlink_to(real_dir_1)  # initial state

        # "Install time": guard is configured with the symlink path
        guard_roots = [str(sym_dir)]

        # Verify initial target is protected
        r1 = _run_guard(guard_script, str(real_dir_1 / "note.md"), guard_roots)
        assert r1.returncode == 2, "Initial real target must be denied"

        # Retarget the symlink (simulates user action post-install)
        sym_dir.unlink()
        sym_dir.symlink_to(real_dir_2)

        # Write to the OLD real target: should now be ALLOWED (guard follows symlink live)
        r2_old = _run_guard(guard_script, str(real_dir_1 / "note.md"), guard_roots)
        assert r2_old.returncode == 0, (
            "After retarget, write to OLD real target must be allowed "
            "(it's no longer under the symlink's current real target).\n"
            f"returncode: {r2_old.returncode}, stderr: {r2_old.stderr!r}"
        )

        # Write to the NEW real target: must be DENIED (execution-time resolution)
        r2_new = _run_guard(guard_script, str(real_dir_2 / "note.md"), guard_roots)
        assert r2_new.returncode == 2, (
            "After retarget, write to NEW real target must be denied "
            "(execution-time symlink resolution).\n"
            f"returncode: {r2_new.returncode}, stderr: {r2_new.stderr!r}"
        )

    def test_write_outside_real_vault_sibling_allowed(
        self, guard_script: Path, tmp_path: Path
    ) -> None:
        """A write to a sibling dir of the real vault (not inside it) is allowed."""
        real_dir = tmp_path / "real-vault"
        real_dir.mkdir(parents=True)
        sibling = tmp_path / "project-work"
        sibling.mkdir(parents=True)

        sym_dir = tmp_path / "vaults" / "default"
        sym_dir.parent.mkdir(parents=True, exist_ok=True)
        sym_dir.symlink_to(real_dir)

        # Sibling path — starts with a prefix that isn't the real vault
        target = str(sibling / "main.py")
        result = _run_guard(guard_script, target, [str(sym_dir)])

        assert result.returncode == 0, (
            f"Write to sibling of real vault must be allowed, got {result.returncode}.\n"
            f"stderr: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# KU1 (b) supplemental: deny via JSON stdout (exit 0 path) also works
# ---------------------------------------------------------------------------


class TestDenyViaJsonStdout:
    """KU1 (b) supplemental: prove the JSON hookSpecificOutput deny shape.

    This is a separate, simpler guard that emits the JSON structure and exits 0.
    It proves the documented alternative deny contract is valid Python.
    The exit-2 path is preferred for Slice 3 (simpler, no JSON parse on Claude
    Code side), but both paths are documented as valid.
    """

    JSON_DENY_SCRIPT = textwrap.dedent("""\
        #!/usr/bin/env python3
        \"\"\"JSON-stdout deny variant — proves KU1 (b) alternative contract.\"\"\"
        import json, os, sys
        from pathlib import Path

        payload = json.load(sys.stdin)
        file_path = payload.get("tool_input", {}).get("file_path", "")
        if not file_path:
            sys.exit(0)

        real_target = os.path.realpath(file_path)
        guard_root = os.path.realpath(
            os.environ.get("LORE_VAULT_GUARD_ROOT", "")
        )

        if guard_root and (
            real_target == guard_root or real_target.startswith(guard_root + os.sep)
        ):
            # JSON stdout deny — exit 0 required for Claude Code to parse JSON
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Write to {file_path!r} denied: path is under "
                        f"guarded vault root."
                    ),
                }
            }))
            sys.exit(0)  # MUST be 0 for JSON to be processed by Claude Code

        sys.exit(0)
    """)

    @pytest.fixture()
    def json_deny_script(self, tmp_path: Path) -> Path:
        script = tmp_path / "vault_guard_json.py"
        script.write_text(self.JSON_DENY_SCRIPT)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script

    def test_json_deny_emits_correct_structure_on_vault_write(
        self, json_deny_script: Path, tmp_path: Path
    ) -> None:
        """JSON-deny variant: vault write → exit 0 + correct hookSpecificOutput JSON."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        target = str(vault / "note.md")

        env = dict(os.environ)
        env["LORE_VAULT_GUARD_ROOT"] = str(vault)
        result = subprocess.run(
            [sys.executable, str(json_deny_script)],
            input=_make_payload(target),
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0, (
            f"JSON deny variant must exit 0, got {result.returncode}"
        )
        output = json.loads(result.stdout)
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "permissionDecisionReason" in hso
        assert len(hso["permissionDecisionReason"]) > 0

    def test_json_deny_exit0_no_output_on_allowed_write(
        self, json_deny_script: Path, tmp_path: Path
    ) -> None:
        """JSON-deny variant: write outside vault → exit 0, no JSON on stdout."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        outside = tmp_path / "src" / "main.py"

        env = dict(os.environ)
        env["LORE_VAULT_GUARD_ROOT"] = str(vault)
        result = subprocess.run(
            [sys.executable, str(json_deny_script)],
            input=_make_payload(str(outside)),
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0
        # stdout must be empty (no JSON) or must not contain a deny decision
        if result.stdout.strip():
            output = json.loads(result.stdout)
            decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
            assert decision != "deny", (
                f"Non-vault write must not produce a deny decision. Got: {output}"
            )
