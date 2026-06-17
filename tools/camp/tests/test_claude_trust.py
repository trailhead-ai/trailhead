"""Tests for Slice 1: claude_trust.pretrust_workspace + HarnessProfile helpers.

Test contract (all with env={"HOME": str(tmp_path)} — never touch real ~/.claude.json):
- Absent ~/.claude.json → file created with projects.<key>.hasTrustDialogAccepted == true;
  key is realpath of launch_dir; created (final) mode is 0o600. (The tmp file's
  0o600 mode during the write window is guaranteed by tempfile.mkstemp, not
  separately asserted here.)
- Existing file → entry merged; unrelated top-level keys and other projects entries
  preserved; existing mode preserved.
- Idempotent: second call produces an identical file.
- Malformed existing JSON → returns without raising, does NOT overwrite; emits camp: stderr.
- Unreadable existing file (OSError/EACCES) → aborts without overwriting; emits camp: stderr.
- Confinement (C2): launch_dir == workspace_root and workspace_root/sub → written;
  launch_dir outside workspace_root → refused with camp: stderr, no write.
- Stderr copy distinguishes unreadable/parse-error from permission-denied from
  out-of-confinement.
- HarnessProfile.resolved_cwd returns the substituted cwd.
- HarnessProfile.is_claude_launch true for claude, false for codex/other.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _read_claude_json(home: Path) -> dict:
    return json.loads((home / ".claude.json").read_text())


# ---------------------------------------------------------------------------
# pretrust_workspace — absent file
# ---------------------------------------------------------------------------


class TestAbsentFile:
    def test_creates_file_with_trust_flag(self, tmp_path):
        from claude_trust import pretrust_workspace

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

        data = _read_claude_json(tmp_path)
        key = str(launch_dir.resolve())
        assert data["projects"][key]["hasTrustDialogAccepted"] is True

    def test_created_file_mode_is_0o600(self, tmp_path):
        from claude_trust import pretrust_workspace

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

        mode = (tmp_path / ".claude.json").stat().st_mode & 0o777
        assert mode == 0o600

    def test_key_is_realpath(self, tmp_path):
        """The project key must be the realpath — important on macOS where /tmp → /private/tmp."""
        from claude_trust import pretrust_workspace

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

        data = _read_claude_json(tmp_path)
        real = str(launch_dir.resolve())
        assert real in data["projects"]

    def test_only_trust_flag_written_no_extra_keys(self, tmp_path):
        """U1-verified: only hasTrustDialogAccepted is written; no companion keys."""
        from claude_trust import pretrust_workspace

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

        data = _read_claude_json(tmp_path)
        key = str(launch_dir.resolve())
        assert list(data.keys()) == ["projects"]
        assert list(data["projects"].keys()) == [key]
        assert list(data["projects"][key].keys()) == ["hasTrustDialogAccepted"]


# ---------------------------------------------------------------------------
# pretrust_workspace — existing file (merge, preserve)
# ---------------------------------------------------------------------------


class TestExistingFile:
    def test_merges_into_existing_file_preserves_other_keys(self, tmp_path):
        from claude_trust import pretrust_workspace

        claude_json = tmp_path / ".claude.json"
        existing = {
            "oauthAccount": {"email": "user@example.com"},
            "projects": {"/other/path": {"hasTrustDialogAccepted": True}},
        }
        claude_json.write_text(json.dumps(existing))
        os.chmod(str(claude_json), 0o600)

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

        data = _read_claude_json(tmp_path)
        # Original keys preserved
        assert data["oauthAccount"] == {"email": "user@example.com"}
        assert data["projects"]["/other/path"]["hasTrustDialogAccepted"] is True
        # New entry added
        key = str(launch_dir.resolve())
        assert data["projects"][key]["hasTrustDialogAccepted"] is True

    def test_preserves_existing_file_mode(self, tmp_path):
        from claude_trust import pretrust_workspace

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({"projects": {}}))
        os.chmod(str(claude_json), 0o640)

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

        mode = claude_json.stat().st_mode & 0o777
        assert mode == 0o640


# ---------------------------------------------------------------------------
# pretrust_workspace — idempotent
# ---------------------------------------------------------------------------


class TestIdempotent:
    def test_second_call_produces_identical_file(self, tmp_path):
        from claude_trust import pretrust_workspace

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})
        first = (tmp_path / ".claude.json").read_text()

        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})
        second = (tmp_path / ".claude.json").read_text()

        assert first == second

    def test_already_trusted_dir_is_noop(self, tmp_path):
        from claude_trust import pretrust_workspace

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        key = str(launch_dir.resolve())
        existing = {"projects": {key: {"hasTrustDialogAccepted": True}}}
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps(existing))
        mtime_before = claude_json.stat().st_mtime_ns

        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

        # File unchanged (mtime unchanged because no write occurred)
        mtime_after = claude_json.stat().st_mtime_ns
        assert mtime_before == mtime_after


# ---------------------------------------------------------------------------
# pretrust_workspace — malformed JSON
# ---------------------------------------------------------------------------


class TestMalformedJson:
    def test_malformed_json_does_not_overwrite(self, tmp_path):
        from claude_trust import pretrust_workspace

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("not valid { json }")
        original_content = claude_json.read_text()

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

        assert claude_json.read_text() == original_content

    def test_malformed_json_does_not_raise(self, tmp_path):
        from claude_trust import pretrust_workspace

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{bad json}")

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        # Must not raise
        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

    def test_malformed_json_emits_camp_stderr(self, tmp_path, capsys):
        from claude_trust import pretrust_workspace

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{bad json}")

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

        err = capsys.readouterr().err
        assert err.startswith("camp:")
        assert "malformed" in err.lower() or "parse" in err.lower() or "json" in err.lower()


# ---------------------------------------------------------------------------
# pretrust_workspace — parseable-but-wrong structure (never-raise on build path)
# ---------------------------------------------------------------------------


class TestUnexpectedStructure:
    """Valid JSON whose shape would break the merge must abort, not raise/clobber.

    Guards the build path (data.setdefault chain) against a non-dict top level or
    a non-dict projects/entry — the module's "never raises" contract must hold
    without relying on the Slice 2 caller's try/except.
    """

    def _run(self, tmp_path, content):
        from claude_trust import pretrust_workspace

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(content)
        original = claude_json.read_text()
        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        pretrust_workspace(
            launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)}
        )
        return claude_json, original

    def test_top_level_not_object_aborts_without_overwrite(self, tmp_path):
        claude_json, original = self._run(tmp_path, '["a", "list"]')
        assert claude_json.read_text() == original

    def test_projects_not_object_aborts_without_overwrite(self, tmp_path):
        claude_json, original = self._run(tmp_path, '{"projects": "nope"}')
        assert claude_json.read_text() == original

    def test_project_entry_not_object_aborts_without_overwrite(self, tmp_path):
        from claude_trust import pretrust_workspace

        claude_json = tmp_path / ".claude.json"
        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()
        key = str(launch_dir.resolve())
        claude_json.write_text(json.dumps({"projects": {key: "should-be-object"}}))
        original = claude_json.read_text()
        pretrust_workspace(
            launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)}
        )
        assert claude_json.read_text() == original

    def test_unexpected_structure_does_not_raise_and_warns(self, tmp_path, capsys):
        self._run(tmp_path, '"just a string"')
        err = capsys.readouterr().err
        assert err.startswith("camp:")
        assert "structure" in err.lower() or "object" in err.lower()


# ---------------------------------------------------------------------------
# pretrust_workspace — unreadable file
# ---------------------------------------------------------------------------


class TestUnreadableFile:
    def test_unreadable_file_does_not_overwrite(self, tmp_path):
        """OSError on read (e.g. EACCES) → abort without overwriting."""
        from claude_trust import pretrust_workspace

        # Create the file first — then simulate it being unreadable on open().
        claude_json = tmp_path / ".claude.json"
        original_content = json.dumps({"projects": {}})
        claude_json.write_text(original_content)

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()

        def _raise_oserror(*_a, **_kw):
            raise OSError("Permission denied")

        with patch("claude_trust.open", side_effect=_raise_oserror):
            # Should not raise
            pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

        # File should be unchanged — the unreadable abort must not overwrite.
        assert claude_json.read_text() == original_content

    def test_unreadable_file_emits_camp_stderr_distinguishable(self, tmp_path, capsys):
        from claude_trust import pretrust_workspace

        # Create the file first.
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({"projects": {}}))

        launch_dir = tmp_path / "ws"
        launch_dir.mkdir()

        def _raise_oserror(*_a, **_kw):
            raise OSError("Permission denied")

        with patch("claude_trust.open", side_effect=_raise_oserror):
            pretrust_workspace(launch_dir, workspace_root=launch_dir, env={"HOME": str(tmp_path)})

        err = capsys.readouterr().err
        assert err.startswith("camp:")
        # Should mention permission/read/unreadable — distinguishable from malformed
        assert (
            "permission" in err.lower()
            or "unreadable" in err.lower()
            or "read" in err.lower()
        )


# ---------------------------------------------------------------------------
# pretrust_workspace — confinement (C2)
# ---------------------------------------------------------------------------


class TestConfinement:
    def test_launch_dir_equals_workspace_root_is_written(self, tmp_path):
        from claude_trust import pretrust_workspace

        ws = tmp_path / "workspace"
        ws.mkdir()
        pretrust_workspace(ws, workspace_root=ws, env={"HOME": str(tmp_path)})

        data = _read_claude_json(tmp_path)
        key = str(ws.resolve())
        assert data["projects"][key]["hasTrustDialogAccepted"] is True

    def test_launch_dir_is_descendant_of_workspace_root_is_written(self, tmp_path):
        from claude_trust import pretrust_workspace

        ws = tmp_path / "workspace"
        sub = ws / "app"
        sub.mkdir(parents=True)
        pretrust_workspace(sub, workspace_root=ws, env={"HOME": str(tmp_path)})

        data = _read_claude_json(tmp_path)
        key = str(sub.resolve())
        assert data["projects"][key]["hasTrustDialogAccepted"] is True

    def test_launch_dir_outside_workspace_root_is_refused(self, tmp_path, capsys):
        from claude_trust import pretrust_workspace

        ws = tmp_path / "workspace"
        ws.mkdir()
        outside = tmp_path / "other"
        outside.mkdir()

        pretrust_workspace(outside, workspace_root=ws, env={"HOME": str(tmp_path)})

        # No file written
        assert not (tmp_path / ".claude.json").exists()
        err = capsys.readouterr().err
        assert err.startswith("camp:")
        assert "confin" in err.lower() or "outside" in err.lower() or "not under" in err.lower()

    def test_dotdot_escape_is_refused(self, tmp_path, capsys):
        from claude_trust import pretrust_workspace

        ws = tmp_path / "workspace"
        ws.mkdir()
        sibling = tmp_path / "sibling"
        sibling.mkdir()

        # Construct a path via .. that resolves outside workspace
        escape = ws / ".." / "sibling"
        pretrust_workspace(escape, workspace_root=ws, env={"HOME": str(tmp_path)})

        err = capsys.readouterr().err
        assert err.startswith("camp:")

    def test_etc_path_is_refused(self, tmp_path, capsys):
        from claude_trust import pretrust_workspace

        ws = tmp_path / "workspace"
        ws.mkdir()

        pretrust_workspace(Path("/etc"), workspace_root=ws, env={"HOME": str(tmp_path)})

        assert not (tmp_path / ".claude.json").exists()
        err = capsys.readouterr().err
        assert err.startswith("camp:")

    def test_stderr_confinement_message_distinguishable_from_read_error(
        self, tmp_path, capsys
    ):
        """Stderr for out-of-confinement must be distinguishable from read errors."""
        from claude_trust import pretrust_workspace

        ws = tmp_path / "workspace"
        ws.mkdir()
        outside = tmp_path / "other"
        outside.mkdir()

        pretrust_workspace(outside, workspace_root=ws, env={"HOME": str(tmp_path)})
        err = capsys.readouterr().err
        # "confinement" or "outside" or "not under" — distinct from "malformed"/"unreadable"
        assert "malform" not in err.lower()
        assert "unreadable" not in err.lower()


# ---------------------------------------------------------------------------
# HarnessProfile.resolved_cwd
# ---------------------------------------------------------------------------


class TestResolvedCwd:
    def _group(self, harness=None):
        g = {"group": {"name": "g"}, "members": [{"name": "r", "repo_root": "/tmp/r"}]}
        if harness is not None:
            g["harness"] = harness
        return g

    def test_default_cwd_is_workspace(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(self._group())
        ws = Path("/work/space")
        cwd = p.resolved_cwd(slug="feat-x", workspace=ws)
        assert cwd == ws

    def test_custom_cwd_template_substituted(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(self._group({"cwd": "{workspace}/app"}))
        ws = Path("/work/space")
        cwd = p.resolved_cwd(slug="feat-x", workspace=ws)
        assert cwd == Path("/work/space/app")

    def test_resolved_cwd_accepts_path_workspace(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(self._group())
        ws = Path("/tmp/myws")
        cwd = p.resolved_cwd(slug="s", workspace=ws)
        assert cwd == ws

    def test_launch_uses_resolved_cwd(self):
        """launch() must produce the same cwd as resolved_cwd."""
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(self._group({"cwd": "{workspace}/sub"}))
        ws = Path("/work/space")
        _, launch_cwd = p.launch(slug="feat-x", workspace=str(ws), is_resume=False)
        resolved = p.resolved_cwd(slug="feat-x", workspace=ws)
        assert launch_cwd == resolved


# ---------------------------------------------------------------------------
# HarnessProfile.is_claude_launch
# ---------------------------------------------------------------------------


class TestIsClaudeLaunch:
    def _group(self, harness=None):
        g = {"group": {"name": "g"}, "members": [{"name": "r", "repo_root": "/tmp/r"}]}
        if harness is not None:
            g["harness"] = harness
        return g

    def test_baked_in_default_is_claude_launch(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(self._group())
        assert p.is_claude_launch() is True

    def test_explicit_claude_binary_is_claude_launch(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(self._group({"new": ["claude", "--some-flag"]}))
        assert p.is_claude_launch() is True

    def test_codex_is_not_claude_launch(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(self._group({"new": ["codex"]}))
        assert p.is_claude_launch() is False

    def test_other_binary_is_not_claude_launch(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(self._group({"new": ["cursor"]}))
        assert p.is_claude_launch() is False

    def test_path_to_claude_binary_is_claude_launch(self):
        from harness_launch import resolve_harness_profile

        p = resolve_harness_profile(self._group({"new": ["/usr/local/bin/claude"]}))
        assert p.is_claude_launch() is True
