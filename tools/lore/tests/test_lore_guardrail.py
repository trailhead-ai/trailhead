"""Tests for the vault write-protection guardrail.

The guardrail is a path-canonicalizing PreToolUse hook (``hooks/vault-guard.py``)
that denies Write/Edit targeting paths under ``$XDG_STATE_HOME/lore/vaults/**``
**and** the resolved real target of the ``default`` symlink. Symlink resolution
happens at hook EXECUTION time, not install time, so a
symlink retargeted after ``lore init`` is always covered.

Covers the test contract:
  - A simulated Write under ``…/vaults/**`` is DENIED (exit 2); a Write outside
    is ALLOWED (exit 0).
  - Mandatory symlink case: with ``default`` a symlink to an arbitrary real dir,
    a Write to the REAL target path (bypassing the canonical prefix) is DENIED.
  - After retargeting the symlink post-install, a Write to the NEW real target is
    DENIED and the OLD real target is ALLOWED (execution-time resolution).
  - Re-run installs no duplicate guardrail entry; unrelated permission rules/hooks
    preserved.
  - ``--local`` installs the guardrail into the project settings file.

Deny = exit code 2 (stderr carries the reason; stdout
ignored). The vault root(s) are passed via the ``LORE_VAULT_GUARD_ROOT`` env var
(colon-separated). The hook ``os.path.realpath``s both target and roots.

All tests inject XDG_STATE_HOME / XDG_CONFIG_HOME / HOME via env and use tmp_path
so they NEVER touch real config, state, vault, or ``~/.claude`` data (Axiom 6).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
GUARD_SCRIPT = HOOKS_DIR / "vault-guard.py"

sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402


# ---------------------------------------------------------------------------
# Guard-hook harness
# ---------------------------------------------------------------------------


def _make_payload(file_path: str, tool_name: str = "Write") -> str:
    """Return a minimal PreToolUse JSON payload string for a write to file_path."""
    return json.dumps(
        {
            "session_id": "test-session",
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path, "file_text": "# x"},
        }
    )


# The runtime guard reads the vault root list from LORE_VAULT_GUARD_ROOT split on
# NEWLINE (a byte that cannot appear in a POSIX path, so a vault path
# containing a literal ':' is not corrupted).
GUARD_ROOT_DELIM = "\n"

# The exemption pattern list uses the same NEWLINE delimiter as the root list.
GUARD_EXEMPT_DELIM = "\n"


def _run_guard(file_path, guard_roots, *, tool_name="Write", payload=None, exempt=None):
    """Invoke the real guard hook with the given file_path, roots and exemptions.

    ``exempt=None`` removes ``LORE_VAULT_GUARD_EXEMPT`` from the child env
    entirely, so a value set in the developer's own shell can never leak in and
    turn a deny-expecting test green.
    """
    env = dict(os.environ)
    env["LORE_VAULT_GUARD_ROOT"] = GUARD_ROOT_DELIM.join(str(r) for r in guard_roots)
    if exempt is None:
        env.pop("LORE_VAULT_GUARD_EXEMPT", None)
    else:
        env["LORE_VAULT_GUARD_EXEMPT"] = GUARD_EXEMPT_DELIM.join(str(p) for p in exempt)
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


# ---------------------------------------------------------------------------
# permissions.deny rule evaluation
# ---------------------------------------------------------------------------


def _vault_deny_rules(deny: list, vaults_root) -> list:
    """The ``Edit(`` deny rules an install generated inside *vaults_root*."""
    prefix = f"Edit(//{str(vaults_root).lstrip('/')}/"
    return [r for r in deny if r.startswith(prefix)]


def _rule_path_glob(rule: str) -> str:
    """The absolute-path glob inside an ``Edit(//abs/path/glob)`` rule.

    The ``//`` double-slash is the harness's absolute-path grammar (a single
    ``/`` would be project-root-relative), so one slash is dropped to recover
    the filesystem path.
    """
    assert rule.startswith("Edit(//") and rule.endswith(")"), rule
    return rule[len("Edit(/") : -1]


def _glob_to_regex(pattern: str):
    """Compile a permission-rule glob under the harness's gitignore semantics.

    ``*`` matches within exactly one path segment; ``**`` matches any number of
    segments. There is no negation syntax — which is why a rule that matches a
    directory cannot be pierced by a narrower allow, and why the deny list must
    avoid matching the sites zone in the first place (see ``_rule_denies``).
    """
    parts = []
    for segment in pattern.split("/"):
        if segment == "**":
            parts.append(".*")
        else:
            parts.append(re.escape(segment).replace(r"\*", "[^/]*"))
    return re.compile("^" + "/".join(parts) + r"\Z")


def _rule_denies(rule: str, path: str) -> bool:
    """True if *rule* blocks a write to *path*.

    A rule blocks the path directly, or blocks one of its ancestor directories —
    a deny that matches a DIRECTORY cascades to everything beneath it.
    """
    regex = _glob_to_regex(_rule_path_glob(rule))
    candidate = path
    while True:
        if regex.match(candidate):
            return True
        parent = os.path.dirname(candidate)
        if parent == candidate:
            return False
        candidate = parent


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
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "echo hi"},
            }
        )
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
            "after retarget, the OLD real target must be allowed (symlink no longer points there)"
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
# 2b. Security-audit fixes (hardening)
# ===========================================================================


class TestGuardToolCoverage:
    """MultiEdit/NotebookEdit must not bypass the matcher.

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
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "NotebookEdit",
                "tool_input": {"notebook_path": str(target), "new_source": "x = 1"},
            }
        )
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
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "NotebookEdit",
                "tool_input": {"notebook_path": str(outside), "new_source": "x = 1"},
            }
        )
        result = _run_guard("", [vault.parent], tool_name="NotebookEdit", payload=payload)
        assert result.returncode == 0


