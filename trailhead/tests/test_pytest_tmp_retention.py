"""The repository's pytest configs keep a test's ``tmp_path`` only when it failed.

A full run of this suite creates roughly a quarter of a million files under the
temp root, and pytest's default keeps the last three sessions' worth whatever
their outcome. On a host where several workspaces share one size-capped ``/tmp``,
those retained passing-test directories exhaust its inodes and make unrelated
runs fail with ``could not create numbered dir``. Keeping only failing tests'
directories preserves what a developer needs to debug and releases the rest.

Every pytest config in the repository is exercised: the root config governs the
whole-suite run, and camp, craft and lore each carry their own config that
governs a run scoped to that tool's tests.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

PYTEST_CONFIGS = [
    REPO_ROOT / "pyproject.toml",
    REPO_ROOT / "tools" / "camp" / "pyproject.toml",
    REPO_ROOT / "tools" / "craft" / "pyproject.toml",
    REPO_ROOT / "tools" / "lore" / "pyproject.toml",
]

THROWAWAY_TESTS = '''
def test_passes(tmp_path):
    (tmp_path / "artifact.txt").write_text("kept only on failure")


def test_fails(tmp_path):
    (tmp_path / "artifact.txt").write_text("kept only on failure")
    assert False, "deliberate failure"
'''


def _retained_test_dirs(temp_root: Path) -> set[str]:
    """Names of per-test ``tmp_path`` directories left under the temp root."""
    retained = set()
    for session_dir in temp_root.glob("pytest-of-*/pytest-[0-9]*"):
        for path in session_dir.rglob("test_*"):
            if path.is_dir() and not path.is_symlink():
                retained.add(path.name)
    return retained


@pytest.mark.parametrize(
    "config", PYTEST_CONFIGS, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_config_retains_tmp_path_of_failing_tests_only(tmp_path, config):
    project = tmp_path / "project"
    project.mkdir()
    (project / "test_throwaway.py").write_text(THROWAWAY_TESTS)
    temp_root = tmp_path / "temp-root"
    temp_root.mkdir()
    env = {**os.environ, "TMPDIR": str(temp_root)}
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_XDIST_AUTO_NUM_WORKERS", None)

    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "-c", str(config),
            "--rootdir", str(project),
            "-p", "no:cacheprovider",
            "-n", "0",
            str(project / "test_throwaway.py"),
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed, 1 passed" in result.stdout, result.stdout
    assert _retained_test_dirs(temp_root) == {"test_fails0"}
