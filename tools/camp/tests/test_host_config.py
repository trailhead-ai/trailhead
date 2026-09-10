"""Tests for camp.host.config — the hosts.toml loader.

Test contract:
- A table declaring only its key resolves `ssh` to that key and `camp_bin` to
  the documented default ("camp").
- A table declaring `ssh` explicitly resolves to that value, not the table key.
- A table declaring `camp_bin` resolves to that path verbatim.
- Several tables load as several hosts, each keyed by its table name.
- An absent file loads as zero hosts and does not raise — distinct from an
  empty file, which also loads as zero hosts.
- Unparsable TOML raises the loader's own error type, and the message names
  the file.
- An unknown key inside a host table raises, naming the offending key —
  mutation-checked by removing the allowlist and confirming the test goes red.
- A non-string `ssh` or `camp_bin` raises rather than being carried through as
  a non-string into a later command line.
- CAMP_CONFIG_DIR points the loader at the overridden directory (it replaces
  the whole app dir; the file is at $CAMP_CONFIG_DIR/hosts.toml, not a nested
  camp/).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.host.config import HostConfigError, load_hosts  # noqa: E402


def _point_at(monkeypatch: pytest.MonkeyPatch, config_dir: Path) -> None:
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(config_dir))


def _write_hosts(config_dir: Path, contents: str) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    hosts_file = config_dir / "hosts.toml"
    hosts_file.write_text(contents, encoding="utf-8")
    return hosts_file


def test_table_with_only_its_key_defaults_ssh_and_camp_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, "[hosts.andromeda]\n")

    hosts = load_hosts()

    assert hosts["andromeda"].ssh == "andromeda"
    assert hosts["andromeda"].camp_bin == "camp"


def test_explicit_ssh_overrides_the_table_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(
        config_dir,
        '[hosts.andromeda]\nssh = "andromeda.local"\n',
    )

    hosts = load_hosts()

    assert hosts["andromeda"].ssh == "andromeda.local"


def test_explicit_camp_bin_resolves_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(
        config_dir,
        '[hosts.andromeda]\ncamp_bin = "/home/tom/.local/state/trailhead/bin/camp"\n',
    )

    hosts = load_hosts()

    assert hosts["andromeda"].camp_bin == "/home/tom/.local/state/trailhead/bin/camp"


def test_several_tables_load_as_several_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(
        config_dir,
        '[hosts.andromeda]\nssh = "andromeda.local"\n\n[hosts.orion]\ncamp_bin = "/opt/camp"\n',
    )

    hosts = load_hosts()

    assert set(hosts) == {"andromeda", "orion"}
    assert hosts["andromeda"].ssh == "andromeda.local"
    assert hosts["orion"].camp_bin == "/opt/camp"


def test_absent_file_loads_as_zero_hosts_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    # config_dir itself does not even exist.

    hosts = load_hosts()

    assert hosts == {}


def test_empty_file_also_loads_as_zero_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, "")

    hosts = load_hosts()

    assert hosts == {}


def test_unparsable_toml_raises_the_loaders_own_error_type_naming_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    hosts_file = _write_hosts(config_dir, "this is not [ valid toml")

    with pytest.raises(HostConfigError) as exc_info:
        load_hosts()

    assert str(hosts_file) in str(exc_info.value)


def test_unknown_key_in_a_host_table_raises_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(
        config_dir,
        '[hosts.andromeda]\nssh = "andromeda"\nbogus_key = "nope"\n',
    )

    with pytest.raises(HostConfigError) as exc_info:
        load_hosts()

    assert "bogus_key" in str(exc_info.value)


def test_non_string_ssh_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, "[hosts.andromeda]\nssh = 42\n")

    with pytest.raises(HostConfigError):
        load_hosts()


def test_non_string_camp_bin_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, "[hosts.andromeda]\ncamp_bin = 7\n")

    with pytest.raises(HostConfigError):
        load_hosts()


def test_camp_config_dir_replaces_the_whole_app_dir_not_a_nested_camp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override_dir = tmp_path / "somewhere-else"
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(override_dir))
    _write_hosts(override_dir, '[hosts.andromeda]\nssh = "andromeda"\n')
    # Confirm the loader does NOT look under override_dir/camp/hosts.toml.
    assert not (override_dir / "camp" / "hosts.toml").exists()

    hosts = load_hosts()

    assert hosts["andromeda"].ssh == "andromeda"
