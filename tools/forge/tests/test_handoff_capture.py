"""Unit + integration tests for the handoff-capture helper.

The deterministic logic behind `/forge:handoff` lives in
`plugins/forge/scripts/handoff_capture.py` so it can be tested without driving a
live Claude Code session. Coverage:

  - git-state capture (synthetic git fixture): branch, ahead-count, dirty flag,
    merge-base-bounded log; graceful/empty on a non-git dir (no crash, no stderr
    leak).
  - lore 3-state detection: WORKING / ABSENT / BROKEN, plus the $LORE_VAULT-unset
    guard (unset -> ABSENT, never the ~/lore shadow default).
  - degraded-file write: out-of-repo `~/.forge/handoffs/<slug>.md` carrying the
    hints + captured git state.
  - cross-repo integration: run the REAL `lore handoff` against a synthetic
    fixture vault and assert the note flips to `shelved`.

All fixtures are synthetic — no real branch names, repo slugs, or machine paths.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "forge" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import handoff_capture as hc  # noqa: E402


# ---------------------------------------------------------------------------
# git fixtures
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> Path:
    """A git repo with a default branch + a feature branch one commit ahead."""
    repo = tmp_path / "alpha-widget"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "config", "user.email", "t@example.test")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed commit")
    _git(repo, "checkout", "-q", "-b", "feature-thing")
    (repo / "work.txt").write_text("work\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add the thing")
    return repo


# ---------------------------------------------------------------------------
# git-state capture
# ---------------------------------------------------------------------------

def test_capture_git_state_reports_branch_ahead_and_clean(synthetic_repo: Path):
    state = hc.capture_git_state(synthetic_repo, default_branch="trunk")
    assert state.is_git is True
    assert state.branch == "feature-thing"
    assert state.ahead_count == 1
    assert state.dirty is False
    assert any("add the thing" in line for line in state.commits)


def test_capture_git_state_flags_dirty_worktree(synthetic_repo: Path):
    (synthetic_repo / "work.txt").write_text("dirty edit\n")
    state = hc.capture_git_state(synthetic_repo, default_branch="trunk")
    assert state.dirty is True


def test_capture_git_state_bounds_log_via_merge_base(synthetic_repo: Path):
    """The commit list must be merge-base bounded — only commits ahead of the
    default branch, never the whole unbounded history."""
    state = hc.capture_git_state(synthetic_repo, default_branch="trunk")
    assert state.ahead_count == 1
    assert not any("seed commit" in line for line in state.commits)


def test_capture_git_state_falls_back_when_default_branch_missing(synthetic_repo: Path):
    """No such default branch -> bounded HEAD~N fallback, never unbounded."""
    state = hc.capture_git_state(
        synthetic_repo, default_branch="does-not-exist", fallback_n=5
    )
    assert state.is_git is True
    assert state.branch == "feature-thing"
    # fallback is bounded: at most fallback_n commits, no crash
    assert len(state.commits) <= 5


def test_capture_git_state_graceful_on_non_git_dir(tmp_path: Path, capfd):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    state = hc.capture_git_state(plain, default_branch="trunk")
    assert state.is_git is False
    assert state.branch == ""
    assert state.ahead_count == 0
    assert state.commits == []
    # no stderr leak from the guarded git probes
    err = capfd.readouterr().err
    assert err == ""


# ---------------------------------------------------------------------------
# lore 3-state detection
# ---------------------------------------------------------------------------

def test_lore_state_absent_when_command_missing(monkeypatch):
    monkeypatch.setattr(hc.shutil, "which", lambda _: None)
    monkeypatch.setenv("LORE_VAULT", "/some/vault")
    assert hc.lore_state() == hc.LoreState.ABSENT


def test_lore_state_absent_when_vault_unset(monkeypatch):
    """$LORE_VAULT unset -> ABSENT, never the ~/lore shadow default."""
    monkeypatch.setattr(hc.shutil, "which", lambda _: "/usr/local/bin/lore")
    monkeypatch.delenv("LORE_VAULT", raising=False)
    assert hc.lore_state() == hc.LoreState.ABSENT


def test_lore_state_broken_when_stats_nonzero(monkeypatch):
    monkeypatch.setattr(hc.shutil, "which", lambda _: "/usr/local/bin/lore")
    monkeypatch.setenv("LORE_VAULT", "/some/vault")
    monkeypatch.setattr(hc, "_lore_stats_ok", lambda: False)
    assert hc.lore_state() == hc.LoreState.BROKEN


def test_lore_state_working_when_all_three_pass(monkeypatch):
    monkeypatch.setattr(hc.shutil, "which", lambda _: "/usr/local/bin/lore")
    monkeypatch.setenv("LORE_VAULT", "/some/vault")
    monkeypatch.setattr(hc, "_lore_stats_ok", lambda: True)
    assert hc.lore_state() == hc.LoreState.WORKING


# ---------------------------------------------------------------------------
# degraded-file write (out of repo)
# ---------------------------------------------------------------------------

def test_write_degraded_handoff_lands_out_of_repo(tmp_path: Path, synthetic_repo: Path):
    handoff_dir = tmp_path / "fake-home" / ".forge" / "handoffs"
    state = hc.capture_git_state(synthetic_repo, default_branch="trunk")
    out = hc.write_degraded_handoff(
        handoff_dir, "alpha-widget", "Next: finish the widget", state
    )
    assert out.exists()
    assert out == handoff_dir / "alpha-widget.md"
    # out of any repo — not under the captured repo's tree
    assert synthetic_repo not in out.parents
    body = out.read_text()
    assert "Next: finish the widget" in body
    assert "feature-thing" in body  # captured git state embedded


def test_write_degraded_handoff_creates_dir_when_missing(tmp_path: Path):
    handoff_dir = tmp_path / "fresh" / ".forge" / "handoffs"
    assert not handoff_dir.exists()
    state = hc.GitState(is_git=False, branch="", ahead_count=0, dirty=False, commits=[])
    out = hc.write_degraded_handoff(handoff_dir, "beta-slug", "hints here", state)
    assert out.exists()
    assert handoff_dir.is_dir()


# ---------------------------------------------------------------------------
# cross-repo integration: drive the REAL lore handoff against a fixture vault
# ---------------------------------------------------------------------------

def _resolve_lore_cli() -> list[str] | None:
    """Locate a runnable lore CLI without hardcoding a machine path.

    Order: `lore` on PATH (preferred), else the local lore plugin's bin under
    $HOME. Returns the argv prefix, or None when neither is available.
    """
    import shutil as _shutil

    on_path = _shutil.which("lore")
    if on_path:
        return [on_path]
    candidate = Path(os.environ["HOME"]) / "code" / "lore" / "plugins" / "lore" / "bin" / "lore"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return [str(candidate)]
    return None


@pytest.fixture
def fixture_vault(tmp_path: Path) -> Path:
    """A minimal git-backed lore vault with one active session note."""
    vault = tmp_path / "synthetic-vault"
    (vault / "sessions").mkdir(parents=True)
    note = vault / "sessions" / "2026-01-02-0900-alpha-widget.md"
    note.write_text(
        "---\n"
        "type: session\n"
        "status: active\n"
        "started: 2026-01-02T09:00:00Z\n"
        "---\n\n"
        "# Session\n\n"
        "## Pickup hints\n\n"
    )
    _git(vault, "init", "-q")
    _git(vault, "config", "user.email", "t@example.test")
    _git(vault, "config", "user.name", "Test")
    _git(vault, "config", "commit.gpgsign", "false")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", "seed vault")
    return vault


def test_real_lore_handoff_with_pickup_hints_file(fixture_vault: Path, tmp_path: Path):
    """Integration: lore handoff --pickup-hints-file writes hints AND flips to shelved.

    This is the actual sequence the skill's working path drives: compose hints
    into a temp file, then call lore handoff --pickup-hints-file <tmp> in one
    atomic call.  The note must be shelved AND carry the ## Pickup hints content.
    """
    cli = _resolve_lore_cli()
    if cli is None:
        pytest.skip("lore CLI not available (not on PATH, no local ~/code/lore bin)")

    note = fixture_vault / "sessions" / "2026-01-02-0900-alpha-widget.md"
    cwd = fixture_vault / "work" / "alpha-widget"
    cwd.mkdir(parents=True)

    hints_file = tmp_path / "pickup_hints.md"
    hints_file.write_text(
        "Next: resume widget implementation\nBlocker: waiting for dependency upgrade\n"
    )

    env = dict(os.environ)
    env["LORE_VAULT"] = str(fixture_vault)

    proc = subprocess.run(
        [*cli, "handoff", "--pickup-hints-file", str(hints_file)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"lore handoff --pickup-hints-file failed: {proc.stderr}"
    assert "Shelved" in proc.stdout

    body = note.read_text()
    # Note flips to shelved.
    assert "status: shelved" in body
    # Pickup hints section carries the composed content.
    assert "## Pickup hints" in body
    assert "Next: resume widget implementation" in body
    assert "Blocker: waiting for dependency upgrade" in body


# ---------------------------------------------------------------------------
# slug containment guard (Security C1 defense-in-depth — review Minor #3)
# ---------------------------------------------------------------------------

def test_write_degraded_handoff_slug_stays_contained_under_handoff_dir(tmp_path: Path):
    """A slug with ../path-escape stays contained under handoff_dir.

    Defense-in-depth: the slug is sanitized so a ../../escape attempt cannot
    write outside ~/.forge/handoffs/ even if a malformed slug reaches the helper.
    """
    handoff_dir = tmp_path / "forge-handoffs"
    state = hc.GitState(is_git=False, branch="", ahead_count=0, dirty=False, commits=[])

    # A slug with path traversal — must NOT escape handoff_dir.
    out = hc.write_degraded_handoff(handoff_dir, "../../escape", "hints", state)

    # The output path must remain inside handoff_dir.
    assert out.resolve().is_relative_to(handoff_dir.resolve()), (
        f"output {out} escaped handoff_dir {handoff_dir}"
    )
    # The file must still be created (not silently dropped).
    assert out.exists()
