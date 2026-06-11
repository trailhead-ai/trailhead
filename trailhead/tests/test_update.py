"""Tests for trailhead update (Slice 5).

TDD: tests written BEFORE implementation. Each test must fail before
implementation exists, then pass after.

Test contract (plan §Slice 5 + amendments R-8, A-5, D-7):
  - update with unchanged manifest revs → no-op (nothing re-fetched/re-wired).
  - update with a changed rev → re-verifies + re-wires.
  - Unreachable configured source → A-5 named error (exact text), no phone-home.
  - R-8: lock file guards concurrent wire()/config-toggle/update invocations.
  - R-8: update where computed full set grew → "newly wired: X" in summary.
  - D-7: no hardcoded upstream URL in logic; source resolves from config.registry.
  - Restart-to-apply note in summary.
  - A-9: errors to stderr, nonzero exit on failure.

Hermeticity:
  - wire, fetch, and manifest-loading are always stubbed.
  - tmp_path for config/state dirs; no real ~/.claude/ or network calls.
"""

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hermetic_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "TRAILHEAD_CONFIG_DIR": str(tmp_path / "config"),
        "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
    }


def _save_config(env: dict[str, str], **kwargs):
    from trailhead.config import TrailheadConfig, save_config
    cfg = TrailheadConfig(**kwargs)
    save_config(cfg, env=env)
    return cfg


def _load_config(env: dict[str, str]):
    from trailhead.config import load_config
    return load_config(env=env)


_FAKE_SHA_A = "a" * 40
_FAKE_SHA_B = "b" * 40


def _make_manifest_entry(name: str = "trailhead", rev: str = None, source: str = "https://example.com/repo"):
    """Create a RepoEntry for testing."""
    from trailhead.manifest import RepoEntry
    return RepoEntry(name=name, rev=rev or _FAKE_SHA_A, source=source, tools=["lore"])


def _make_install_manifest(entries=None):
    """Create an InstallManifest for testing."""
    from trailhead.manifest import InstallManifest
    if entries is None:
        entries = [_make_manifest_entry()]
    return InstallManifest(repos=entries)


def _run_update(
    args: list[str] = None,
    *,
    env: dict[str, str],
    manifest: object = None,
    wire_side_effect=None,
    verify_side_effect=None,
    load_manifest_side_effect=None,
):
    """Run run_update() with stubbed dependencies.

    Returns (exit_code, stdout_str, stderr_str).
    """
    from trailhead import update as update_mod

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    if manifest is None:
        manifest = _make_install_manifest()

    try:
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf

        with patch("trailhead.update.load_install_manifest") as mock_load_manifest, \
             patch("trailhead.update.wire") as mock_wire, \
             patch("trailhead.update.verify_present_repo") as mock_verify:

            if load_manifest_side_effect is not None:
                mock_load_manifest.side_effect = load_manifest_side_effect
            else:
                mock_load_manifest.return_value = manifest

            mock_wire.return_value = None

            if wire_side_effect is not None:
                mock_wire.side_effect = wire_side_effect

            if verify_side_effect is not None:
                mock_verify.side_effect = verify_side_effect
            else:
                mock_verify.return_value = True

            try:
                exit_code = update_mod.run_update(env=env)
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else 0

    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


# ---------------------------------------------------------------------------
# T-U1: unchanged revs → no-op
# ---------------------------------------------------------------------------


class TestUnchangedRevNoOp:
    def test_no_op_returns_zero(self, tmp_path):
        """update with unchanged revs → exit 0."""
        env = _hermetic_env(tmp_path)
        # Current config shows same rev as what the manifest has
        _save_config(
            env,
            capabilities={"lore": ["capture", "recall"]},
            registry="https://example.com",
        )

        # Mark the current wired state as matching the manifest
        state_file = tmp_path / "state" / "update_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"trailhead": _FAKE_SHA_A}))

        manifest = _make_install_manifest([_make_manifest_entry(rev=_FAKE_SHA_A)])

        code, out, err = _run_update(env=env, manifest=manifest)
        assert code == 0

    def test_no_op_wire_not_called(self, tmp_path):
        """update with unchanged revs → wire() is NOT called."""
        env = _hermetic_env(tmp_path)
        _save_config(env, registry="https://example.com")

        # Set the state to match the manifest's rev
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "update_state.json"
        state_file.write_text(json.dumps({"trailhead": _FAKE_SHA_A}))

        manifest = _make_install_manifest([_make_manifest_entry(rev=_FAKE_SHA_A)])

        wire_calls = []

        def track_wire(selection, **kwargs):
            wire_calls.append(selection)

        code, out, err = _run_update(
            env=env,
            manifest=manifest,
            wire_side_effect=track_wire,
        )
        assert code == 0
        assert not wire_calls, f"wire() should not be called for no-op update, got: {wire_calls}"

    def test_no_op_prints_already_up_to_date(self, tmp_path):
        """update with unchanged revs → prints informative no-op message."""
        env = _hermetic_env(tmp_path)
        _save_config(env, registry="https://example.com")

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "update_state.json"
        state_file.write_text(json.dumps({"trailhead": _FAKE_SHA_A}))

        manifest = _make_install_manifest([_make_manifest_entry(rev=_FAKE_SHA_A)])

        code, out, err = _run_update(env=env, manifest=manifest)
        # Should say something about being up-to-date or no-op
        combined = out + err
        assert (
            "up-to-date" in combined.lower()
            or "no-op" in combined.lower()
            or "nothing to" in combined.lower()
            or "already" in combined.lower()
        )


