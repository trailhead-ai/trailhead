"""EPHEMERAL assumption-probe test — Unknown B (default-vault symlink + exemption).

Not part of the permanent suite. Written to resolve a Known Unknown blocking
``task/sites-guardrail-carve-out-across-three-enforcement-layers`` before that
slice restructures ``vault-guard.py``. Delete this file once the slice lands
its own permanent test contract (it duplicates none of it).

Question: can the ``default`` vault symlink point OUTSIDE the vaults root, and
if so, does the planned ``LORE_VAULT_GUARD_EXEMPT`` fnmatch pattern
(``<vaults_root>/*/sites`` matched against realpaths) still cover a write made
through it? Uses the REAL ``hooks/vault-guard.py`` (subprocess, and direct
import of its ``_real`` helper for the realpath the guard actually computes) —
no re-implementation of its canonicalization.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
GUARD_SCRIPT = PLUGIN_ROOT / "hooks" / "vault-guard.py"

GUARD_ROOT_DELIM = "\n"


def _load_guard_module():
    """Import the real vault-guard.py by file path (hyphenated, not importable normally)."""
    spec = importlib.util.spec_from_file_location("vault_guard_probe", GUARD_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_payload(file_path: str) -> str:
    return json.dumps(
        {
            "session_id": "probe",
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": file_path, "file_text": "# x"},
        }
    )


def _run_guard(file_path, guard_roots):
    """Invoke the REAL guard hook (subprocess) exactly as lore init wires it:
    LORE_VAULT_GUARD_ROOT = vaults_root + '\\n' + vaults_root/default."""
    env = dict(os.environ)
    env["LORE_VAULT_GUARD_ROOT"] = GUARD_ROOT_DELIM.join(str(r) for r in guard_roots)
    return subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input=_make_payload(str(file_path)),
        capture_output=True,
        text=True,
        env=env,
    )


class TestDefaultSymlinkExemptionCoverage:
    def test_inside_pointing_default_symlink_is_denied_today_and_exempt_pattern_matches(
        self, tmp_path
    ):
        """default -> a dir INSIDE vaults_root. Write through default/sites/x.html.

        Today (no exemption mechanism exists yet): the root match fires, so the
        write is denied. The planned exemption pattern ``<vaults_root>/*/sites``
        (matched on realpaths) WOULD match this realpath, correctly covering it.
        """
        vaults_root = tmp_path / "vaults"
        vaults_root.mkdir()
        real_default = vaults_root / "real-default-vault"
        real_default.mkdir()
        default_link = vaults_root / "default"
        default_link.symlink_to(real_default)

        target = default_link / "sites" / "x.html"
        # Mirrors _install_guardrail's guard_root_value: vaults_root AND
        # vaults_root/default as two separate roots.
        roots = [vaults_root, default_link]

        result = _run_guard(target, roots)
        assert result.returncode == 2, (
            "sanity check: today, with no exemption mechanism, a write through an "
            f"inside-pointing default vault must be denied by the root match; "
            f"stderr={result.stderr!r}"
        )

        guard = _load_guard_module()
        real_target = guard._real(str(target))
        exempt_pattern = guard._real(str(vaults_root)) + "/*/sites"

        assert fnmatch.fnmatch(os.path.dirname(real_target), exempt_pattern), (
            f"planned exemption pattern {exempt_pattern!r} does not match the "
            f"inside-pointing default's realpath dirname "
            f"{os.path.dirname(real_target)!r} — exemption design fails even the "
            "easy case"
        )

    def test_outside_pointing_default_symlink_is_denied_today_but_exempt_pattern_does_not_match(
        self, tmp_path
    ):
        """default -> a dir OUTSIDE vaults_root entirely. Write through default/sites/x.html.

        Contrary to the possibility raised in the unknown ("the deny itself
        never fires and exemption is moot"), the deny DOES fire: the guard is
        configured with vaults_root/default as an EXPLICIT second root (not
        merely covered via the vaults_root prefix), and the hook resolves that
        root's realpath on every call — so it always covers wherever the
        symlink currently points, inside or outside vaults_root.

        The planned exemption pattern is anchored at vaults_root
        (``<vaults_root>/*/sites``), so it does NOT match a realpath that has
        escaped vaults_root entirely. Net effect: a sites write through an
        outside-pointing default vault would be denied by the root match and
        NOT exempted — the opposite of the intended free-write behavior.
        """
        vaults_root = tmp_path / "vaults"
        vaults_root.mkdir()
        outside = tmp_path / "outside-adopted-vault"  # sibling of vaults_root, NOT under it
        outside.mkdir()
        default_link = vaults_root / "default"
        default_link.symlink_to(outside)

        target = default_link / "sites" / "x.html"
        roots = [vaults_root, default_link]

        result = _run_guard(target, roots)
        assert result.returncode == 2, (
            "the deny must fire for a write through an outside-pointing default "
            "symlink: vaults_root/default is an explicit guard root in its own "
            f"right, independent of the vaults_root prefix; stderr={result.stderr!r}"
        )

        guard = _load_guard_module()
        real_target = guard._real(str(target))
        exempt_pattern = guard._real(str(vaults_root)) + "/*/sites"

        assert not fnmatch.fnmatch(os.path.dirname(real_target), exempt_pattern), (
            f"planned exemption pattern {exempt_pattern!r} unexpectedly matched "
            f"an outside-pointing default's realpath dirname "
            f"{os.path.dirname(real_target)!r} — if this assertion fails, the "
            "exemption gap this test documents does not exist"
        )
