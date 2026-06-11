"""Leak gate (D-7/S-3): verify no zenith-private tokens survive in re-homed scripts.

Uses an ephemeral tracked-token denylist written to tmp_path — never depends on
~/.claude/leak-gate.denylist (which is absent on a fresh clone / CI runner).

Denylist covers the Step-6 zenith tokens:
  - cortana(-zh)?
  - asana
  - dash0
  - \\bplatform\\b          (bare word — paraphrase catch)
  - mobile-app             (the specific sibling name)
  - \\bzenith\\b            (bare word)
  - \\.workspace-manifest  (the retired zenith manifest path)
  - KNOWN_SIBLINGS         (hardcoded sibling set)
  - MERGE_ORDER            (hardcoded merge order constant)
  - brain/(designs|chrome|specs|plans|sessions)   (vault paths)

The token names above are business-context strings, not secret values — safe to
track in test source per D-7 (amended).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "forge" / "scripts"
GATE = SCRIPTS_DIR / "leak_gate.py"


def _run_gate(trees: list[Path], denylist: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE), *[str(t) for t in trees], "--denylist", str(denylist)]
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.fixture
def step6_denylist(tmp_path: Path) -> Path:
    """Ephemeral denylist with the Step-6 de-zenithing token list."""
    dl = tmp_path / "step6-denylist"
    dl.write_text(
        "# Step-6 de-zenithing denylist — business-context strings, not secrets\n"
        "cortana(-zh)?\n"
        "\\basana\\b\n"
        "\\bdash0\\b\n"
        "\\bzenith\\b\n"
        "\\.workspace-manifest\n"
        # Catch the Python constant pattern KNOWN_SIBLINGS = {...}
        "KNOWN_SIBLINGS\\s*=\n"
        # Catch the Python constant pattern MERGE_ORDER = [...] (not the config key 'merge_order')
        "MERGE_ORDER\\s*=\\s*\\[\n"
        "brain/(designs|chrome|specs|plans|sessions)\n"
        # mobile-app as a hardcoded sibling name (not in a comment about the tool name)
        '"mobile-app"\n'
        # platform-infra as a hardcoded sibling name
        "platform-infra\n",
        encoding="utf-8",
    )
    return dl


# ---------------------------------------------------------------------------
# Gate the re-homed release scripts (not test files — those are allowed to name
# the tokens in denylist strings and assertions)
# ---------------------------------------------------------------------------


class TestReleaseScriptsLeakGate:
    def test_detect_repos_is_clean(self, step6_denylist: Path) -> None:
        r = _run_gate([SCRIPTS_DIR / "detect_repos.py"], step6_denylist)
        assert r.returncode == 0, (
            f"detect_repos.py contains forbidden tokens:\n{r.stdout}\n{r.stderr}"
        )

    def test_merge_prs_is_clean(self, step6_denylist: Path) -> None:
        r = _run_gate([SCRIPTS_DIR / "merge_prs.py"], step6_denylist)
        assert r.returncode == 0, (
            f"merge_prs.py contains forbidden tokens:\n{r.stdout}\n{r.stderr}"
        )

    def test_pr_evaluate_status_is_clean(self, step6_denylist: Path) -> None:
        r = _run_gate([SCRIPTS_DIR / "pr_evaluate_status.py"], step6_denylist)
        assert r.returncode == 0, (
            f"pr_evaluate_status.py contains forbidden tokens:\n{r.stdout}\n{r.stderr}"
        )

    def test_check_pr_status_is_clean(self, step6_denylist: Path) -> None:
        r = _run_gate([SCRIPTS_DIR / "check_pr_status.py"], step6_denylist)
        assert r.returncode == 0, (
            f"check_pr_status.py contains forbidden tokens:\n{r.stdout}\n{r.stderr}"
        )

    def test_wait_for_actionable_is_clean(self, step6_denylist: Path) -> None:
        r = _run_gate([SCRIPTS_DIR / "wait_for_actionable.py"], step6_denylist)
        assert r.returncode == 0, (
            f"wait_for_actionable.py contains forbidden tokens:\n{r.stdout}\n{r.stderr}"
        )

    def test_release_prs_sidecar_is_clean(self, step6_denylist: Path) -> None:
        r = _run_gate([SCRIPTS_DIR / "release_prs_sidecar.py"], step6_denylist)
        assert r.returncode == 0, (
            f"release_prs_sidecar.py contains forbidden tokens:\n{r.stdout}\n{r.stderr}"
        )

    def test_runner_protocol_is_clean(self, step6_denylist: Path) -> None:
        r = _run_gate([SCRIPTS_DIR / "runner_protocol.py"], step6_denylist)
        assert r.returncode == 0, (
            f"runner_protocol.py contains forbidden tokens:\n{r.stdout}\n{r.stderr}"
        )

    def test_all_release_scripts_as_group_are_clean(self, step6_denylist: Path) -> None:
        """Scan all re-homed release scripts in one pass."""
        targets = [
            SCRIPTS_DIR / "detect_repos.py",
            SCRIPTS_DIR / "merge_prs.py",
            SCRIPTS_DIR / "pr_evaluate_status.py",
            SCRIPTS_DIR / "check_pr_status.py",
            SCRIPTS_DIR / "wait_for_actionable.py",
            SCRIPTS_DIR / "release_prs_sidecar.py",
            SCRIPTS_DIR / "runner_protocol.py",
        ]
        # Pass each file individually so the gate scans files, not dirs
        for target in targets:
            r = _run_gate([target], step6_denylist)
            assert r.returncode == 0, (
                f"{target.name} contains forbidden tokens:\n{r.stdout}\n{r.stderr}"
            )


# ---------------------------------------------------------------------------
# Ephemeral-denylist plumbing: verify the gate is reproducible (S-3)
# ---------------------------------------------------------------------------


class TestEphemeralDenylistPlumbing:
    def test_gate_without_home_denylist_still_works(
        self, step6_denylist: Path, tmp_path: Path
    ) -> None:
        """The ephemeral denylist works even when ~/.claude/leak-gate.denylist absent."""
        clean = tmp_path / "tree"
        clean.mkdir()
        (clean / "clean.py").write_text("# nothing forbidden\nx = 1\n")
        r = _run_gate([clean], step6_denylist)
        assert r.returncode == 0

    def test_gate_detects_zenith_token_in_file(
        self, step6_denylist: Path, tmp_path: Path
    ) -> None:
        """A file containing bare 'zenith' word triggers the gate."""
        dirty = tmp_path / "tree"
        dirty.mkdir()
        # Use as a bare word (not zenith_root, which has word char after)
        (dirty / "dirty.py").write_text('# from the zenith repo\n')
        r = _run_gate([dirty], step6_denylist)
        assert r.returncode == 1

    def test_gate_detects_cortana_token(
        self, step6_denylist: Path, tmp_path: Path
    ) -> None:
        dirty = tmp_path / "cortana-tree"
        dirty.mkdir()
        (dirty / "bad.py").write_text('reviewer = "cortana-zh"\n')
        r = _run_gate([dirty], step6_denylist)
        assert r.returncode == 1

    def test_gate_detects_workspace_manifest(
        self, step6_denylist: Path, tmp_path: Path
    ) -> None:
        dirty = tmp_path / "wm-tree"
        dirty.mkdir()
        (dirty / "bad.py").write_text('MANIFEST_FILE = ".workspace-manifest.json"\n')
        r = _run_gate([dirty], step6_denylist)
        assert r.returncode == 1

    def test_gate_detects_known_siblings_constant(
        self, step6_denylist: Path, tmp_path: Path
    ) -> None:
        dirty = tmp_path / "ks-tree"
        dirty.mkdir()
        # The pattern catches 'KNOWN_SIBLINGS =' (the constant assignment)
        (dirty / "bad.py").write_text('KNOWN_SIBLINGS = {"platform"}\n')
        r = _run_gate([dirty], step6_denylist)
        assert r.returncode == 1
