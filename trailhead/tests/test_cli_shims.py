"""The CLI shim set built by ``install.build_cli_shims``: re-rooting and the real ``trailhead`` shim."""

import shutil
import subprocess
import sys
from pathlib import Path

from trailhead import __version__, install
from trailhead.pathint import resolve_shim_dir


def _fenced_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        "TRAILHEAD_STATE_DIR": str(tmp_path / "state"),
        "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude-dir"),
    }


def _fake_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "trailhead").write_text("#!/bin/sh\n")
    (root / "bin" / "trailhead").chmod(0o755)
    return root


def test_a_second_build_from_a_different_root_repoints_the_trailhead_shim(tmp_path):
    env = _fenced_env(tmp_path)
    camp = tmp_path / "camp-bin"
    camp.write_text("#!/bin/sh\n")
    root_a = _fake_root(tmp_path, "root-a")
    root_b = _fake_root(tmp_path, "root-b")

    shim_dir = install.build_cli_shims({"camp": camp}, env=env, root=root_a)
    assert str(root_a / "bin" / "trailhead") in (shim_dir / "trailhead").read_text()

    rebuilt = install.build_cli_shims({"camp": camp}, env=env, root=root_b)

    assert rebuilt == shim_dir == resolve_shim_dir(env=env)
    content = (shim_dir / "trailhead").read_text()
    assert str(root_b / "bin" / "trailhead") in content
    assert str(root_a) not in content


def _minimal_path(tmp_path: Path, shim_dir: Path) -> str:
    """PATH holding only the shim dir, the few tools the shim and launcher need, and a floor-meeting python3."""
    tools = tmp_path / "_tools"
    tools.mkdir()
    for name in ("bash", "env", "dirname", "readlink", "grep", "head"):
        (tools / name).symlink_to(shutil.which(name))
    (tools / "python3").symlink_to(Path(sys.executable).resolve())
    return f"{shim_dir}:{tools}"


def test_the_built_trailhead_shim_runs_version_by_bare_name_from_an_unrelated_cwd(tmp_path):
    env = _fenced_env(tmp_path)
    camp = tmp_path / "camp-bin"
    camp.write_text("#!/bin/sh\n")
    shim_dir = install.build_cli_shims({"camp": camp}, env=env)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    r = subprocess.run(
        ["trailhead", "--version"],
        env={**env, "PATH": _minimal_path(tmp_path, shim_dir)},
        cwd=elsewhere,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"trailhead {__version__}"


def _shim_names(shim_dir: Path) -> set[str]:
    return {p.name for p in shim_dir.iterdir()}


def test_the_trailhead_shim_is_built_when_the_roots_bin_trailhead_is_executable(tmp_path):
    env = _fenced_env(tmp_path)
    camp = tmp_path / "camp-bin"
    camp.write_text("#!/bin/sh\n")
    root = _fake_root(tmp_path, "root")
    (root / "bin" / "trailhead").chmod(0o755)

    shim_dir = install.build_cli_shims({"camp": camp}, env=env, root=root)

    assert {"camp", "trailhead"} <= _shim_names(shim_dir)
    assert str(root / "bin" / "trailhead") in (shim_dir / "trailhead").read_text()


def test_no_trailhead_shim_is_built_when_the_roots_bin_trailhead_is_not_executable(tmp_path):
    env = _fenced_env(tmp_path)
    camp = tmp_path / "camp-bin"
    camp.write_text("#!/bin/sh\n")
    ranger = _fake_root(tmp_path, "root")
    (ranger / "bin" / "trailhead").chmod(0o644)
    (ranger / "bin" / "ranger").write_text("#!/bin/sh\n")

    shim_dir = install.build_cli_shims({"camp": camp}, env=env, root=ranger)

    assert "trailhead" not in _shim_names(shim_dir)
    assert {"camp", "ranger"} <= _shim_names(shim_dir)


def test_no_trailhead_shim_is_built_when_the_root_has_no_bin_trailhead(tmp_path):
    env = _fenced_env(tmp_path)
    camp = tmp_path / "camp-bin"
    camp.write_text("#!/bin/sh\n")
    root = tmp_path / "bare-root"
    root.mkdir()

    shim_dir = install.build_cli_shims({"camp": camp}, env=env, root=root)

    assert _shim_names(shim_dir) == {"camp"}
