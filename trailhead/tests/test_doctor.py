"""Tests for trailhead/doctor.py — read-only install-state report.

doctor never gates (exit_code is always 0); it reports what's installed,
discovered from on-disk markers. which/python probes are injected.
"""

import os
import subprocess
from pathlib import Path

from trailhead.doctor import run_doctor
from trailhead.wire import default_manifest_paths


def _env(tmp_path: Path) -> dict[str, str]:
    return {**os.environ, "TRAILHEAD_STATE_DIR": str(tmp_path)}


def _make_tree(tmp_path: Path, hname: str, tools: list[str], *, registered=True, mkt="trailhead"):
    root = tmp_path / "composed" / hname
    (root / ".claude-plugin").mkdir(parents=True)
    if mkt is not None:
        import json

        (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"name": mkt}))
    if registered:
        (root / ".trailhead-registered").write_text("{}")
    for t in tools:
        (root / f".trailhead-installed-{t}").write_text("{}")


def _fake_py(cmd):
    return subprocess.CompletedProcess(cmd, 0, stdout="Python 3.11.4\n", stderr="")


class TestEmpty:
    def test_exit_zero_with_no_state(self, tmp_path):
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert r.exit_code == 0
        assert r.data["harnesses"] == {}

    def test_human_output_mentions_no_harnesses(self, tmp_path):
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert "no harnesses installed" in r.human_output


class TestReport:
    def test_reports_installed_tools(self, tmp_path):
        _make_tree(tmp_path, "claude_code", ["lore", "camp"])
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        info = r.data["harnesses"]["claude_code"]
        assert info["registered"] is True
        assert set(info["installed"]) == {"lore", "camp"}
        assert info["marketplace"] == "trailhead"
        assert r.exit_code == 0

    def test_clis_reported_from_which(self, tmp_path):
        _make_tree(tmp_path, "claude_code", ["lore"])

        def which(n):
            return f"/shim/{n}" if n == "camp" else None

        r = run_doctor(env=_env(tmp_path), which_runner=which, python_version_runner=_fake_py)
        assert r.data["clis"]["camp"] == "/shim/camp"
        assert r.data["clis"]["lore"] is None

    def test_portage_cli_reported_from_which(self, tmp_path):
        # portage is CLI-bearing (its manifest declares cli_bin) just like
        # camp/lore, discovered generically rather than off a hardcoded list.
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: f"/shim/{n}" if n == "portage" else None,
            python_version_runner=_fake_py,
        )
        assert set(r.data["clis"]) == {"camp", "lore", "portage", "ranger"}
        assert r.data["clis"]["portage"] == "/shim/portage"

    def test_python_version_reported(self, tmp_path):
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert "3.11.4" in r.data["python3_version"]

    def test_exit_zero_even_when_cli_missing(self, tmp_path):
        # No pass/fail gating — a missing CLI on PATH is informational only.
        _make_tree(tmp_path, "claude_code", ["lore"])
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert r.exit_code == 0

    def test_shim_dir_presence(self, tmp_path):
        (tmp_path / "bin").mkdir()
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert r.data["shim_dir_present"] is True

    def test_unresolvable_harness_reports_empty_state(self, tmp_path):
        # A composed dir whose name isn't a registered Harness (e.g. leftover from
        # an uninstalled/renamed harness) must still be reported, not crash doctor.
        (tmp_path / "composed" / "not_a_real_harness").mkdir(parents=True)
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        info = r.data["harnesses"]["not_a_real_harness"]
        assert info == {"registered": False, "installed": [], "marketplace": None}
        assert r.exit_code == 0


class TestMalformedManifest:
    def test_marketplace_none_when_malformed(self, tmp_path):
        root = tmp_path / "composed" / "claude_code"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "marketplace.json").write_text("{not json")
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert r.data["harnesses"]["claude_code"]["marketplace"] is None

    def test_human_output_distinguishes_malformed_from_absent(self, tmp_path):
        root = tmp_path / "composed" / "claude_code"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "marketplace.json").write_text("{not json")
        (root / ".trailhead-registered").write_text("{}")
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert "marketplace: (unreadable)" in r.human_output
        assert "marketplace: (none)" not in r.human_output

    def test_human_output_reports_absent_when_no_manifest_file(self, tmp_path):
        _make_tree(tmp_path, "claude_code", [], mkt=None)
        r = run_doctor(
            env=_env(tmp_path), which_runner=lambda n: None, python_version_runner=_fake_py
        )
        assert "marketplace: (none)" in r.human_output
        assert "marketplace: (unreadable)" not in r.human_output


