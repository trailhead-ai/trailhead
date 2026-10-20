"""Behavioural tests for ``tools/lore/scripts/seed-eval-vault``.

The seeder builds a throwaway lore vault in a runner eval case's working
directory. Every test runs it as a real subprocess with a hand-built
environment — HOME (with cwd inside it) and every lore/XDG directory inside ``tmp_path`` — so
nothing here can reach the operator's own lore config, state or ``~/.gitconfig``.
``LORE_PUBLISH_DISABLE`` keeps lore's detached publish worker from being spawned.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SEEDER = REPO_ROOT / "tools" / "lore" / "scripts" / "seed-eval-vault"
PYTHON = os.path.realpath(sys.executable)

SPEC_BODY = (
    "# A spec\n\n## Acceptance Criteria\n\n"
    "- **AC1.** The first thing holds. *Verified by: manual check.*\n"
    "- **AC2.** The second thing holds. *Verified by: manual check.*\n"
)

ADR = {"kind": "adr", "title": "Use the fixture queue", "status": "active", "body": "We use the queue.\n"}
SPEC = {"kind": "spec", "title": "Queue spec", "status": "ready", "body": SPEC_BODY}
DECISION = {"kind": "decision", "title": "Pick a transport", "status": "active", "body": "Transport picked.\n"}
TASK = {"kind": "task", "title": "Wire the queue", "status": "done", "body": "Wired.\n"}


@pytest.fixture
def sandbox(tmp_path):
    home = tmp_path / "home"
    cwd = home / "cwd"
    cwd.mkdir(parents=True)
    return tmp_path


def clean_env(sandbox: Path, **extra: str) -> dict[str, str]:
    env = {
        "HOME": str(sandbox / "home"),
        "PATH": "/usr/bin:/bin",
        "LORE_PUBLISH_DISABLE": "1",
    }
    env.update(extra)
    return env


def write_records(sandbox: Path, records) -> Path:
    path = sandbox / "records.json"
    path.write_text(records if isinstance(records, str) else json.dumps(records), encoding="utf-8")
    return path


def seed(sandbox: Path, records, *, env=None, python: str = PYTHON, cwd: Path | None = None):
    path = write_records(sandbox, records)
    return subprocess.run(
        [python, str(SEEDER), str(path)],
        cwd=cwd if cwd is not None else sandbox / "home" / "cwd",
        env=env if env is not None else clean_env(sandbox),
        capture_output=True,
        text=True,
    )


def seeded_lore(sandbox: Path, *args: str, stdin: str | None = None, env=None):
    return subprocess.run(
        ["./lore", *args],
        cwd=sandbox / "home" / "cwd",
        env=env if env is not None else clean_env(sandbox),
        input=stdin,
        capture_output=True,
        text=True,
    )


def show(sandbox: Path, record_id: str) -> dict:
    result = seeded_lore(sandbox, "record", "show", record_id, "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() if p.is_file() else b""
        for p in sorted(root.rglob("*"))
    }


def home_snapshot(sandbox: Path) -> dict[str, bytes]:
    """The fenced HOME's contents, less the runner's cwd that lives inside it."""
    return {
        name: data
        for name, data in snapshot(sandbox / "home").items()
        if name != "cwd" and not name.startswith("cwd/")
    }


def test_seeds_each_record_at_its_declared_status_and_body(sandbox):
    result = seed(sandbox, [ADR, SPEC])
    assert result.returncode == 0, result.stderr

    adr = show(sandbox, "adr/use-the-fixture-queue")
    spec = show(sandbox, "spec/queue-spec")

    assert (adr["sidecar"]["status"], adr["body"]) == ("active", ADR["body"])
    assert (spec["sidecar"]["status"], spec["body"]) == ("ready", SPEC_BODY)


def test_status_comes_from_the_input_not_a_default(sandbox):
    result = seed(sandbox, [{**ADR, "status": "draft"}])
    assert result.returncode == 0, result.stderr

    assert show(sandbox, "adr/use-the-fixture-queue")["sidecar"]["status"] == "draft"


def test_all_four_shapes_land_at_their_declared_status_and_criteria_are_read(sandbox):
    result = seed(sandbox, [ADR, SPEC, DECISION, TASK])
    assert result.returncode == 0, result.stderr

    statuses = {
        rid: show(sandbox, rid)["sidecar"]["status"]
        for rid in (
            "adr/use-the-fixture-queue",
            "spec/queue-spec",
            "decision/pick-a-transport",
            "task/wire-the-queue",
        )
    }
    assert statuses == {
        "adr/use-the-fixture-queue": "active",
        "spec/queue-spec": "ready",
        "decision/pick-a-transport": "active",
        "task/wire-the-queue": "done",
    }
    criteria = seeded_lore(sandbox, "record", "criteria", "spec/queue-spec")
    assert criteria.returncode == 0, criteria.stderr
    assert json.loads(criteria.stdout)["declared"] == ["AC1", "AC2"]


def test_amend_of_the_seeded_active_adr_stamps_provenance_from_the_seeded_identity(sandbox):
    assert not (sandbox / "home" / ".gitconfig").exists()
    result = seed(sandbox, [ADR])
    assert result.returncode == 0, result.stderr

    revision = show(sandbox, "adr/use-the-fixture-queue")["revision"]
    amend = seeded_lore(
        sandbox,
        "record", "amend", "adr/use-the-fixture-queue",
        "--reason", "fix a typo", "--initiated-by", "operator", "--base", revision,
        stdin="We use the queue, correctly.\n",
    )

    assert amend.returncode == 0, amend.stderr
    amended = show(sandbox, "adr/use-the-fixture-queue")
    assert amended["body"] == "We use the queue, correctly.\n"
    assert amended["sidecar"]["updated-by"] == "eval@example.invalid"


def test_wrapper_runs_with_no_pythonpath_and_no_lore_on_path(sandbox):
    result = seed(sandbox, [ADR])
    assert result.returncode == 0, result.stderr
    env = clean_env(sandbox)
    assert "PYTHONPATH" not in env and env["PATH"] == "/usr/bin:/bin"

    shown = seeded_lore(sandbox, "record", "show", "adr/use-the-fixture-queue", env=env)

    assert shown.returncode == 0, shown.stderr
    assert shown.stdout == ADR["body"]


def test_wrapper_resolves_trailhead_from_the_copy_beside_it(sandbox):
    result = seed(sandbox, [ADR])
    assert result.returncode == 0, result.stderr
    assert seeded_lore(sandbox, "record", "show", "adr/use-the-fixture-queue").returncode == 0

    (sandbox / "home" / "cwd" / "trailhead" / "paths.py").write_text("raise RuntimeError('copy was used')\n")

    broken = seeded_lore(sandbox, "record", "show", "adr/use-the-fixture-queue")
    assert broken.returncode != 0
    assert "copy was used" in broken.stderr


def test_copied_trailhead_package_carries_no_bytecode_cache(sandbox):
    (REPO_ROOT / "trailhead" / "__pycache__").mkdir(exist_ok=True)
    result = seed(sandbox, [ADR])
    assert result.returncode == 0, result.stderr

    copied = sandbox / "home" / "cwd" / "trailhead"
    assert (copied / "paths.py").is_file()
    assert list(copied.rglob("__pycache__")) == []


def test_wrapper_execs_the_resolved_interpreter_not_the_symlink_that_started_the_seeder(sandbox):
    link = sandbox / "py-link"
    link.symlink_to(PYTHON)
    result = seed(sandbox, [ADR], python=str(link))
    assert result.returncode == 0, result.stderr

    link.unlink()

    shown = seeded_lore(sandbox, "record", "show", "adr/use-the-fixture-queue")
    assert shown.returncode == 0, shown.stderr


def test_preexisting_lore_config_refuses_and_leaves_everything_unchanged(sandbox):
    home = sandbox / "home"
    target = home / ".config" / "lore" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"vaults": []}')
    before_home = snapshot(home)
    before_cwd = snapshot(sandbox / "home" / "cwd")

    result = seed(sandbox, [ADR])

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert snapshot(home) == before_home
    assert snapshot(sandbox / "home" / "cwd") == before_cwd == {}


def test_preexisting_gitconfig_with_an_identity_is_kept_and_its_identity_is_stamped(sandbox):
    gitconfig = sandbox / "home" / ".gitconfig"
    original = b"[user]\n\tname = Runner Identity\n\temail = runner@example.invalid\n"
    gitconfig.write_bytes(original)

    result = seed(sandbox, [ADR])
    assert result.returncode == 0, result.stderr
    assert gitconfig.read_bytes() == original

    amended = seeded_lore(
        sandbox, "record", "amend", "adr/use-the-fixture-queue",
        "--reason", "fix", "--initiated-by", "operator",
        "--diff", stdin="--- a\n+++ b\n@@ -1 +1 @@\n-We use the queue.\n+We use the stream.\n",
    )
    assert amended.returncode == 0, amended.stderr
    entry = show(sandbox, "adr/use-the-fixture-queue")["sidecar"]["change-history"][0]
    assert entry["by"] == "runner@example.invalid"


def test_preexisting_gitconfig_without_an_email_refuses_and_creates_nothing(sandbox):
    gitconfig = sandbox / "home" / ".gitconfig"
    original = b"[user]\n\tname = Nameless\n"
    gitconfig.write_bytes(original)
    env = clean_env(sandbox, GIT_CONFIG_NOSYSTEM="1")

    result = seed(sandbox, [ADR], env=env)

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert "user.email" in result.stderr
    assert gitconfig.read_bytes() == original
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert not (sandbox / "home" / ".config").exists()


OVERRIDES = {
    "XDG_CONFIG_HOME": "xdg-config",
    "XDG_STATE_HOME": "xdg-state",
    "LORE_CONFIG_DIR": "lore-config",
    "LORE_STATE_DIR": "lore-state",
}


@pytest.mark.parametrize("holds_config", [True, False], ids=["with-config", "empty"])
@pytest.mark.parametrize("var", sorted(OVERRIDES))
def test_override_pointing_outside_home_refuses_and_leaves_that_directory_unchanged(sandbox, var, holds_config):
    outside = sandbox / "outside"
    lore_dir = outside / "lore" if var.startswith("XDG") else outside
    lore_dir.mkdir(parents=True)
    if holds_config:
        (lore_dir / "config.json").write_bytes(b'{"vaults": []}')
        (lore_dir / "index.sqlite").write_bytes(b"state")
    before = snapshot(outside)

    result = seed(sandbox, [ADR], env=clean_env(sandbox, **{var: str(outside)}))

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert "outside HOME" in result.stderr
    assert snapshot(outside) == before
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert home_snapshot(sandbox) == {}


@pytest.mark.parametrize("var", sorted(OVERRIDES))
def test_override_pointing_inside_home_seeds_there(sandbox, var):
    inside = sandbox / "home" / OVERRIDES[var]
    env = clean_env(sandbox, **{var: str(inside)})

    result = seed(sandbox, [ADR], env=env)

    assert result.returncode == 0, result.stderr
    home = sandbox / "home"
    if var.endswith("CONFIG_DIR") or var == "XDG_CONFIG_HOME":
        assert list(inside.rglob("config.json")) != []
        assert not (home / ".config" / "lore").exists()
    else:
        assert list(inside.rglob("index.sqlite")) != []
        assert (home / ".config" / "lore" / "config.json").is_file()
    shown = seeded_lore(sandbox, "record", "show", "adr/use-the-fixture-queue", env=env)
    assert shown.returncode == 0, shown.stderr


def test_preexisting_config_at_an_overridden_location_inside_home_refuses(sandbox):
    inside = sandbox / "home" / "elsewhere"
    inside.mkdir()
    (inside / "config.json").write_bytes(b'{"vaults": []}')

    result = seed(sandbox, [ADR], env=clean_env(sandbox, LORE_CONFIG_DIR=str(inside)))

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert not (sandbox / "home" / ".gitconfig").exists()


def test_title_with_path_segments_and_shell_metacharacters_seeds_only_a_plain_named_record(sandbox):
    hostile = {**ADR, "title": "../../escape; $(touch pwned) `id` | x"}

    result = seed(sandbox, [hostile])

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (sandbox / "home" / "cwd").iterdir()) == ["lore", "trailhead", "vault"]
    assert sorted(p.name for p in sandbox.iterdir()) == ["home", "records.json"]
    created = sorted(p.stem for p in (sandbox / "home" / "cwd" / "vault" / "adr").glob("*.md"))
    assert created == ["escape-touch-pwned-id-x"]
    assert show(sandbox, "adr/escape-touch-pwned-id-x")["body"] == ADR["body"]


@pytest.mark.parametrize("kind", ["../adr", "adrs", "", "ADR"])
def test_kind_outside_lores_kinds_is_refused_before_any_write(sandbox, kind):
    result = seed(sandbox, [ADR, {**ADR, "title": "Second", "kind": kind}])

    assert result.returncode != 0
    assert "kind" in result.stderr
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert home_snapshot(sandbox) == {}


@pytest.mark.parametrize(
    "records, named",
    [
        ('{"kind": "adr"}', "list"),
        ("not json", "JSON"),
        ([{k: v for k, v in ADR.items() if k != "status"}], "status"),
        ([{**ADR, "title": ""}], "title"),
        ([{**ADR, "body": 7}], "body"),
        (["adr"], "record"),
    ],
)
def test_malformed_records_json_exits_nonzero_naming_the_problem_and_creates_nothing(sandbox, records, named):
    result = seed(sandbox, records)

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert named in result.stderr
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert home_snapshot(sandbox) == {}


def test_missing_records_json_is_refused_cleanly(sandbox):
    result = subprocess.run(
        [PYTHON, str(SEEDER), str(sandbox / "absent.json")],
        cwd=sandbox / "home" / "cwd", env=clean_env(sandbox), capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert "Traceback" not in result.stderr
    assert snapshot(sandbox / "home" / "cwd") == {}


def test_existing_vault_directory_in_cwd_is_refused_untouched(sandbox):
    (sandbox / "home" / "cwd" / "vault").mkdir()
    (sandbox / "home" / "cwd" / "vault" / "keep").write_text("mine")

    result = seed(sandbox, [ADR])

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert snapshot(sandbox / "home" / "cwd") == {"vault": b"", "vault/keep": b"mine"}
    assert home_snapshot(sandbox) == {}


def _case_tree(sandbox: Path) -> Path:
    """A repo-shaped tree whose pieces link to this checkout, holding a fresh case dir.

    The case's fixtures sit at the same depth under the tree as under the real
    repo, so the scaffold's repo lookup is exercised for real.
    """
    repo = sandbox / "repo"
    plugin = repo / "tools" / "lore" / "plugins" / "lore"
    plugin.mkdir(parents=True)
    (repo / "tools" / "lore" / "scripts").symlink_to(REPO_ROOT / "tools" / "lore" / "scripts")
    (repo / "trailhead").symlink_to(REPO_ROOT / "trailhead")
    for name in ("cli", "lore", "_bootstrap.py"):
        (plugin / name).symlink_to(REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / name)
    fixtures = plugin / "evals" / "a-case" / "fixtures"
    fixtures.mkdir(parents=True)
    return fixtures


@pytest.mark.parametrize("has_venv", [True, False], ids=["repo-venv", "path-python3"])
def test_scaffold_pattern_seeds_a_vault_and_wrapper_from_a_copied_case_dir(sandbox, has_venv):
    scaffold = REPO_ROOT / "tools" / "lore" / "plugins" / "lore" / "evals" / "scaffold.sh"
    fixtures = _case_tree(sandbox)
    shutil.copy(scaffold, fixtures / "scaffold.sh")
    os.chmod(fixtures / "scaffold.sh", 0o755)
    (fixtures / "records.json").write_text(json.dumps([ADR]))
    stubs = sandbox / "stubs"
    stubs.mkdir()
    if has_venv:
        (stubs / "python3").write_text("#!/bin/sh\nexit 99\n")
        (stubs / "python3").chmod(0o755)
        venv_bin = sandbox / "repo" / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").symlink_to(PYTHON)
    else:
        (stubs / "python3").symlink_to(PYTHON)
    env = clean_env(sandbox, PATH=f"{stubs}:/usr/bin:/bin")

    result = subprocess.run(
        [str(fixtures / "scaffold.sh")], cwd=sandbox / "home" / "cwd", env=env,
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert show(sandbox, "adr/use-the-fixture-queue")["body"] == ADR["body"]


def test_dangling_gitconfig_symlink_refuses_and_writes_nothing_through_it(sandbox):
    outside = sandbox / "outside"
    outside.mkdir()
    target = outside / "stolen-gitconfig"
    (sandbox / "home" / ".gitconfig").symlink_to(target)

    result = seed(sandbox, [ADR])

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert not target.exists()
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert not (sandbox / "home" / ".config").exists()


def test_live_gitconfig_symlink_refuses_and_leaves_its_target_unchanged(sandbox):
    outside = sandbox / "outside"
    outside.mkdir()
    target = outside / "real-gitconfig"
    target.write_text("[user]\n\temail = someone@example.invalid\n")
    (sandbox / "home" / ".gitconfig").symlink_to(target)

    result = seed(sandbox, [ADR])

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert target.read_text() == "[user]\n\temail = someone@example.invalid\n"
    assert snapshot(sandbox / "home" / "cwd") == {}


def test_nonempty_lore_state_dir_refuses_and_leaves_it_unchanged(sandbox):
    state = sandbox / "home" / "lore-state"
    (state / "vaults" / "live").mkdir(parents=True)
    (state / "vaults" / "live" / "keep").write_text("mine")
    before = home_snapshot(sandbox)

    result = seed(sandbox, [ADR], env=clean_env(sandbox, LORE_STATE_DIR=str(state)))

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert home_snapshot(sandbox) == before
    assert snapshot(sandbox / "home" / "cwd") == {}


def test_empty_lore_state_dir_seeds(sandbox):
    state = sandbox / "home" / "lore-state"
    state.mkdir()

    result = seed(sandbox, [ADR], env=clean_env(sandbox, LORE_STATE_DIR=str(state)))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("var", ["LORE_CONFIG_DIR", "LORE_STATE_DIR"])
def test_lore_dir_symlinked_outside_home_refuses_and_leaves_the_target_unchanged(sandbox, var):
    outside = sandbox / "outside"
    outside.mkdir()
    (outside / "keep").write_text("mine")
    link = sandbox / "home" / "lore-link"
    link.symlink_to(outside)
    before = snapshot(outside)

    result = seed(sandbox, [ADR], env=clean_env(sandbox, **{var: str(link)}))

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert "outside HOME" in result.stderr
    assert snapshot(outside) == before
    assert snapshot(sandbox / "home" / "cwd") == {}


def test_default_config_dir_symlinked_outside_home_refuses(sandbox):
    outside = sandbox / "outside"
    outside.mkdir()
    config_parent = sandbox / "home" / ".config"
    config_parent.mkdir()
    (config_parent / "lore").symlink_to(outside)

    result = seed(sandbox, [ADR])

    assert result.returncode != 0
    assert "outside HOME" in result.stderr
    assert snapshot(outside) == {}
    assert snapshot(sandbox / "home" / "cwd") == {}


@pytest.mark.parametrize("name", ["lore", "trailhead"])
def test_existing_cwd_entry_is_refused_untouched(sandbox, name):
    (sandbox / "home" / "cwd" / name).write_text("mine")

    result = seed(sandbox, [ADR])

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert snapshot(sandbox / "home" / "cwd") == {name: b"mine"}
    assert home_snapshot(sandbox) == {}


@pytest.mark.parametrize("kind,status", [("adr", "done"), ("task", "active"), ("spec", "bogus")])
def test_status_outside_the_kinds_vocabulary_refuses_before_creating_a_vault(sandbox, kind, status):
    result = seed(sandbox, [{**ADR, "kind": kind, "status": status}])

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert status in result.stderr
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert home_snapshot(sandbox) == {}


def test_home_reached_through_a_symlink_still_seeds(sandbox):
    link = sandbox / "home-link"
    link.symlink_to(sandbox / "home")

    result = seed(sandbox, [ADR], env=clean_env(sandbox, HOME=str(link)))

    assert result.returncode == 0, result.stderr
    assert (sandbox / "home" / ".config" / "lore" / "config.json").is_file()


def load_seeder():
    loader = importlib.machinery.SourceFileLoader("seed_eval_vault_under_test", str(SEEDER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def seed_in_process(sandbox: Path, records, monkeypatch, *, env=None, **patches):
    """Run the seeder's ``main`` in this process so a module seam can be patched.

    The environment is replaced wholesale by ``clean_env``'s, so nothing of the
    operator's own HOME or lore/XDG variables is visible to the run.
    """
    module = load_seeder()
    for name, value in patches.items():
        monkeypatch.setattr(module, name, value)
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name, value in (env if env is not None else clean_env(sandbox)).items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(sandbox / "home" / "cwd")
    return module.main(["seed-eval-vault", str(write_records(sandbox, records))])


def test_home_that_is_the_accounts_own_home_refuses_and_writes_nothing(sandbox, monkeypatch, capsys):
    account_home = lambda: sandbox / "home"  # noqa: E731

    code = seed_in_process(sandbox, [ADR], monkeypatch, account_home=account_home)

    err = capsys.readouterr().err
    assert code != 0
    assert err.startswith("seed-eval-vault: ")
    assert "account" in err
    assert home_snapshot(sandbox) == {}
    assert snapshot(sandbox / "home" / "cwd") == {}


def test_home_reached_through_a_symlink_to_the_accounts_home_refuses(sandbox, monkeypatch, capsys):
    link = sandbox / "home-link"
    link.symlink_to(sandbox / "home")
    env = clean_env(sandbox, HOME=str(link))

    code = seed_in_process(sandbox, [ADR], monkeypatch, env=env, account_home=lambda: sandbox / "home")

    assert code != 0
    assert "account" in capsys.readouterr().err
    assert home_snapshot(sandbox) == {}


def test_home_that_is_not_the_accounts_home_seeds(sandbox, monkeypatch, capsys):
    code = seed_in_process(sandbox, [ADR], monkeypatch, account_home=lambda: sandbox / "elsewhere")

    assert code == 0, capsys.readouterr().err
    assert (sandbox / "home" / "cwd" / "vault" / "adr" / "use-the-fixture-queue.md").is_file()


def test_cwd_outside_home_refuses_and_writes_nothing(sandbox):
    stray = sandbox / "stray-cwd"
    stray.mkdir()

    result = seed(sandbox, [ADR], cwd=stray)

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert "current directory" in result.stderr
    assert snapshot(stray) == {}
    assert home_snapshot(sandbox) == {}


def test_cwd_symlinked_out_of_home_refuses(sandbox):
    outside = sandbox / "stray-cwd"
    outside.mkdir()
    (sandbox / "home" / "cwd-link").symlink_to(outside)

    result = seed(sandbox, [ADR], cwd=sandbox / "home" / "cwd-link")

    assert result.returncode != 0
    assert "current directory" in result.stderr
    assert snapshot(outside) == {}


def test_cwd_nested_inside_home_seeds(sandbox):
    nested = sandbox / "home" / "a" / "b"
    nested.mkdir(parents=True)

    result = seed(sandbox, [ADR], cwd=nested)

    assert result.returncode == 0, result.stderr
    assert (nested / "vault" / "adr" / "use-the-fixture-queue.md").is_file()


@pytest.mark.parametrize("shadowed", ["XDG_CONFIG_HOME", "XDG_STATE_HOME"])
def test_xdg_override_outside_home_refuses_even_when_lore_dir_overrides_shadow_it(sandbox, shadowed):
    outside = sandbox / "outside"
    outside.mkdir()
    (outside / "keep").write_text("mine")
    before = snapshot(outside)
    env = clean_env(
        sandbox,
        LORE_CONFIG_DIR=str(sandbox / "home" / "lore-config"),
        LORE_STATE_DIR=str(sandbox / "home" / "lore-state"),
        **{shadowed: str(outside)},
    )

    result = seed(sandbox, [ADR], env=env)

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert shadowed in result.stderr
    assert "outside HOME" in result.stderr
    assert snapshot(outside) == before
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert home_snapshot(sandbox) == {}


def test_xdg_override_inside_home_shadowed_by_lore_dirs_seeds(sandbox):
    home = sandbox / "home"
    env = clean_env(
        sandbox,
        LORE_CONFIG_DIR=str(home / "lore-config"),
        LORE_STATE_DIR=str(home / "lore-state"),
        XDG_CONFIG_HOME=str(home / "xdg-config"),
        XDG_STATE_HOME=str(home / "xdg-state"),
    )

    result = seed(sandbox, [ADR], env=env)

    assert result.returncode == 0, result.stderr


def test_seeding_spawns_no_publish_worker_even_if_the_caller_did_not_disable_it(sandbox):
    env = clean_env(sandbox)
    del env["LORE_PUBLISH_DISABLE"]

    result = seed(sandbox, [ADR, SPEC], env=env)
    assert result.returncode == 0, result.stderr
    publish_dir = sandbox / "home" / ".local" / "state" / "lore" / "publish"
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if list(publish_dir.glob("*.log")) or list(publish_dir.glob("*.json")):
            break
        time.sleep(0.1)

    assert [p.name for p in publish_dir.glob("*") if p.suffix in (".log", ".json")] == []


@pytest.mark.parametrize("title", ["--status", "-x", "line one\nline two", "bell\x07", "tab\there", "nul\x00"])
def test_title_with_a_leading_dash_or_control_character_is_refused_before_any_write(sandbox, title):
    result = seed(sandbox, [ADR, {**ADR, "title": title}])

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert "title" in result.stderr
    assert home_snapshot(sandbox) == {}
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert not (sandbox / "home" / ".gitconfig").exists()


def _fake_repo(sandbox: Path) -> Path:
    repo = sandbox / "fake-repo"
    shutil.copytree(
        REPO_ROOT / "trailhead", repo / "trailhead", ignore=shutil.ignore_patterns("__pycache__")
    )
    git_env = {"PATH": "/usr/bin:/bin", "HOME": str(sandbox), "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        [
            "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "-q", "-m", "x",
        ],
    ):
        subprocess.run(["git", "-C", str(repo), *args], env=git_env, check=True, capture_output=True)
    return repo


def test_trailhead_copy_holds_only_git_tracked_files_and_the_wrapper_still_runs(sandbox, monkeypatch, capsys):
    repo = _fake_repo(sandbox)
    (repo / "trailhead" / "stray-untracked.txt").write_text("secret")
    (repo / "trailhead" / "untracked-dir").mkdir()
    (repo / "trailhead" / "untracked-dir" / "note.txt").write_text("secret")
    (repo / "trailhead" / "tracked-link.py").symlink_to(sandbox / "records.json")
    subprocess.run(
        ["git", "-C", str(repo), "add", "trailhead/tracked-link.py"],
        check=True, env={"PATH": "/usr/bin:/bin", "HOME": str(sandbox)},
    )

    code = seed_in_process(sandbox, [ADR], monkeypatch, REPO_ROOT=repo)

    assert code == 0, capsys.readouterr().err
    copied = sandbox / "home" / "cwd" / "trailhead"
    assert (copied / "paths.py").is_file()
    assert not (copied / "stray-untracked.txt").exists()
    assert not (copied / "untracked-dir").exists()
    assert not (copied / "tracked-link.py").exists()
    shown = seeded_lore(sandbox, "record", "show", "adr/use-the-fixture-queue")
    assert shown.returncode == 0, shown.stderr
    assert shown.stdout == ADR["body"]


def test_repo_that_is_not_a_git_checkout_is_refused_before_any_write(sandbox, monkeypatch, capsys):
    not_git = sandbox / "plain-copy"
    shutil.copytree(REPO_ROOT / "trailhead", not_git / "trailhead", ignore=shutil.ignore_patterns("__pycache__"))

    code = seed_in_process(sandbox, [ADR], monkeypatch, REPO_ROOT=not_git)

    assert code != 0
    assert capsys.readouterr().err.startswith("seed-eval-vault: ")
    assert home_snapshot(sandbox) == {}
    assert snapshot(sandbox / "home" / "cwd") == {}


def test_missing_git_binary_is_refused_rather_than_falling_back_to_a_full_copy(sandbox):
    empty = sandbox / "empty-bin"
    empty.mkdir()

    result = seed(sandbox, [ADR], env=clean_env(sandbox, PATH=str(empty)))

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert "git" in result.stderr
    assert home_snapshot(sandbox) == {}
    assert snapshot(sandbox / "home" / "cwd") == {}


FIRST = {"kind": "decision", "title": "Use Postgres", "status": "active", "body": "Postgres it is.\n"}


def linking(*related, **extra):
    return {"kind": "adr", "title": "Adopt the store", "status": "active", "body": "Adopted.\n", "related": list(related), **extra}


def test_related_entry_lands_under_its_kind_in_the_sidecar(sandbox):
    result = seed(sandbox, [FIRST, linking("decision=use-postgres")])
    assert result.returncode == 0, result.stderr

    assert show(sandbox, "adr/adopt-the-store")["sidecar"]["related"]["decision"] == ["use-postgres"]


def test_two_related_entries_produce_both_links(sandbox):
    result = seed(sandbox, [FIRST, ADR, linking("decision=use-postgres", "adr=use-the-fixture-queue")])
    assert result.returncode == 0, result.stderr

    assert show(sandbox, "adr/adopt-the-store")["sidecar"]["related"] == {
        "decision": ["use-postgres"],
        "adr": ["use-the-fixture-queue"],
    }


def test_record_without_related_has_no_related_key(sandbox):
    result = seed(sandbox, [FIRST, ADR])
    assert result.returncode == 0, result.stderr

    assert "related" not in show(sandbox, "adr/use-the-fixture-queue")["sidecar"]


@pytest.mark.parametrize(
    "related",
    ["decision=use-postgres", ["use-postgres"], ["decision="], ["=use-postgres"], [7], ["nokind=use-postgres"]],
)
def test_malformed_related_is_refused_and_creates_nothing(sandbox, related):
    result = seed(sandbox, [FIRST, {**linking(), "related": related}])

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert "related" in result.stderr
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert home_snapshot(sandbox) == {}


@pytest.mark.parametrize("records", [
    [linking("decision=use-postgress"), FIRST],
    [FIRST, linking("adr=use-postgres")],
    [linking("decision=adopt-the-store")],
    [linking("adr=adopt-the-store")],
])
def test_related_naming_no_earlier_seeded_record_is_refused_and_creates_nothing(sandbox, records):
    result = seed(sandbox, records)

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert "related" in result.stderr
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert home_snapshot(sandbox) == {}


def test_search_by_related_edge_returns_the_record_that_links_the_earlier_one(sandbox):
    result = seed(sandbox, [FIRST, linking("decision=use-postgres")])
    assert result.returncode == 0, result.stderr

    found = seeded_lore(sandbox, "search", "related-decision:use-postgres")
    assert found.returncode == 0, found.stderr
    assert "adr/adopt-the-store" in found.stdout
    assert "decision/use-postgres" not in found.stdout


def test_unknown_field_is_refused_and_creates_nothing(sandbox):
    result = seed(sandbox, [FIRST, {**ADR, "title": "Other", "relatedd": ["decision=use-postgres"]}])

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert "relatedd" in result.stderr
    assert snapshot(sandbox / "home" / "cwd") == {}
    assert home_snapshot(sandbox) == {}