class TestGuardUnsetRootWarns:
    """An empty/unset LORE_VAULT_GUARD_ROOT must warn on stderr while
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
    """``os.path.realpath`` preserves input case, so on a case-insensitive
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
    """The root list delimiter is a NEWLINE, not ``os.pathsep`` (``:``),
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


class TestGuardExemptZone:
    """``LORE_VAULT_GUARD_EXEMPT`` carves the per-vault ``sites`` subtree out of
    the deny, without loosening the guard anywhere else.

    Patterns are newline-delimited, canonicalized like the roots (realpath +
    casefold) and matched segment-wise against the target's real path: a ``*``
    stands for exactly one path segment and never spans a separator. A target at
    or under a directory a pattern names is allowed; everything else in the vault
    is still denied.
    """

    def _vaults(self, tmp_path):
        """Build ``<tmp>/vaults/default`` and return (vaults_root, vault)."""
        vaults_root = tmp_path / "vaults"
        vault = vaults_root / "default"
        vault.mkdir(parents=True)
        return vaults_root, vault

    def _pattern(self, vaults_root):
        """The exemption pattern ``lore init`` writes."""
        return f"{vaults_root}/*/sites"

    def test_write_under_sites_is_allowed(self, tmp_path):
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "sites" / "demo" / "index.html"
        result = _run_guard(target, [vaults_root], exempt=[self._pattern(vaults_root)])
        assert result.returncode == 0, (
            "a write under a vault's sites subtree must be allowed when the "
            f"exemption pattern covers it; stderr={result.stderr!r}"
        )

    def test_both_zones_are_exempt_when_both_patterns_are_registered(self, tmp_path):
        """The exemption list is a LIST: a second pattern joins the first rather
        than replacing it, and both zones are writable in the same session.

        This is the property the Outpost config zone rests on — that the guard
        is a multi-zone mechanism, not a single-valued variable that happens to
        be read with a glob.
        """
        vaults_root, vault = self._vaults(tmp_path)
        patterns = [f"{vaults_root}/*/sites", f"{vaults_root}/*/outpost"]
        for zone, target in (
            ("sites", vault / "sites" / "demo" / "index.html"),
            ("outpost", vault / "outpost" / "streams" / "a-stream.json"),
        ):
            result = _run_guard(target, [vaults_root], exempt=patterns)
            assert result.returncode == 0, (
                f"the {zone} zone must be writable when both patterns are "
                f"registered; stderr={result.stderr!r}"
            )

    def test_a_second_zone_does_not_unguard_the_record_trees(self, tmp_path):
        """Registering another zone must not widen the deny anywhere else."""
        vaults_root, vault = self._vaults(tmp_path)
        patterns = [f"{vaults_root}/*/sites", f"{vaults_root}/*/outpost"]
        for target in (
            vault / "adr" / "some-decision.md",
            vault / "task" / "some-task.md",
            vault / "task" / "some-task" / "outpost" / "sneaky.json",
            vault / "outpost-archive" / "x.json",
        ):
            result = _run_guard(target, [vaults_root], exempt=patterns)
            assert result.returncode == 2, (
                f"{target} must stay denied with both zones registered; "
                f"stderr={result.stderr!r}"
            )

    def test_record_write_in_the_same_vault_is_still_denied(self, tmp_path):
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "adr" / "some-decision.md"
        result = _run_guard(target, [vaults_root], exempt=[self._pattern(vaults_root)])
        assert result.returncode == 2, (
            "the exemption must not leak past the sites subtree — a record write "
            f"in the same vault stays denied; stderr={result.stderr!r}"
        )

    def test_sites_dir_nested_below_the_vault_top_level_is_denied(self, tmp_path):
        """``*`` matches ONE segment, so only a vault's top-level sites is exempt."""
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "task" / "some-task" / "sites" / "index.html"
        result = _run_guard(target, [vaults_root], exempt=[self._pattern(vaults_root)])
        assert result.returncode == 2, (
            "a sites directory nested deeper than the vault top level is a record "
            f"tree, not the free-write zone; stderr={result.stderr!r}"
        )

    def test_sibling_directory_sharing_the_prefix_is_denied(self, tmp_path):
        """``sites-archive`` is not ``sites`` — the segment must match whole."""
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "sites-archive" / "index.html"
        result = _run_guard(target, [vaults_root], exempt=[self._pattern(vaults_root)])
        assert result.returncode == 2, (
            "a directory that merely shares the 'sites' prefix must not be "
            f"exempt; stderr={result.stderr!r}"
        )

    def test_sites_write_through_an_inside_pointing_default_symlink_is_allowed(
        self, tmp_path
    ):
        """The ``default`` symlink resolves to a real vault under the vaults root,
        so the realpath the guard computes is still covered by the pattern."""
        vaults_root = tmp_path / "vaults"
        vaults_root.mkdir()
        real_default = vaults_root / "real-default"
        real_default.mkdir()
        default_link = vaults_root / "default"
        default_link.symlink_to(real_default)

        target = default_link / "sites" / "index.html"
        result = _run_guard(
            target,
            [vaults_root, default_link],
            exempt=[self._pattern(vaults_root)],
        )
        assert result.returncode == 0, (
            "a sites write through a default symlink pointing inside the vaults "
            f"root must be allowed; stderr={result.stderr!r}"
        )

    def test_sites_write_through_an_escaped_default_symlink_stays_denied(self, tmp_path):
        """A ``default`` symlink pointing OUTSIDE the vaults root is its own
        realpath-resolved guard root, so the deny still fires — and the pattern,
        anchored at the vaults root, cannot match the escaped real path. The
        write is blocked: fail-closed, and pinned here so the posture is a
        decision rather than an accident.
        """
        vaults_root = tmp_path / "vaults"
        vaults_root.mkdir()
        outside = tmp_path / "adopted-vault"  # sibling of vaults_root, not under it
        outside.mkdir()
        default_link = vaults_root / "default"
        default_link.symlink_to(outside)

        target = default_link / "sites" / "index.html"
        result = _run_guard(
            target,
            [vaults_root, default_link],
            exempt=[self._pattern(vaults_root)],
        )
        assert result.returncode == 2, (
            "a sites write through a default symlink that escapes the vaults root "
            f"must stay denied; stderr={result.stderr!r}"
        )

    def test_alternate_case_sites_path_is_allowed(self, tmp_path):
        """Both sides are casefolded, so an alternate-case spelling of an exempt
        path is exempt too — the same canonicalization the deny side uses."""
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "SITES" / "Index.HTML"
        result = _run_guard(target, [vaults_root], exempt=[self._pattern(vaults_root)])
        assert result.returncode == 0, (
            "an alternate-case spelling names the same sites subtree on a "
            f"case-insensitive filesystem; stderr={result.stderr!r}"
        )

    def test_unset_exempt_denies_a_sites_write(self, tmp_path):
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "sites" / "index.html"
        result = _run_guard(target, [vaults_root])  # env var absent entirely
        assert result.returncode == 2, (
            "with no exemption configured the guard must deny everything under a "
            f"vault root (fail-closed); stderr={result.stderr!r}"
        )

    def test_empty_exempt_denies_a_sites_write(self, tmp_path):
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "sites" / "index.html"
        result = _run_guard(target, [vaults_root], exempt=[])  # empty value
        assert result.returncode == 2, (
            "an empty exemption value must not be read as 'exempt everything'; "
            f"stderr={result.stderr!r}"
        )

    def test_malformed_pattern_lines_are_ignored_and_the_guard_still_denies(
        self, tmp_path
    ):
        """Junk lines are skipped; they neither crash the guard nor blanket-allow."""
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "adr" / "some-decision.md"
        result = _run_guard(
            target,
            [vaults_root],
            exempt=["", "   ", "not/an/absolute/path", "*", "**"],
        )
        assert result.returncode == 2, (
            "malformed exemption lines must be ignored, leaving the guard intact; "
            f"stderr={result.stderr!r}"
        )

    def test_malformed_lines_do_not_disable_a_valid_pattern(self, tmp_path):
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "sites" / "index.html"
        result = _run_guard(
            target,
            [vaults_root],
            exempt=["   ", "relative/sites", self._pattern(vaults_root)],
        )
        assert result.returncode == 0, (
            "a valid exemption pattern must still apply when malformed lines sit "
            f"beside it; stderr={result.stderr!r}"
        )

    def test_vaults_root_containing_glob_metacharacters_keeps_its_carve_out(
        self, tmp_path
    ):
        """A vaults root whose literal path contains ``[``, ``]`` or ``?`` must
        still match its own exemption pattern: those bytes are legal in a POSIX
        path, and reading them as glob syntax would silently drop the carve-out
        while every other path kept working."""
        vaults_root = tmp_path / "we[ir]d?root" / "vaults"
        vault = vaults_root / "default"
        vault.mkdir(parents=True)
        target = vault / "sites" / "index.html"
        result = _run_guard(target, [vaults_root], exempt=[self._pattern(vaults_root)])
        assert result.returncode == 0, (
            "glob metacharacters in the literal part of an exemption pattern must "
            f"be matched literally; stderr={result.stderr!r}"
        )

    def test_metacharacter_pattern_does_not_match_a_different_real_path(self, tmp_path):
        """Positive control for the escaping: with the literal characters taken
        literally, a path that only matches when they are read as wildcards must
        stay denied."""
        vaults_root = tmp_path / "vaults"
        vault = vaults_root / "default"
        vault.mkdir(parents=True)
        target = vault / "sites" / "index.html"
        # `[v]aults` and `vault?` both glob-match this real path; neither names it.
        result = _run_guard(
            target,
            [vaults_root],
            exempt=[f"{tmp_path}/[v]aults/*/sites", f"{tmp_path}/vault?/*/sites"],
        )
        assert result.returncode == 2, (
            "an exemption pattern must name a real path literally, not glob onto "
            f"it; stderr={result.stderr!r}"
        )

    def test_pattern_outside_every_guard_root_is_ignored_with_a_warning(self, tmp_path):
        """A pattern that does not resolve under any configured guard root can
        only widen the guard, never carve a zone out of it — a bare ``/*`` line
        would exempt the whole filesystem. Such a pattern is ignored, and the
        guard says so rather than failing silently."""
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "sites" / "index.html"
        result = _run_guard(target, [vaults_root], exempt=["/*"])
        assert result.returncode == 2, (
            "a pattern outside every guard root must not exempt anything; "
            f"stderr={result.stderr!r}"
        )
        assert "/*" in result.stderr, (
            "the ignored pattern must be named on stderr so a misconfiguration is "
            f"observable; stderr={result.stderr!r}"
        )

    def test_root_star_pattern_is_ignored_and_record_stays_denied(self, tmp_path):
        """``<root>/*`` matches every child of the vaults root and everything
        under it — it would exempt every record tree wholesale. It is one segment
        too shallow to be a real carve-out (which names a ``<vault>/<zone>``
        subtree), so it is ignored with a warning and a record write stays denied.
        """
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "adr" / "some-decision.md"
        result = _run_guard(target, [vaults_root], exempt=[f"{vaults_root}/*"])
        assert result.returncode == 2, (
            "an over-broad `<root>/*` exemption must not exempt record trees; "
            f"stderr={result.stderr!r}"
        )
        assert f"{vaults_root}/*" in result.stderr, (
            "the ignored over-broad pattern must be named on stderr so the "
            f"misconfiguration is observable; stderr={result.stderr!r}"
        )

    def test_bare_root_pattern_is_ignored_and_record_stays_denied(self, tmp_path):
        """A bare ``<root>`` (no wildcard) resolves to the vaults root itself and
        would match every path under it. It is not strictly deeper than the root,
        so it is ignored with a warning and a record write stays denied.
        """
        vaults_root, vault = self._vaults(tmp_path)
        target = vault / "adr" / "some-decision.md"
        result = _run_guard(target, [vaults_root], exempt=[str(vaults_root)])
        assert result.returncode == 2, (
            "a bare `<root>` exemption must not exempt record trees; "
            f"stderr={result.stderr!r}"
        )
        assert str(vaults_root) in result.stderr, (
            "the ignored over-broad pattern must be named on stderr so the "
            f"misconfiguration is observable; stderr={result.stderr!r}"
        )

    def test_legit_sites_pattern_still_carves_and_still_denies_records(self, tmp_path):
        """The ``<root>/*/sites`` pattern names a subtree two segments below the
        root — the real carve-out shape — so it still exempts a sites write while
        a record write in the same vault stays denied, now that the shallower
        `<root>/*` and bare `<root>` are rejected."""
        vaults_root, vault = self._vaults(tmp_path)
        sites_write = _run_guard(
            vault / "sites" / "index.html",
            [vaults_root],
            exempt=[self._pattern(vaults_root)],
        )
        assert sites_write.returncode == 0, (
            "the legitimate sites carve-out must still be allowed; "
            f"stderr={sites_write.stderr!r}"
        )
        record_write = _run_guard(
            vault / "adr" / "some-decision.md",
            [vaults_root],
            exempt=[self._pattern(vaults_root)],
        )
        assert record_write.returncode == 2, (
            "the legitimate sites carve-out must not leak onto record trees; "
            f"stderr={record_write.stderr!r}"
        )

    def test_exempt_patterns_use_the_newline_delimiter(self, tmp_path):
        """A vaults root containing a literal ':' must still be exemptable — the
        delimiter is a newline, which cannot appear in a POSIX path."""
        vaults_root = tmp_path / "weird:colon" / "vaults"
        vault = vaults_root / "default"
        vault.mkdir(parents=True)
        target = vault / "sites" / "index.html"
        result = _run_guard(target, [vaults_root], exempt=[self._pattern(vaults_root)])
        assert result.returncode == 0, (
            "a colon-containing vaults root must still carve out its sites zone; "
            f"stderr={result.stderr!r}"
        )


