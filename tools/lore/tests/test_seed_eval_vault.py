"""Behavioural tests for ``tools/lore/scripts/seed-eval-vault``.

The seeder builds a throwaway lore vault in a runner eval case's working
directory. Every test runs it as a real subprocess with a hand-built
environment — HOME, cwd and every lore/XDG directory inside ``tmp_path`` — so
nothing here can reach the operator's own lore config, state or ``~/.gitconfig``.
``LORE_PUBLISH_DISABLE`` keeps lore's detached publish worker from being spawned.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
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


def seed(sandbox: Path, records, *, env=None, python: str = PYTHON):
    path = write_records(sandbox, records)
    return subprocess.run(
        [python, str(SEEDER), str(path)],
        cwd=sandbox / "cwd",
        env=env if env is not None else clean_env(sandbox),
        capture_output=True,
        text=True,
    )


def seeded_lore(sandbox: Path, *args: str, stdin: str | None = None, env=None):
    return subprocess.run(
        ["./lore", *args],
        cwd=sandbox / "cwd",
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

    (sandbox / "cwd" / "trailhead" / "paths.py").write_text("raise RuntimeError('copy was used')\n")

    broken = seeded_lore(sandbox, "record", "show", "adr/use-the-fixture-queue")
    assert broken.returncode != 0
    assert "copy was used" in broken.stderr


def test_copied_trailhead_package_carries_no_bytecode_cache(sandbox):
    (REPO_ROOT / "trailhead" / "__pycache__").mkdir(exist_ok=True)
    result = seed(sandbox, [ADR])
    assert result.returncode == 0, result.stderr

    copied = sandbox / "cwd" / "trailhead"
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
    before_cwd = snapshot(sandbox / "cwd")

    result = seed(sandbox, [ADR])

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert snapshot(home) == before_home
    assert snapshot(sandbox / "cwd") == before_cwd == {}


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
    assert snapshot(sandbox / "cwd") == {}
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
    assert snapshot(sandbox / "cwd") == {}
    assert snapshot(sandbox / "home") == {}


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
    assert snapshot(sandbox / "cwd") == {}
    assert not (sandbox / "home" / ".gitconfig").exists()


def test_title_with_path_segments_and_shell_metacharacters_seeds_only_a_plain_named_record(sandbox):
    hostile = {**ADR, "title": "../../escape; $(touch pwned) `id` | x"}

    result = seed(sandbox, [hostile])

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (sandbox / "cwd").iterdir()) == ["lore", "trailhead", "vault"]
    assert sorted(p.name for p in sandbox.iterdir()) == ["cwd", "home", "records.json"]
    created = sorted(p.stem for p in (sandbox / "cwd" / "vault" / "adr").glob("*.md"))
    assert created == ["escape-touch-pwned-id-x"]
    assert show(sandbox, "adr/escape-touch-pwned-id-x")["body"] == ADR["body"]


@pytest.mark.parametrize("kind", ["../adr", "adrs", "", "ADR"])
def test_kind_outside_lores_kinds_is_refused_before_any_write(sandbox, kind):
    result = seed(sandbox, [ADR, {**ADR, "title": "Second", "kind": kind}])

    assert result.returncode != 0
    assert "kind" in result.stderr
    assert snapshot(sandbox / "cwd") == {}
    assert snapshot(sandbox / "home") == {}


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
    assert snapshot(sandbox / "cwd") == {}
    assert snapshot(sandbox / "home") == {}


def test_missing_records_json_is_refused_cleanly(sandbox):
    result = subprocess.run(
        [PYTHON, str(SEEDER), str(sandbox / "absent.json")],
        cwd=sandbox / "cwd", env=clean_env(sandbox), capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert "Traceback" not in result.stderr
    assert snapshot(sandbox / "cwd") == {}


def test_existing_vault_directory_in_cwd_is_refused_untouched(sandbox):
    (sandbox / "cwd" / "vault").mkdir()
    (sandbox / "cwd" / "vault" / "keep").write_text("mine")

    result = seed(sandbox, [ADR])

    assert result.returncode != 0
    assert result.stderr.startswith("seed-eval-vault: ")
    assert snapshot(sandbox / "cwd") == {"vault": b"", "vault/keep": b"mine"}
    assert snapshot(sandbox / "home") == {}


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
    (repo / ".venv").symlink_to(REPO_ROOT / ".venv")
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
    else:
        (sandbox / "repo" / ".venv").unlink()
        (stubs / "python3").symlink_to(PYTHON)
    env = clean_env(sandbox, PATH=f"{stubs}:/usr/bin:/bin")

    result = subprocess.run(
        [str(fixtures / "scaffold.sh")], cwd=sandbox / "cwd", env=env,
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert show(sandbox, "adr/use-the-fixture-queue")["body"] == ADR["body"]
