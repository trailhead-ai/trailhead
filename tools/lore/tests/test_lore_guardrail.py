"""Tests for Slice 3, S5: the vault write-protection guardrail.

The guardrail is a path-canonicalizing PreToolUse hook (``hooks/vault-guard.py``)
that denies Write/Edit targeting paths under ``$XDG_STATE_HOME/lore/vaults/**``
**and** the resolved real target of the ``default`` symlink. Symlink resolution
happens at hook EXECUTION time, not install time (council Reliability), so a
symlink retargeted after ``lore init`` is always covered.

Covers every bullet of the Slice 3 test contract:
  - A simulated Write under ``…/vaults/**`` is DENIED (exit 2); a Write outside
    is ALLOWED (exit 0).
  - Mandatory symlink case: with ``default`` a symlink to an arbitrary real dir,
    a Write to the REAL target path (bypassing the canonical prefix) is DENIED.
  - After retargeting the symlink post-install, a Write to the NEW real target is
    DENIED and the OLD real target is ALLOWED (execution-time resolution).
  - Re-run installs no duplicate guardrail entry; unrelated permission rules/hooks
    preserved.
  - ``--local`` installs the guardrail into the project settings file.

KU1 (VALIDATED, Slice 0): deny = exit code 2 (stderr carries the reason; stdout
ignored). The vault root(s) are passed via the ``LORE_VAULT_GUARD_ROOT`` env var
(colon-separated). The hook ``os.path.realpath``s both target and roots.

All tests inject XDG_STATE_HOME / XDG_CONFIG_HOME / HOME via env and use tmp_path
so they NEVER touch real config, state, vault, or ``~/.claude`` data (Axiom 6).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
GUARD_SCRIPT = HOOKS_DIR / "vault-guard.py"

sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402


# ---------------------------------------------------------------------------
# Guard-hook harness
# ---------------------------------------------------------------------------

def _make_payload(file_path: str, tool_name: str = "Write") -> str:
    """Return a minimal PreToolUse JSON payload string for a write to file_path."""
    return json.dumps({
        "session_id": "test-session",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path, "file_text": "# x"},
    })


# The runtime guard reads the vault root list from LORE_VAULT_GUARD_ROOT split on
# NEWLINE (fix 4 — a byte that cannot appear in a POSIX path, so a vault path
# containing a literal ':' is not corrupted).
GUARD_ROOT_DELIM = "\n"


def _run_guard(file_path, guard_roots, *, tool_name="Write", payload=None):
    """Invoke the real guard hook with the given file_path and guard roots."""
    env = dict(os.environ)
    env["LORE_VAULT_GUARD_ROOT"] = GUARD_ROOT_DELIM.join(str(r) for r in guard_roots)
    return subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input=payload if payload is not None else _make_payload(str(file_path), tool_name),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# init harness (mirrors test_lore_init / test_lore_init_hooks)
# ---------------------------------------------------------------------------

def _run_init(args, *, state, config, home, cwd=None, extra=None):
    """Run `lore init` with isolated XDG dirs + an isolated HOME (Axiom 6)."""
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_CONFIG_HOME"] = str(config)
    env["HOME"] = str(home)
    env["LORE_EMAIL"] = "tester@example.com"
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def _dirs(tmp_path):
    state = tmp_path / "state"
    config = tmp_path / "config"
    home = tmp_path / "home"
    for d in (state, config, home):
        d.mkdir(parents=True, exist_ok=True)
    return state, config, home


# ===========================================================================
# 1. The guard hook script: deny under vault, allow outside (exit-2 contract)
# ===========================================================================

class TestGuardHookDenyAllow:
    def test_guard_hook_script_exists(self):
        assert GUARD_SCRIPT.is_file(), f"missing guard hook script: {GUARD_SCRIPT}"

    def test_write_inside_vault_is_denied(self, tmp_path):
        """A Write under the guarded vault root is denied (exit 2)."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        target = vault / "records" / "note.md"

        result = _run_guard(target, [vault.parent])
        assert result.returncode == 2, (
            f"expected exit 2 (deny) for write inside vault, got {result.returncode}; "
            f"stderr={result.stderr!r}"
        )
        assert result.stderr.strip(), "deny must carry a human-readable reason on stderr"

    def test_write_outside_vault_is_allowed(self, tmp_path):
        """A Write outside the guarded vault root is allowed (exit 0)."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        outside = tmp_path / "project" / "src" / "main.py"
        outside.parent.mkdir(parents=True)

        result = _run_guard(outside, [vault.parent])
        assert result.returncode == 0, (
            f"expected exit 0 (allow) outside vault, got {result.returncode}; "
            f"stderr={result.stderr!r}"
        )

    def test_write_to_vault_root_itself_is_denied(self, tmp_path):
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        result = _run_guard(vault, [vault.parent])
        assert result.returncode == 2

    def test_edit_tool_under_vault_is_denied(self, tmp_path):
        """The matcher is Edit|Write — an Edit under the vault is also denied."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        target = vault / "note.md"
        result = _run_guard(target, [vault.parent], tool_name="Edit")
        assert result.returncode == 2

    def test_no_file_path_in_payload_is_allowed(self, tmp_path):
        """A payload with no file_path (e.g. Bash) defers — exit 0, no crash."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        payload = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hi"},
        })
        result = _run_guard("", [vault.parent], payload=payload)
        assert result.returncode == 0

    def test_empty_guard_root_env_allows(self, tmp_path):
        """No configured roots → nothing to guard → allow (never crash)."""
        target = tmp_path / "anywhere" / "note.md"
        target.parent.mkdir(parents=True)
        result = _run_guard(target, [])
        assert result.returncode == 0

    def test_malformed_stdin_does_not_crash_into_deny(self, tmp_path):
        """Non-JSON stdin must not hard-deny every tool call (fail-open on parse)."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        result = _run_guard("", [vault.parent], payload="not json at all")
        # The guard must not block unrelated tools on a parse error.
        assert result.returncode == 0


