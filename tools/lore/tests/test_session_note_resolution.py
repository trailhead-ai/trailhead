"""Session-note resolution: by session-id (exact, cwd-independent) and by a
robust worktree-name detection that matches how the note filename is created.

These cover the resolver in `vault.py` plus the `lore session-note` CLI
subcommand that fronts it. The motivating bug: callers degraded to a fuzzy
worktree+mtime guess because the session-id was never consulted.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


def load_script(name: str):
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    for cached in (name, "vault", "frontmatter", "status_validator", "sessions", "config"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cli(args, env=None, cwd=None):
    full_env = dict(os.environ)
    # Drop session-id env that the host shell may carry, so tests are hermetic.
    for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_PROJECT_DIR"):
        full_env.pop(k, None)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, cwd=cwd,
    )


def _write_session_record(session_dir: Path, session_id: str, *, extra: str = "") -> Path:
    """Write a singular session record: session/<id>.md with # session: <id> header.

    This is the Slice-1 shape: a first-class record under session/ (singular),
    identified by its stem and confirmed by the body header.
    """
    p = session_dir / f"{session_id}.md"
    p.write_text(
        f"# session: {session_id}\n\n{extra}"
        # A body mention of a different id must NOT cause a false match.
        f"Note: decoy-{session_id} referenced here.\n"
    )
    return p


# ---------------------------------------------------------------------------
# find_session_note_by_session_id
# ---------------------------------------------------------------------------

def test_by_session_id_matches_singular_record(tmp_path):
    """By-id resolver finds the singular session/<id>.md record by stem + header."""
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    _write_session_record(sd, "aaa")
    want = _write_session_record(sd, "bbb")

    v = load_script("vault")
    assert v.find_session_note_by_session_id(vault, "bbb") == want


def test_by_session_id_matches_frontmatter(tmp_path):
    """By-id resolver finds the correct singular record among multiple."""
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    _write_session_record(sd, "aaa")
    want = _write_session_record(sd, "bbb")

    v = load_script("vault")
    assert v.find_session_note_by_session_id(vault, "bbb") == want


def test_by_session_id_matches_bucketed_note(tmp_path):
    """By-id resolution finds a singular session record (no bucket nesting needed
    for the session/ dir — records are flat under session/)."""
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    _write_session_record(sd, "flat")
    want = _write_session_record(sd, "bucketed")

    v = load_script("vault")
    assert v.find_session_note_by_session_id(vault, "bucketed") == want


def test_by_session_id_ignores_body_mentions(tmp_path):
    """A decoy id in the body must not match — stem + header confirmation only."""
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    _write_session_record(sd, "real")

    v = load_script("vault")
    # decoy-real is mentioned in the body but is not the stem; must not match.
    assert v.find_session_note_by_session_id(vault, "decoy-real") is None


def test_by_session_id_empty_returns_none(tmp_path):
    vault = tmp_path / "v"
    (vault / "session").mkdir(parents=True)
    v = load_script("vault")
    assert v.find_session_note_by_session_id(vault, "") is None


def test_by_session_id_no_sessions_dir_returns_none(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    v = load_script("vault")
    assert v.find_session_note_by_session_id(vault, "aaa") is None


# ---------------------------------------------------------------------------
# find_session_note_by_session_id: GUID session records (singular shape)
# ---------------------------------------------------------------------------

# A canonical GUID is what session_store names the capture key.
_GUID = "11111111-2222-4333-8444-555555555555"
_OTHER_GUID = "99999999-8888-4777-8666-555555555555"


def test_by_session_id_matches_body_only_guid_file(tmp_path):
    """A GUID-keyed session/<GUID>.md record is found by the id resolver."""
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    want = _write_session_record(
        sd, _GUID, extra="- candidate ... kind=lesson phase=Build\n"
    )

    v = load_script("vault")
    assert v.find_session_note_by_session_id(vault, _GUID) == want


def test_by_session_id_body_only_requires_stem_match(tmp_path):
    """A different GUID's session record must not match."""
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    _write_session_record(sd, _OTHER_GUID)

    v = load_script("vault")
    assert v.find_session_note_by_session_id(vault, _GUID) is None


def test_by_session_id_frontmatter_still_wins_over_body_only(tmp_path):
    """A session record keyed by a non-GUID id is also found correctly."""
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    want = _write_session_record(sd, "legacy")

    v = load_script("vault")
    assert v.find_session_note_by_session_id(vault, "legacy") == want


