"""
Package-shape tests for Slice 1 (Option B invariant).

After `pip install -e .` in a clean venv:
- `import trailhead` works and the console script is on PATH
- `tools/` data dirs are NOT importable top-level packages
- bare install places only the `trailhead` distribution (no lore/forge/camp dists)
"""

import importlib.util
import subprocess
import sys
from pathlib import Path


def _venv_script(name: str) -> str:
    """Resolve a console-script path relative to the active venv's bin/."""
    return str(Path(sys.executable).parent / name)


def test_import_trailhead():
    """trailhead is importable after pip install -e ."""
    import trailhead  # noqa: F401 (testing importability is the point)

    assert trailhead.__version__ == "0.1.0"


def test_console_script_version():
    """trailhead console script is installed in the venv and responds to --version."""
    script = _venv_script("trailhead")
    result = subprocess.run(
        [script, "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "0.1.0" in result.stdout


def test_lore_not_importable():
    """tools/lore is a data dir — NOT an importable top-level package (Option B invariant)."""
    spec = importlib.util.find_spec("lore")
    assert spec is None, (
        f"'lore' should not be importable as a top-level package; got spec={spec}. "
        "tools/ must not be auto-discovered by setuptools."
    )


def test_forge_not_importable():
    """tools/forge is a data dir — NOT an importable top-level package (Option B invariant)."""
    spec = importlib.util.find_spec("forge")
    assert spec is None, (
        f"'forge' should not be importable as a top-level package; got spec={spec}. "
        "tools/ must not be auto-discovered by setuptools."
    )


def test_only_trailhead_distribution_installed():
    """bare pip install -e . installs only the trailhead dist; no lore/forge/camp dists."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "show", "trailhead"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "trailhead dist must be installed"

    for unwanted in ("lore", "forge", "camp"):
        check = subprocess.run(
            [sys.executable, "-m", "pip", "show", unwanted],
            capture_output=True,
            text=True,
        )
        assert check.returncode != 0, (
            f"'{unwanted}' distribution must NOT be installed by bare `pip install trailhead`"
        )
