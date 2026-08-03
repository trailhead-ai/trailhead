"""Tests for ranger.drain.preflight — the drain-specific startup checks.

Test contract:
- `find_execute_procedure` finds craft's execute.md under any composed
  harness root and refuses, naming the remediation, when it is absent.
- `check_portage_presence` is True when portage's plugin marker is composed
  under any harness root, False (never raising) otherwise.
- `run_preflight` runs the shared sweep checks (procedure, provenance,
  group, vault) before returning, and sets `degraded` from portage presence
  rather than refusing on it — the one check that never raises.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "ranger" / "plugins" / "ranger"
_CAMP_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

for _p in (_PLUGIN_DIR, _CAMP_PLUGIN_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ranger.drain import preflight as drain_preflight  # noqa: E402


def _install_execute_procedure(tmp_path: Path, *, harness: str = "harness-a") -> Path:
    procedure = (
        tmp_path
        / "state"
        / "trailhead"
        / "composed"
        / harness
        / "plugins"
        / "craft"
        / "skills"
        / "_shared"
        / "execute.md"
    )
    procedure.parent.mkdir(parents=True, exist_ok=True)
    procedure.write_text("# execute procedure\n", encoding="utf-8")
    return procedure


def _install_portage_marker(tmp_path: Path, *, harness: str = "harness-a") -> Path:
    marker = (
        tmp_path
        / "state"
        / "trailhead"
        / "composed"
        / harness
        / "plugins"
        / "portage"
        / ".claude-plugin"
        / "plugin.json"
    )
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}", encoding="utf-8")
    return marker


def _env(tmp_path: Path) -> dict[str, str]:
    return {"TRAILHEAD_STATE_DIR": str(tmp_path / "state" / "trailhead")}


# ---------------------------------------------------------------------------
# find_execute_procedure
# ---------------------------------------------------------------------------


def test_find_execute_procedure_refuses_when_absent(tmp_path):
    with pytest.raises(drain_preflight.PreflightError, match="execute procedure"):
        drain_preflight.find_execute_procedure(env=_env(tmp_path))


def test_find_execute_procedure_refusal_names_remediation(tmp_path):
    with pytest.raises(drain_preflight.PreflightError) as exc:
        drain_preflight.find_execute_procedure(env=_env(tmp_path))

    assert "trailhead install --plugin craft" in str(exc.value)
    assert "plugins/craft/skills/_shared/execute.md" in str(exc.value)


def test_find_execute_procedure_finds_it_under_any_harness(tmp_path):
    procedure = _install_execute_procedure(tmp_path, harness="some-other-harness")

    found_procedure, templates_root = drain_preflight.find_execute_procedure(env=_env(tmp_path))

    assert found_procedure == procedure
    assert templates_root == procedure.parents[2] / "templates"


# ---------------------------------------------------------------------------
# check_portage_presence
# ---------------------------------------------------------------------------


def test_check_portage_presence_false_when_absent(tmp_path):
    assert drain_preflight.check_portage_presence(env=_env(tmp_path)) is False


def test_check_portage_presence_true_when_composed(tmp_path):
    _install_portage_marker(tmp_path)

    assert drain_preflight.check_portage_presence(env=_env(tmp_path)) is True


def test_check_portage_presence_never_raises_on_a_missing_composed_dir(tmp_path):
    # tmp_path/state/trailhead/composed does not exist at all.
    assert drain_preflight.check_portage_presence(env=_env(tmp_path)) is False


# ---------------------------------------------------------------------------
# run_preflight
# ---------------------------------------------------------------------------


def _write_group_config(camp_config_dir: Path, *, group: str, repo: Path) -> None:
    groups = camp_config_dir / "groups"
    groups.mkdir(parents=True, exist_ok=True)
    (groups / f"{group}.toml").write_text(
        textwrap.dedent(
            f"""\
            [group]
            name = "{group}"

            [[members]]
            name = "member"
            repo_root = "{repo}"
            """
        ),
        encoding="utf-8",
    )


def _vault_resolve_runner(payload: dict):
    def runner(cmd, **kwargs):
        assert cmd[:3] == ["lore", "vault", "resolve"]
        return subprocess.CompletedProcess(cmd, 0, stdout=__import__("json").dumps(payload), stderr="")

    return runner


def test_run_preflight_refuses_before_the_group_check_when_procedure_is_absent(tmp_path):
    env = _env(tmp_path)

    with pytest.raises(drain_preflight.PreflightError, match="execute procedure"):
        drain_preflight.run_preflight(cwd=tmp_path, env=env)


def _full_env(tmp_path: Path, *, camp_config: Path, camp_state: Path) -> dict[str, str]:
    # `env=` replaces os.environ wholesale for every trailhead.paths resolver
    # this call chain touches (see trailhead/paths.py), so every var any of
    # them needs must be present here — monkeypatch.setenv would be invisible
    # to a resolver called with an explicit env dict.
    return {
        "TRAILHEAD_STATE_DIR": str(tmp_path / "state" / "trailhead"),
        "CAMP_CONFIG_DIR": str(camp_config),
        "CAMP_STATE_DIR": str(camp_state),
        "LORE_EMAIL": "drain-tests@example.invalid",
    }


def test_run_preflight_returns_degraded_true_when_portage_absent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    camp_config = tmp_path / "config" / "camp"
    camp_state = tmp_path / "state" / "camp"
    _write_group_config(camp_config, group="mygroup", repo=repo)
    _install_execute_procedure(tmp_path)

    env = _full_env(tmp_path, camp_config=camp_config, camp_state=camp_state)
    runner = _vault_resolve_runner(
        {"scope": "team", "vault": "myvault", "path": "/vaults/myvault", "source": {"team": "myvault"}}
    )

    result = drain_preflight.run_preflight(cwd=repo, env=env, runner=runner)

    assert result["degraded"] is True
    assert result["group"] == "mygroup"
    assert result["vault"] == "myvault"
    assert result["committer_email"] == "drain-tests@example.invalid"


def test_run_preflight_returns_degraded_false_when_portage_present(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    camp_config = tmp_path / "config" / "camp"
    camp_state = tmp_path / "state" / "camp"
    _write_group_config(camp_config, group="mygroup", repo=repo)
    _install_execute_procedure(tmp_path)
    _install_portage_marker(tmp_path)

    env = _full_env(tmp_path, camp_config=camp_config, camp_state=camp_state)
    runner = _vault_resolve_runner(
        {"scope": "team", "vault": "myvault", "path": "/vaults/myvault", "source": {"team": "myvault"}}
    )

    result = drain_preflight.run_preflight(cwd=repo, env=env, runner=runner)

    assert result["degraded"] is False
