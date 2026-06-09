"""Tests for the quarried camp worktree spine.

Test contract from the plan (Slice 0):
- U2 regression: spine imports without dev_env modules (they are absent).
- repos.py manifest read/resolve: round-trip a synthetic manifest via
  read_manifest / resolve_manifest_for / repo_path.
- slug normalize/validate: accept/reject the right inputs.
- git-wrapper shapes: _git / _git_out form the expected argv.
- cmd_sweep with no registry → orphan_instances={}, no NotImplementedError.
- cmd_sweep --prune path hitting stub → raises NotImplementedError.
- D-H guard: the guard function emits a legible message on ImportError,
  not a raw traceback.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — locate the scripts dir under the plugin tree
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# U2 regression: spine imports without dev_env engine
# ---------------------------------------------------------------------------


def test_repos_imports_without_dev_env() -> None:
    """repos.py must be importable with dev_env.* modules absent."""
    import repos  # noqa: F401 — this is the import under test


def test_spine_imports_without_dev_env() -> None:
    """spine.py (worktree handlers) must be importable with dev_env.* absent."""
    import spine  # noqa: F401 — this is the import under test


def test_no_dev_env_in_sys_modules_after_import() -> None:
    """After importing repos + spine, no dev_env.* module must appear in sys.modules."""
    import repos  # noqa: F401
    import spine  # noqa: F401
    for mod in sys.modules:
        assert not mod.startswith("dev_env"), (
            f"dev_env module leaked into sys.modules: {mod!r}"
        )


# ---------------------------------------------------------------------------
# repos.py manifest read / resolve round-trip
# ---------------------------------------------------------------------------


def test_read_manifest_returns_none_when_absent(tmp_path: Path) -> None:
    import repos
    assert repos.read_manifest(tmp_path) is None


def test_read_manifest_returns_data(tmp_path: Path) -> None:
    import repos
    manifest = {"name": "test-slug", "repos": []}
    (tmp_path / repos._MANIFEST_FILENAME).write_text(json.dumps(manifest))
    result = repos.read_manifest(tmp_path)
    assert result is not None
    assert result["name"] == "test-slug"


def test_read_manifest_returns_none_on_bad_json(tmp_path: Path) -> None:
    import repos
    (tmp_path / repos._MANIFEST_FILENAME).write_text("not json{")
    result = repos.read_manifest(tmp_path)
    assert result is None


def test_resolve_manifest_for_direct(tmp_path: Path) -> None:
    import repos
    manifest = {"name": "slug1", "repos": [{"name": "alpha", "worktree_path": str(tmp_path / "alpha")}]}
    (tmp_path / repos._MANIFEST_FILENAME).write_text(json.dumps(manifest))
    result = repos.resolve_manifest_for(tmp_path)
    assert result is not None
    assert result["name"] == "slug1"


def test_resolve_manifest_for_sibling_marker(tmp_path: Path) -> None:
    import repos
    manifest_dir = tmp_path / "zenith-wt"
    manifest_dir.mkdir()
    manifest = {"name": "sibling-slug", "repos": []}
    (manifest_dir / repos._MANIFEST_FILENAME).write_text(json.dumps(manifest))
    sibling_dir = tmp_path / "sibling-wt"
    sibling_dir.mkdir()
    (sibling_dir / repos._SIBLING_MARKER).write_text(
        str(manifest_dir / repos._MANIFEST_FILENAME)
    )
    result = repos.resolve_manifest_for(sibling_dir)
    assert result is not None
    assert result["name"] == "sibling-slug"


def test_resolve_manifest_for_returns_none_when_absent(tmp_path: Path) -> None:
    import repos
    result = repos.resolve_manifest_for(tmp_path)
    assert result is None


def test_repo_path_from_manifest(tmp_path: Path) -> None:
    import repos
    sibling = tmp_path / "alpha-wt"
    sibling.mkdir()
    manifest = {"name": "s", "repos": [{"name": "alpha", "worktree_path": str(sibling)}]}
    (tmp_path / repos._MANIFEST_FILENAME).write_text(json.dumps(manifest))
    result = repos.repo_path(tmp_path, "alpha")
    assert result == sibling


def test_repo_path_fallback(tmp_path: Path) -> None:
    import repos
    result = repos.repo_path(tmp_path, "missing-repo")
    assert result == tmp_path / "missing-repo"


# ---------------------------------------------------------------------------
# Slug normalize / validate
# ---------------------------------------------------------------------------


def test_normalize_slug_lowercases() -> None:
    from spine import _normalize_slug
    result, changed = _normalize_slug("MySlug")
    assert result == "myslug"
    assert changed is True


def test_normalize_slug_replaces_non_alnum() -> None:
    from spine import _normalize_slug
    result, changed = _normalize_slug("my feature branch")
    assert result == "my-feature-branch"
    assert changed is True


def test_normalize_slug_trims_dashes() -> None:
    from spine import _normalize_slug
    result, changed = _normalize_slug("-hello-")
    assert result == "hello"
    assert changed is True


def test_normalize_slug_already_clean() -> None:
    from spine import _normalize_slug
    result, changed = _normalize_slug("clean-slug-123")
    assert result == "clean-slug-123"
    assert changed is False


def test_validate_slug_accepts_valid() -> None:
    from spine import _validate_slug
    _validate_slug("valid-slug-123")  # must not raise / exit


def test_validate_slug_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from spine import _validate_slug
    with pytest.raises(SystemExit):
        _validate_slug("")


def test_validate_slug_rejects_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    from spine import _validate_slug
    with pytest.raises(SystemExit):
        _validate_slug("Bad-Slug")


def test_resolve_slug_rejects_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    from spine import _resolve_slug
    with pytest.raises(SystemExit):
        _resolve_slug("../evil")


def test_resolve_slug_rejects_shell_metachar(monkeypatch: pytest.MonkeyPatch) -> None:
    from spine import _resolve_slug
    with pytest.raises(SystemExit):
        _resolve_slug("bad;slug")


def test_resolve_slug_normalizes_and_returns() -> None:
    from spine import _resolve_slug
    result = _resolve_slug("My Feature")
    assert result == "my-feature"


# ---------------------------------------------------------------------------
# git-wrapper shapes
# ---------------------------------------------------------------------------


def test_git_forms_correct_argv(tmp_path: Path) -> None:
    """_git forms [git, -C, <root>, ...args] and passes shell=False."""
    from spine import _git
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _git(tmp_path, "status", "--porcelain")
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert cmd == ["git", "-C", str(tmp_path), "status", "--porcelain"]
    assert call_args.kwargs.get("shell") is not True


def test_git_out_returns_stripped_stdout(tmp_path: Path) -> None:
    from spine import _git_out
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="  main  \n", stderr="")
        result = _git_out(tmp_path, "rev-parse", "--abbrev-ref", "HEAD")
    assert result == "main"


def test_git_out_returns_empty_on_nonzero(tmp_path: Path) -> None:
    from spine import _git_out
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="something", stderr="err")
        result = _git_out(tmp_path, "status")
    assert result == ""


# ---------------------------------------------------------------------------
# cmd_sweep: no registry → orphan_instances={}, no NotImplementedError
# ---------------------------------------------------------------------------


def test_cmd_sweep_no_registry_returns_empty_instances(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """cmd_sweep with no dev-env registry must return orphan_instances={}."""
    from spine import cmd_sweep
    with patch("spine._workspace_root", return_value=tmp_path), \
         patch("spine._canonical_root", return_value=tmp_path), \
         patch("spine._active_worktree_names", return_value=set()), \
         patch("spine._collect_orphan_worktrees", return_value={}):
        cmd_sweep(["--json"])
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["orphan_instances"] == {}


def test_cmd_sweep_no_registry_does_not_call_import_dev_env(tmp_path: Path) -> None:
    """cmd_sweep without --prune must not call _import_dev_env (stub)."""
    from spine import cmd_sweep
    with patch("spine._workspace_root", return_value=tmp_path), \
         patch("spine._canonical_root", return_value=tmp_path), \
         patch("spine._active_worktree_names", return_value=set()), \
         patch("spine._collect_orphan_worktrees", return_value={}), \
         patch("spine._import_dev_env") as mock_import:
        cmd_sweep([])
    mock_import.assert_not_called()


def test_cmd_sweep_prune_with_orphan_instance_raises_not_implemented(
    tmp_path: Path,
) -> None:
    """cmd_sweep --prune hitting the stub should raise NotImplementedError."""
    from spine import cmd_sweep
    reg_dir = tmp_path / ".worktree-dev"
    reg_dir.mkdir()
    vanished_root = tmp_path / "vanished-wt"
    registry = {
        "schema_version": 3,
        "instances": {
            "inst-001": {
                "paths": {"worktree_root": str(vanished_root)}
            }
        },
    }
    (reg_dir / "registry.json").write_text(json.dumps(registry))

    with patch("spine._workspace_root", return_value=tmp_path), \
         patch("spine._canonical_root", return_value=tmp_path), \
         patch("spine._active_worktree_names", return_value=set()), \
         patch("spine._collect_orphan_worktrees", return_value={}):
        with pytest.raises(NotImplementedError):
            cmd_sweep(["--prune"])


# ---------------------------------------------------------------------------
# JSON schema stability: status/sweep retain dev_env keys as null/{}
# ---------------------------------------------------------------------------


def test_sweep_report_retains_dev_env_schema_keys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json output must have orphan_instances (not dropped), defaulting to {}."""
    from spine import cmd_sweep
    with patch("spine._workspace_root", return_value=tmp_path), \
         patch("spine._canonical_root", return_value=tmp_path), \
         patch("spine._active_worktree_names", return_value=set()), \
         patch("spine._collect_orphan_worktrees", return_value={}):
        cmd_sweep(["--json"])
    report = json.loads(capsys.readouterr().out)
    assert "orphan_instances" in report


# ---------------------------------------------------------------------------
# D-H guard: legible ImportError, not raw traceback
# ---------------------------------------------------------------------------


def test_trailhead_paths_guard_emits_legible_message_on_import_error() -> None:
    """The guard function emits a human-readable message when trailhead.paths fails."""
    from spine import _check_trailhead_paths_importable
    import io

    buf = io.StringIO()
    result = _check_trailhead_paths_importable(
        _raise_import_error=True,
        _out=buf,
    )
    output = buf.getvalue()
    assert result is False
    assert "trailhead" in output.lower() or "install" in output.lower()


def test_trailhead_paths_guard_succeeds_when_importable() -> None:
    """The guard function returns True when trailhead.paths is importable."""
    from spine import _check_trailhead_paths_importable
    result = _check_trailhead_paths_importable()
    assert result is True
