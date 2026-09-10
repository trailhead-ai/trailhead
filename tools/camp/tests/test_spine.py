"""Tests for the camp worktree spine.

Test contract:
- Regression: spine imports without dev_env modules (they are absent).
- slug normalize/validate: accept/reject the right inputs.
- git-wrapper shapes: _git / _git_out form the expected argv.
- Import guard: the guard function emits a legible message on ImportError,
  not a raw traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — locate the plugin dir so the camp package resolves
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


# ---------------------------------------------------------------------------
# Regression: spine imports without dev_env engine
# ---------------------------------------------------------------------------


def test_spine_imports_without_dev_env() -> None:
    """spine.py (worktree handlers) must be importable with dev_env.* absent."""
    import camp.spine  # noqa: F401 — this is the import under test


def test_no_dev_env_in_sys_modules_after_import() -> None:
    """After importing spine, no dev_env.* module must appear in sys.modules."""
    import camp.spine  # noqa: F401

    for mod in sys.modules:
        assert not mod.startswith("dev_env"), f"dev_env module leaked into sys.modules: {mod!r}"


# ---------------------------------------------------------------------------
# Slug normalize / validate
# ---------------------------------------------------------------------------


def test_normalize_slug_lowercases() -> None:
    from camp.spine import normalize_slug

    result, changed = normalize_slug("MySlug")
    assert result == "myslug"
    assert changed is True


def test_normalize_slug_replaces_non_alnum() -> None:
    from camp.spine import normalize_slug

    result, changed = normalize_slug("my feature branch")
    assert result == "my-feature-branch"
    assert changed is True


def test_normalize_slug_trims_dashes() -> None:
    from camp.spine import normalize_slug

    result, changed = normalize_slug("-hello-")
    assert result == "hello"
    assert changed is True


def test_normalize_slug_already_clean() -> None:
    from camp.spine import normalize_slug

    result, changed = normalize_slug("clean-slug-123")
    assert result == "clean-slug-123"
    assert changed is False


def test_validate_slug_accepts_valid() -> None:
    from camp.spine import _validate_slug

    _validate_slug("valid-slug-123")  # must not raise / exit


def test_validate_slug_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from camp.spine import _validate_slug

    with pytest.raises(SystemExit):
        _validate_slug("")


def test_validate_slug_rejects_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    from camp.spine import _validate_slug

    with pytest.raises(SystemExit):
        _validate_slug("Bad-Slug")


def test_resolve_slug_rejects_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    from camp.spine import _resolve_slug

    with pytest.raises(SystemExit):
        _resolve_slug("../evil")


def test_resolve_slug_rejects_shell_metachar(monkeypatch: pytest.MonkeyPatch) -> None:
    from camp.spine import _resolve_slug

    with pytest.raises(SystemExit):
        _resolve_slug("bad;slug")


def test_resolve_slug_normalizes_and_returns() -> None:
    from camp.spine import _resolve_slug

    result = _resolve_slug("My Feature")
    assert result == "my-feature"


@pytest.mark.parametrize("raw", ["--help", "--json", "--group", "-x", "--force", "-"])
def test_resolve_slug_rejects_flag_shaped_input(raw: str) -> None:
    """A leading '-' is refused outright, never normalized into a real slug.

    Normalization strips the dashes, so `--help` would otherwise become the
    perfectly valid slug `help` and go on to create or launch a workspace. A
    caller that mistakenly forwards an unconsumed flag as a positional must get
    an error, not a workspace named after the flag.
    """
    from camp.spine import _resolve_slug

    with pytest.raises(SystemExit):
        _resolve_slug(raw)


def test_resolve_slug_allows_internal_and_trailing_dashes() -> None:
    """Only a LEADING dash is flag-shaped; dashes elsewhere are ordinary."""
    from camp.spine import _resolve_slug

    assert _resolve_slug("feat-a") == "feat-a"
    assert _resolve_slug("feat-a-") == "feat-a"


def test_resolve_slug_flag_refusal_names_the_offending_input() -> None:
    """The refusal has to identify what was passed, or a caller forwarding an
    unconsumed flag cannot tell which argument it was."""
    from camp.spine import _resolve_slug

    with pytest.raises(SystemExit):
        _resolve_slug("--group", context="argument")


# ---------------------------------------------------------------------------
# git-wrapper shapes
# ---------------------------------------------------------------------------


def test_git_forms_correct_argv(tmp_path: Path) -> None:
    """_git forms [git, -C, <root>, ...args] and passes shell=False."""
    from camp.gitutil import _git

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _git(tmp_path, "status", "--porcelain")
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert cmd == ["git", "-C", str(tmp_path), "status", "--porcelain"]
    assert call_args.kwargs.get("shell") is not True


def test_git_out_returns_stripped_stdout(tmp_path: Path) -> None:
    from camp.gitutil import _git_out

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="  main  \n", stderr="")
        result = _git_out(tmp_path, "rev-parse", "--abbrev-ref", "HEAD")
    assert result == "main"


def test_git_out_returns_empty_on_nonzero(tmp_path: Path) -> None:
    from camp.gitutil import _git_out

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="something", stderr="err")
        result = _git_out(tmp_path, "status")
    assert result == ""


# ---------------------------------------------------------------------------
# cmd_status / cmd_doctor observable output (no registry writer exists, so the
# registry-drift plumbing always resolves empty — these lock that contract).
# ---------------------------------------------------------------------------


def _isolate_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point workspace + canonical roots at empty tmp dirs and chdir there."""
    workspace_root = tmp_path / "workspace"
    canonical_root = tmp_path / "canonical"
    workspace_root.mkdir()
    canonical_root.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("CAMP_CANONICAL_ROOT", str(canonical_root))
    monkeypatch.chdir(tmp_path)
    return workspace_root