class TestBrokenCliManifest:
    def test_broken_manifest_does_not_crash_doctor(self, tmp_path):
        broken = tmp_path / "broken_capabilities.toml"
        broken.write_text("this is not valid toml [[[")
        manifest_paths = {
            "lore": default_manifest_paths()["lore"],
            "broken": broken,
        }
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: None,
            python_version_runner=_fake_py,
            manifest_paths=manifest_paths,
        )
        assert r.exit_code == 0
        assert "broken" not in r.data["clis"]

    def test_other_cli_bearing_tools_still_reported(self, tmp_path):
        broken = tmp_path / "broken_capabilities.toml"
        broken.write_text("this is not valid toml [[[")
        manifest_paths = {
            "lore": default_manifest_paths()["lore"],
            "broken": broken,
        }
        r = run_doctor(
            env=_env(tmp_path),
            which_runner=lambda n: None,
            python_version_runner=_fake_py,
            manifest_paths=manifest_paths,
        )
        assert "lore" in r.data["clis"]


class TestBookmarkRetentionWarning:
    """doctor warns when a camp bookmark's session transcript is approaching the
    harness's retention cleanup, so a user can resume or re-capture it before the
    harness deletes it out from under the bookmark.

    Everything is injected through env: the camp state dir holding the bookmark
    store, and the Claude dir holding the retention setting.
    """

    def _env(self, tmp_path: Path, *, cleanup_days: int | None = None) -> dict[str, str]:
        claude_dir = tmp_path / "claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        body = {} if cleanup_days is None else {"cleanupPeriodDays": cleanup_days}
        import json

        (claude_dir / "settings.json").write_text(json.dumps(body))
        _make_tree(tmp_path, "claude_code", ["camp"])
        return {
            **os.environ,
            "TRAILHEAD_STATE_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
            "TRAILHEAD_CLAUDE_DIR": str(claude_dir),
        }

    def _seed_bookmark(self, tmp_path: Path, ref: str, *, age_days: float) -> None:
        import json
        import time

        state = tmp_path / "camp-state"
        state.mkdir(parents=True, exist_ok=True)
        transcript = state / f"{ref}.jsonl"
        transcript.write_text("{}\n")
        old = time.time() - age_days * 86400
        os.utime(transcript, (old, old))

        store = state / "bookmarks.json"
        data = json.loads(store.read_text()) if store.exists() else {
            "schema_version": 1,
            "bookmarks": {},
        }
        data["bookmarks"][ref] = {
            "ref": ref,
            "group": "demo",
            "slug": ref,
            "session_id": f"sess-{ref}",
            "transcript_path": str(transcript),
            "note": "",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        store.write_text(json.dumps(data))

    def _run(self, env):
        return run_doctor(
            env=env, which_runner=lambda n: None, python_version_runner=_fake_py
        )

    def test_warns_naming_the_at_risk_ref_and_the_settings_key(self, tmp_path):
        env = self._env(tmp_path, cleanup_days=10)
        self._seed_bookmark(tmp_path, "alpha", age_days=9)
        r = self._run(env)
        assert len(r.data["warnings"]) == 1
        warning = r.data["warnings"][0]
        assert "alpha" in warning
        assert "cleanupPeriodDays" in warning
        assert warning in r.human_output
        assert r.exit_code == 0

    def test_no_warning_below_the_threshold(self, tmp_path):
        env = self._env(tmp_path, cleanup_days=10)
        self._seed_bookmark(tmp_path, "alpha", age_days=3)
        assert self._run(env).data["warnings"] == []

    def test_no_warning_without_bookmarks(self, tmp_path):
        env = self._env(tmp_path, cleanup_days=10)
        assert self._run(env).data["warnings"] == []

    def test_names_every_at_risk_ref_not_only_the_oldest(self, tmp_path):
        env = self._env(tmp_path, cleanup_days=10)
        self._seed_bookmark(tmp_path, "alpha", age_days=9)
        self._seed_bookmark(tmp_path, "beta", age_days=8.5)
        self._seed_bookmark(tmp_path, "gamma", age_days=1)
        warning = self._run(env).data["warnings"][0]
        assert "alpha" in warning and "beta" in warning
        assert "gamma" not in warning

    def test_bookmark_with_a_gone_transcript_is_not_at_risk(self, tmp_path):
        """A transcript already deleted is a staleness problem `camp bookmark ls`
        reports, not an approaching-expiry warning doctor should raise."""
        env = self._env(tmp_path, cleanup_days=10)
        self._seed_bookmark(tmp_path, "alpha", age_days=9)
        (tmp_path / "camp-state" / "alpha.jsonl").unlink()
        assert self._run(env).data["warnings"] == []

    def test_uses_the_thirty_day_default_when_the_setting_is_absent(self, tmp_path):
        env = self._env(tmp_path)
        self._seed_bookmark(tmp_path, "alpha", age_days=27)
        self._seed_bookmark(tmp_path, "young", age_days=20)
        warning = self._run(env).data["warnings"][0]
        assert "alpha" in warning
        assert "young" not in warning

    def test_skips_silently_when_no_harness_reports_a_retention_window(self, tmp_path):
        """The seam's degrading None means "no window to warn about" — doctor must
        say nothing rather than guess one."""
        env = {
            **os.environ,
            "TRAILHEAD_STATE_DIR": str(tmp_path),
            "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
        }
        (tmp_path / "composed" / "not_a_real_harness").mkdir(parents=True)
        self._seed_bookmark(tmp_path, "alpha", age_days=900)
        r = self._run(env)
        assert r.data["warnings"] == []
        assert r.exit_code == 0

    def test_corrupt_bookmark_store_does_not_crash_doctor(self, tmp_path):
        env = self._env(tmp_path, cleanup_days=10)
        state = tmp_path / "camp-state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "bookmarks.json").write_text("{not json")
        r = self._run(env)
        assert r.data["warnings"] == []
        assert r.exit_code == 0

    def test_human_output_has_no_warning_section_when_clean(self, tmp_path):
        env = self._env(tmp_path, cleanup_days=10)
        assert "warnings:" not in self._run(env).human_output

    def test_json_report_carries_the_warning(self, tmp_path):
        """`trailhead doctor --json` dumps `data` verbatim, so the warning has to
        live there and not only in the rendered text."""
        env = self._env(tmp_path, cleanup_days=10)
        self._seed_bookmark(tmp_path, "alpha", age_days=9)
        r = run_doctor(
            as_json=True,
            env=env,
            which_runner=lambda n: None,
            python_version_runner=_fake_py,
        )
        import json as _json

        round_tripped = _json.loads(_json.dumps(r.data))
        assert any("alpha" in w for w in round_tripped["warnings"])


class TestBookmarkStoreShapePin:
    """doctor._load_bookmarks re-implements camp's on-disk bookmark-store shape
    by reading bookmarks.json directly, since trailhead cannot import camp
    (the harness-agnostic boundary). Pin that shape here by round-tripping a
    REAL record through camp.bookmark.store.upsert and reading it back with
    doctor's own reader, so the two can never silently drift — same idiom as
    test_bookmark_ls.py's sys.path import of camp for a cross-package check.
    """

    @staticmethod
    def _import_camp_store():
        import sys

        repo_root = Path(__file__).resolve().parents[2]
        plugin_dir = repo_root / "tools" / "camp" / "plugins" / "camp"
        if str(plugin_dir) not in sys.path:
            sys.path.insert(0, str(plugin_dir))
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        from camp.bookmark import store

        return store

    def test_reads_a_record_written_by_camps_own_store(self, tmp_path: Path) -> None:
        from trailhead.doctor import _load_bookmarks

        store = self._import_camp_store()
        env = {"CAMP_STATE_DIR": str(tmp_path / "camp-state")}
        record = store.upsert(
            {
                "ref": "alpha",
                "group": "demo",
                "slug": "alpha",
                "session_id": "sess-alpha",
                "transcript_path": "/nonexistent/alpha.jsonl",
                "note": "mid-refactor",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            env=env,
        )

        [loaded] = _load_bookmarks(env)
        for key in ("ref", "group", "slug", "session_id", "transcript_path", "note", "updated_at"):
            assert loaded[key] == record[key], key

    def test_schema_version_key_is_present_alongside_the_bookmarks_map(
        self, tmp_path: Path
    ) -> None:
        """Pin that store.upsert always writes schema_version at the top level —
        doctor's reader ignores it, but if the store ever stopped writing it (or
        renamed it) that would be exactly the drift this pin exists to catch."""
        import json

        store = self._import_camp_store()
        env = {"CAMP_STATE_DIR": str(tmp_path / "camp-state")}
        store.upsert(
            {
                "ref": "alpha",
                "group": "demo",
                "slug": "alpha",
                "session_id": "sess-alpha",
                "transcript_path": "/nonexistent/alpha.jsonl",
                "note": "",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            env=env,
        )

        raw = json.loads(store.store_path(env=env).read_text())
        assert raw["schema_version"] == store.SCHEMA_VERSION
        assert "alpha" in raw["bookmarks"]
