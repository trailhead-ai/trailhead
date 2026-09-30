"""``lore vault config --makes-vault-content yes|no`` records this host's role."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import CLI_PATH  # noqa: E402

from lore.vault import config as vault_config  # noqa: E402


def _env(tmp_path, **extra):
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["LORE_EMAIL"] = "tester@example.com"
    env["EDITOR"] = "true"
    env.update(extra)
    return env


def _cfg_path(tmp_path):
    return tmp_path / "config" / "lore" / "config.json"


def _run(tmp_path, *args, **extra):
    return subprocess.run(
        [sys.executable, str(CLI_PATH), "vault", "config", *args],
        capture_output=True, text=True, env=_env(tmp_path, **extra),
    )


def _seed(tmp_path, text, mode=0o600):
    p = _cfg_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(text)
    p.chmod(mode)
    return p


_DEFAULT_VAULT = {"name": "default", "scope": "default"}
_VALID = json.dumps({"vaults": [_DEFAULT_VAULT]})


def _accepted_by_lore(tmp_path):
    return vault_config.load_config(str(_cfg_path(tmp_path)), env=_env(tmp_path))


def _mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def _read(tmp_path):
    return json.loads(_cfg_path(tmp_path).read_text())


def test_yes_writes_true_and_reader_sees_it(tmp_path):
    _seed(tmp_path, _VALID)
    res = _run(tmp_path, "--makes-vault-content", "yes")
    assert res.returncode == 0, res.stderr
    assert _read(tmp_path)["makes_vault_content"] is True
    assert vault_config.read_makes_vault_content(_env(tmp_path)) is True
    assert [v.name for v in _accepted_by_lore(tmp_path)] == ["default"]


def test_no_writes_false_and_reader_sees_it(tmp_path):
    _seed(tmp_path, _VALID)
    res = _run(tmp_path, "--makes-vault-content", "no")
    assert res.returncode == 0, res.stderr
    assert _read(tmp_path)["makes_vault_content"] is False
    assert vault_config.read_makes_vault_content(_env(tmp_path)) is False


def test_rerun_with_other_answer_flips(tmp_path):
    _seed(tmp_path, _VALID)
    assert _run(tmp_path, "--makes-vault-content", "yes").returncode == 0
    assert _run(tmp_path, "--makes-vault-content", "no").returncode == 0
    assert _read(tmp_path)["makes_vault_content"] is False
    assert vault_config.read_makes_vault_content(_env(tmp_path)) is False


def test_other_keys_survive(tmp_path):
    vaults = [
        {"name": "default", "scope": "default"},
        {"name": "team", "scope": "team", "shared": True},
    ]
    _seed(tmp_path, json.dumps({
        "vaults": vaults,
        "record_url_base": "https://example.test/base",
        "publish_retry_max": 7,
    }))
    assert _run(tmp_path, "--makes-vault-content", "yes").returncode == 0
    cfg = _read(tmp_path)
    assert cfg["vaults"] == vaults
    assert cfg["record_url_base"] == "https://example.test/base"
    assert cfg["publish_retry_max"] == 7


def test_non_bool_value_is_repaired_when_the_config_is_valid(tmp_path):
    _seed(tmp_path, json.dumps({"vaults": [_DEFAULT_VAULT], "makes_vault_content": "maybe"}))
    res = _run(tmp_path, "--makes-vault-content", "no")
    assert res.returncode == 0, res.stderr
    assert _read(tmp_path)["makes_vault_content"] is False
    assert vault_config.read_makes_vault_content(_env(tmp_path)) is False
    assert _accepted_by_lore(tmp_path)


def test_no_config_is_refused_naming_lore_init_and_nothing_is_created(tmp_path):
    res = _run(tmp_path, "--makes-vault-content", "yes")
    assert res.returncode != 0
    assert "lore init" in res.stderr
    assert not _cfg_path(tmp_path).exists()


@pytest.mark.parametrize("text", [
    json.dumps({"record_url_base": "https://example.test"}),
    json.dumps({"vaults": []}),
    json.dumps({"vaults": [{"name": "team", "scope": "team"}]}),
    json.dumps({"vaults": [_DEFAULT_VAULT, {"name": "team", "scope": "bogus"}]}),
])
def test_config_that_would_not_validate_is_refused_naming_lore_init_and_untouched(tmp_path, text):
    p = _seed(tmp_path, text, mode=0o640)
    before = p.read_bytes()
    res = _run(tmp_path, "--makes-vault-content", "yes")
    assert res.returncode != 0
    assert "lore init" in res.stderr
    assert p.read_bytes() == before
    assert _mode(p) == 0o640


def test_existing_0600_stays_0600(tmp_path):
    p = _seed(tmp_path, _VALID, mode=0o600)
    assert _run(tmp_path, "--makes-vault-content", "yes").returncode == 0
    assert _mode(p) == 0o600


def test_existing_mode_is_kept(tmp_path):
    p = _seed(tmp_path, _VALID, mode=0o640)
    assert _run(tmp_path, "--makes-vault-content", "yes").returncode == 0
    assert _mode(p) == 0o640


def test_invalid_json_refused_and_untouched(tmp_path):
    p = _seed(tmp_path, "{not json", mode=0o640)
    before = p.read_bytes()
    res = _run(tmp_path, "--makes-vault-content", "yes")
    assert res.returncode != 0
    assert "config.json" in res.stderr
    assert p.read_bytes() == before
    assert _mode(p) == 0o640


def test_json_array_refused_and_untouched(tmp_path):
    p = _seed(tmp_path, "[1, 2]", mode=0o640)
    before = p.read_bytes()
    res = _run(tmp_path, "--makes-vault-content", "no")
    assert res.returncode != 0
    assert "config.json" in res.stderr
    assert p.read_bytes() == before
    assert _mode(p) == 0o640


def test_invalid_answer_is_argparse_error_and_untouched(tmp_path):
    p = _seed(tmp_path, _VALID)
    before = p.read_bytes()
    res = _run(tmp_path, "--makes-vault-content", "maybe")
    assert res.returncode == 2
    assert "invalid choice" in res.stderr
    assert p.read_bytes() == before


def test_no_flag_opens_editor_and_writes_nothing(tmp_path):
    p = _seed(tmp_path, _VALID)
    before = p.read_bytes()
    marker = tmp_path / "editor-args"
    editor = tmp_path / "fake-editor"
    editor.write_text(f'#!/bin/sh\necho "$1" > "{marker}"\n')
    editor.chmod(0o755)
    res = _run(tmp_path, EDITOR=str(editor))
    assert res.returncode == 0, res.stderr
    assert marker.read_text().strip() == str(p)
    assert p.read_bytes() == before