# ===========================================================================
# 2. Mandatory symlink case — execution-time real-target resolution
# ===========================================================================

class TestGuardHookSymlinkResolution:
    def test_write_to_real_target_of_symlinked_vault_is_denied(self, tmp_path):
        """Guard root is the SYMLINK path; write targets the REAL path → denied."""
        real_dir = tmp_path / "real-vault"
        real_dir.mkdir()
        vaults = tmp_path / "vaults"
        sym = vaults / "default"
        vaults.mkdir(parents=True)
        sym.symlink_to(real_dir)

        real_target = real_dir / "records" / "note.md"
        # Guard configured with the canonical vaults dir + the symlink path.
        result = _run_guard(real_target, [vaults, sym])
        assert result.returncode == 2, (
            "write to the real target of a symlinked vault must be denied; "
            f"stderr={result.stderr!r}"
        )

    def test_write_to_new_real_target_after_retarget_is_denied(self, tmp_path):
        """After retargeting the symlink post-install, the NEW real target is denied
        and the OLD real target is allowed (proves execution-time resolution)."""
        real_v1 = tmp_path / "real-v1"
        real_v2 = tmp_path / "real-v2"
        real_v1.mkdir()
        real_v2.mkdir()
        vaults = tmp_path / "vaults"
        sym = vaults / "default"
        vaults.mkdir(parents=True)
        sym.symlink_to(real_v1)

        roots = [vaults, sym]  # "install-time" config — the symlink path, not its target

        r1 = _run_guard(real_v1 / "note.md", roots)
        assert r1.returncode == 2, "initial real target must be denied"

        # Retarget the symlink (user action post-install).
        sym.unlink()
        sym.symlink_to(real_v2)

        r_old = _run_guard(real_v1 / "note.md", roots)
        assert r_old.returncode == 0, (
            "after retarget, the OLD real target must be allowed "
            "(symlink no longer points there)"
        )
        r_new = _run_guard(real_v2 / "note.md", roots)
        assert r_new.returncode == 2, (
            "after retarget, the NEW real target must be denied "
            "(execution-time, not install-time, resolution)"
        )

    def test_sibling_of_real_vault_is_allowed(self, tmp_path):
        real_dir = tmp_path / "real-vault"
        real_dir.mkdir()
        sibling = tmp_path / "real-vault-sibling"
        sibling.mkdir()
        vaults = tmp_path / "vaults"
        sym = vaults / "default"
        vaults.mkdir(parents=True)
        sym.symlink_to(real_dir)

        result = _run_guard(sibling / "main.py", [vaults, sym])
        assert result.returncode == 0


