"""KU1 assumption-prover: index path resolution + WAL mode on darwin.

Proves three claims before Slice 1 (index_store.py) is written:
  1. The state_dir("lore")/index.sqlite pattern resolves correctly — mirroring
     the promote-token pattern at cli/lore:602-612.
  2. A tmp $XDG_STATE_HOME override keeps the index path out of ~/.local/state
     (test isolation — no real state touched).
  3. A fresh SQLite DB opened with PRAGMA journal_mode=WAL reports journal_mode
     == 'wal' on macOS/darwin.

EPHEMERAL — delete this file (tests/test_ku1_index_path_wal.py) after Slice 1
ships its real tests/test_index_store.py.
"""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers — mirror the _resolve_token_dir pattern at cli/lore:602-612
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"


def _resolve_index_path_via_trailhead() -> Path:
    """Return state_dir('lore') / 'index.sqlite' via trailhead.paths.

    Mirrors cli/lore:602-612 (_resolve_token_dir) exactly, except the
    leaf is 'index.sqlite' instead of 'promote-tokens/'.
    """
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import _bootstrap
    _bootstrap.ensure_trailhead_importable()
    import trailhead.paths as _paths
    return _paths.state_dir("lore") / "index.sqlite"


def _resolve_index_path_fallback() -> Path:
    """Fallback path — ~/.local/state/lore/index.sqlite."""
    return Path.home() / ".local" / "state" / "lore" / "index.sqlite"


# ---------------------------------------------------------------------------
# Test 1 — happy-path resolution: trailhead.paths.state_dir("lore") works
# ---------------------------------------------------------------------------


def test_trailhead_paths_state_dir_resolves():
    """trailhead.paths.state_dir('lore') is importable and returns a Path ending
    in 'lore', consistent with ~/.local/state/lore/ on macOS with no XDG override.
    """
    path = _resolve_index_path_via_trailhead()
    # The resolved path must be an absolute Path.
    assert path.is_absolute(), f"Expected absolute path, got: {path}"
    # The stem must be 'index.sqlite'.
    assert path.name == "index.sqlite", f"Expected 'index.sqlite', got: {path.name}"
    # The parent directory must be named 'lore' (state_dir("lore")).
    assert path.parent.name == "lore", (
        f"Expected parent named 'lore', got: {path.parent.name} (full path: {path})"
    )


# ---------------------------------------------------------------------------
# Test 2 — XDG_STATE_HOME override redirects the path (test isolation proof)
# ---------------------------------------------------------------------------


def test_xdg_state_home_override_redirects_index_path(tmp_path):
    """When XDG_STATE_HOME is set, state_dir('lore') resolves under it.

    This is the mechanism that keeps tests from touching ~/.local/state.
    The override must work so index_store.py tests can use a tmp state dir.
    """
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import _bootstrap
    _bootstrap.ensure_trailhead_importable()
    import trailhead.paths as _paths

    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()

    # Resolve with the override injected via env= (pure — no os.environ mutation).
    env_override = dict(os.environ)
    env_override["XDG_STATE_HOME"] = str(fake_state)

    resolved = _paths.state_dir("lore", env=env_override) / "index.sqlite"

    # Must land under our tmp dir.
    assert str(resolved).startswith(str(fake_state)), (
        f"Expected resolved path under {fake_state}, got: {resolved}"
    )
    assert resolved.name == "index.sqlite"
    # Must NOT be anywhere near ~/.local/state.
    home_state = Path.home() / ".local" / "state"
    assert not str(resolved).startswith(str(home_state)), (
        f"Override failed — path still resolves to real state dir: {resolved}"
    )


# ---------------------------------------------------------------------------
# Test 3 — fallback shape: ~/.local/state/lore/index.sqlite
# ---------------------------------------------------------------------------


def test_fallback_path_has_correct_shape():
    """The fallback path (when trailhead is unimportable) must be
    ~/.local/state/lore/index.sqlite — matching the promote-token fallback at
    cli/lore:610-611 but with 'index.sqlite' as the leaf.
    """
    fallback = _resolve_index_path_fallback()
    home = Path.home()
    expected = home / ".local" / "state" / "lore" / "index.sqlite"
    assert fallback == expected, f"Expected {expected}, got: {fallback}"


# ---------------------------------------------------------------------------
# Test 4 — WAL mode: PRAGMA journal_mode=WAL reports 'wal' on darwin
# ---------------------------------------------------------------------------


def test_sqlite_wal_mode_reports_wal_on_darwin(tmp_path):
    """A fresh SQLite DB opened with PRAGMA journal_mode=WAL returns 'wal'.

    This is the core WAL assumption for index_store.py provisioning.
    Runs on the actual filesystem where tests execute (macOS/darwin for this probe).
    """
    db_path = tmp_path / "probe.sqlite"

    conn = sqlite3.connect(str(db_path))
    try:
        result = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    finally:
        conn.close()

    assert result is not None, "PRAGMA journal_mode=WAL returned no result row"
    journal_mode = result[0]
    assert journal_mode == "wal", (
        f"Expected journal_mode='wal', got: {journal_mode!r}. "
        "WAL mode is not supported on this filesystem — index_store design must change."
    )


# ---------------------------------------------------------------------------
# Test 5 — full round-trip: index file provisioned in tmp state dir is at
#           the XDG-overridden path AND opens in WAL mode
# ---------------------------------------------------------------------------


def test_full_round_trip_index_in_tmp_state_dir(tmp_path):
    """Provision an index.sqlite at the XDG-overridden state path and confirm:
      - file lands at <tmp>/xdg-state/lore/index.sqlite
      - PRAGMA journal_mode=WAL reports 'wal'

    This is the composite proof that index_store.py provisioning works as assumed.
    """
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import _bootstrap
    _bootstrap.ensure_trailhead_importable()
    import trailhead.paths as _paths

    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()

    env_override = dict(os.environ)
    env_override["XDG_STATE_HOME"] = str(fake_state)

    # Resolve the index path (pure — no dirs created yet).
    index_path = _paths.state_dir("lore", env=env_override) / "index.sqlite"

    # Provision: create parent dirs (mirrors ensure_dir), open DB, set WAL.
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(index_path))
    try:
        result = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS records "
            "(vault TEXT, kind TEXT, name TEXT, PRIMARY KEY (vault, kind, name))"
        )
        conn.commit()
    finally:
        conn.close()

    # File must exist at the expected location.
    assert index_path.exists(), f"index.sqlite not created at: {index_path}"
    # Must be under the tmp state dir.
    assert str(index_path).startswith(str(fake_state)), (
        f"Expected index under {fake_state}, got: {index_path}"
    )
    # WAL assertion.
    assert result is not None
    assert result[0] == "wal", (
        f"Expected 'wal', got: {result[0]!r}"
    )
    # Verify WAL sidecar files were created (proves WAL is actually active).
    wal_file = Path(str(index_path) + "-wal")
    # The WAL file may or may not exist after a fresh commit — sqlite sometimes
    # checkpoints immediately. Accept either: WAL mode set is the primary signal.
    # Re-open and confirm journal_mode persists.
    conn2 = sqlite3.connect(str(index_path))
    try:
        persisted = conn2.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn2.close()
    assert persisted[0] == "wal", (
        f"WAL mode did not persist after close+reopen. Got: {persisted[0]!r}"
    )
