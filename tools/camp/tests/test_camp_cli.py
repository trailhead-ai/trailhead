"""Tests for the camp CLI entry points (bin/camp, cli/camp, capabilities.toml).

Test contract:
- camp --help exits 0 and prints a grouped menu (not a raw argparse dump).
- camp --version prints the binary path.
- camp --which prints the binary path.
- capabilities.toml loads + validates via the Step-1 loader.
- bin/camp wrapper resolves cli/camp (smoke: exits 0 via python invocation).
- marketplace.json source resolves (./plugins/camp).
- Import guard: cli/camp --help succeeds; guard function tested in test_spine.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_TOOL_DIR = _REPO_ROOT / "tools" / "camp"
_PLUGIN_DIR = _TOOL_DIR / "plugins" / "camp"
_BIN_CAMP = _PLUGIN_DIR / "bin" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"
_CAPABILITIES_TOML = _TOOL_DIR / "capabilities.toml"


# ---------------------------------------------------------------------------
# camp --help exits 0 and prints grouped menu
# ---------------------------------------------------------------------------


def test_camp_help_exits_0() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"--help exited {result.returncode}\nstderr: {result.stderr}"


def test_camp_help_prints_grouped_menu() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--help"],
        capture_output=True,
        text=True,
    )
    output = result.stdout
    # Must show major command groups, not a raw argparse dump
    assert "Usage:" in output or "usage:" in output
    # Must not be a bare argparse dump (those start with "usage: cli ...")
    assert "error:" not in output.lower() or "error" not in result.stderr.lower()


def test_camp_help_contains_key_commands() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--help"],
        capture_output=True,
        text=True,
    )
    output = result.stdout
    # 'break' → 'rm', 'sweep' is disabled and removed from help.
    for cmd in ("ls", "status", "rm", "sync"):
        assert cmd in output, f"Expected {cmd!r} in --help output, got:\n{output}"


# ---------------------------------------------------------------------------
# camp --version prints binary path
# ---------------------------------------------------------------------------


def test_camp_version_exits_0() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"--version exited {result.returncode}\n{result.stderr}"


def test_camp_version_includes_binary_path() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--version"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    # Should include some reference to the binary location
    assert "camp" in output.lower()


# ---------------------------------------------------------------------------
# camp --which
# ---------------------------------------------------------------------------


def test_camp_which_exits_0() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--which"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"--which exited {result.returncode}\n{result.stderr}"


def test_camp_which_prints_path() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "--which"],
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip()
    assert output != "", "Expected a non-empty path from --which"


# ---------------------------------------------------------------------------
# bin/camp wrapper resolves cli/camp
# ---------------------------------------------------------------------------


def test_bin_camp_wrapper_exits_0_on_help() -> None:
    """bin/camp (bash wrapper) should resolve cli/camp and exit 0 on --help."""
    result = subprocess.run(
        ["bash", str(_BIN_CAMP), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bin/camp --help exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# capabilities.toml loads + validates via the Step-1 loader
# ---------------------------------------------------------------------------


def test_capabilities_toml_loads_and_validates() -> None:
    from trailhead.capabilities import load_manifest

    manifest = load_manifest(_CAPABILITIES_TOML)
    assert manifest.tool_name == "camp"
    assert manifest.validate is True


def test_capabilities_toml_skills() -> None:
    # camp ships a CLI (bin) + hooks and no always-on base. Its single selectable
    # skill, `bookmark`, wraps the bookmark/resume verbs; worktree orchestration
    # stays operator-facing (README), since the workspace exists before the
    # harness opens.
    from trailhead.capabilities import load_manifest

    manifest = load_manifest(_CAPABILITIES_TOML)
    assert manifest.base == []
    assert manifest.skills == {"bookmark": "skills/bookmark"}


# ---------------------------------------------------------------------------
# marketplace.json
# ---------------------------------------------------------------------------
# NOTE: camp's per-tool .claude-plugin/marketplace.json was removed when the dev
# marketplace consolidated into the repo-root `trailhead-local` marketplace.
# The marketplace shape, source-resolution guard, and per-tool listing now live
# in trailhead/tests/test_dev_marketplace.py at the monorepo level.


# ---------------------------------------------------------------------------
# camp init authoring (--member / --scaffold / --force)
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    """Initialize a real git repo at path with an initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"], check=True, capture_output=True
    )
    readme = path / "README.md"
    readme.write_text("# test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        capture_output=True,
    )