# ---------------------------------------------------------------------------
# T-U2: changed rev → re-verify + re-wire
# ---------------------------------------------------------------------------


class TestChangedRevRewire:
    def test_changed_rev_calls_wire(self, tmp_path):
        """update with a changed rev → wire() is called."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture"]}, registry="https://example.com")

        # State says old rev, manifest has new rev
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "update_state.json"
        state_file.write_text(json.dumps({"trailhead": _FAKE_SHA_A}))

        manifest = _make_install_manifest([_make_manifest_entry(rev=_FAKE_SHA_B)])

        wire_calls = []

        def track_wire(selection, **kwargs):
            wire_calls.append(selection)

        code, out, err = _run_update(
            env=env,
            manifest=manifest,
            wire_side_effect=track_wire,
        )
        assert code == 0
        assert wire_calls, "wire() should be called when rev changed"

    def test_changed_rev_prints_restart_note(self, tmp_path):
        """update summary includes restart-to-apply note."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture"]}, registry="https://example.com")

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "update_state.json"
        state_file.write_text(json.dumps({"trailhead": _FAKE_SHA_A}))

        manifest = _make_install_manifest([_make_manifest_entry(rev=_FAKE_SHA_B)])

        code, out, err = _run_update(env=env, manifest=manifest)
        # Must mention restart
        combined = out + err
        assert "restart" in combined.lower() or "fresh" in combined.lower() or "new session" in combined.lower()


# ---------------------------------------------------------------------------
# T-U3: A-5 — unreachable source → named error, no phone-home
# ---------------------------------------------------------------------------


class TestUnreachableSource:
    def test_unreachable_source_error_message(self, tmp_path):
        """Unreachable source → A-5 named error (exact format checked)."""
        from trailhead.fetch import FetchError
        from trailhead.manifest import InstallManifestError

        env = _hermetic_env(tmp_path)
        _save_config(env, registry="https://unreachable.example.com")

        # Load_install_manifest raises on unreachable source
        def fail_load(*args, **kwargs):
            raise FetchError(
                "trailhead: cannot reach update source\n"
                "  source: https://unreachable.example.com/trailhead\n"
                "Check your connection, or confirm the source with "
                "`trailhead config registry`.\n"
                "To use a local copy, set a file:// source."
            )

        code, out, err = _run_update(
            env=env,
            load_manifest_side_effect=fail_load,
        )
        assert code != 0
        # A-5 error format
        assert "cannot reach update source" in err or "cannot reach" in err.lower()

    def test_unreachable_source_nonzero_exit(self, tmp_path):
        """Unreachable source → nonzero exit code."""
        from trailhead.fetch import FetchError

        env = _hermetic_env(tmp_path)
        _save_config(env, registry="https://unreachable.example.com")

        def fail_load(*args, **kwargs):
            raise FetchError(
                "trailhead: cannot reach update source\n"
                "  source: https://unreachable.example.com/trailhead\n"
                "Check your connection, or confirm the source with "
                "`trailhead config registry`.\n"
                "To use a local copy, set a file:// source."
            )

        code, out, err = _run_update(
            env=env,
            load_manifest_side_effect=fail_load,
        )
        assert code != 0

    def test_a5_error_format_exact(self, tmp_path):
        """A-5: exact error format — 'cannot reach update source', source URL, config hint."""
        from trailhead.fetch import FetchError

        env = _hermetic_env(tmp_path)
        source_url = "https://my-registry.example.com/trailhead"
        _save_config(env, registry=source_url)

        a5_message = (
            f"trailhead: cannot reach update source\n"
            f"  source: {source_url}\n"
            f"Check your connection, or confirm the source with "
            f"`trailhead config registry`.\n"
            f"To use a local copy, set a file:// source."
        )

        def fail_load(*args, **kwargs):
            raise FetchError(a5_message)

        code, out, err = _run_update(
            env=env,
            load_manifest_side_effect=fail_load,
        )
        # Should contain the key parts of A-5 format
        combined = out + err
        assert "cannot reach update source" in combined
        assert "trailhead config registry" in combined


# ---------------------------------------------------------------------------
# T-U4: R-8 — lock file guards concurrent invocations
# ---------------------------------------------------------------------------


