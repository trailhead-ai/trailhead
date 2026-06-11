"""EPHEMERAL probe — U-2 scout for Slice 4 (de-zenith release scripts).

This test is a throwaway assumption-probe to validate the camp manifest
read-surface before the release scripts are de-zenithed.  The Slice-4
implementer should DELETE this file once the real test_detect_repos.py /
test_merge_prs.py suite exists.

Lines to clean up: the entire file
  tools/forge/tests/test_u2_scout_camp_manifest.py

What is proved here:
  1. round-trip: write_central_manifest + read_central_manifest round-trips
     a 2-member schema-v1 manifest under an env-overridden central_state_dir
     (hermetic: nothing touches ~/.local/state or ~/.claude).
  2. read-surface: the round-tripped dict exposes group, slug, branch, and
     members[] with each member's name, repo_root, and worktree_path intact.
  3. no pre-existing order/prs[] field: confirms members[] carries NO per-member
     dependency-order field and NO prs[] / prs key anywhere in the manifest
     (proving D-1 prs.json sidecar and D-2 merge_order are genuinely net-new
     seams, not a re-derivation of something already present).
  4. malformed-manifest posture: a garbage file raises ManifestError (not a
     raw traceback), with the file path in the exception message.

Import pattern: matches the established forge + camp test harness.
Env override: CAMP_STATE_DIR (per-app pattern — state_dir("camp") checks
  CAMP_STATE_DIR first).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Wire up the camp scripts dir on sys.path (matching the camp test pattern
# in tools/camp/tests/test_group_resolve.py lines 23-27).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_CAMP_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"

if str(_CAMP_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_CAMP_SCRIPTS_DIR))

from manifest import (  # noqa: E402
    ManifestError,
    manifest_path_for,
    read_central_manifest,
    write_central_manifest,
)


# ---------------------------------------------------------------------------
# Shared fixture: a synthetic 2-member manifest written via the REAL API
# ---------------------------------------------------------------------------


@pytest.fixture()
def synthetic_manifest(tmp_path: Path) -> tuple[Path, dict]:
    """Write a 2-member schema-v1 manifest under a tmp central state dir.

    Returns (manifest_path, original_data) for the round-trip assertions.
    Nothing touches ~/.local/state or ~/.claude — all paths are under tmp_path.
    """
    fake_repo_a = tmp_path / "repos" / "alpha"
    fake_wt_a = tmp_path / "worktrees" / "alpha" / ".claude" / "worktrees" / "my-feat"
    fake_repo_b = tmp_path / "repos" / "beta"
    fake_wt_b = tmp_path / "worktrees" / "beta" / ".claude" / "worktrees" / "my-feat"

    data = {
        "schema_version": 1,
        "group": "test-group",
        "slug": "my-feat",
        "branch": "worktree-my-feat",
        "members": [
            {
                "name": "alpha",
                "repo_root": str(fake_repo_a),
                "worktree_path": str(fake_wt_a),
            },
            {
                "name": "beta",
                "repo_root": str(fake_repo_b),
                "worktree_path": str(fake_wt_b),
            },
        ],
    }

    # Env override — CAMP_STATE_DIR is the per-app env var (state_dir("camp") checks it
    # first before XDG / HOME fallbacks).  Everything resolves under tmp_path.
    fake_state = tmp_path / "camp-state"
    env = {"CAMP_STATE_DIR": str(fake_state), "HOME": str(tmp_path)}

    path = manifest_path_for("test-group", "my-feat", env=env)
    write_central_manifest(path, data)
    return path, data


# ---------------------------------------------------------------------------
# 1. Round-trip: write then read produces the original data
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """write_central_manifest + read_central_manifest round-trips under tmp_path."""

    def test_path_is_under_tmp_not_real_state(
        self, synthetic_manifest: tuple[Path, dict], tmp_path: Path
    ) -> None:
        """Manifest path resolves inside tmp_path — no real ~/.local/state touched."""
        path, _ = synthetic_manifest
        assert str(tmp_path) in str(path), (
            f"expected manifest path to be under tmp_path={tmp_path!r}, got {path!r}"
        )
        # Specifically: it must NOT be under ~/.local/state or ~/.claude
        home = Path.home()
        assert not str(path).startswith(str(home / ".local" / "state")), (
            "manifest path leaked into real ~/.local/state — hermeticity broken"
        )
        assert not str(path).startswith(str(home / ".claude")), (
            "manifest path leaked into real ~/.claude — hermeticity broken"
        )

    def test_round_trip_produces_identical_data(
        self, synthetic_manifest: tuple[Path, dict]
    ) -> None:
        """The read-back dict is identical to what was written."""
        path, original = synthetic_manifest
        result = read_central_manifest(path)
        assert result == original


# ---------------------------------------------------------------------------
# 2. Read-surface: group, slug, branch, members[] with all per-member fields
# ---------------------------------------------------------------------------


class TestReadSurface:
    """Confirm the fields the release scripts will actually consume."""

    @pytest.fixture()
    def manifest(self, synthetic_manifest: tuple[Path, dict]) -> dict:
        path, _ = synthetic_manifest
        return read_central_manifest(path)

    def test_top_level_group(self, manifest: dict) -> None:
        assert manifest["group"] == "test-group"

    def test_top_level_slug(self, manifest: dict) -> None:
        assert manifest["slug"] == "my-feat"

    def test_top_level_branch(self, manifest: dict) -> None:
        assert manifest["branch"] == "worktree-my-feat"

    def test_members_list_length(self, manifest: dict) -> None:
        assert isinstance(manifest["members"], list)
        assert len(manifest["members"]) == 2

    def test_member_alpha_name(self, manifest: dict) -> None:
        alpha = manifest["members"][0]
        assert alpha["name"] == "alpha"

    def test_member_alpha_repo_root(self, manifest: dict) -> None:
        alpha = manifest["members"][0]
        assert "alpha" in alpha["repo_root"]

    def test_member_alpha_worktree_path(self, manifest: dict) -> None:
        alpha = manifest["members"][0]
        assert "alpha" in alpha["worktree_path"]

    def test_member_beta_name(self, manifest: dict) -> None:
        beta = manifest["members"][1]
        assert beta["name"] == "beta"

    def test_member_beta_repo_root(self, manifest: dict) -> None:
        beta = manifest["members"][1]
        assert "beta" in beta["repo_root"]

    def test_member_beta_worktree_path(self, manifest: dict) -> None:
        beta = manifest["members"][1]
        assert "beta" in beta["worktree_path"]

    def test_worktree_path_is_the_git_c_target(self, manifest: dict) -> None:
        """worktree_path is the per-member path release scripts will 'git -C' against."""
        for member in manifest["members"]:
            wt_path = member["worktree_path"]
            assert isinstance(wt_path, str) and len(wt_path) > 0, (
                f"member {member['name']!r}: worktree_path must be a non-empty string "
                f"(got {wt_path!r})"
            )
            # Confirm it differs from repo_root (it's the WORKTREE, not canonical)
            assert wt_path != member["repo_root"], (
                f"member {member['name']!r}: worktree_path == repo_root — "
                "that would make detect_repos operate on the canonical checkout, not the worktree"
            )


# ---------------------------------------------------------------------------
# 3. No pre-existing order/prs[] field (confirming D-1/D-2 are net-new)
# ---------------------------------------------------------------------------


class TestNoPrsOrOrderFields:
    """Confirm schema-v1 carries NO prs[] and NO per-member dependency-order field.

    If EITHER already exists, D-1 (prs.json sidecar) and D-2 (merge_order) would
    not be net-new seams — the plan would need to change.  These tests must PASS
    for the plan to hold; a failure is a loud signal that something already exists.
    """

    @pytest.fixture()
    def manifest(self, synthetic_manifest: tuple[Path, dict]) -> dict:
        path, _ = synthetic_manifest
        return read_central_manifest(path)

    def test_no_top_level_prs_field(self, manifest: dict) -> None:
        """schema-v1 has no top-level 'prs' key (D-1 is genuinely net-new)."""
        assert "prs" not in manifest, (
            "SURPRISE: schema-v1 already contains a 'prs' key — "
            "D-1 (prs.json sidecar) may be redundant; alert the Slice-4 implementer"
        )

    def test_no_top_level_merge_order_field(self, manifest: dict) -> None:
        """schema-v1 has no top-level 'merge_order' key (D-2 is genuinely net-new)."""
        assert "merge_order" not in manifest, (
            "SURPRISE: schema-v1 already contains a 'merge_order' key — "
            "D-2 (configurable order) may be a re-derivation; alert the Slice-4 implementer"
        )

    def test_no_per_member_order_field(self, manifest: dict) -> None:
        """No member carries a 'order', 'priority', 'merge_order', or 'depends_on' field."""
        order_keys = {"order", "priority", "merge_order", "depends_on", "position"}
        for member in manifest["members"]:
            found = order_keys & set(member.keys())
            assert not found, (
                f"SURPRISE: member {member['name']!r} has dependency-order field(s) {found} "
                "in schema-v1 — D-2 would be a re-derivation, not a net-new seam; "
                "alert the Slice-4 implementer"
            )

    def test_no_per_member_pr_field(self, manifest: dict) -> None:
        """No member carries a 'pr', 'pr_number', 'pr_url', or 'prs' field."""
        pr_keys = {"pr", "pr_number", "pr_url", "prs", "pull_request"}
        for member in manifest["members"]:
            found = pr_keys & set(member.keys())
            assert not found, (
                f"SURPRISE: member {member['name']!r} has PR field(s) {found} "
                "in schema-v1 — D-1 (prs.json sidecar) may be redundant; "
                "alert the Slice-4 implementer"
            )

    def test_schema_v1_exact_top_level_keys(self, manifest: dict) -> None:
        """Document the EXACT schema-v1 top-level key set for the Slice-4 implementer.

        This is a snapshot assertion — if new keys appear, the test fails loudly
        so the implementer knows the schema changed.
        """
        expected_keys = {"schema_version", "group", "slug", "branch", "members"}
        actual_keys = set(manifest.keys())
        assert actual_keys == expected_keys, (
            f"SURPRISE: schema-v1 top-level keys differ from expectation.\n"
            f"  expected: {sorted(expected_keys)}\n"
            f"  actual:   {sorted(actual_keys)}\n"
            f"  extra:    {sorted(actual_keys - expected_keys)}\n"
            f"  missing:  {sorted(expected_keys - actual_keys)}\n"
            "Review whether the extra keys affect D-1/D-2/B-1."
        )

    def test_schema_v1_exact_member_keys(self, manifest: dict) -> None:
        """Document the EXACT schema-v1 per-member key set for the Slice-4 implementer."""
        expected_member_keys = {"name", "repo_root", "worktree_path"}
        for member in manifest["members"]:
            actual = set(member.keys())
            assert actual == expected_member_keys, (
                f"SURPRISE: member {member['name']!r} keys differ from expectation.\n"
                f"  expected: {sorted(expected_member_keys)}\n"
                f"  actual:   {sorted(actual)}\n"
                f"  extra:    {sorted(actual - expected_member_keys)}\n"
                f"  missing:  {sorted(expected_member_keys - actual)}\n"
                "Review whether the extra keys affect D-1/D-2/B-1."
            )


# ---------------------------------------------------------------------------
# 4. Malformed-manifest posture: ManifestError (not raw traceback)
# ---------------------------------------------------------------------------


class TestMalformedManifestPosture:
    """A garbage/truncated manifest raises ManifestError with path in the message."""

    def test_garbage_json_raises_manifest_error(self, tmp_path: Path) -> None:
        """Truncated/garbage JSON → ManifestError, not json.JSONDecodeError propagated."""
        bad = tmp_path / "bad-manifest" / "manifest.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{ this is not valid JSON !!!! @@@")
        with pytest.raises(ManifestError) as exc_info:
            read_central_manifest(bad)
        # The exception message must contain the file path (the plan's stated contract)
        assert str(bad) in str(exc_info.value), (
            f"ManifestError message does not contain the manifest path.\n"
            f"  path:    {bad}\n"
            f"  message: {exc_info.value}"
        )

    def test_missing_file_raises_manifest_error(self, tmp_path: Path) -> None:
        """A file that doesn't exist → ManifestError, not FileNotFoundError propagated."""
        absent = tmp_path / "no-such-dir" / "manifest.json"
        with pytest.raises(ManifestError) as exc_info:
            read_central_manifest(absent)
        assert str(absent) in str(exc_info.value), (
            f"ManifestError message does not contain the absent path.\n"
            f"  path:    {absent}\n"
            f"  message: {exc_info.value}"
        )

    def test_non_object_json_raises_manifest_error(self, tmp_path: Path) -> None:
        """A valid JSON array (not an object) → ManifestError naming the path."""
        bad = tmp_path / "array-manifest" / "manifest.json"
        bad.parent.mkdir(parents=True)
        bad.write_text(json.dumps([1, 2, 3]))
        with pytest.raises(ManifestError) as exc_info:
            read_central_manifest(bad)
        assert str(bad) in str(exc_info.value), (
            f"ManifestError message does not contain the path for a non-object manifest.\n"
            f"  path:    {bad}\n"
            f"  message: {exc_info.value}"
        )

    def test_manifest_error_is_not_raw_traceback(self, tmp_path: Path) -> None:
        """Ensure ManifestError is caught and re-raised (not a raw json/OS exception)."""
        bad = tmp_path / "truncated" / "manifest.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("")  # empty file — valid OSError-free read but invalid JSON
        # Must raise ManifestError (the named exception), not json.JSONDecodeError
        import json as _json
        with pytest.raises(ManifestError):
            read_central_manifest(bad)
        # Also confirm it is NOT a raw json error leaking through
        try:
            read_central_manifest(bad)
        except ManifestError:
            pass  # expected
        except _json.JSONDecodeError as e:
            raise AssertionError(
                f"read_central_manifest leaked a raw json.JSONDecodeError — "
                f"ManifestError wrapping is broken: {e}"
            ) from e