# ===========================================================================
# 2b. Security-audit fixes (Slice 3 hardening, S5)
# ===========================================================================

class TestGuardToolCoverage:
    """Fix 1: MultiEdit/NotebookEdit must not bypass the matcher.

    The guard extracts the target from ``tool_input.file_path`` OR
    ``tool_input.notebook_path`` (whichever is present), so a MultiEdit
    (``file_path``) and a NotebookEdit (``notebook_path``) under the vault are
    both denied; outside the vault both are allowed.
    """

    def test_multiedit_under_vault_is_denied(self, tmp_path):
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        target = vault / "records" / "note.md"
        result = _run_guard(target, [vault.parent], tool_name="MultiEdit")
        assert result.returncode == 2, (
            f"MultiEdit under the vault must be denied; got {result.returncode}; "
            f"stderr={result.stderr!r}"
        )

    def test_multiedit_outside_vault_is_allowed(self, tmp_path):
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        outside = tmp_path / "project" / "main.py"
        outside.parent.mkdir(parents=True)
        result = _run_guard(outside, [vault.parent], tool_name="MultiEdit")
        assert result.returncode == 0

    def test_notebookedit_under_vault_is_denied(self, tmp_path):
        """NotebookEdit's payload field is ``notebook_path``, not ``file_path``."""
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        target = vault / "records" / "note.ipynb"
        payload = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "NotebookEdit",
            "tool_input": {"notebook_path": str(target), "new_source": "x = 1"},
        })
        result = _run_guard("", [vault.parent], tool_name="NotebookEdit", payload=payload)
        assert result.returncode == 2, (
            "NotebookEdit (notebook_path) under the vault must be denied; "
            f"got {result.returncode}; stderr={result.stderr!r}"
        )

    def test_notebookedit_outside_vault_is_allowed(self, tmp_path):
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        outside = tmp_path / "project" / "analysis.ipynb"
        outside.parent.mkdir(parents=True)
        payload = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "NotebookEdit",
            "tool_input": {"notebook_path": str(outside), "new_source": "x = 1"},
        })
        result = _run_guard("", [vault.parent], tool_name="NotebookEdit", payload=payload)
        assert result.returncode == 0