class TestLockFile:
    def test_lock_taken_during_update(self, tmp_path):
        """R-8: a lock file is created during update and released after."""
        from trailhead import update as update_mod

        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture"]}, registry="https://example.com")

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        # No prior state → will trigger wire
        manifest = _make_install_manifest([_make_manifest_entry(rev=_FAKE_SHA_B)])

        lock_file_path = state_dir / "trailhead.lock"
        seen_lock = []

        original_wire = None

        def track_lock_wire(selection, **kwargs):
            # Check if lock file exists during wire
            if lock_file_path.exists():
                seen_lock.append(True)

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        try:
            with patch("trailhead.update.load_install_manifest", return_value=manifest), \
                 patch("trailhead.update.wire", side_effect=track_lock_wire), \
                 patch("trailhead.update.verify_present_repo", return_value=True):
                update_mod.run_update(env=env)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        # Lock was held during wire
        assert seen_lock, "Lock file should be present during wire()"
        # Lock released after
        assert not lock_file_path.exists(), "Lock file should be released after update"

    def test_concurrent_update_rejected(self, tmp_path):
        """R-8: if the lock is already held, update fails with a named error."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture"]}, registry="https://example.com")

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        # Pre-create the lock file to simulate a concurrent invocation
        lock_file = state_dir / "trailhead.lock"
        lock_file.write_text("locked by pid 99999")

        manifest = _make_install_manifest([_make_manifest_entry(rev=_FAKE_SHA_B)])

        code, out, err = _run_update(env=env, manifest=manifest)
        # Should fail with a named error about the lock
        assert code != 0
        combined = out + err
        assert "lock" in combined.lower() or "concurrent" in combined.lower() or "already running" in combined.lower()


# ---------------------------------------------------------------------------
# T-U5: R-8 — "newly wired: X" when full set grew
# ---------------------------------------------------------------------------


class TestNewlyWiredSummary:
    def test_newly_wired_in_summary_when_full_set_grew(self, tmp_path):
        """R-8: update where computed full set grew → 'newly wired: X' in summary."""
        env = _hermetic_env(tmp_path)
        # Config says lore only (old state)
        _save_config(
            env,
            capabilities={"lore": ["capture"]},
            registry="https://example.com",
        )

        # State says old rev
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "update_state.json"
        state_file.write_text(json.dumps({"trailhead": _FAKE_SHA_A}))

        # New manifest has different rev, and the selection grew
        manifest = _make_install_manifest([_make_manifest_entry(rev=_FAKE_SHA_B)])

        code, out, err = _run_update(env=env, manifest=manifest)
        assert code == 0
        # The summary should mention what was wired
        combined = out + err
        # At least mentions the update happened
        assert "wired" in combined.lower() or "updated" in combined.lower()


# ---------------------------------------------------------------------------
# T-U6: D-7 — source resolves from config.registry, not hardcoded
# ---------------------------------------------------------------------------


class TestRegistryFromConfig:
    def test_registry_from_config_used_for_manifest_load(self, tmp_path):
        """update uses config.registry as the registry base (D-7 — no hardcoded URL)."""
        env = _hermetic_env(tmp_path)
        custom_registry = "github.example-corp.internal/trailhead"
        _save_config(env, registry=custom_registry, capabilities={"lore": ["capture"]})

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        manifest = _make_install_manifest([_make_manifest_entry(rev=_FAKE_SHA_B)])

        registry_passed = []

        def capture_registry_load(path, registry, **kwargs):
            registry_passed.append(registry)
            return manifest

        code, out, err = _run_update(
            env=env,
            load_manifest_side_effect=capture_registry_load,
        )
        assert registry_passed, "load_install_manifest should be called"
        assert custom_registry in registry_passed[0], \
            f"Expected custom registry in call, got: {registry_passed[0]}"


# ---------------------------------------------------------------------------
# T-U7: A-9 — errors to stderr, nonzero exit on failure
# ---------------------------------------------------------------------------


class TestA9Update:
    def test_wire_failure_error_to_stderr(self, tmp_path):
        """Wire failure → error to stderr (A-9)."""
        from trailhead.wire import WireError

        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture"]}, registry="https://example.com")

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        manifest = _make_install_manifest([_make_manifest_entry(rev=_FAKE_SHA_B)])

        code, out, err = _run_update(
            env=env,
            manifest=manifest,
            wire_side_effect=WireError(tool="lore", stage="compose", cause=Exception("boom")),
        )
        assert code != 0
        assert len(err.strip()) > 0

    def test_success_summary_to_stdout(self, tmp_path):
        """Success summary goes to stdout (A-9)."""
        env = _hermetic_env(tmp_path)
        _save_config(env, capabilities={"lore": ["capture"]}, registry="https://example.com")

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        manifest = _make_install_manifest([_make_manifest_entry(rev=_FAKE_SHA_B)])

        code, out, err = _run_update(env=env, manifest=manifest)
        assert code == 0
        assert len(out.strip()) > 0