# ---------------------------------------------------------------------------
# detect_worktree_name
# ---------------------------------------------------------------------------

def test_detect_prefers_claude_project_dir(monkeypatch, tmp_path):
    """CLAUDE_PROJECT_DIR basename wins — it is what named the note."""
    monkeypatch.setenv(
        "CLAUDE_PROJECT_DIR",
        "/Users/x/code/orchestrator/.claude/worktrees/my-feature",
    )
    v = load_script("vault")
    # cwd is somewhere unrelated; env must still win.
    assert v.detect_worktree_name(cwd=tmp_path) == "my-feature"


def test_detect_walks_worktrees_segment(monkeypatch, tmp_path):
    """With no CLAUDE_PROJECT_DIR, a `.claude/worktrees/<name>/` segment in a
    sibling-repo or subdir cwd resolves to <name>."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    v = load_script("vault")
    cwd = Path("/Users/x/code/platform/.claude/worktrees/my-feature/apps/platform")
    assert v.detect_worktree_name(cwd=cwd) == "my-feature"


def test_detect_falls_back_to_cwd_basename(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    plain = tmp_path / "just-a-dir"
    plain.mkdir()
    v = load_script("vault")
    assert v.detect_worktree_name(cwd=plain) == "just-a-dir"


def test_detect_uses_git_toplevel_basename(monkeypatch, tmp_path):
    """A subdir of a git repo (not under .claude/worktrees) resolves to the
    repo toplevel basename, not the subdir name."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = tmp_path / "myrepo"
    (repo / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    v = load_script("vault")
    assert v.detect_worktree_name(cwd=repo / "sub") == "myrepo"


# ---------------------------------------------------------------------------
# resolve_session_note (combinator)
# ---------------------------------------------------------------------------

def test_resolve_session_id_beats_worktree_fallback(tmp_path):
    """The exact session-id match wins over the worktree fallback.

    Both session/<mine>.md and session/<feat>.md exist. The resolver must
    return the id match, not the worktree fallback.
    """
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    mine = _write_session_record(sd, "mine")
    _write_session_record(sd, "feat")

    v = load_script("vault")
    got = v.resolve_session_note(vault, session_id="mine", worktree_name="feat")
    assert got == mine


def test_resolve_falls_back_to_worktree_when_id_unmatched(tmp_path):
    """When session_id finds no record, the worktree fallback is tried."""
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    feat = _write_session_record(sd, "feat")

    v = load_script("vault")
    # session id 'absent' matches nothing → worktree record for 'feat'.
    got = v.resolve_session_note(vault, session_id="absent", worktree_name="feat")
    assert got == feat


# ---------------------------------------------------------------------------
# `lore session-note` CLI
# ---------------------------------------------------------------------------

def test_cli_resolves_via_claude_code_session_id_env(tmp_path):
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    _write_session_record(sd, "old")
    want = _write_session_record(sd, "live")

    r = run_cli(
        ["session-note"],
        env={"LORE_VAULT": str(vault), "CLAUDE_CODE_SESSION_ID": "live"},
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"session/{want.name}"


def test_cli_session_id_flag_overrides_env(tmp_path):
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    want = _write_session_record(sd, "flagged")
    _write_session_record(sd, "envid")

    r = run_cli(
        ["session-note", "--session-id", "flagged"],
        env={"LORE_VAULT": str(vault), "CLAUDE_CODE_SESSION_ID": "envid"},
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"session/{want.name}"


def test_cli_worktree_flag_fallback(tmp_path):
    vault = tmp_path / "v"
    sd = vault / "session"
    sd.mkdir(parents=True)
    _write_session_record(sd, "alpha")
    want = _write_session_record(sd, "beta")

    # No session id at all → resolve by explicit --worktree.
    r = run_cli(
        ["session-note", "--worktree", "beta"],
        env={"LORE_VAULT": str(vault)},
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"session/{want.name}"


def test_cli_miss_exits_1_with_diagnostic(tmp_path):
    vault = tmp_path / "v"
    (vault / "session").mkdir(parents=True)

    r = run_cli(
        ["session-note", "--session-id", "nope", "--worktree", "ghost"],
        env={"LORE_VAULT": str(vault)},
    )
    assert r.returncode == 1
    assert not r.stdout.strip()
    # Diagnostic explains what was tried, so callers don't run exploratory ls.
    assert "session-note" in r.stderr
    assert "nope" in r.stderr
    assert "ghost" in r.stderr

