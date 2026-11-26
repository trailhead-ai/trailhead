"""Where a docket change writes: only the default vault's docket directory, whatever argv or environment says.

The target is whatever `lore vault ls --json` reports as the default vault. No flag and no environment
variable the commands could be given moves it, so a wrapper that redirects the tool for a test must do it
through the runner it hands the command table, not through the command line or the environment.
"""
from __future__ import annotations

import pytest

from trailhead.tests._git_support import hermetic_git_env  # noqa: F401  (fixture)

from test_changes import env, ref  # noqa: F401  (env is a fixture)


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("name", [
    "DOCKET_VAULT", "DOCKET_VAULT_PATH", "DOCKET_DIR", "DOCKET_HOME", "DOCKET_ROOT", "LORE_VAULT",
    "LORE_DEFAULT_VAULT", "LORE_VAULT_PATH", "XDG_STATE_HOME", "HOME", "TRAILHEAD_ROOT",
])
def test_no_environment_variable_moves_the_write_target(env, monkeypatch, tmp_path, name):
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    monkeypatch.setenv(name, str(decoy))
    code, doc, _ = env.run("add", "--", ref("task/n1"))
    assert (code, doc["outcome"]) == (0, "saved")
    assert env.entries() == ["task/n1"]
    assert _files(decoy) == []


@pytest.mark.parametrize("flag", ["--vault", "--vault-path", "--path", "--store", "--dir", "--root", "--list-file"])
def test_no_command_line_flag_moves_the_write_target(env, tmp_path, capsys, flag):
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    with pytest.raises(SystemExit) as exit_info:
        env.run("add", flag, str(decoy), "--", ref("task/n1"))
    assert exit_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert _files(decoy) == []
    assert env.entries() is None


def test_a_ref_naming_another_vault_still_writes_inside_the_default_vault(env, tmp_path):
    env.fake.know(ref("task/n1", "other"))
    code, doc, err = env.run("add", "--", ref("task/n1", "other"))
    assert code == 0, err
    assert doc["outcome"] == "saved"
    assert env.entries("other") == ["task/n1"]
    assert _files(tmp_path / "vault" / "sites") == ["docket/lists/other.json"]
    assert not (tmp_path / "vault" / "other").exists()
