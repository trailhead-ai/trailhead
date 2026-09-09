"""
Package-shape tests for git-only distribution.

trailhead is distributed via git clone (+ homebrew later), NOT PyPI — the
name is taken by an unrelated active package (0.1.2). The shared lib
(trailhead.paths) is reached by putting the repo root on sys.path, NOT via
pip install.

Invariants:
- `import trailhead` and `import trailhead.paths` work when the repo root is
  on sys.path (the git-clone scenario).
- `bin/trailhead --version` works from a fresh clone with no pip install.
- `tools/camp/plugins/camp/bin/camp --version` works the same way.
- tools/craft is a data dir — NOT an importable top-level package (Option B
  invariant). tools/lore and tools/camp are exempt from this invariant: each
  ships a real `lore`/`camp` package (plugins/<tool>/<tool>/) so their own
  test suites can use real relative imports; see each tool's refactor plan.
- bare editable install does NOT pull in lore/craft/camp dists.
"""

import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

# Repo root is the directory that contains trailhead/paths.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_TRAILHEAD = _REPO_ROOT / "bin" / "trailhead"
_BIN_CAMP = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "bin" / "camp"


def _clean_env() -> dict[str, str]:
    """Return an env without PYTHONPATH so we're testing real sys.path resolution."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def test_import_trailhead_from_repo_root():
    """import trailhead works when the repo root is on sys.path (git-clone scenario).

    git-only distribution; no PyPI (name taken); shared lib via sys.path.
    We run in a subprocess with PYTHONPATH set to the repo root so this test
    is independent of whatever the current process has installed.
    """
    env = _clean_env()
    env["PYTHONPATH"] = str(_REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", "import trailhead; assert trailhead.__version__ == '0.1.0'"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"import trailhead failed with PYTHONPATH={_REPO_ROOT}:\n{result.stderr}"
    )


def test_import_trailhead_paths_from_repo_root():
    """import trailhead.paths works when the repo root is on sys.path.

    This is the critical import that camp's _bootstrap helper must make work.
    git-only distribution; no PyPI (name taken); shared lib via sys.path.
    """
    env = _clean_env()
    env["PYTHONPATH"] = str(_REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", "import trailhead.paths; print('ok')"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"import trailhead.paths failed with PYTHONPATH={_REPO_ROOT}:\n{result.stderr}"
    )
    assert "ok" in result.stdout


def test_bin_trailhead_version_no_pip():
    """bin/trailhead --version works from the repo root without any pip install.

    git-only distribution; no PyPI (name taken). bin/trailhead is the
    canonical CLI entry point replacing the pip console-script.
    """
    assert _BIN_TRAILHEAD.exists(), f"bin/trailhead not found at {_BIN_TRAILHEAD}"
    env = _clean_env()
    result = subprocess.run(
        [str(_BIN_TRAILHEAD), "--version"],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bin/trailhead --version failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "0.1.0" in result.stdout, f"expected '0.1.0' in output, got: {result.stdout!r}"


def test_bin_camp_version_no_pip():
    """bin/camp --version works from the repo root without any pip install.

    The camp wrapper invokes cli/camp which calls _bootstrap.ensure_trailhead_importable()
    before touching trailhead.paths, so it must work on a fresh git clone.
    """
    assert _BIN_CAMP.exists(), f"bin/camp not found at {_BIN_CAMP}"
    env = _clean_env()
    result = subprocess.run(
        [str(_BIN_CAMP), "--version"],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bin/camp --version failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "0.1.0" in result.stdout, f"expected '0.1.0' in output, got: {result.stdout!r}"


def test_the_distribution_ships_only_the_trailhead_package():
    """`tools/` is data, not distribution content (the Option B invariant).

    Read off the built distribution's own metadata rather than probing for the
    absence of a `craft` import: `top_level.txt` and RECORD are what setuptools
    actually produced from `[tool.setuptools] packages`, so this states what the
    build ships instead of naming one thing it happens not to. A `tools/` tree
    swept in by auto-discovery shows up here as an extra top-level entry, whatever
    it is called.
    """
    dist = importlib.metadata.distribution("trailhead")

    top_level = (dist.read_text("top_level.txt") or "").split()
    assert top_level == ["trailhead"], (
        f"the distribution declares top-level packages {top_level}; only 'trailhead' "
        "should ship. Check `[tool.setuptools] packages` in pyproject.toml — tools/ "
        "must not be auto-discovered."
    )

    shipped_roots = {str(f).split("/")[0] for f in (dist.files or [])}
    tool_dirs = sorted(r for r in shipped_roots if r in {"tools", "craft", "lore", "camp"})
    assert not tool_dirs, (
        f"the distribution ships {tool_dirs}, which are plugin data dirs reached via "
        "CLAUDE_PLUGIN_ROOT, not importable packages"
    )
