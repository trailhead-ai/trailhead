"""Tests for camp.host.config.self_host_name — this host's own declared name.

Test contract:
- No hosts.toml at all -> None, raises nothing.
- hosts.toml present with no self_name key -> None.
- self_name = "andromeda" -> "andromeda".
- The committed shared fixture (self_name + a per-table remote-host entry)
  loads through the real loader and returns the name — proving the
  reserved-scalar coexistence the sibling loader depends on.
- self_name outside the strict charset (uppercase, dot, space, shell
  metacharacter, embedded newline, trailing newline) -> HostConfigError; the
  CLI surface prints `camp: <message>` and exits nonzero with no traceback.
  The trailing-newline case is a separate test from the embedded-newline case.
- self_name a non-string (int, table) -> HostConfigError.
- Empty string -> HostConfigError, not None.
- Malformed TOML -> HostConfigError naming path and reason.
- The config directory is resolved through the injected environment; no test
  ever reads the operator's real ~/.config/camp.
- `camp doctor` reports the declared name in an informational row on the text
  rendering when a name is declared, and reports "not declared" when none is.
- `camp doctor --json` carries the same row; its value is the declared name,
  or reports it as not declared when none is.
- The row never fails the overall `doctor` verdict, whether or not a name is
  declared.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _env(config_root: Path) -> dict[str, str]:
    return {"CAMP_CONFIG_DIR": str(config_root), "HOME": str(config_root.parent / "home")}


def _write_hosts_toml(config_root: Path, content: str) -> None:
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "hosts.toml").write_text(content, encoding="utf-8")


def test_no_hosts_toml_returns_none(tmp_path: Path) -> None:
    from camp.host.config import self_host_name

    env = _env(tmp_path / "config")
    assert self_host_name(env=env) is None


def test_hosts_toml_present_with_no_self_name_key_returns_none(tmp_path: Path) -> None:
    from camp.host.config import self_host_name

    config_root = tmp_path / "config"
    _write_hosts_toml(config_root, '[hosts.orion]\nssh = "orion.local"\n')

    env = _env(config_root)
    assert self_host_name(env=env) is None


def test_declared_self_name_is_returned(tmp_path: Path) -> None:
    from camp.host.config import self_host_name

    config_root = tmp_path / "config"
    _write_hosts_toml(config_root, 'self_name = "andromeda"\n')

    env = _env(config_root)
    assert self_host_name(env=env) == "andromeda"


def test_shared_fixture_coexists_with_a_remote_host_table(tmp_path: Path) -> None:
    from camp.host.config import self_host_name

    config_root = tmp_path / "config"
    fixture_text = (_FIXTURES_DIR / "hosts.toml").read_text(encoding="utf-8")
    _write_hosts_toml(config_root, fixture_text)

    env = _env(config_root)
    assert self_host_name(env=env) == "andromeda"


@pytest.mark.parametrize(
    "bad_name",
    [
        "Andromeda",
        "andromeda.local",
        "an dromeda",
        "andromeda;rm -rf /",
        "andro\nmeda",
    ],
    ids=["uppercase", "dot", "space", "shell-metacharacter", "embedded-newline"],
)
def test_self_name_outside_strict_charset_raises(tmp_path: Path, bad_name: str) -> None:
    from camp.host.config import HostConfigError, self_host_name

    config_root = tmp_path / "config"
    _write_hosts_toml(config_root, f'self_name = {bad_name!r}\n')

    env = _env(config_root)
    with pytest.raises(HostConfigError):
        self_host_name(env=env)


def test_self_name_with_trailing_newline_raises(tmp_path: Path) -> None:
    """Separate from the embedded-newline case: this is the specific bypass
    \\Z anchoring (rather than $) exists to defeat."""
    from camp.host.config import HostConfigError, self_host_name

    config_root = tmp_path / "config"
    _write_hosts_toml(config_root, 'self_name = "andromeda\\n"\n')

    env = _env(config_root)
    with pytest.raises(HostConfigError):
        self_host_name(env=env)


def test_self_name_non_string_integer_raises(tmp_path: Path) -> None:
    from camp.host.config import HostConfigError, self_host_name

    config_root = tmp_path / "config"
    _write_hosts_toml(config_root, "self_name = 42\n")

    env = _env(config_root)
    with pytest.raises(HostConfigError):
        self_host_name(env=env)


def test_self_name_non_string_table_raises(tmp_path: Path) -> None:
    from camp.host.config import HostConfigError, self_host_name

    config_root = tmp_path / "config"
    _write_hosts_toml(config_root, "[self_name]\nx = 1\n")

    env = _env(config_root)
    with pytest.raises(HostConfigError):
        self_host_name(env=env)


def test_self_name_empty_string_raises_not_none(tmp_path: Path) -> None:
    from camp.host.config import HostConfigError, self_host_name

    config_root = tmp_path / "config"
    _write_hosts_toml(config_root, 'self_name = ""\n')

    env = _env(config_root)
    with pytest.raises(HostConfigError, match="must not be empty"):
        self_host_name(env=env)


def test_malformed_toml_raises_naming_path_and_reason(tmp_path: Path) -> None:
    from camp.host.config import HostConfigError, self_host_name

    config_root = tmp_path / "config"
    _write_hosts_toml(config_root, "self_name = \n")

    env = _env(config_root)
    with pytest.raises(HostConfigError) as exc_info:
        self_host_name(env=env)
    message = str(exc_info.value)
    assert str(config_root / "hosts.toml") in message


def test_module_reexports_self_host_name_from_package_root(tmp_path: Path) -> None:
    from camp.host import self_host_name

    env = _env(tmp_path / "config")
    assert self_host_name(env=env) is None


def test_cli_surface_renders_clean_error_and_exits_nonzero(tmp_path: Path) -> None:
    """A malformed self_name reaches an actual CLI invocation (camp doctor,
    which now reports the self-declared host name) and comes back as a clean
    `camp: <message>` line on stderr with no traceback."""
    config_root = tmp_path / "config"
    _write_hosts_toml(config_root, 'self_name = "Not Valid"\n')

    env = {
        **os.environ,
        **_env(config_root),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
    }
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "doctor"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert any(line.startswith("camp: ") for line in stderr_lines), result.stderr
    assert "Traceback" not in result.stderr


def _run_doctor(tmp_path: Path, config_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        **_env(config_root),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
        "CAMP_TEST_ASDF_PRESENT": "1",
    }
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), "doctor", *args],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_doctor_text_reports_declared_name(tmp_path: Path) -> None:
    config_root = tmp_path / "config"
    _write_hosts_toml(config_root, 'self_name = "andromeda"\n')

    result = _run_doctor(tmp_path, config_root, [])

    assert "andromeda" in result.stdout, result.stdout
    assert result.returncode == 0, result.stderr


def test_doctor_text_reports_not_declared_when_absent(tmp_path: Path) -> None:
    config_root = tmp_path / "config"

    result = _run_doctor(tmp_path, config_root, [])

    assert "not declared" in result.stdout, result.stdout
    assert result.returncode == 0, result.stderr


def test_doctor_json_reports_declared_name(tmp_path: Path) -> None:
    config_root = tmp_path / "config"
    _write_hosts_toml(config_root, 'self_name = "andromeda"\n')

    result = _run_doctor(tmp_path, config_root, ["--json"])

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    host_row = next(c for c in report["checks"] if c["check"] == "host_name")
    assert host_row["details"] == "andromeda"
    assert host_row["pass"] is True
    assert report["pass"] is True


def test_doctor_json_reports_not_declared_when_absent(tmp_path: Path) -> None:
    config_root = tmp_path / "config"

    result = _run_doctor(tmp_path, config_root, ["--json"])

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    host_row = next(c for c in report["checks"] if c["check"] == "host_name")
    assert host_row["details"] == "not declared"
    assert host_row["pass"] is True
    assert report["pass"] is True
