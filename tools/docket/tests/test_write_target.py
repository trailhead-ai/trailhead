"""Where a docket change writes: the default vault's docket directory, as lore reports it.

Docket reads no environment variable and has no flag for the target; the target is whatever
`lore vault ls --json` reports as the default vault. What these tests pin is docket's own side: with the
runner fixed, no variable in the process environment and no command-line flag changes where docket writes.
Real lore resolves its default vault from its own configuration and environment, which is outside what
docket (or a test with a scripted runner) can pin; a wrapper that redirects the tool for a test does it
through the runner it hands the command table.
"""
from __future__ import annotations

import pytest

from _docket_env import docket_env, hermetic_git_env, ref  # noqa: F401  (fixtures)



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
