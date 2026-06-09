"""Slice 4 tests: session lifecycle + harvest hooks.

Covers (TDD — written before the hooks):
- sessions.ensure_session_note: creates a note with valid `session` frontmatter
  and the five required body headings; resumes a note modified inside the window.
- session-context.py (SessionStart): creates a session note, emits a baseline
  vault index; resolves the vault via $LORE_VAULT; emits the footgun warning when
  $LORE_VAULT is unset and ~/lore is absent; never raises (emits {} on error).
- finalize-session-note.py (WorktreeRemove): sets status: complete + ended: and
  commits ONLY when the git-toplevel assertion passes; skips cleanly otherwise;
  an atomic write killed mid-write leaves the original note intact.
- harvest-candidates.py: routes a `## Harvest candidates` block to
  harvest-pending.md; no-op when the block is absent.
- permission-log.py: appends an entry.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def load_hook(name: str):
    """Load a hook module from hooks/ by stem, freshly each call."""
    for d in (str(HOOKS_DIR), str(SCRIPTS_DIR)):
        if d not in sys.path:
            sys.path.insert(0, d)
    for cached in (name, "sessions", "vault", "frontmatter", "status_validator"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_sessions():
    for d in (str(SCRIPTS_DIR),):
        if d not in sys.path:
            sys.path.insert(0, d)
    for cached in ("sessions", "vault", "frontmatter", "status_validator"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location("sessions", SCRIPTS_DIR / "sessions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_script(name: str):
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "sessions").mkdir(parents=True)
    return vault


def _git_vault(tmp_path: Path) -> Path:
    """A vault that is its own git repo (toplevel == vault)."""
    vault = _make_vault(tmp_path)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@e.st"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Tester"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "commit.gpgsign", "false"],
                   check=True, capture_output=True)
    return vault


NOW_ISO = "2026-06-02T12:00:00Z"
NOW_HUMAN = "2026-06-02 12:00 UTC"


# ---------------------------------------------------------------------------
# sessions.ensure_session_note
# ---------------------------------------------------------------------------

class TestEnsureSessionNote:
    def test_creates_note_with_session_type(self, tmp_path):
        vault = _make_vault(tmp_path)
        s = load_sessions()
        note, created = s.ensure_session_note(
            vault=vault, worktree_name="my-feature", branch="feat/x",
            project="my-project", now_iso=NOW_ISO, now_human=NOW_HUMAN,
            session_id="sid-1",
        )
        assert created is True
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["type"] == "session"
        assert fm["status"] == "active"
        assert fm["project"] == "my-project"
        assert fm["worktree"] == "my-feature"

    def test_status_passes_validator_for_session(self, tmp_path):
        vault = _make_vault(tmp_path)
        s = load_sessions()
        note, _ = s.ensure_session_note(
            vault=vault, worktree_name="wt", branch="b", project="p",
            now_iso=NOW_ISO, now_human=NOW_HUMAN, session_id="sid",
        )
        fm = load_script("frontmatter").parse_frontmatter(note)
        sv = load_script("status_validator")
        assert sv.is_valid_status(fm["type"], fm["status"])

    def test_body_has_five_required_headings(self, tmp_path):
        vault = _make_vault(tmp_path)
        s = load_sessions()
        note, _ = s.ensure_session_note(
            vault=vault, worktree_name="wt", branch="b", project="p",
            now_iso=NOW_ISO, now_human=NOW_HUMAN, session_id="sid",
        )
        text = note.read_text()
        for heading in ("## What we did", "## Decided", "## Deferred",
                        "## Learned", "## Open questions"):
            assert heading in text, f"missing {heading}"

    def test_filename_format_date_time_worktree(self, tmp_path):
        vault = _make_vault(tmp_path)
        s = load_sessions()
        note, _ = s.ensure_session_note(
            vault=vault, worktree_name="cool-wt", branch="b", project="p",
            now_iso=NOW_ISO, now_human=NOW_HUMAN, session_id="sid",
        )
        assert note.name == "2026-06-02-1200-cool-wt.md"

    def test_subsystems_inline_empty(self, tmp_path):
        vault = _make_vault(tmp_path)
        s = load_sessions()
        note, _ = s.ensure_session_note(
            vault=vault, worktree_name="wt", branch="b", project="p",
            now_iso=NOW_ISO, now_human=NOW_HUMAN, session_id="sid",
        )
        assert "subsystems: []" in note.read_text()

    def test_resumes_recent_note_for_same_worktree(self, tmp_path):
        vault = _make_vault(tmp_path)
        s = load_sessions()
        note1, c1 = s.ensure_session_note(
            vault=vault, worktree_name="wt", branch="b", project="p",
            now_iso=NOW_ISO, now_human=NOW_HUMAN, session_id="sid",
        )
        # Second call with a later timestamp but within the resume window:
        note2, c2 = s.ensure_session_note(
            vault=vault, worktree_name="wt", branch="b", project="p",
            now_iso="2026-06-02T12:05:00Z", now_human="2026-06-02 12:05 UTC",
            session_id="sid",
        )
        assert c1 is True
        assert c2 is False
        assert note1 == note2

    def test_creates_fresh_note_when_outside_window(self, tmp_path):
        # A *different* session arriving outside the resume window gets a fresh
        # note. (Same-session resume is covered separately — session_id is the
        # primary resume signal and overrides the window.)
        vault = _make_vault(tmp_path)
        s = load_sessions()
        note1, _ = s.ensure_session_note(
            vault=vault, worktree_name="wt", branch="b", project="p",
            now_iso=NOW_ISO, now_human=NOW_HUMAN, session_id="sid-old",
        )
        # Backdate note1 well outside the resume window.
        old = time.time() - (s.RESUME_WINDOW_SECONDS + 60)
        os.utime(note1, (old, old))
        note2, c2 = s.ensure_session_note(
            vault=vault, worktree_name="wt", branch="b", project="p",
            now_iso="2026-06-02T13:00:00Z", now_human="2026-06-02 13:00 UTC",
            session_id="sid-new",
        )
        assert c2 is True
        assert note1 != note2

    def test_resumes_same_session_id_outside_window(self, tmp_path):
        # Regression: `camp` resumes via `claude -r <slug>`, preserving the
        # Claude session_id. Resuming hours later (well past the mtime window)
        # must reuse the existing note, not fork a duplicate.
        vault = _make_vault(tmp_path)
        s = load_sessions()
        note1, c1 = s.ensure_session_note(
            vault=vault, worktree_name="wt", branch="b", project="p",
            now_iso=NOW_ISO, now_human=NOW_HUMAN, session_id="sid",
        )
        old = time.time() - (s.RESUME_WINDOW_SECONDS + 3600)
        os.utime(note1, (old, old))
        note2, c2 = s.ensure_session_note(
            vault=vault, worktree_name="wt", branch="b", project="p",
            now_iso="2026-06-02T16:00:00Z", now_human="2026-06-02 16:00 UTC",
            session_id="sid",
        )
        assert c1 is True
        assert c2 is False
        assert note1 == note2

    def test_fresh_note_when_matching_session_is_terminal(self, tmp_path):
        # An explicit finish/shelve is respected: even if the same session_id
        # comes back, a terminal note is left alone and a fresh note is created.
        vault = _make_vault(tmp_path)
        s = load_sessions()
        note1, _ = s.ensure_session_note(
            vault=vault, worktree_name="wt", branch="b", project="p",
            now_iso=NOW_ISO, now_human=NOW_HUMAN, session_id="sid",
        )
        s.finalize_note(note1, ended_iso="2026-06-02T12:30:00Z", status="complete")
        old = time.time() - (s.RESUME_WINDOW_SECONDS + 60)
        os.utime(note1, (old, old))
        note2, c2 = s.ensure_session_note(
            vault=vault, worktree_name="wt", branch="b", project="p",
            now_iso="2026-06-02T16:00:00Z", now_human="2026-06-02 16:00 UTC",
            session_id="sid",
        )
        assert c2 is True
        assert note1 != note2

    def test_other_worktree_note_not_resumed(self, tmp_path):
        vault = _make_vault(tmp_path)
        s = load_sessions()
        s.ensure_session_note(
            vault=vault, worktree_name="alpha", branch="b", project="p",
            now_iso=NOW_ISO, now_human=NOW_HUMAN, session_id="sid",
        )
        note2, c2 = s.ensure_session_note(
            vault=vault, worktree_name="beta", branch="b", project="p",
            now_iso="2026-06-02T12:02:00Z", now_human="2026-06-02 12:02 UTC",
            session_id="sid",
        )
        assert c2 is True
        assert note2.name.endswith("-beta.md")


# ---------------------------------------------------------------------------
# session-context.py (SessionStart)
# ---------------------------------------------------------------------------

def _run_session_context(stdin_payload: dict, env: dict, cwd: Path):
    """Run session-context.py main() in-process with patched stdin/stdout/env."""
    mod = load_hook("session-context")
    out = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
            with mock.patch("sys.stdout", out):
                with mock.patch.object(os, "getcwd", return_value=str(cwd)):
                    mod.main()
    return out.getvalue()


class TestSessionContext:
    def test_creates_session_note_and_emits_index(self, tmp_path):
        vault = _make_vault(tmp_path)
        cwd = tmp_path / "my-worktree"
        cwd.mkdir()
        env = {"LORE_VAULT": str(vault), "PATH": os.environ.get("PATH", "")}
        out = _run_session_context({"session_id": "abc"}, env, cwd)
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        # A session note was created in its YYYY-MM month bucket.
        notes = list((vault / "sessions").glob("*/*.md"))
        assert len(notes) == 1
        assert notes[0].name.endswith("-my-worktree.md")
        fm = load_script("frontmatter").parse_frontmatter(notes[0])
        assert fm["type"] == "session"
        sv = load_script("status_validator")
        assert sv.is_valid_status(fm["type"], fm["status"])
        # Index references the session note and lists lore commands
        assert "/lore:defer" in ctx
        assert notes[0].name in ctx

    def test_resolves_vault_via_lore_vault(self, tmp_path):
        vault = _make_vault(tmp_path)
        cwd = tmp_path / "wt"
        cwd.mkdir()
        env = {"LORE_VAULT": str(vault), "PATH": os.environ.get("PATH", "")}
        _run_session_context({"session_id": "x"}, env, cwd)
        # The note landed in the $LORE_VAULT vault (its month bucket), not elsewhere
        assert list((vault / "sessions").glob("*/*.md"))

    def test_footgun_warning_when_unset_and_no_default(self, tmp_path):
        """$LORE_VAULT unset AND ~/lore absent → visible warning in context."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        cwd = tmp_path / "wt"
        cwd.mkdir()
        env = {"HOME": str(fake_home), "PATH": os.environ.get("PATH", "")}
        out = _run_session_context({"session_id": "x"}, env, cwd)
        data = json.loads(out)
        ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "LORE_VAULT unset" in ctx

    def test_no_warning_when_lore_vault_set(self, tmp_path):
        vault = _make_vault(tmp_path)
        cwd = tmp_path / "wt"
        cwd.mkdir()
        env = {"LORE_VAULT": str(vault), "PATH": os.environ.get("PATH", "")}
        out = _run_session_context({"session_id": "x"}, env, cwd)
        assert "LORE_VAULT unset" not in out

    def test_emits_empty_json_on_error(self, tmp_path):
        """Broken stdin must not raise — emit valid JSON dict."""
        mod = load_hook("session-context")
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("NOT JSON {{{")):
            with mock.patch("sys.stdout", out):
                mod.main()
        data = json.loads(out.getvalue())
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# finalize-session-note.py (WorktreeRemove)
# ---------------------------------------------------------------------------

