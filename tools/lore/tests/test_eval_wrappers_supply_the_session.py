"""An eval case's lore wrappers log to the case's own session.

A real session's lore calls land in that session because Claude Code sets
``CLAUDE_CODE_SESSION_ID`` for it. In an eval run that variable names the runner's session, which no
grader can know, while the graders read the case's fixed session. So each case's scaffold writes
wrappers that supply the case's session themselves, and the model runs the procedure's commands
exactly as the procedure gives them, with no session flag of its own.

Each test runs a case's real scaffold (one that writes no wrapper is skipped) in a fenced HOME, logs a
candidate through each wrapper it wrote with the runner's session in the environment and no flag, and
reads where it landed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.seam_real

REPO_ROOT = Path(__file__).resolve().parents[3]
CASE_SESSION = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
RUNNER_SESSION = "12345678-1234-4123-8123-123456789abc"


def _scaffolds() -> list[Path]:
    return sorted(REPO_ROOT.glob("tools/*/plugins/*/evals/*/fixtures/scaffold.sh"))


SCAFFOLDS = _scaffolds()


def _env(home: Path, **extra: str) -> dict[str, str]:
    stubs = home.parent / "stubs"
    stubs.mkdir(exist_ok=True)
    python3 = stubs / "python3"
    if not python3.exists():
        python3.symlink_to(os.path.realpath(sys.executable))
    env = {
        "HOME": str(home),
        "PATH": f"{stubs}:/usr/bin:/bin",
        "LORE_PUBLISH_DISABLE": "1",
    }
    env.update(extra)
    return env


@pytest.mark.parametrize(
    "scaffold", SCAFFOLDS, ids=[s.parents[1].name for s in SCAFFOLDS]
)
def test_every_wrapper_the_scaffold_writes_logs_to_the_case_session(tmp_path, scaffold):
    home = tmp_path / "home"
    cwd = home / "cwd"
    cwd.mkdir(parents=True)
    built = subprocess.run(
        ["bash", str(scaffold)], cwd=cwd, env=_env(home), capture_output=True, text=True
    )
    assert built.returncode == 0, built.stderr
    wrappers = [w for w in (cwd / "lore", cwd / "bin" / "lore") if w.is_file()]
    if not wrappers:
        pytest.skip("this case's scaffold writes no lore wrapper")

    for n, wrapper in enumerate(wrappers):
        logged = subprocess.run(
            [str(wrapper), "session", "candidate", "--kind", "lesson", "--phase", "Build"],
            cwd=cwd,
            env=_env(home, CLAUDE_CODE_SESSION_ID=RUNNER_SESSION),
            input=f"wrapper probe {n}\n",
            capture_output=True,
            text=True,
        )
        assert logged.returncode == 0, (wrapper, logged.stderr)

    case_note = cwd / "vault" / "session" / f"{CASE_SESSION}.md"
    text = case_note.read_text(encoding="utf-8") if case_note.exists() else ""
    assert [f"wrapper probe {n}" in text for n in range(len(wrappers))] == [True] * len(wrappers)