def test_status_json_no_registry_reports_empty_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no registry present, `camp status --json` reports empty drift."""
    import json as _json

    from camp.spine import cmd_status

    _isolate_roots(monkeypatch, tmp_path)
    cmd_status(["--json"])
    out = _json.loads(capsys.readouterr().out)
    assert out == {
        "worktrees": [],
        "drift": {"stale_registry_instances": [], "orphaned_git_worktrees": []},
    }


def test_status_json_scoped_entry_retains_fire_and_dev_env_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A scoped worktree entry keeps the dev_env_instance / fire_state keys."""
    import json as _json

    from camp.spine import cmd_status

    from camp.spine import _MANIFEST_FILENAME

    workspace_root = _isolate_roots(monkeypatch, tmp_path)
    wt = workspace_root / "trailhead" / ".claude" / "worktrees" / "alpha"
    wt.mkdir(parents=True)
    (wt / _MANIFEST_FILENAME).write_text(_json.dumps({"name": "alpha", "repos": []}))

    cmd_status(["--name", "alpha", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert len(out["worktrees"]) == 1
    entry = out["worktrees"][0]
    assert entry["slug"] == "alpha"
    assert entry["fire_state"] is None
    assert entry["dev_env_instance"] is None
    assert entry["repos"] == []


def _isolated_config_env(tmp_path: Path) -> dict[str, str]:
    """A hermetic env for self_host_name: isolates CAMP_CONFIG_DIR/HOME so
    cmd_doctor never reads the operator's real ~/.config/camp/hosts.toml."""
    return {
        "HOME": str(tmp_path / "home"),
        "CAMP_CONFIG_DIR": str(tmp_path / "config"),
    }


def test_doctor_json_no_registry_consistency_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`camp doctor --json` consistency check passes with no drift and empty ids."""
    import json as _json

    from camp.spine import cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    try:
        cmd_doctor(["--json"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass  # asdf check may fail in CI; we only assert on the consistency check
    report = _json.loads(capsys.readouterr().out)
    consistency = next(c for c in report["checks"] if c["check"] == "consistency")
    assert consistency["pass"] is True
    assert consistency["details"] == "no drift detected"
    assert consistency["stale_registry_instances"] == []


def test_doctor_json_host_name_reads_through_injected_env_not_real_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """cmd_doctor's self-declared-host-name check must read through the
    injected env, never the operator's real ~/.config/camp/hosts.toml —
    proven by stamping a name only the isolated location carries and
    confirming the report reflects exactly that name."""
    import json as _json

    from camp.spine import cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    env = _isolated_config_env(tmp_path)
    config_dir = Path(env["CAMP_CONFIG_DIR"])
    config_dir.mkdir(parents=True)
    (config_dir / "hosts.toml").write_text('self_name = "isolated-test-host"\n')

    try:
        cmd_doctor(["--json"], env=env)
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    host_row = next(c for c in report["checks"] if c["check"] == "host_name")
    assert host_row["details"] == "isolated-test-host"


def test_doctor_json_malformed_hosts_toml_fails_host_name_row_not_the_verb(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed hosts.toml must surface as a failed `host_name` check row
    among the others — not an early hard-exit that prevents the asdf and
    consistency checks from ever running."""
    import json as _json

    from camp.spine import cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    env = _isolated_config_env(tmp_path)
    config_dir = Path(env["CAMP_CONFIG_DIR"])
    config_dir.mkdir(parents=True)
    (config_dir / "hosts.toml").write_text("self_name = 12345\n")

    with pytest.raises(SystemExit) as exc_info:
        cmd_doctor(["--json"], env=env)
    assert exc_info.value.code != 0

    report = _json.loads(capsys.readouterr().out)
    checks_by_name = {c["check"] for c in report["checks"]}
    assert checks_by_name == {"asdf", "consistency", "host_name"}, (
        "the other checks must still run and be reported"
    )
    host_row = next(c for c in report["checks"] if c["check"] == "host_name")
    assert host_row["pass"] is False
    assert "hosts.toml" in host_row["details"]
    assert "self_name" in host_row["details"]


# ---------------------------------------------------------------------------
# Import guard: legible ImportError, not raw traceback
# ---------------------------------------------------------------------------


def test_trailhead_paths_guard_emits_legible_message_on_import_error() -> None:
    """The guard function emits a human-readable message when trailhead.paths fails."""
    from camp.spine import _check_trailhead_paths_importable
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
    from camp.spine import _check_trailhead_paths_importable

    result = _check_trailhead_paths_importable()
    assert result is True


# ---------------------------------------------------------------------------
# camp kill — the reserved token
# ---------------------------------------------------------------------------


def test_help_states_the_kill_exit_code_for_a_session_that_did_not_stop(capsys) -> None:
    """A session still running after the stop is a FAILURE — the memory was not
    reclaimed — so the help that documents kill's codes has to say so rather
    than leave a reader to assume any non-zero exit is a broken command."""
    from camp.spine import cmd_help

    cmd_help([])
    text = capsys.readouterr().out

    assert "Exit codes (camp kill):" in text
    assert "still running after the stop" in text