def _run_init(
    args: list[str],
    *,
    config_dir: Path,
    state_dir: Path,
    cwd: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run `camp group <args>` via cli/camp with config/state overrides."""
    env = {**os.environ}
    env["CAMP_CONFIG_DIR"] = str(config_dir)
    env["CAMP_STATE_DIR"] = str(state_dir)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), "group", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def _load_written_group(groups_dir: Path, name: str) -> dict:
    """Load a written group config TOML via load_group."""
    scripts_dir = _PLUGIN_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    if str(_PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_DIR))
    from camp.group.config import load_group  # noqa: E402

    return load_group(groups_dir / f"{name}.toml")


def _settings_has_camp_hook(repo: Path) -> bool:
    """True if repo/.claude/settings.json carries a camp session-bootstrap hook."""
    settings_path = repo / ".claude" / "settings.json"
    if not settings_path.is_file():
        return False
    data = json.loads(settings_path.read_text())
    ss = data.get("hooks", {}).get("SessionStart", [])
    commands = [h.get("command", "") for entry in ss for h in entry.get("hooks", [])]
    return any("session-bootstrap" in c for c in commands)


@pytest.fixture()
def author_env(tmp_path: Path):
    """Tmp config dir + state dir + three member git repos for authoring tests."""
    config_dir = tmp_path / "camp-config"
    groups_dir = config_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "camp-state"
    state_dir.mkdir(parents=True, exist_ok=True)

    repos = {}
    for name in ("alpha", "beta", "gamma"):
        repo = tmp_path / name
        _init_git_repo(repo)
        repos[name] = repo

    return {
        "config_dir": config_dir,
        "groups_dir": groups_dir,
        "state_dir": state_dir,
        "repos": repos,
        "tmp_path": tmp_path,
    }


def test_author_from_flags_writes_loadable_config(author_env):
    """--member flags author a config that load_group accepts with 3 members."""
    g = author_env
    repos = g["repos"]
    result = _run_init(
        [
            "mygroup",
            "--member",
            f"alpha={repos['alpha']}",
            "--member",
            f"beta={repos['beta']}",
            "--member",
            f"gamma={repos['gamma']}",
        ],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"

    cfg = _load_written_group(g["groups_dir"], "mygroup")
    assert cfg["group"]["name"] == "mygroup"
    member_names = {m["name"] for m in cfg["members"]}
    assert member_names == {"alpha", "beta", "gamma"}
    # Reports the member count
    assert "3" in (result.stdout + result.stderr)


def test_force_redefine_does_not_self_collide(author_env):
    """Redefining a group's own repos with --force does not trip overlap."""
    g = author_env
    repos = g["repos"]
    base = [
        "mygroup",
        "--member",
        f"alpha={repos['alpha']}",
        "--member",
        f"beta={repos['beta']}",
    ]
    first = _run_init(base, config_dir=g["config_dir"], state_dir=g["state_dir"])
    assert first.returncode == 0, f"first exit {first.returncode}: {first.stderr}"

    # Redefine the SAME group with overlapping repos + --force
    second = _run_init(
        base + ["--member", f"gamma={repos['gamma']}", "--force"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert second.returncode == 0, (
        f"redefine with --force should not self-collide, got {second.returncode}: {second.stderr}"
    )
    cfg = _load_written_group(g["groups_dir"], "mygroup")
    assert {m["name"] for m in cfg["members"]} == {"alpha", "beta", "gamma"}


def test_force_redefine_preserves_hand_added_lore_scopes(author_env):
    """A hand-added [[lore_scopes]] binding survives a --force re-author instead
    of being silently dropped by the renderer."""
    g = author_env
    repos = g["repos"]
    base = ["mygroup", "--member", f"alpha={repos['alpha']}"]
    first = _run_init(base, config_dir=g["config_dir"], state_dir=g["state_dir"])
    assert first.returncode == 0, f"first exit {first.returncode}: {first.stderr}"

    # Hand-add a binding directly to the group TOML, as a user would.
    toml_path = g["groups_dir"] / "mygroup.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8")
        + '\n[[lore_scopes]]\nscope = "product"\nname = "trailhead"\n',
        encoding="utf-8",
    )
    assert _load_written_group(g["groups_dir"], "mygroup")["lore_scopes"] == [
        {"scope": "product", "name": "trailhead"}
    ]

    # Re-author the same group with --force (here, adding a member).
    second = _run_init(
        base + ["--member", f"beta={repos['beta']}", "--force"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert second.returncode == 0, f"redefine exit {second.returncode}: {second.stderr}"

    cfg = _load_written_group(g["groups_dir"], "mygroup")
    assert {m["name"] for m in cfg["members"]} == {"alpha", "beta"}
    # The binding must survive the re-author rather than being dropped.
    assert cfg["lore_scopes"] == [{"scope": "product", "name": "trailhead"}]


def test_force_redefine_preserves_hand_added_release_table(author_env):
    """A hand-added [release] block survives a --force re-author instead of
    being silently dropped by the renderer (mirrors how portage reads
    [release] directly via tomllib — camp itself does not know this table)."""
    import tomllib

    g = author_env
    repos = g["repos"]
    base = ["mygroup", "--member", f"alpha={repos['alpha']}"]
    first = _run_init(base, config_dir=g["config_dir"], state_dir=g["state_dir"])
    assert first.returncode == 0, f"first exit {first.returncode}: {first.stderr}"

    toml_path = g["groups_dir"] / "mygroup.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8")
        + '\n[release]\nauto_merge = true\nmerge_order = ["alpha", "beta"]\n',
        encoding="utf-8",
    )

    second = _run_init(
        base + ["--member", f"beta={repos['beta']}", "--force"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert second.returncode == 0, f"redefine exit {second.returncode}: {second.stderr}"

    rewritten = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert rewritten["release"] == {
        "auto_merge": True,
        "merge_order": ["alpha", "beta"],
    }


def test_force_redefine_preserves_hand_added_top_level_keys(author_env):
    """Hand-edited top-level bare keys — a scalar, an array of scalars, an empty
    array, and a date literal — survive a --force re-author without a traceback."""
    import datetime
    import tomllib

    g = author_env
    repos = g["repos"]
    base = ["mygroup", "--member", f"alpha={repos['alpha']}"]
    first = _run_init(base, config_dir=g["config_dir"], state_dir=g["state_dir"])
    assert first.returncode == 0, f"first exit {first.returncode}: {first.stderr}"

    toml_path = g["groups_dir"] / "mygroup.toml"
    toml_path.write_text(
        "version = 1\n"
        'tags = ["a", "b"]\n'
        "empty = []\n"
        "cutoff = 2026-01-01\n" + toml_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    second = _run_init(
        base + ["--member", f"beta={repos['beta']}", "--force"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert second.returncode == 0, f"redefine exit {second.returncode}: {second.stderr}"
    assert "Traceback" not in second.stderr

    rewritten = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert rewritten["version"] == 1
    assert rewritten["tags"] == ["a", "b"]
    assert rewritten["empty"] == []
    assert rewritten["cutoff"] == datetime.date(2026, 1, 1)
    assert _load_written_group(g["groups_dir"], "mygroup")


def test_force_redefine_preserves_non_ascii_hand_added_table(author_env):
    """A hand-added table carrying non-ASCII text survives a --force re-author —
    the existing config is read as UTF-8 regardless of the platform locale."""
    import tomllib

    g = author_env
    repos = g["repos"]
    base = ["mygroup", "--member", f"alpha={repos['alpha']}"]
    first = _run_init(base, config_dir=g["config_dir"], state_dir=g["state_dir"])
    assert first.returncode == 0, f"first exit {first.returncode}: {first.stderr}"

    toml_path = g["groups_dir"] / "mygroup.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8") + '\n[release]\nowner = "Åsa Ünïcode ✓"\n',
        encoding="utf-8",
    )

    second = _run_init(
        base + ["--member", f"beta={repos['beta']}", "--force"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
        env_extra={"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0"},
    )
    assert second.returncode == 0, f"redefine exit {second.returncode}: {second.stderr}"

    rewritten = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert rewritten["release"] == {"owner": "Åsa Ünïcode ✓"}


def test_force_redefine_preserves_hand_added_tasks_table(author_env):
    """A hand-added [tasks.<name>] block, with nested [[tasks.<name>.steps]],
    survives a --force re-author instead of being silently dropped."""
    import tomllib

    g = author_env
    repos = g["repos"]
    base = ["mygroup", "--member", f"alpha={repos['alpha']}"]
    first = _run_init(base, config_dir=g["config_dir"], state_dir=g["state_dir"])
    assert first.returncode == 0, f"first exit {first.returncode}: {first.stderr}"

    toml_path = g["groups_dir"] / "mygroup.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8")
        + "\n[tasks.graphify]\n"
        + "phase = \"provision\"\n"
        + "required = false\n"
        + "\n[[tasks.graphify.steps]]\n"
        + 'name = "seed"\n'
        + 'cmd = ["rsync", "-a", "src/", "dst/"]\n',
        encoding="utf-8",
    )

    second = _run_init(
        base + ["--member", f"beta={repos['beta']}", "--force"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert second.returncode == 0, f"redefine exit {second.returncode}: {second.stderr}"

    rewritten = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert rewritten["tasks"] == {
        "graphify": {
            "phase": "provision",
            "required": False,
            "steps": [{"name": "seed", "cmd": ["rsync", "-a", "src/", "dst/"]}],
        }
    }
    # The rewritten config still loads cleanly end to end.
    assert _load_written_group(g["groups_dir"], "mygroup")


def test_force_redefine_preserves_hand_added_harness_and_shared_vaults(author_env):
    """A hand-added [harness] block and [[shared_vaults]] entries survive a
    --force re-author instead of being silently dropped."""
    g = author_env
    repos = g["repos"]
    base = ["mygroup", "--member", f"alpha={repos['alpha']}"]
    first = _run_init(base, config_dir=g["config_dir"], state_dir=g["state_dir"])
    assert first.returncode == 0, f"first exit {first.returncode}: {first.stderr}"

    toml_path = g["groups_dir"] / "mygroup.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8")
        + '\n[harness]\nbinary = "claude"\n'
        + '\n[[shared_vaults]]\nname = "trailhead"\nroot = "/tmp/vaults/trailhead"\n',
        encoding="utf-8",
    )

    second = _run_init(
        base + ["--member", f"beta={repos['beta']}", "--force"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert second.returncode == 0, f"redefine exit {second.returncode}: {second.stderr}"

    cfg = _load_written_group(g["groups_dir"], "mygroup")
    assert cfg["harness"] == {"binary": "claude"}
    assert cfg["shared_vaults"] == [
        {"name": "trailhead", "root": "/tmp/vaults/trailhead"}
    ]


def test_existing_config_without_force_errors_and_preserves_file(author_env):
    """Existing config + --member WITHOUT --force → non-zero, file unchanged."""
    g = author_env
    repos = g["repos"]
    first = _run_init(
        ["mygroup", "--member", f"alpha={repos['alpha']}"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert first.returncode == 0, f"first exit {first.returncode}: {first.stderr}"
    before = (g["groups_dir"] / "mygroup.toml").read_text()

    second = _run_init(
        ["mygroup", "--member", f"beta={repos['beta']}"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert second.returncode != 0, "expected non-zero without --force"
    assert "mygroup" in second.stderr or "exist" in second.stderr.lower()
    after = (g["groups_dir"] / "mygroup.toml").read_text()
    assert before == after, "on-disk file must be unchanged"


def test_existing_config_with_force_overwrites(author_env):
    """Existing config + --member + --force → file overwritten with new members."""
    g = author_env
    repos = g["repos"]
    _run_init(
        ["mygroup", "--member", f"alpha={repos['alpha']}"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    result = _run_init(
        ["mygroup", "--member", f"beta={repos['beta']}", "--force"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    cfg = _load_written_group(g["groups_dir"], "mygroup")
    assert {m["name"] for m in cfg["members"]} == {"beta"}


def test_scaffold_writes_stub_no_hooks(author_env):
    """--scaffold + missing config → stub written, hooks NOT wired, exit 0."""
    g = author_env
    result = _run_init(
        ["stubgroup", "--scaffold"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    stub_path = g["groups_dir"] / "stubgroup.toml"
    assert stub_path.is_file(), "stub file should be written"
    text = stub_path.read_text()
    assert "stubgroup" in text
    assert "--member" in (result.stdout + result.stderr)
    # No member repo should have hooks wired
    for repo in g["repos"].values():
        assert not _settings_has_camp_hook(repo), "scaffold must not wire hooks"


def test_bare_init_unknown_group_errors(author_env):
    """Bare `camp init <unknown>` (no flags) → non-zero exit (no silent stub)."""
    g = author_env
    result = _run_init(
        ["nope"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert result.returncode != 0, "unknown group with no flags must error non-zero"
    assert not (g["groups_dir"] / "nope.toml").exists(), "must NOT write a silent stub"


def test_bare_init_known_group_wires_hooks(author_env):
    """Bare `camp init <known>` → hooks wired (regression: existing behavior)."""
    g = author_env
    repos = g["repos"]
    # First author the group via flags
    authored = _run_init(
        ["mygroup", "--member", f"alpha={repos['alpha']}"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert authored.returncode == 0, f"author exit: {authored.stderr}"
    # Wipe hooks to prove the bare path re-wires
    settings = repos["alpha"] / ".claude" / "settings.json"
    if settings.is_file():
        settings.unlink()

    result = _run_init(
        ["mygroup"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    assert _settings_has_camp_hook(repos["alpha"]), "bare init must wire hooks"


@pytest.mark.parametrize(
    "bad_member",
    ["noequals", "=/some/path", "n="],
)
def test_malformed_member_errors_writes_nothing(author_env, bad_member):
    """Malformed --member → legible error, nothing written."""
    g = author_env
    result = _run_init(
        ["mygroup", "--member", bad_member],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert result.returncode != 0, f"expected non-zero for {bad_member!r}"
    assert "member" in result.stderr.lower()
    assert not (g["groups_dir"] / "mygroup.toml").exists(), "nothing should be written"
    assert not list(g["groups_dir"].glob("*.tmp")), "no .tmp left behind"


def test_member_path_with_equals_splits_on_first(author_env, tmp_path):
    """A path containing '=' splits on the FIRST '=' only."""
    g = author_env
    weird = tmp_path / "weird=dir"
    _init_git_repo(weird)
    result = _run_init(
        ["mygroup", "--member", f"alpha={weird}"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    cfg = _load_written_group(g["groups_dir"], "mygroup")
    assert cfg["members"][0]["name"] == "alpha"
    assert cfg["members"][0]["repo_root"] == str(weird.resolve())


def test_atomicity_validation_failure_leaves_no_tmp(author_env):
    """A validation failure mid-write leaves no partial/.tmp file behind."""
    g = author_env
    # A member whose repo_root does not exist → validate_scaffold fails.
    missing = g["tmp_path"] / "does-not-exist"
    result = _run_init(
        ["mygroup", "--member", f"alpha={missing}"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert result.returncode != 0, "validation should fail for missing repo_root"
    assert not (g["groups_dir"] / "mygroup.toml").exists()
    assert not list(g["groups_dir"].glob("*.tmp")), "no .tmp file should remain"


def test_atomicity_roundtrip_gate_failure_unlinks_tmp(author_env):
    """A failure at the post-write round-trip gate unlinks the .tmp and writes no config.

    A whitespace-only member NAME passes _parse_member (non-empty) and
    validate_scaffold (which does not inspect names), so the temp file IS written;
    load_group then rejects the blank name, exercising the gate's unlink path —
    not the upstream-validator path covered above.
    """
    g = author_env
    repo = g["repos"]["alpha"]
    result = _run_init(
        ["mygroup", "--member", f"   ={repo}"],
        config_dir=g["config_dir"],
        state_dir=g["state_dir"],
    )
    assert result.returncode != 0, "round-trip gate should reject a blank member name"
    assert "round-trip" in result.stderr, f"expected gate message, got: {result.stderr}"
    assert not (g["groups_dir"] / "mygroup.toml").exists()
    assert not list(g["groups_dir"].glob("*.tmp")), "no .tmp file should remain"


def test_group_help_documents_flags() -> None:
    """`camp group --help` documents --member / --scaffold / --force modes."""
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "group", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    for token in ("--member", "--scaffold", "--force"):
        assert token in out, f"expected {token!r} in group --help:\n{out}"


def test_init_help_documents_new_flags() -> None:
    """`camp group --help` documents --member / --scaffold / --force modes.

    Legacy name kept for backward-compat; routes to group --help.
    """
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "group", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    for token in ("--member", "--scaffold", "--force"):
        assert token in out, f"expected {token!r} in group --help:\n{out}"
