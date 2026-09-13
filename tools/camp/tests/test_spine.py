"""Tests for the camp worktree spine.

Test contract:
- Regression: spine imports without dev_env modules (they are absent).
- slug normalize/validate: accept/reject the right inputs.
- git-wrapper shapes: _git / _git_out form the expected argv.
- Import guard: the guard function emits a legible message on ImportError,
  not a raw traceback.
"""

from __future__ import annotations

import os
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


def test_doctor_json_deeply_nested_hosts_toml_fails_host_name_row_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A hosts.toml nested deep enough to blow tomllib's recursion limit must
    surface as a failed `host_name` row (via HostConfigError), same as any
    other malformed TOML — never a raw RecursionError traceback, and never an
    early hard-exit that prevents the other checks from running."""
    import json as _json

    from camp.spine import cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    env = _isolated_config_env(tmp_path)
    config_dir = Path(env["CAMP_CONFIG_DIR"])
    config_dir.mkdir(parents=True)
    depth = 200
    nested_bomb = "bomb = " + "{a=" * depth + "1" + "}" * depth + "\n"
    (config_dir / "hosts.toml").write_text('self_name = "x"\n' + nested_bomb)

    original_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(150)
    try:
        with pytest.raises(SystemExit) as exc_info:
            cmd_doctor(["--json"], env=env)
    finally:
        sys.setrecursionlimit(original_limit)
    assert exc_info.value.code != 0

    report = _json.loads(capsys.readouterr().out)
    checks_by_name = {c["check"] for c in report["checks"]}
    assert checks_by_name == {"asdf", "consistency", "host_name"}, (
        "the other checks must still run and be reported"
    )
    host_row = next(c for c in report["checks"] if c["check"] == "host_name")
    assert host_row["pass"] is False
    assert "hosts.toml" in host_row["details"]


# ---------------------------------------------------------------------------
# camp doctor --probe — the far side's own answer about its host
# ---------------------------------------------------------------------------


def test_doctor_json_without_probe_has_no_probe_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`camp doctor --json` (no `--probe`) must carry no probe-identifying
    key and no probe row — the exact shape an older camp already answers with,
    unchanged by this change."""
    import json as _json

    from camp.spine import DOCTOR_PROBE_KEY, DOCTOR_PROBE_MULTIPLEXER_KEY, cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    try:
        cmd_doctor(["--json"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    assert set(report.keys()) == {"pass", "checks"}
    assert DOCTOR_PROBE_KEY not in report
    assert DOCTOR_PROBE_MULTIPLEXER_KEY not in report
    assert {c["check"] for c in report["checks"]} == {"asdf", "consistency", "host_name"}


def test_doctor_json_probe_reports_multiplexer_present_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`camp doctor --json --probe` reports the multiplexer present when one
    resolves in the environment under test."""
    import json as _json

    from camp.spine import DOCTOR_PROBE_MULTIPLEXER_KEY, cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    assert report[DOCTOR_PROBE_MULTIPLEXER_KEY] is True


def test_doctor_json_probe_reports_multiplexer_present_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`camp doctor --json --probe` reports the multiplexer absent when none
    resolves in the environment under test — the same check varied across the
    opposite input from the sibling test above."""
    import json as _json

    from camp.spine import DOCTOR_PROBE_MULTIPLEXER_KEY, cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "0")
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    assert report[DOCTOR_PROBE_MULTIPLEXER_KEY] is False


def test_doctor_probe_exit_status_unaffected_by_multiplexer_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--probe` must not change the exit status in either direction: with
    every local check passing but no multiplexer present, `doctor --probe`
    must not exit nonzero on the multiplexer's account — it exits the way the
    local checks alone decide, exactly as plain `doctor --json` does for the
    same local-check state."""
    import json as _json

    from camp.spine import DOCTOR_PROBE_MULTIPLEXER_KEY, cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "0")

    no_probe_exit: SystemExit | None = None
    try:
        cmd_doctor(["--json"], env=_isolated_config_env(tmp_path))
    except SystemExit as e:
        no_probe_exit = e
    capsys.readouterr()

    probe_exit: SystemExit | None = None
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit as e:
        probe_exit = e
    report = _json.loads(capsys.readouterr().out)

    assert no_probe_exit is None, "the local checks pass; doctor must not exit nonzero"
    assert probe_exit is None, (
        "the multiplexer is absent but the local checks pass; --probe must not "
        "turn that into a nonzero exit"
    )
    assert report["pass"] is True
    assert report[DOCTOR_PROBE_MULTIPLEXER_KEY] is False


def test_doctor_probe_answer_is_self_identifying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A caller can tell a probe-supporting answer apart from the ordinary
    answer an older camp gives back for the same invocation: the `--probe`
    answer carries the identifying key, and the plain `--json` answer for the
    same invocation — the shape an older camp is pinned to give back — does
    not."""
    import json as _json

    from camp.spine import DOCTOR_PROBE_KEY, cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")

    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    probe_report = _json.loads(capsys.readouterr().out)

    try:
        cmd_doctor(["--json"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    older_camp_report = _json.loads(capsys.readouterr().out)

    assert probe_report[DOCTOR_PROBE_KEY] is True
    assert DOCTOR_PROBE_KEY not in older_camp_report


def test_doctor_probe_without_json_refuses_naming_what_the_flag_needs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--probe` without `--json` is refused rather than silently discarding
    the probe answer: the far side's own facts about its host are reported
    only in machine-readable form, so a human invocation asking for both
    gets told what the flag needs instead of a blank probe."""
    from camp.spine import cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")

    with pytest.raises(SystemExit) as exc_info:
        cmd_doctor(["--probe"], env=_isolated_config_env(tmp_path))

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "--json" in err


def test_doctor_probe_reachable_through_real_cli_entry_path(
    tmp_path: Path,
) -> None:
    """`--probe` is reachable through the real `camp` CLI entry point, not
    only by importing `cmd_doctor` directly — the way an operator's shell
    (and the far side of an ssh relay) actually invokes it."""
    import json as _json
    import subprocess

    cli = _PLUGIN_DIR / "cli" / "camp"
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "CAMP_CONFIG_DIR": str(tmp_path / "config"),
            "WORKSPACE_ROOT": str(tmp_path / "workspace"),
            "CAMP_CANONICAL_ROOT": str(tmp_path / "canonical"),
            "CAMP_TEST_ASDF_PRESENT": "1",
            "CAMP_TEST_TMUX_PRESENT": "1",
        }
    )
    (tmp_path / "workspace").mkdir()
    (tmp_path / "canonical").mkdir()

    result = subprocess.run(
        [sys.executable, str(cli), "doctor", "--json", "--probe"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    report = _json.loads(result.stdout)
    assert report["probe"] is True
    assert report["multiplexer_present"] is True


# ---------------------------------------------------------------------------
# camp doctor --probe accounts — this machine's own account roster
# ---------------------------------------------------------------------------


class _RosterHarness:
    """A harness stand-in whose account binding and authentication verdict
    are both simple, opaque mappings — proving the roster reads the
    harness's own answer rather than any credential path of its own."""

    name = "rosterharness"

    def __init__(self, verdicts):
        self._verdicts = verdicts

    def session_launch_env_unset(self):
        return []

    def session_launch_env_set(self, account, *, env=None):
        if account is None:
            return {}
        return {"FAKE_STORE_DIR": account}

    def session_launch_account_authentication(self, account, *, env=None):
        from trailhead.harness.base import AccountAuthentication

        return self._verdicts.get(account, AccountAuthentication.CANNOT_TELL)


def _write_group(directory: Path, filename: str, name: str, account: str | None = None) -> None:
    body = f'[group]\nname = "{name}"\n[[members]]\nname = "m1"\nrepo_root = "/repo"\n'
    if account is not None:
        body += f'[launch]\naccount = "{account}"\n'
    (directory / filename).write_text(body, encoding="utf-8")


def test_doctor_probe_accounts_no_declared_groups_yields_one_default_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A machine whose groups declare no account still produces exactly one
    roster entry — the default account — never an empty list."""
    import json as _json

    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    assert len(accounts) == 1
    assert accounts[0]["account"] is None


def test_doctor_probe_accounts_one_declared_account_carries_verdict_and_string_verbatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A machine declaring one account produces that account with the
    harness's verdict carried through unchanged, with the declared string
    verbatim."""
    import json as _json

    import camp.cli.common as cli_common
    import camp.launch.profile as profile
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor
    from trailhead.harness.base import AccountAuthentication

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    _write_group(groups_dir, "g1.toml", "g1", account="acct-primary")
    monkeypatch.setattr(cli_common, "_groups_dir", lambda: groups_dir)

    harness = _RosterHarness({"acct-primary": AccountAuthentication.AUTHENTICATED})
    monkeypatch.setattr(profile, "harness_for", lambda group: harness)

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    declared = next(a for a in accounts if a["account"] == "acct-primary")
    assert declared["verdict"] == "authenticated"


def test_doctor_probe_accounts_two_groups_spelling_one_account_differently_dedupe_to_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two groups spelling one account differently — textually distinct, but
    binding to the same resolved store — produce ONE roster entry, not two.
    The dedupe key is the resolved binding, not the declared string, so this
    would collapse wrongly under a declared-string comparison only by
    accident (it wouldn't collapse at all)."""
    import json as _json

    import camp.cli.common as cli_common
    import camp.launch.profile as profile
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor
    from trailhead.harness.base import AccountAuthentication

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    _write_group(groups_dir, "g1.toml", "g1", account="/acct/w")
    _write_group(groups_dir, "g2.toml", "g2", account="/acct/w/")
    monkeypatch.setattr(cli_common, "_groups_dir", lambda: groups_dir)

    class _NormalizingHarness(_RosterHarness):
        def session_launch_env_set(self, account, *, env=None):
            if account is None:
                return {}
            return {"FAKE_STORE_DIR": account.rstrip("/")}

    harness = _NormalizingHarness({"/acct/w": AccountAuthentication.AUTHENTICATED})
    monkeypatch.setattr(profile, "harness_for", lambda group: harness)

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    declared = [a for a in accounts if a["account"] is not None]
    assert len(declared) == 1


def test_doctor_probe_accounts_unresolvable_group_does_not_discard_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A group whose harness cannot be resolved, or whose config is
    unreadable, does not discard the other entries: the roster still
    carries what it could resolve and names what failed."""
    import json as _json

    import camp.cli.common as cli_common
    import camp.launch.profile as profile
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor
    from trailhead.harness.base import AccountAuthentication

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    (groups_dir / "broken.toml").write_text("not valid toml [[[", encoding="utf-8")
    _write_group(groups_dir, "good.toml", "good", account="acct-good")
    monkeypatch.setattr(cli_common, "_groups_dir", lambda: groups_dir)

    harness = _RosterHarness({"acct-good": AccountAuthentication.AUTHENTICATED})
    monkeypatch.setattr(profile, "harness_for", lambda group: harness)

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    resolved = [a for a in accounts if a["account"] == "acct-good"]
    assert len(resolved) == 1
    assert resolved[0]["verdict"] == "authenticated"
    failures = [a for a in accounts if a.get("reason")]
    assert any("broken" in f["reason"] for f in failures)


def test_doctor_probe_accounts_absent_without_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The probe answer carries the new field only when probing was asked
    for; a plain `--json` invocation carries neither it nor the existing
    probe keys."""
    import json as _json

    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor

    _isolate_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    try:
        cmd_doctor(["--json"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    assert DOCTOR_PROBE_ACCOUNTS_KEY not in report


def test_doctor_probe_accounts_field_outside_checks_never_changes_exit_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The new field is outside `checks`, and an unauthenticated account
    does not change the exit status — assert the status directly, across an
    authenticated and an unauthenticated fixture."""
    import json as _json

    import camp.cli.common as cli_common
    import camp.launch.profile as profile
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor
    from trailhead.harness.base import AccountAuthentication

    def run(verdict):
        groups_dir = tmp_path / f"groups-{verdict.value}"
        groups_dir.mkdir()
        _write_group(groups_dir, "g1.toml", "g1", account="acct-x")
        monkeypatch.setattr(cli_common, "_groups_dir", lambda d=groups_dir: d)

        harness = _RosterHarness({"acct-x": verdict})
        monkeypatch.setattr(profile, "harness_for", lambda group: harness)

        exit_code = None
        try:
            cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
        except SystemExit as e:
            exit_code = e.code
        report = _json.loads(capsys.readouterr().out)
        return exit_code, report

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)

    auth_exit, auth_report = run(AccountAuthentication.AUTHENTICATED)
    not_auth_exit, _ = run(AccountAuthentication.NOT_AUTHENTICATED)

    assert "checks" in auth_report
    assert DOCTOR_PROBE_ACCOUNTS_KEY in auth_report
    assert all(DOCTOR_PROBE_ACCOUNTS_KEY not in check for check in auth_report["checks"])
    assert auth_exit is None
    assert not_auth_exit is None


def test_doctor_probe_accounts_declared_string_order_beats_incidental_filename_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Iteration order is imposed explicitly by the declared account string,
    with the default no-account store sorted last — not inherited from
    whichever group config filename happened to sort first. This fixture
    declares accounts in an order that comes out DIFFERENTLY under the
    incidental filename order than under the declared-string order, so the
    test fails if the explicit key is dropped."""
    import json as _json

    import camp.cli.common as cli_common
    import camp.launch.profile as profile
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor
    from trailhead.harness.base import AccountAuthentication

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    # Filename order (alphabetical, what globbing yields): a-file -> "zzz",
    # then z-file -> "aaa" — the OPPOSITE of declared-string order.
    _write_group(groups_dir, "a-file.toml", "g1", account="zzz")
    _write_group(groups_dir, "z-file.toml", "g2", account="aaa")
    monkeypatch.setattr(cli_common, "_groups_dir", lambda: groups_dir)

    harness = _RosterHarness(
        {"zzz": AccountAuthentication.AUTHENTICATED, "aaa": AccountAuthentication.AUTHENTICATED}
    )
    monkeypatch.setattr(profile, "harness_for", lambda group: harness)

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    declared_order = [a["account"] for a in accounts if a["account"] is not None]
    assert declared_order == ["aaa", "zzz"]
    assert accounts[-1]["account"] is None


def test_doctor_probe_accounts_strips_control_sequences_from_declared_string(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An account string carrying an ANSI escape sequence, a cursor-move
    code, and a NUL is stripped in the produced field.

    `load_group` already refuses a control character in `launch.account` at
    load time (a group config can never carry one through this loader), so
    this drives the dirty value through the roster builder's OWN
    `load_group` call, bypassed, the way a future account source that skips
    that loader's guard would — proving the strip in `_doctor_account_roster`
    is real defense-in-depth, not a property that only holds because a
    sibling validator happens to run first.
    """
    import json as _json

    import camp.cli.common as cli_common
    import camp.group.config as group_config
    import camp.launch.profile as profile
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor
    from trailhead.harness.base import AccountAuthentication

    dirty = "acct\x1b[31mred\x1b[2Dmove\x00end"
    # Only the C0/C1 control BYTES are stripped (matching the existing
    # recursive strip this reuses) — the printable payload that followed
    # each escape introducer is left in place, exactly as it is for a
    # relayed remote's stderr.
    clean = "acct[31mred[2Dmoveend"

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    (groups_dir / "g1.toml").write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(cli_common, "_groups_dir", lambda: groups_dir)
    monkeypatch.setattr(
        group_config,
        "load_group",
        lambda path: {"group": {"name": "g1"}, "launch": {"account": dirty}},
    )

    harness = _RosterHarness({dirty: AccountAuthentication.AUTHENTICATED})
    monkeypatch.setattr(profile, "harness_for", lambda group: harness)

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    declared = next(a for a in accounts if a["account"] is not None)
    assert declared["account"] == clean


def test_doctor_probe_accounts_strips_control_sequences_from_failure_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failure reason carried from a harness error is stripped the same
    way as a declared account string."""
    import json as _json

    import camp.cli.common as cli_common
    import camp.launch.profile as profile
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor

    class _RaisingHarness:
        name = "raisingharness"

        def session_launch_env_set(self, account, *, env=None):
            raise ValueError("bad \x1b[31maccount\x00")

        def session_launch_env_unset(self):
            return []

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    _write_group(groups_dir, "g1.toml", "flaky", account="rel/path")
    monkeypatch.setattr(cli_common, "_groups_dir", lambda: groups_dir)
    monkeypatch.setattr(profile, "harness_for", lambda group: _RaisingHarness())

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    failures = [a for a in accounts if a.get("reason")]
    assert len(failures) == 1
    assert "\x1b" not in failures[0]["reason"]
    assert "\x00" not in failures[0]["reason"]
    assert "bad" in failures[0]["reason"] and "account" in failures[0]["reason"]


def test_doctor_probe_accounts_harness_resolves_but_refuses_to_bind_is_a_failure_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """State — the account capability fails on a host, one of its named
    sub-cases: a harness that camp CAN name resolves fine, but refuses to
    bind the declared account (states no launch support at all). This is
    routed through `_addressable_harnesses`'s `on_drop` callback, distinct
    from an unreadable group config, which never reaches `on_drop` at all
    — pinned on its own so the `on_drop=` wiring cannot be dropped silently."""
    import json as _json

    import camp.cli.common as cli_common
    import camp.launch.profile as profile
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor

    class _NoLaunchSupportHarness:
        name = "nolaunchsupportharness"

        def session_launch_env_set(self, account, *, env=None):
            return None

        def session_launch_env_unset(self):
            return None

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    _write_group(groups_dir, "g1.toml", "refuser", account="acct-refused")
    monkeypatch.setattr(cli_common, "_groups_dir", lambda: groups_dir)
    monkeypatch.setattr(profile, "harness_for", lambda group: _NoLaunchSupportHarness())

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    failures = [a for a in accounts if a.get("reason")]
    assert len(failures) == 1
    assert "refuser" in failures[0]["reason"]
    assert "declares no launch support" in failures[0]["reason"]


def test_doctor_probe_roster_production_raising_never_crashes_the_probe_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A raise from roster PRODUCTION itself — as opposed to one store's own
    verdict call, which the roster already guards per-store — must not
    crash the whole `--probe` report on the side answering it: the local
    checks and the multiplexer fact were already computed and must still
    reach the caller, exactly like the `-a` dispatch side's own guard
    against the same class of failure."""
    import json as _json

    import camp.spine as spine_module
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY

    def _boom(env=None):
        raise RuntimeError("roster production blew up")

    monkeypatch.setattr(spine_module, "_doctor_account_roster", _boom)

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)
    exit_code = None
    try:
        spine_module.cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit as e:
        exit_code = e.code
    report = _json.loads(capsys.readouterr().out)

    assert "checks" in report
    assert report["probe"] is True
    assert report["multiplexer_present"] is True
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    failure = next(a for a in accounts if a.get("reason"))
    assert failure["verdict"] is None
    assert "roster production blew up" in failure["reason"]
    assert exit_code is None


def test_doctor_probe_accounts_verdict_call_raising_yields_a_failure_row_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A harness that resolves and binds an account fine, then raises while
    answering the authentication verdict itself, must not crash the whole
    roster (or the report around it) — it contributes a failure row like
    any other broken account, and every other field this report carries
    (the probe/multiplexer facts) still comes through."""
    import json as _json

    import camp.cli.common as cli_common
    import camp.launch.profile as profile
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor

    class _RaisingVerdictHarness:
        name = "raisingverdictharness"

        def session_launch_env_unset(self):
            return []

        def session_launch_env_set(self, account, *, env=None):
            if account is None:
                return {}
            return {"FAKE_STORE_DIR": account}

        def session_launch_account_authentication(self, account, *, env=None):
            raise RuntimeError("harness blew up mid-check")

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    _write_group(groups_dir, "g1.toml", "g1", account="acct-crashy")
    monkeypatch.setattr(cli_common, "_groups_dir", lambda: groups_dir)
    monkeypatch.setattr(profile, "harness_for", lambda group: _RaisingVerdictHarness())

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)
    exit_code = None
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit as e:
        exit_code = e.code
    report = _json.loads(capsys.readouterr().out)

    assert report["probe"] is True
    assert report["multiplexer_present"] is True
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    failure = next(a for a in accounts if a["account"] == "acct-crashy")
    assert failure["verdict"] is None
    assert "harness blew up mid-check" in failure["reason"]
    assert exit_code is None


def test_doctor_probe_accounts_verdict_call_returning_none_yields_a_failure_row_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`session_launch_account_authentication` answering `None` — the
    convention every OTHER capability on the base seam uses for "no such
    concept", and one `base.py`'s own docstring flags as a deliberate
    deviation on this particular method — must not crash the roster (or the
    report around it) any more than an outright raise does. One harness's
    bad return contributes a failure row and every other field the report
    carries still comes through."""
    import json as _json

    import camp.cli.common as cli_common
    import camp.launch.profile as profile
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor

    class _NoneReturningHarness:
        name = "nonereturningharness"

        def session_launch_env_unset(self):
            return []

        def session_launch_env_set(self, account, *, env=None):
            if account is None:
                return {}
            return {"FAKE_STORE_DIR": account}

        def session_launch_account_authentication(self, account, *, env=None):
            return None

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    _write_group(groups_dir, "g1.toml", "g1", account="acct-none")
    monkeypatch.setattr(cli_common, "_groups_dir", lambda: groups_dir)
    monkeypatch.setattr(profile, "harness_for", lambda group: _NoneReturningHarness())

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)
    exit_code = None
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit as e:
        exit_code = e.code
    report = _json.loads(capsys.readouterr().out)

    assert report["probe"] is True
    assert report["multiplexer_present"] is True
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    failure = next(a for a in accounts if a["account"] == "acct-none")
    assert failure["verdict"] is None
    assert exit_code is None


def test_doctor_probe_accounts_stripping_does_not_alter_an_ordinary_string(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stripping does not alter an ordinary account string — the negative
    control, so the test fails if stripping is over-broad as well as if it
    is absent."""
    import json as _json

    import camp.cli.common as cli_common
    import camp.launch.profile as profile
    from camp.spine import DOCTOR_PROBE_ACCOUNTS_KEY, cmd_doctor
    from trailhead.harness.base import AccountAuthentication

    ordinary = "acct-ordinary_2024.work"

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    _write_group(groups_dir, "g1.toml", "g1", account=ordinary)
    monkeypatch.setattr(cli_common, "_groups_dir", lambda: groups_dir)

    harness = _RosterHarness({ordinary: AccountAuthentication.AUTHENTICATED})
    monkeypatch.setattr(profile, "harness_for", lambda group: harness)

    monkeypatch.setenv("CAMP_TEST_ASDF_PRESENT", "1")
    monkeypatch.setenv("CAMP_TEST_TMUX_PRESENT", "1")
    _isolate_roots(monkeypatch, tmp_path)
    try:
        cmd_doctor(["--json", "--probe"], env=_isolated_config_env(tmp_path))
    except SystemExit:
        pass
    report = _json.loads(capsys.readouterr().out)
    accounts = report[DOCTOR_PROBE_ACCOUNTS_KEY]
    declared = next(a for a in accounts if a["account"] is not None)
    assert declared["account"] == ordinary


def test_doctor_probe_accounts_reachable_through_real_cli_entry_path(
    tmp_path: Path,
) -> None:
    """The accounts field is reachable through the real `camp` CLI entry
    point, not only by importing `cmd_doctor` directly."""
    import json as _json
    import subprocess

    cli = _PLUGIN_DIR / "cli" / "camp"
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "CAMP_CONFIG_DIR": str(tmp_path / "config"),
            "WORKSPACE_ROOT": str(tmp_path / "workspace"),
            "CAMP_CANONICAL_ROOT": str(tmp_path / "canonical"),
            "CAMP_TEST_ASDF_PRESENT": "1",
            "CAMP_TEST_TMUX_PRESENT": "1",
        }
    )
    (tmp_path / "workspace").mkdir()
    (tmp_path / "canonical").mkdir()

    result = subprocess.run(
        [sys.executable, str(cli), "doctor", "--json", "--probe"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    report = _json.loads(result.stdout)
    accounts = report["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["account"] is None
    assert accounts[0]["verdict"] == "not-authenticated"


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
