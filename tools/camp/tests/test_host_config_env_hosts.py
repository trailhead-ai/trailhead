"""Tests for camp.host.config.env_host_names — the top-level ``env_hosts`` setting.

Test contract:
- No hosts file, or a file without ``env_hosts``, gives ``[]``.
- ``env_hosts`` is returned in the setting's own order, not declaration order.
- A name equal to ``self_name`` with no ``[hosts.<name>]`` table is accepted.
- An undeclared name, a duplicate, a non-list, a non-string element, and an
  element outside camp's host-name charset each raise ``HostConfigError``
  naming the file and the entry.
- A file carrying ``env_hosts`` loads through ``load_hosts()`` with the same
  answer as without it.
- The README's documented ``env_hosts`` block is accepted by ``env_host_names``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.host.config import (  # noqa: E402
    HostConfigError,
    env_host_names,
    load_hosts,
)

README = Path(__file__).resolve().parents[1] / "README.md"


def _write_hosts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, contents: str
) -> Path:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(config_dir))
    hosts_file = config_dir / "hosts.toml"
    hosts_file.write_text(contents, encoding="utf-8")
    return hosts_file


def test_no_hosts_file_gives_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path / "absent"))

    assert env_host_names() == []


def test_file_without_env_hosts_gives_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_hosts(monkeypatch, tmp_path, '[hosts.a]\n')

    assert env_host_names() == []


def test_returns_the_settings_own_order_not_declaration_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_hosts(
        monkeypatch,
        tmp_path,
        'env_hosts = ["b", "a"]\n[hosts.a]\n[hosts.b]\n',
    )

    assert env_host_names() == ["b", "a"]


def test_env_argument_selects_the_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path / "other"))
    cfg = tmp_path / "injected"
    cfg.mkdir()
    (cfg / "hosts.toml").write_text(
        'env_hosts = ["a"]\n[hosts.a]\n', encoding="utf-8"
    )

    assert env_host_names(env={"CAMP_CONFIG_DIR": str(cfg)}) == ["a"]


def test_self_name_is_accepted_without_a_host_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_hosts(
        monkeypatch, tmp_path, 'self_name = "sunrise"\nenv_hosts = ["sunrise"]\n'
    )

    assert env_host_names() == ["sunrise"]


def test_name_matching_neither_a_table_nor_self_name_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_hosts(
        monkeypatch,
        tmp_path,
        'self_name = "sunrise"\nenv_hosts = ["ghost"]\n[hosts.a]\n',
    )

    with pytest.raises(HostConfigError) as exc:
        env_host_names()

    assert str(path) in str(exc.value)
    assert "ghost" in str(exc.value)


def test_duplicate_name_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_hosts(
        monkeypatch, tmp_path, 'env_hosts = ["a", "a"]\n[hosts.a]\n'
    )

    with pytest.raises(HostConfigError) as exc:
        env_host_names()

    assert str(path) in str(exc.value)
    assert "'a'" in str(exc.value)


def test_non_list_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_hosts(monkeypatch, tmp_path, 'env_hosts = "a"\n[hosts.a]\n')

    with pytest.raises(HostConfigError) as exc:
        env_host_names()

    assert str(path) in str(exc.value)
    assert "env_hosts" in str(exc.value)


def test_non_string_element_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_hosts(monkeypatch, tmp_path, 'env_hosts = [7]\n[hosts.a]\n')

    with pytest.raises(HostConfigError) as exc:
        env_host_names()

    assert str(path) in str(exc.value)
    assert "7" in str(exc.value)


@pytest.mark.parametrize("bad", ["-oProxyCommand=x", "Upper", "", "a b"])
def test_element_outside_the_host_name_charset_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    path = _write_hosts(
        monkeypatch, tmp_path, f'env_hosts = ["{bad}"]\n[hosts."{bad}"]\n'
    )

    with pytest.raises(HostConfigError) as exc:
        env_host_names()

    assert str(path) in str(exc.value)
    assert repr(bad) in str(exc.value)


def test_load_hosts_answer_is_the_same_with_and_without_env_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = '[hosts.a]\nssh = "a.lan"\n'
    _write_hosts(monkeypatch, tmp_path, body)
    without = load_hosts()

    _write_hosts(monkeypatch, tmp_path, 'env_hosts = ["nope", 3, "nope"]\n' + body)

    assert load_hosts() == without


def test_the_documented_readme_env_hosts_block_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = [
        b
        for b in re.findall(r"```toml\n(.*?)```", README.read_text(), re.DOTALL)
        if "env_hosts" in b
    ]
    assert blocks, "README no longer documents env_hosts"

    for block in blocks:
        _write_hosts(monkeypatch, tmp_path, block)
        assert env_host_names()
