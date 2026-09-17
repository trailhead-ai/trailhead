"""scripts/bootstrap-venv — interpreter selection and venv convergence.

Test contract:
- Given several satisfying interpreters on PATH, the newest-named one creates the
  venv (the candidate list is ordered, and the first satisfying name wins).
- Given interpreters below the floor, they are passed over for a satisfying one.
- Given nothing at or above the floor, the script exits non-zero naming the floor
  rather than leaving a repo with no usable interpreter and no venv.
- Given an existing venv that already imports xdist, nothing is installed — the
  script is cheap to re-run, which is what lets .envrc call it on every entry.
- Given an existing venv that cannot import xdist, pytest-xdist is installed into
  it.

Fixtures put stub interpreters on a synthetic PATH: plain shell scripts that
answer ``--version``, record every invocation to a log, and, for ``-m venv``,
write a stub ``bin/python`` into the target so the convergence branch has
something to probe. Real Python and real pip are never involved.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]  # trailhead root
_REAL_SCRIPT = _REPO_ROOT / "scripts" / "bootstrap-venv"
_BASH = shutil.which("bash")
assert _BASH is not None, "bash must be on PATH to run these tests"

_COREUTILS = ["dirname", "readlink", "grep", "head", "mkdir", "touch", "cat", "chmod"]


def _coreutils_dir(root: Path) -> Path:
    utils = root / "_coreutils"
    if utils.exists():
        return utils
    utils.mkdir(parents=True)
    for name in _COREUTILS:
        real = shutil.which(name)
        if real:
            (utils / name).symlink_to(real)
    return utils


def _executable(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _stub_interpreter(directory: Path, name: str, *, version: str, log: Path) -> Path:
    """A fake interpreter: answers --version, logs calls, fakes `-m venv`."""
    directory.mkdir(parents=True, exist_ok=True)
    return _executable(
        directory / name,
        f'''#!/bin/sh
if [ "$1" = "--version" ]; then
    echo "Python {version}"
    exit 0
fi
echo "{name} $@" >> "{log}"
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
    mkdir -p "$3/bin"
    cat > "$3/bin/python" <<'INNER'
#!/bin/sh
echo "venv-python $@" >> "{log}"
if [ "$1" = "-c" ]; then
    [ -f "$(dirname "$0")/../HAS_XDIST" ] && exit 0
    exit 1
fi
exit 0
INNER
    chmod 755 "$3/bin/python"
fi
exit 0
''',
    )


def _repo(tmp_path: Path) -> Path:
    """A throwaway repo carrying the real script at scripts/bootstrap-venv."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    script = repo / "scripts" / "bootstrap-venv"
    script.write_text(_REAL_SCRIPT.read_text())
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return repo


def _existing_venv(repo: Path, *, log: Path, has_xdist: bool) -> None:
    venv = repo / ".venv"
    (venv / "bin").mkdir(parents=True)
    if has_xdist:
        (venv / "HAS_XDIST").touch()
    _executable(
        venv / "bin" / "python",
        f'''#!/bin/sh
echo "venv-python $@" >> "{log}"
if [ "$1" = "-c" ]; then
    [ -f "$(dirname "$0")/../HAS_XDIST" ] && exit 0
    exit 1
fi
exit 0
''',
    )


def _run(repo: Path, path_dirs: list[Path], *, coreutils_root: Path):
    path_value = ":".join(str(d) for d in [*path_dirs, _coreutils_dir(coreutils_root)])
    return subprocess.run(
        [_BASH, str(repo / "scripts" / "bootstrap-venv")],
        capture_output=True,
        text=True,
        env={"PATH": path_value},
    )


@pytest.fixture
def log(tmp_path: Path) -> Path:
    return tmp_path / "calls.log"


def test_the_first_satisfying_candidate_creates_the_venv(tmp_path, log):
    repo = _repo(tmp_path)
    interpreters = tmp_path / "bin"
    _stub_interpreter(interpreters, "python3.11", version="3.11.9", log=log)
    _stub_interpreter(interpreters, "python3.13", version="3.13.0", log=log)

    result = _run(repo, [interpreters], coreutils_root=tmp_path)

    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "python3.13 -m venv" in calls, calls
    assert "python3.11 -m venv" not in calls, calls


def test_an_interpreter_below_the_floor_is_passed_over(tmp_path, log):
    repo = _repo(tmp_path)
    interpreters = tmp_path / "bin"
    _stub_interpreter(interpreters, "python3", version="3.9.6", log=log)
    _stub_interpreter(interpreters, "python3.12", version="3.12.4", log=log)

    result = _run(repo, [interpreters], coreutils_root=tmp_path)

    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "python3.12 -m venv" in calls, calls
    assert "python3 -m venv" not in calls, calls


def test_no_satisfying_interpreter_exits_nonzero_naming_the_floor(tmp_path, log):
    repo = _repo(tmp_path)
    interpreters = tmp_path / "bin"
    _stub_interpreter(interpreters, "python3", version="3.9.6", log=log)

    result = _run(repo, [interpreters], coreutils_root=tmp_path)

    assert result.returncode != 0
    assert "3.11" in result.stderr, result.stderr
    assert not (repo / ".venv").exists()


def test_an_existing_venv_with_xdist_installs_nothing(tmp_path, log):
    repo = _repo(tmp_path)
    _existing_venv(repo, log=log, has_xdist=True)
    interpreters = tmp_path / "bin"
    _stub_interpreter(interpreters, "python3.13", version="3.13.0", log=log)

    result = _run(repo, [interpreters], coreutils_root=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "pip install" not in log.read_text(), log.read_text()


def test_an_existing_venv_without_xdist_gets_it_installed(tmp_path, log):
    repo = _repo(tmp_path)
    _existing_venv(repo, log=log, has_xdist=False)
    interpreters = tmp_path / "bin"
    _stub_interpreter(interpreters, "python3.13", version="3.13.0", log=log)

    result = _run(repo, [interpreters], coreutils_root=tmp_path)

    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "pip install" in calls and "pytest-xdist" in calls, calls
