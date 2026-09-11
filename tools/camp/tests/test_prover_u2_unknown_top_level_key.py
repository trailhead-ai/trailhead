"""U2 assumption prover — ephemeral, delete after landing real coverage.

Question: does the CURRENT host-file reader reject, warn about, or silently
ignore an unrecognised TOP-LEVEL key in hosts.toml (as opposed to an unknown
key inside a [hosts.<name>] table, which is already known to raise)?

Not TDD-for-implementation: this probes existing, unmodified behaviour. See
task/camp-doctor-answers-for-every-declared-host, Known Unknown U2.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.host.config import HostConfigError, load_hosts, self_host_name  # noqa: E402


def _point_at(monkeypatch: pytest.MonkeyPatch, config_dir: Path) -> None:
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(config_dir))


def _write_hosts(config_dir: Path, contents: str) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    hosts_file = config_dir / "hosts.toml"
    hosts_file.write_text(contents, encoding="utf-8")
    return hosts_file


def test_load_hosts_tolerates_unrecognised_top_level_scalar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown top-level scalar alongside a valid host table must not
    stop load_hosts() from loading the declared hosts.
    """
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(
        config_dir,
        'connect_timeout = 5\n\n[hosts.andromeda]\n',
    )

    hosts = load_hosts()

    assert hosts["andromeda"].ssh == "andromeda"
    assert hosts["andromeda"].camp_bin == "camp"


def test_load_hosts_still_rejects_unknown_key_inside_host_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contrast: an unknown key INSIDE a host table is already validated and
    must still raise — confirming the two levels behave differently.
    """
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(
        config_dir,
        '[hosts.andromeda]\nbogus = "nope"\n',
    )

    with pytest.raises(HostConfigError, match="bogus"):
        load_hosts()


def test_self_host_name_tolerates_unrecognised_top_level_scalar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """self_host_name() is the other current top-level reader of the same
    file; it must also tolerate an unrelated unknown top-level scalar.
    """
    config_dir = tmp_path / "cfg"
    _point_at(monkeypatch, config_dir)
    _write_hosts(
        config_dir,
        'connect_timeout = 5\nself_name = "andromeda"\n',
    )

    assert self_host_name() == "andromeda"