class TestGuardDocstringScope:
    """Accurate comment on the no-path allow branch, and an explicit
    accepted-out-of-scope note for Bash-mediated writes.
    """

    def test_module_docstring_documents_the_exempt_env_var(self):
        src = GUARD_SCRIPT.read_text()
        assert "LORE_VAULT_GUARD_EXEMPT" in src

    def test_module_docstring_documents_the_escaped_default_symlink(self):
        """The one case where the exemption cannot apply must be stated where a
        reader of the guard will find it, not left as folklore."""
        src = GUARD_SCRIPT.read_text()
        opening = src.index('"""')
        docstring = src[opening + 3 : src.index('"""', opening + 3)]
        assert "outside the vaults root" in docstring, (
            "the guard docstring must state that a `default` symlink pointing "
            "outside the vaults root cannot be covered by an exemption pattern"
        )

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
        return load_script("lore.config.settings_writer")

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
        settings.write_text(
            json.dumps({"permissions": {"deny": ["Bash(rm:*)"], "allow": ["Read(*)"]}})
        )
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

    def test_remove_permission_deny_removes_rule(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {"permissions": {"deny": ["Write(//abs/vaults/**)", "Bash(rm:*)"]}}
            )
        )
        sw.remove_permission_deny(settings, "Write(//abs/vaults/**)")
        data = json.loads(settings.read_text())
        assert data["permissions"]["deny"] == ["Bash(rm:*)"]

    def test_remove_permission_deny_noop_when_absent(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        before = json.dumps({"permissions": {"deny": ["Bash(rm:*)"]}})
        settings.write_text(before)
        sw.remove_permission_deny(settings, "Write(//abs/vaults/**)")
        assert settings.read_text() == before, "no-op removal rewrote the file"

    def test_remove_permission_deny_noop_when_file_missing(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        sw.remove_permission_deny(settings, "Write(//abs/vaults/**)")
        assert not settings.exists(), "removal created a settings file"

    def test_upsert_permission_allow_adds_rule(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        sw.upsert_permission_allow(settings, "Bash(lore:*)")
        data = json.loads(settings.read_text())
        assert "Bash(lore:*)" in data["permissions"]["allow"]

    def test_upsert_permission_allow_is_idempotent(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        sw.upsert_permission_allow(settings, "Bash(lore:*)")
        sw.upsert_permission_allow(settings, "Bash(lore:*)")
        data = json.loads(settings.read_text())
        allow = data["permissions"]["allow"]
        assert allow.count("Bash(lore:*)") == 1

    def test_upsert_permission_allow_preserves_existing_rules(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {
                    "permissions": {"deny": ["Bash(rm:*)"], "allow": ["Read(*)"]},
                    "env": {"SOME_VAR": "1"},
                    "hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": []}]},
                }
            )
        )
        sw.upsert_permission_allow(settings, "Bash(lore:*)")
        data = json.loads(settings.read_text())
        assert data["permissions"]["deny"] == ["Bash(rm:*)"]
        assert "Read(*)" in data["permissions"]["allow"]
        assert "Bash(lore:*)" in data["permissions"]["allow"]
        assert data["env"] == {"SOME_VAR": "1"}, "unrelated top-level key not preserved"
        assert data["hooks"] == {"PreToolUse": [{"matcher": "Edit", "hooks": []}]}, (
            "unrelated top-level key not preserved"
        )

    def test_upsert_permission_allow_raises_on_corrupt_settings(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        corrupt = "{ not json ]"
        settings.write_text(corrupt)
        with pytest.raises(ValueError):
            sw.upsert_permission_allow(settings, "Bash(lore:*)")
        assert settings.read_text() == corrupt, "corrupt settings clobbered"

    def test_remove_permission_allow_removes_rule(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {"permissions": {"allow": ["Bash(lore:*)", "Read(*)"]}}
            )
        )
        sw.remove_permission_allow(settings, "Bash(lore:*)")
        data = json.loads(settings.read_text())
        assert data["permissions"]["allow"] == ["Read(*)"]

    def test_remove_permission_allow_noop_when_absent(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        before = json.dumps({"permissions": {"allow": ["Read(*)"]}})
        settings.write_text(before)
        sw.remove_permission_allow(settings, "Bash(lore:*)")
        assert settings.read_text() == before, "no-op removal rewrote the file"

    def test_remove_permission_allow_noop_when_file_missing(self, tmp_path):
        sw = self._sw()
        settings = tmp_path / ".claude" / "settings.json"
        sw.remove_permission_allow(settings, "Bash(lore:*)")
        assert not settings.exists(), "removal created a settings file"

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
        matchers = [
            e.get("matcher")
            for e in data["hooks"]["PreToolUse"]
            if any("vault-guard" in h.get("command", "") for h in e.get("hooks", []))
        ]
        assert matchers and all("Edit" in m and "Write" in m for m in matchers), (
            f"guard matcher must be Edit|Write, got {matchers!r}"
        )

    def test_guard_command_is_an_absolute_resolved_path_not_a_placeholder(self, tmp_path):
        """The installed command must NOT rely on ``${CLAUDE_PLUGIN_ROOT}``.

        This hook is wired into user-global settings.json, not declared via a
        plugin manifest, so Claude Code never expands that variable here — it
        would degrade to a literal, nonexistent ``/hooks/vault-guard.py`` path.
        The command must instead carry an absolute path that actually resolves
        to the real ``hooks/vault-guard.py`` file on disk.
        """
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        cmds = [c for c in self._pretooluse_commands(data) if "vault-guard" in c]
        assert cmds, "no vault-guard PreToolUse command installed"
        for cmd in cmds:
            assert "${CLAUDE_PLUGIN_ROOT}" not in cmd, (
                f"guard command must not depend on the unexpanded "
                f"${{CLAUDE_PLUGIN_ROOT}} placeholder: {cmd!r}"
            )
            assert str(GUARD_SCRIPT) in cmd, (
                f"guard command must carry the resolved absolute path to "
                f"{GUARD_SCRIPT}, got: {cmd!r}"
            )
            assert Path(GUARD_SCRIPT).is_absolute()

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
            f"LORE_VAULT_GUARD_ROOT must include the vaults dir {vaults}; got {guard_root!r}"
        )

    def test_init_guard_root_uses_newline_delimiter(self, tmp_path):
        """The install side must join the root list on NEWLINE (not ':'),
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
        """The PreToolUse matcher must also cover MultiEdit and NotebookEdit."""
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        matchers = [
            e.get("matcher", "")
            for e in data["hooks"]["PreToolUse"]
            if any("vault-guard" in h.get("command", "") for h in e.get("hooks", []))
        ]
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

    def test_init_adds_edit_deny_only(self, tmp_path):
        """Every static deny is an Edit( rule (Edit(path) rules cover all
        file-editing tools; a Write(path) rule never matches and makes Claude
        Code warn at startup), anchored with the // double-slash absolute
        grammar. What that rule set contains is pinned separately by
        ``TestInitKindGeneratedDenyList``."""
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        deny = data.get("permissions", {}).get("deny", [])
        write_rules = [r for r in deny if r.startswith("Write(//") and "vaults" in r]
        edit_rules = [r for r in deny if r.startswith("Edit(//") and "vaults" in r]
        assert edit_rules, f"missing Edit(//…vaults/**) static deny: {deny!r}"
        assert not write_rules, f"unmatched Write(//…) rule present: {deny!r}"

    def test_init_adds_bash_lore_permission_allow(self, tmp_path):
        """The install is symmetric: the CLI the deny rules force every write
        through must itself be sanctioned, via a fixed blanket allow rule —
        and adding it leaves permissions.deny exactly what a fresh install's
        generated deny list would be on its own, independently derived here
        from the record model's kinds rather than read back from the same
        install run."""
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        allow = data.get("permissions", {}).get("allow", [])
        assert "Bash(lore:*)" in allow, f"expected Bash(lore:*) allow rule, got {allow!r}"
        deny = data.get("permissions", {}).get("deny", [])
        assert "Bash(lore:*)" not in deny

        vaults = state / "lore" / "vaults"
        prefix = f"//{str(vaults).lstrip('/')}"
        kinds = load_script("lore.record.model").KINDS
        lock_name = load_script("lore.locking").VAULT_LOCK_NAME
        expected_deny = (
            {f"Edit({prefix}/*/{kind}/**)" for kind in kinds}
            | {f"Edit({prefix}/*/.git/**)"}
            | {f"Edit({prefix}/*/{name})" for name in (".gitignore", lock_name)}
        )
        assert set(deny) == expected_deny, (
            f"unexpected extra or missing deny rules after adding the allow "
            f"rule: {sorted(set(deny) ^ expected_deny)!r}"
        )

    def test_init_removes_legacy_blanket_denies(self, tmp_path):
        """The two blanket rules an earlier install could leave behind are
        removed on re-init.

        ``Write(//…/vaults/**)`` never matched a file-editing tool and made
        Claude Code warn at startup; ``Edit(//…/vaults/**)`` matched the vaults
        directory itself and so cascaded over every vault subtree — including
        the sites zone, which no narrower rule can then re-open, because a deny
        always beats an allow.
        """
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        vaults = state / "lore" / "vaults"
        blanket = f"//{str(vaults).lstrip('/')}/**"
        legacy = [f"Write({blanket})", f"Edit({blanket})"]

        settings, data = self._read_user_settings(home)
        data["permissions"]["deny"][:0] = legacy
        settings.write_text(json.dumps(data))

        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr
        _, data = self._read_user_settings(home)
        deny = data["permissions"]["deny"]
        for rule in legacy:
            assert rule not in deny, f"legacy blanket rule not removed: {rule!r}"
        assert _vault_deny_rules(deny, vaults), f"deny list emptied out: {deny!r}"

    def test_rerun_installs_no_duplicate_guard(self, tmp_path):
        state, config, home = _dirs(tmp_path)
        _run_init(["init"], state=state, config=config, home=home)
        _, first = self._read_user_settings(home)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        cmds = self._pretooluse_commands(data)
        guard_cmds = [c for c in cmds if "vault-guard" in c]
        assert len(guard_cmds) == 1, f"re-run duplicated the guard entry: {guard_cmds!r}"
        deny = data.get("permissions", {}).get("deny", [])
        vault_denies = [r for r in deny if "vaults" in r]
        assert vault_denies == list(dict.fromkeys(vault_denies)), (
            f"re-run duplicated a deny rule: {vault_denies!r}"
        )
        assert vault_denies == [
            r for r in first["permissions"]["deny"] if "vaults" in r
        ], f"re-run changed the deny rules: {vault_denies!r}"
        allow = data.get("permissions", {}).get("allow", [])
        assert allow.count("Bash(lore:*)") == 1, (
            f"re-run duplicated the allow rule: {allow!r}"
        )

    def test_init_preserves_unrelated_settings(self, tmp_path):
        """An existing unrelated hook + permission rule survive the guardrail install."""
        state, config, home = _dirs(tmp_path)
        settings = home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": "other-guard.py"}],
                            }
                        ]
                    },
                    "permissions": {"deny": ["Bash(curl:*)"]},
                    "env": {"FOO": "bar"},
                }
            )
        )

        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        data = json.loads(settings.read_text())
        cmds = self._pretooluse_commands(data)
        assert "other-guard.py" in cmds, "unrelated PreToolUse hook was dropped"
        assert "Bash(curl:*)" in data["permissions"]["deny"], "unrelated deny dropped"
        assert data.get("env", {}).get("FOO") == "bar", "unrelated env dropped"

    def test_init_sets_guard_exempt_env(self, tmp_path):
        """The hook's exemption list names every free-write zone a vault has.

        Two zones today: the static-site tree and the Outpost config tree. The
        value is a newline-joined list, and the order is the order lore
        provisions them in — asserted whole rather than by membership so a
        silently dropped zone cannot pass.
        """
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr

        _, data = self._read_user_settings(home)
        exempt = data.get("env", {}).get("LORE_VAULT_GUARD_EXEMPT", "")
        vaults = state / "lore" / "vaults"
        assert exempt.split("\n") == [
            f"{vaults}/*/sites",
            f"{vaults}/*/outpost",
        ], f"expected the per-vault sites and outpost exemption patterns; got {exempt!r}"

    def test_init_aborts_cleanly_on_corrupt_settings(self, tmp_path):
        """A present-but-corrupt settings file → clean `error:` + nonzero, no traceback
        (mirrors the config-seed pattern; settings_writer raises ValueError)."""
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


# ===========================================================================
# 5. The settings-layer deny list: generated per record kind, sites left open
# ===========================================================================


class TestInitKindGeneratedDenyList:
    """``lore init`` denies each record tree by name instead of denying the
    whole vaults subtree.

    A blanket ``Edit(//<vaults_root>/**)`` matches the vaults directory itself,
    and a deny that matches a directory cascades to everything beneath it with
    no way to pierce it — so it would re-block the sites zone the hook exempts.
    The generated list therefore names one rule per record kind plus a literal
    rule per file lore scaffolds at a vault root, and nothing that can match a
    directory at the vault root.
    """

    def _install(self, tmp_path):
        """Run a clean ``lore init`` and return (vaults_root, generated rules)."""
        state, config, home = _dirs(tmp_path)
        res = _run_init(["init"], state=state, config=config, home=home)
        assert res.returncode == 0, res.stderr
        data = json.loads((home / ".claude" / "settings.json").read_text())
        vaults = state / "lore" / "vaults"
        return vaults, _vault_deny_rules(data["permissions"]["deny"], vaults)

    def _kinds(self):
        return load_script("lore.record.model").KINDS

    def test_one_deny_per_record_kind(self, tmp_path):
        """The rule set is derived from the record model's kind set, so a kind
        added to the model shows up in the rendered rules on the next install —
        and a hand-maintained list that drifted from it fails here."""
        vaults, rules = self._install(tmp_path)
        prefix = f"Edit(//{str(vaults).lstrip('/')}/*/"
        denied_trees = {
            r[len(prefix) : -len("/**)")] for r in rules if r.endswith("/**)")
        }
        assert denied_trees == set(self._kinds()) | {".git"}, (
            f"the denied subtrees must be exactly the record model's kinds plus "
            f"the vault's own .git dir; got {sorted(denied_trees)!r}"
        )

    def test_vault_git_dir_is_denied(self, tmp_path):
        """A vault root's ``.git`` is not a record tree, but ``.git/hooks/*`` is
        executable code that runs on every vault operation — it needs its own
        rule, since the sites carve-out means nothing broader can cover it."""
        vaults, rules = self._install(tmp_path)
        prefix = f"//{str(vaults).lstrip('/')}"
        assert f"Edit({prefix}/*/.git/**)" in rules, (
            f"missing the vault .git deny rule; got {rules!r}"
        )

    def test_vault_root_files_are_denied_by_literal_rule(self, tmp_path):
        """The files lore scaffolds at a vault root are named outright: a
        directory-capable catch-all (``…/*/*``) would cascade over the sites
        zone as well."""
        vaults, rules = self._install(tmp_path)
        prefix = f"//{str(vaults).lstrip('/')}"
        lock_name = load_script("lore.locking").VAULT_LOCK_NAME
        for name in (".gitignore", lock_name):
            assert f"Edit({prefix}/*/{name})" in rules, (
                f"missing literal vault-root deny for {name}; got {rules!r}"
            )
        assert f"Edit({prefix}/*/*)" not in rules, (
            "a vault-root catch-all matches the sites directory itself and "
            f"cascades over everything in it; got {rules!r}"
        )

    def test_the_rule_set_is_exactly_the_kinds_plus_git_plus_the_root_files(self, tmp_path):
        vaults, rules = self._install(tmp_path)
        prefix = f"//{str(vaults).lstrip('/')}"
        expected = (
            {f"Edit({prefix}/*/{kind}/**)" for kind in self._kinds()}
            | {f"Edit({prefix}/*/.git/**)"}
            | {f"Edit({prefix}/*/{name})" for name in (".gitignore", ".lore.lock")}
        )
        assert set(rules) == expected, (
            f"unexpected extra or missing deny rules: {sorted(set(rules) ^ expected)!r}"
        )

    def test_no_generated_rule_matches_a_sites_path(self, tmp_path):
        """The whole point of the restructure: nothing in the generated list may
        block the free-write zone, directly or by cascading over a parent."""
        vaults, rules = self._install(tmp_path)
        vault = f"{vaults}/default"
        for path in (
            f"{vault}/sites",
            f"{vault}/sites/demo",
            f"{vault}/sites/demo/index.html",
            f"{vault}/sites/demo/assets/app.js",
            f"{vault}/sites/demo/.well-known/probe.txt",
        ):
            blocking = [r for r in rules if _rule_denies(r, path)]
            assert not blocking, f"{path} is blocked by {blocking!r}"

    def test_record_trees_and_root_files_are_still_blocked(self, tmp_path):
        """Positive control: the same evaluation must show the rules biting
        everywhere they are supposed to, or the test above proves nothing."""
        vaults, rules = self._install(tmp_path)
        vault = f"{vaults}/default"
        for path in (
            f"{vault}/adr/some-decision.md",
            f"{vault}/task/some-task.md",
            f"{vault}/session/2026/note.md",
            f"{vault}/.gitignore",
            f"{vault}/.lore.lock",
            f"{vault}/.git/hooks/pre-commit",
            f"{vault}/.git/config",
        ):
            assert any(_rule_denies(r, path) for r in rules), (
                f"{path} must stay denied; rules={rules!r}"
            )