def _seed_session_note(vault: Path, worktree: str = "wt") -> Path:
    s = load_sessions()
    note, _ = s.ensure_session_note(
        vault=vault, worktree_name=worktree, branch="b", project="p",
        now_iso=NOW_ISO, now_human=NOW_HUMAN, session_id="sid",
    )
    # Add real body content so it isn't treated as an empty skeleton (if such
    # logic exists) and to make a meaningful finalize.
    note.write_text(note.read_text() + "\nDid real work here.\n")
    return note


def _run_finalize(payload: dict, vault: Path):
    mod = load_hook("finalize-session-note")
    out = io.StringIO()
    env = {"LORE_VAULT": str(vault), "PATH": os.environ.get("PATH", ""),
           "HOME": os.environ.get("HOME", "")}
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with mock.patch("sys.stdout", out):
                mod.main()
    return out.getvalue(), mod


class TestFinalize:
    def test_sets_status_complete_and_ended(self, tmp_path):
        vault = _git_vault(tmp_path)
        note = _seed_session_note(vault)
        _run_finalize({"worktree": "wt"}, vault)
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "complete"
        assert fm["ended"]
        assert fm["ended"] != ""

    def test_commits_when_toplevel_matches(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_session_note(vault)
        _run_finalize({"worktree": "wt"}, vault)
        log = subprocess.run(
            ["git", "-C", str(vault), "log", "--oneline"],
            capture_output=True, text=True,
        )
        assert log.returncode == 0
        assert log.stdout.strip(), "expected a commit in the vault"

    def test_skips_commit_when_vault_not_git_toplevel(self, tmp_path):
        """Vault not a git repo → no commit, clean skip (logged notice)."""
        vault = _make_vault(tmp_path)  # not a git repo
        note = _seed_session_note(vault)
        out, _ = _run_finalize({"worktree": "wt"}, vault)
        # status still updated (file write is independent of commit)
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "complete"
        # No git repo was created/committed in the vault
        assert not (vault / ".git").exists()

    def test_skips_commit_when_toplevel_is_parent(self, tmp_path):
        """$LORE_VAULT points at a subdir of a git repo → toplevel != vault →
        skip the commit rather than operate on the parent tree."""
        outer = tmp_path / "outer"
        outer.mkdir()
        subprocess.run(["git", "init", str(outer)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(outer), "config", "user.email", "t@e.st"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(outer), "config", "user.name", "T"],
                       check=True, capture_output=True)
        vault = outer / "vault"
        (vault / "sessions").mkdir(parents=True)
        note = _seed_session_note(vault)
        _run_finalize({"worktree": "wt"}, vault)
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "complete"
        # The outer repo must have NO commits (we refused to operate on it)
        log = subprocess.run(
            ["git", "-C", str(outer), "log", "--oneline"],
            capture_output=True, text=True,
        )
        assert log.returncode != 0 or not log.stdout.strip()

    def test_mid_write_crash_leaves_original_intact(self, tmp_path):
        """If the finalize write fails after the temp file is written, the
        ORIGINAL note must remain intact (atomic temp + os.replace)."""
        vault = _git_vault(tmp_path)
        note = _seed_session_note(vault)
        original = note.read_text()
        mod = load_hook("finalize-session-note")
        # Patch os.replace to raise — simulates a kill after the temp write
        # but before the atomic swap completes.
        with mock.patch.object(mod.os, "replace", side_effect=OSError("boom")):
            ok = mod.write_note_atomic(note, "TOTALLY DIFFERENT CONTENT")
        assert ok is False
        assert note.read_text() == original, "original note was clobbered"
        # No stray temp files left behind in the note's month bucket (the temp
        # file is created alongside the note via tempfile.mkstemp(dir=...)).
        leftovers = [p for p in note.parent.iterdir() if p != note]
        assert leftovers == [], f"temp files left behind: {leftovers}"


# ---------------------------------------------------------------------------
# harvest-candidates.py
# ---------------------------------------------------------------------------

HARVEST_TEXT = (
    "Here is my report.\n\n"
    "## Harvest candidates\n\n"
    "- gotcha: the widget frobnicates on tuesdays\n"
    "- dead-end: tried caching, made it slower (revive if cache is shared)\n"
)


def _run_harvest(payload: dict, vault: Path):
    mod = load_hook("harvest-candidates")
    env = {"LORE_VAULT": str(vault), "PATH": os.environ.get("PATH", ""),
           "HOME": os.environ.get("HOME", "")}
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with mock.patch("sys.stdout", io.StringIO()):
                rc = mod.main()
    return rc


class TestHarvest:
    def test_routes_block_to_pending_file(self, tmp_path):
        vault = _make_vault(tmp_path)
        payload = {
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "sdd-implementer"},
            "tool_response": {"content": HARVEST_TEXT},
            "cwd": str(tmp_path / "wt"),
        }
        _run_harvest(payload, vault)
        pending = vault / "harvest-pending.md"
        assert pending.exists()
        text = pending.read_text()
        assert "frobnicates on tuesdays" in text
        assert "tried caching" in text

    def test_noop_when_block_absent(self, tmp_path):
        vault = _make_vault(tmp_path)
        payload = {
            "tool_name": "Agent",
            "tool_input": {},
            "tool_response": {"content": "Just a normal report, no harvest."},
            "cwd": str(tmp_path / "wt"),
        }
        _run_harvest(payload, vault)
        assert not (vault / "harvest-pending.md").exists()

    def test_noop_when_not_agent_tool(self, tmp_path):
        vault = _make_vault(tmp_path)
        payload = {
            "tool_name": "Bash",
            "tool_response": {"content": HARVEST_TEXT},
        }
        _run_harvest(payload, vault)
        assert not (vault / "harvest-pending.md").exists()

    def test_never_raises_on_garbage(self, tmp_path):
        vault = _make_vault(tmp_path)
        mod = load_hook("harvest-candidates")
        env = {"LORE_VAULT": str(vault), "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("sys.stdin", io.StringIO("not json {{{")):
                with mock.patch("sys.stdout", io.StringIO()):
                    assert mod.main() == 0


# ---------------------------------------------------------------------------
# permission-log.py
# ---------------------------------------------------------------------------

class TestPermissionLog:
    def test_appends_entry(self, tmp_path):
        vault = _make_vault(tmp_path)
        mod = load_hook("permission-log")
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git -C foo status"},
            "session_id": "sess-1",
        }
        env = {"LORE_VAULT": str(vault), "PATH": os.environ.get("PATH", ""),
               "HOME": os.environ.get("HOME", "")}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
                with mock.patch("sys.stdout", io.StringIO()):
                    rc = mod.main()
        assert rc == 0
        log_dir = vault / "permission-log"
        logs = list(log_dir.glob("*.jsonl"))
        assert len(logs) == 1
        line = json.loads(logs[0].read_text().strip())
        assert line["tool"] == "Bash"
        assert line["signature"] == "git status"

    def test_resolves_log_under_vault(self, tmp_path):
        """The log lives under the resolved $LORE_VAULT, not a hardcoded path."""
        vault = _make_vault(tmp_path)
        mod = load_hook("permission-log")
        payload = {"tool_name": "Read", "tool_input": {"file_path": "/x/y.txt"},
                   "session_id": "s2"}
        env = {"LORE_VAULT": str(vault), "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
                with mock.patch("sys.stdout", io.StringIO()):
                    mod.main()
        assert (vault / "permission-log" / "s2.jsonl").exists()

    def test_never_raises_on_garbage(self, tmp_path):
        vault = _make_vault(tmp_path)
        mod = load_hook("permission-log")
        env = {"LORE_VAULT": str(vault), "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("sys.stdin", io.StringIO("garbage {{{")):
                with mock.patch("sys.stdout", io.StringIO()):
                    assert mod.main() == 0


# ---------------------------------------------------------------------------
# hooks.json registration
# ---------------------------------------------------------------------------

class TestHooksJson:
    def test_registers_all_four_events(self):
        data = json.loads((HOOKS_DIR / "hooks.json").read_text())
        hooks = data["hooks"]
        for event in ("SessionStart", "PreToolUse", "PostToolUse", "WorktreeRemove"):
            assert event in hooks, f"missing {event}"

    def test_session_start_points_at_session_context(self):
        data = json.loads((HOOKS_DIR / "hooks.json").read_text())
        cmds = [
            h["command"]
            for entry in data["hooks"]["SessionStart"]
            for h in entry["hooks"]
        ]
        assert any("session-context.py" in c for c in cmds)
        assert not any("session_smoke" in c for c in cmds)

    def test_post_tool_use_matches_agent(self):
        data = json.loads((HOOKS_DIR / "hooks.json").read_text())
        entries = data["hooks"]["PostToolUse"]
        matchers = [e.get("matcher", "") for e in entries]
        assert any("Agent" in m or "subagent" in m.lower() for m in matchers)

    def test_smoke_files_deleted(self):
        assert not (HOOKS_DIR / "session_smoke.py").exists()
        assert not (HOOKS_DIR / "_shared_smoke.py").exists()


# ---------------------------------------------------------------------------
# M1: sessions.py nested-name collision
# session_note_path / all_session_notes_for_worktree must not let worktree
# 'foo' match '…-super-foo.md'.
# ---------------------------------------------------------------------------

def _seed_note(sessions_dir: Path, stamp: str, worktree: str) -> Path:
    """Write a minimal session note with correct filename and worktree frontmatter."""
    p = sessions_dir / f"{stamp}-{worktree}.md"
    p.write_text(
        f"---\ntype: session\nworktree: {worktree}\nstatus: active\n---\n\n# Session\n"
    )
    return p


class TestSessionNoteNestedNameCollision:
    def test_session_note_path_no_false_match_on_super_prefix(self, tmp_path):
        """session_note_path('foo') must not return a 'super-foo' note."""
        vault = _make_vault(tmp_path)
        sessions_dir = vault / "sessions"
        _seed_note(sessions_dir, "2026-06-02-1000", "super-foo")
        foo = _seed_note(sessions_dir, "2026-06-01-1000", "foo")

        s = load_sessions()
        result = s.session_note_path(vault, "foo")
        assert result == foo

    def test_session_note_path_returns_none_when_only_prefix_note_exists(self, tmp_path):
        """If only 'super-foo' exists, looking for 'foo' returns None."""
        vault = _make_vault(tmp_path)
        sessions_dir = vault / "sessions"
        _seed_note(sessions_dir, "2026-06-02-1000", "super-foo")

        s = load_sessions()
        result = s.session_note_path(vault, "foo")
        assert result is None

    def test_all_notes_for_worktree_no_false_match_on_super_prefix(self, tmp_path):
        """all_session_notes_for_worktree('foo') excludes 'super-foo' notes."""
        vault = _make_vault(tmp_path)
        sessions_dir = vault / "sessions"
        _seed_note(sessions_dir, "2026-06-02-1000", "super-foo")
        foo = _seed_note(sessions_dir, "2026-06-01-1000", "foo")

        s = load_sessions()
        results = s.all_session_notes_for_worktree(vault, "foo")
        assert results == [foo]

    def test_all_notes_for_worktree_empty_when_only_prefix_note_exists(self, tmp_path):
        """all_session_notes_for_worktree('foo') returns [] if only super-foo exists."""
        vault = _make_vault(tmp_path)
        sessions_dir = vault / "sessions"
        _seed_note(sessions_dir, "2026-06-02-1000", "super-foo")

        s = load_sessions()
        results = s.all_session_notes_for_worktree(vault, "foo")
        assert results == []


# ---------------------------------------------------------------------------
# M2: commit_vault must not sweep unrelated dirty vault files
# ---------------------------------------------------------------------------

class TestFinalizeCommitScope:
    def test_unrelated_dirty_file_stays_unstaged_after_finalize(self, tmp_path):
        """An unrelated dirty vault file must NOT be included in the finalize commit."""
        vault = _git_vault(tmp_path)

        # Create an initial commit so there's a HEAD to diff against
        readme = vault / "README.md"
        readme.write_text("init\n")
        subprocess.run(["git", "-C", str(vault), "add", "README.md"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(vault), "commit", "-m", "init"],
                       check=True, capture_output=True)

        # Seed the session note we'll finalize
        _seed_session_note(vault, worktree="wt")

        # Create an unrelated dirty file in the vault (not staged)
        unrelated = vault / "decisions" / "foo.md"
        unrelated.parent.mkdir(parents=True, exist_ok=True)
        unrelated.write_text("---\ntype: decision\nstatus: accepted\n---\n\nfoo\n")

        _run_finalize({"worktree": "wt"}, vault)

        # The session note should be committed
        log = subprocess.run(
            ["git", "-C", str(vault), "log", "--oneline"],
            capture_output=True, text=True,
        )
        assert "finalize wt" in log.stdout

        # The unrelated file must still be untracked/dirty (not committed).
        # Git may report the whole directory as untracked when none of its
        # files have been staged, so check for either form.
        status = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain"],
            capture_output=True, text=True,
        )
        assert "decisions" in status.stdout, (
            f"Expected decisions/ to remain dirty but status was: {status.stdout!r}"
        )