class TestGuardUnsetRootWarns:
    """Fix 2: an empty/unset LORE_VAULT_GUARD_ROOT must warn on stderr while
    still exiting 0 — converting silent mis-protection into an observable signal.
    """

    def test_empty_guard_root_warns_on_stderr_but_allows(self, tmp_path):
        target = tmp_path / "anywhere" / "note.md"
        target.parent.mkdir(parents=True)
        result = _run_guard(target, [])  # empty root list
        assert result.returncode == 0, "empty root list must still allow (exit 0)"
        assert result.stderr.strip(), (
            "an empty/unset LORE_VAULT_GUARD_ROOT must emit a warning on stderr"
        )
        assert "unguarded" in result.stderr.lower()

    def test_unset_guard_root_warns_on_stderr_but_allows(self, tmp_path):
        """With the env var entirely absent, same observable warning + exit 0."""
        target = tmp_path / "anywhere" / "note.md"
        target.parent.mkdir(parents=True)
        env = dict(os.environ)
        env.pop("LORE_VAULT_GUARD_ROOT", None)
        result = subprocess.run(
            [sys.executable, str(GUARD_SCRIPT)],
            input=_make_payload(str(target)),
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert result.stderr.strip(), "unset root must emit a stderr warning"


class TestGuardCaseInsensitiveBypass:
    """Fix 3: ``os.path.realpath`` preserves input case, so on a case-insensitive
    FS an alternate-case spelling evades the prefix check. The guard casefolds
    both the resolved target and the resolved root before comparing.
    """

    def test_alternate_case_path_is_denied(self, tmp_path):
        vault = tmp_path / "vaults" / "default"
        vault.mkdir(parents=True)
        target = vault / "records" / "note.md"

        # Spell the target with a swapped-case leading segment. On a
        # case-insensitive FS this names the SAME file; the guard must still deny.
        alt = str(target)
        swapped = alt.swapcase()
        result = _run_guard(swapped, [vault.parent])
        assert result.returncode == 2, (
            "an alternate-case spelling of a guarded vault path must be denied "
            f"(casefold both sides); got {result.returncode}; stderr={result.stderr!r}"
        )


class TestGuardColonInPath:
    """Fix 4: the root list delimiter is a NEWLINE, not ``os.pathsep`` (``:``),
    so a vault root whose path contains a literal ':' still guards correctly.
    """

    def test_colon_in_vault_path_still_guards(self, tmp_path):
        vaults = tmp_path / "weird:colon" / "vaults"
        vaults.mkdir(parents=True)
        inside = vaults / "default" / "note.md"
        result = _run_guard(inside, [vaults])
        assert result.returncode == 2, (
            "a write inside a colon-containing vault root must be denied "
            f"(newline-delimited root list); got {result.returncode}; "
            f"stderr={result.stderr!r}"
        )

    def test_colon_in_vault_path_allows_outside(self, tmp_path):
        vaults = tmp_path / "weird:colon" / "vaults"
        vaults.mkdir(parents=True)
        outside = tmp_path / "weird:colon" / "src" / "main.py"
        outside.parent.mkdir(parents=True)
        result = _run_guard(outside, [vaults])
        assert result.returncode == 0


class TestGuardDocstringScope:
    """Fixes 6 & 7: accurate comment on the no-path allow branch, and an explicit
    accepted-out-of-scope note for Bash-mediated writes.
    """

    def test_module_docstring_documents_accepted_bash_gap(self):
        src = GUARD_SCRIPT.read_text()
        # The module docstring must explicitly call Bash writes accepted/out-of-scope.
        assert "Bash" in src
        assert "out-of-scope" in src.lower() or "out of scope" in src.lower()

    def test_no_path_allow_branch_comment_is_not_bash(self):
        """The missing-path allow branch must NOT be commented as 'e.g. Bash'
        (Bash never matches the matcher and never reaches the hook)."""
        src = GUARD_SCRIPT.read_text()
        assert "e.g. Bash" not in src, (
            "the no-file-path allow branch comment must not claim it fires for Bash"
        )


# ===========================================================================
# 3. settings_writer: permissions.deny upsert (defense-in-depth)
# ===========================================================================

class TestSettingsWriterPermissionDeny:
    def _sw(self):
        return load_script("settings_writer")

    def test_upsert_permission_deny_adds_rule(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        sw.upsert_permission_deny(settings, "Write(//abs/vaults/**)")
        data = json.loads(settings.read_text())
        assert "Write(//abs/vaults/**)" in data["permissions"]["deny"]

    def test_upsert_permission_deny_is_idempotent(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        sw.upsert_permission_deny(settings, "Write(//abs/vaults/**)")
        sw.upsert_permission_deny(settings, "Write(//abs/vaults/**)")
        data = json.loads(settings.read_text())
        deny = data["permissions"]["deny"]
        assert deny.count("Write(//abs/vaults/**)") == 1

    def test_upsert_permission_deny_preserves_existing_rules(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "permissions": {"deny": ["Bash(rm:*)"], "allow": ["Read(*)"]}
        }))
        sw.upsert_permission_deny(settings, "Write(//abs/vaults/**)")
        data = json.loads(settings.read_text())
        assert "Bash(rm:*)" in data["permissions"]["deny"]
        assert data["permissions"]["allow"] == ["Read(*)"]

    def test_upsert_permission_deny_raises_on_corrupt_settings(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        corrupt = "{ not json ]"
        settings.write_text(corrupt)
        with pytest.raises(ValueError):
            sw.upsert_permission_deny(settings, "Write(//x/**)")
        assert settings.read_text() == corrupt, "corrupt settings clobbered"

    def test_set_env_var_sets_and_preserves(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"env": {"FOO": "bar"}}))
        sw.set_env_var(settings, "LORE_VAULT_GUARD_ROOT", "/x/vaults")
        data = json.loads(settings.read_text())
        assert data["env"]["LORE_VAULT_GUARD_ROOT"] == "/x/vaults"
        assert data["env"]["FOO"] == "bar"

    def test_set_env_var_is_idempotent(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        sw.set_env_var(settings, "LORE_VAULT_GUARD_ROOT", "/x/vaults")
        before = settings.read_text()
        sw.set_env_var(settings, "LORE_VAULT_GUARD_ROOT", "/x/vaults")
        assert settings.read_text() == before, "unchanged set_env_var rewrote the file"


# ===========================================================================
# 4. cmd_init wiring: installs the guardrail into the resolved settings.json
# ===========================================================================

class TestInitInstallsGuardrail:
    """The guardrail install is user-global: ``lore init`` writes the PreToolUse
    vault-guard into ``~/.claude/settings.json`` (HOME isolated via Axiom 6).
    ``--local`` is gone, but this REAL guardrail-install behavior must survive the
    rewire, so these point at the user-global settings file.
    """

    def _read_user_settings(self, home):
        settings = home / ".claude" / "settings.json"
        assert settings.is_file(), "lore init did not write ~/.claude/settings.json"
        return settings, json.loads(settings.read_text())

    def _pretooluse_commands(self, data):
        out = []
        for entry in data.get("hooks", {}).get("PreToolUse", []):
            for h in entry.get("hooks", []):
                out.append(h.get("command", ""))
        return out

    def test_init_installs_pretooluse_guard(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        cmds = self._pretooluse_commands(data)
        assert any("vault-guard" in c for c in cmds), (
            f"no PreToolUse vault-guard entry installed; PreToolUse cmds={cmds!r}"
        )
        # The matcher must cover Edit and Write.
        matchers = [e.get("matcher") for e in data["hooks"]["PreToolUse"]
                    if any("vault-guard" in h.get("command", "")
                           for h in e.get("hooks", []))]
        assert matchers and all(
            "Edit" in m and "Write" in m for m in matchers
        ), f"guard matcher must be Edit|Write, got {matchers!r}"

    def test_init_sets_guard_root_env(self, tmp_path):
        """The settings must give the hook the vault root via LORE_VAULT_GUARD_ROOT,
        pointing at the absolute vaults dir under XDG_STATE_HOME."""
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        guard_root = data.get("env", {}).get("LORE_VAULT_GUARD_ROOT", "")
        vaults = state / "lore" / "vaults"
        assert str(vaults) in guard_root, (
            f"LORE_VAULT_GUARD_ROOT must include the vaults dir {vaults}; "
            f"got {guard_root!r}"
        )

    def test_init_guard_root_uses_newline_delimiter(self, tmp_path):
        """Fix 4: the install side must join the root list on NEWLINE (not ':'),
        so a vault path containing a literal ':' is not corrupted. The value
        covers both the vaults dir and vaults/default, so it must be multi-entry."""
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        guard_root = data.get("env", {}).get("LORE_VAULT_GUARD_ROOT", "")
        vaults = state / "lore" / "vaults"
        default_link = vaults / "default"
        assert "\n" in guard_root, (
            f"LORE_VAULT_GUARD_ROOT must be newline-delimited; got {guard_root!r}"
        )
        parts = guard_root.split("\n")
        assert str(vaults) in parts
        assert str(default_link) in parts

    def test_init_matcher_covers_multiedit_and_notebookedit(self, tmp_path):
        """Fix 1: the PreToolUse matcher must also cover MultiEdit and NotebookEdit."""
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        matchers = [e.get("matcher", "") for e in data["hooks"]["PreToolUse"]
                    if any("vault-guard" in h.get("command", "")
                           for h in e.get("hooks", []))]
        assert matchers, "no guard matcher found"
        for m in matchers:
            assert "MultiEdit" in m, f"matcher must cover MultiEdit, got {m!r}"
            assert "NotebookEdit" in m, f"matcher must cover NotebookEdit, got {m!r}"

    def test_init_adds_static_permission_deny(self, tmp_path):
        """Defense-in-depth: a coarse permissions.deny over the vaults subtree,
        using the // double-slash absolute-path grammar."""
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        deny = data.get("permissions", {}).get("deny", [])
        assert any("//" in r and "vaults" in r for r in deny), (
            f"expected a //abs vaults static deny rule, got {deny!r}"
        )

    def test_init_adds_symmetric_write_and_edit_deny(self, tmp_path):
        """Fix 5: the static deny must cover both Write( and Edit( over vaults/**,
        each anchored with the // double-slash absolute grammar."""
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        deny = data.get("permissions", {}).get("deny", [])
        write_rules = [r for r in deny if r.startswith("Write(//") and "vaults" in r]
        edit_rules = [r for r in deny if r.startswith("Edit(//") and "vaults" in r]
        assert write_rules, f"missing Write(//…vaults/**) static deny: {deny!r}"
        assert edit_rules, f"missing symmetric Edit(//…vaults/**) static deny: {deny!r}"

    def test_rerun_installs_no_duplicate_guard(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run_init(["init"], state=state, config=config, home=home)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        cmds = self._pretooluse_commands(data)
        guard_cmds = [c for c in cmds if "vault-guard" in c]
        assert len(guard_cmds) == 1, (
            f"re-run duplicated the guard entry: {guard_cmds!r}"
        )
        deny = data.get("permissions", {}).get("deny", [])
        vault_denies = [r for r in deny if "vaults" in r]
        # Two rules expected (Write + Edit, fix 5); re-run must not duplicate either.
        assert len(vault_denies) == 2, f"re-run changed the deny rules: {vault_denies!r}"
        assert len(set(vault_denies)) == 2, f"re-run duplicated a deny rule: {vault_denies!r}"

    def test_init_preserves_unrelated_settings(self, tmp_path):
        """An existing unrelated hook + permission rule survive the guardrail install."""
        state, config, home = _dirs(tmp_path)
        settings = home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash",
                     "hooks": [{"type": "command", "command": "other-guard.py"}]}
                ]
            },
            "permissions": {"deny": ["Bash(curl:*)"]},
            "env": {"FOO": "bar"},
        }))

        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        data = json.loads(settings.read_text())
        cmds = self._pretooluse_commands(data)
        assert "other-guard.py" in cmds, "unrelated PreToolUse hook was dropped"
        assert "Bash(curl:*)" in data["permissions"]["deny"], "unrelated deny dropped"
        assert data.get("env", {}).get("FOO") == "bar", "unrelated env dropped"

    def test_init_aborts_cleanly_on_corrupt_settings(self, tmp_path):
        """A present-but-corrupt settings file → clean `error:` + nonzero, no traceback
        (mirrors the Slice 1 config-seed pattern; settings_writer raises ValueError)."""
        state, config, home = _dirs(tmp_path)
        settings = home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        corrupt = "{ broken json ]"
        settings.write_text(corrupt)

        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode != 0, "corrupt settings must fail init"
        assert "error:" in res.stderr.lower()
        assert "Traceback" not in res.stderr, "must not leak a raw traceback"
        assert settings.read_text() == corrupt, "corrupt settings clobbered"
