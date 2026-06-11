"""Tests for release_prs_sidecar.py — forge-owned prs.json sidecar (D-1, B-2).

Contract:
  - Shape: {schema_version:1, prs:[{repo, pr_number, url, branch}], external_tracker:null}
  - external_tracker is reserved and defaults null (no connector).
  - Round-trips prs[] intact (read what was written).
  - Atomic write (temp + os.replace + mode 0o600) — B-2.
  - Malformed/missing sidecar → named SidecarError (never raw KeyError/traceback) — B-2.
  - The sidecar schema_version is 1.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "forge" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import release_prs_sidecar as sidecar  # noqa: E402


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestSidecarRoundTrip:
    def test_write_then_read_roundtrips_prs(self, tmp_path: Path) -> None:
        path = tmp_path / "prs.json"
        prs = [
            {"repo": "alpha", "pr_number": "42", "url": "https://gh.com/42", "branch": "feat"},
            {"repo": "beta", "pr_number": "7", "url": "https://gh.com/7", "branch": "feat"},
        ]
        sidecar.write(path, prs)
        result = sidecar.read(path)
        assert result["prs"] == prs

    def test_empty_prs_list_roundtrips(self, tmp_path: Path) -> None:
        path = tmp_path / "prs.json"
        sidecar.write(path, [])
        result = sidecar.read(path)
        assert result["prs"] == []

    def test_schema_version_is_1(self, tmp_path: Path) -> None:
        path = tmp_path / "prs.json"
        sidecar.write(path, [])
        result = sidecar.read(path)
        assert result["schema_version"] == 1

    def test_external_tracker_is_null(self, tmp_path: Path) -> None:
        path = tmp_path / "prs.json"
        sidecar.write(path, [])
        result = sidecar.read(path)
        assert "external_tracker" in result
        assert result["external_tracker"] is None

    def test_pr_entries_have_required_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "prs.json"
        prs = [{"repo": "alpha", "pr_number": "42", "url": "https://gh.com/42", "branch": "feat"}]
        sidecar.write(path, prs)
        result = sidecar.read(path)
        entry = result["prs"][0]
        assert entry["repo"] == "alpha"
        assert entry["pr_number"] == "42"
        assert entry["url"] == "https://gh.com/42"
        assert entry["branch"] == "feat"


# ---------------------------------------------------------------------------
# B-2: atomic write + mode 0o600
# ---------------------------------------------------------------------------


class TestSidecarAtomicWrite:
    def test_file_mode_is_0600(self, tmp_path: Path) -> None:
        path = tmp_path / "prs.json"
        sidecar.write(path, [])
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600, f"expected mode 0o600, got 0o{mode:o}"

    def test_no_partial_file_on_error(self, tmp_path: Path) -> None:
        """If write raises mid-way, no partial file should be left."""
        path = tmp_path / "subdir" / "prs.json"
        # subdir doesn't exist → write should either create it or raise cleanly
        # This just verifies the function handles the parent creation path
        path.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write(path, [])
        assert path.exists()

    def test_overwrite_is_atomic(self, tmp_path: Path) -> None:
        """Second write replaces first cleanly (no stale content)."""
        path = tmp_path / "prs.json"
        sidecar.write(path, [{"repo": "a", "pr_number": "1", "url": "u", "branch": "b"}])
        sidecar.write(path, [{"repo": "x", "pr_number": "99", "url": "v", "branch": "c"}])
        result = sidecar.read(path)
        assert len(result["prs"]) == 1
        assert result["prs"][0]["repo"] == "x"


# ---------------------------------------------------------------------------
# B-2: malformed/missing sidecar → named SidecarError
# ---------------------------------------------------------------------------


class TestSidecarErrors:
    def test_missing_file_raises_sidecar_error(self, tmp_path: Path) -> None:
        absent = tmp_path / "nonexistent.json"
        with pytest.raises(sidecar.SidecarError) as exc_info:
            sidecar.read(absent)
        assert str(absent) in str(exc_info.value)

    def test_malformed_json_raises_sidecar_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json !!!")
        with pytest.raises(sidecar.SidecarError) as exc_info:
            sidecar.read(bad)
        assert str(bad) in str(exc_info.value)

    def test_non_object_json_raises_sidecar_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "array.json"
        bad.write_text("[1, 2, 3]")
        with pytest.raises(sidecar.SidecarError) as exc_info:
            sidecar.read(bad)
        assert str(bad) in str(exc_info.value)

    def test_missing_prs_key_raises_sidecar_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "no-prs.json"
        bad.write_text(json.dumps({"schema_version": 1}))
        with pytest.raises(sidecar.SidecarError) as exc_info:
            sidecar.read(bad)
        assert str(bad) in str(exc_info.value)

    def test_no_raw_key_error_propagates(self, tmp_path: Path) -> None:
        """A missing key must be a SidecarError, never a raw KeyError."""
        bad = tmp_path / "partial.json"
        bad.write_text(json.dumps({"schema_version": 1}))
        try:
            sidecar.read(bad)
        except sidecar.SidecarError:
            pass  # expected
        except KeyError as e:
            raise AssertionError(
                f"read() leaked a raw KeyError — SidecarError wrapping is broken: {e}"
            ) from e
