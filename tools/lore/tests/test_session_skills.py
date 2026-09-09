"""The `flush` skill's documented CLI path, exercised against the real CLI.

The skill carries the judgment (which candidates become records); `lore flush`
carries the mechanical flip from dirty to clean. This runs that command in a
fenced, git-initialized vault and asserts the session sidecar it left behind.

What the skill *says* is checked where it can be executed:
``test_documented_commands`` resolves every ``lore …`` invocation and every
``/tool:skill`` pointer in the plugin tree against the real parser and manifest,
and ``test_registrable`` proves the frontmatter registers.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import make_vault as _make_vault, run_cli as _run

SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _git_init(vault: Path) -> None:
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    for k, v in (("user.email", "t@e.st"), ("user.name", "Tester"),
                 ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(vault), "config", k, v],
                       check=True, capture_output=True)


def _commit_baseline(vault: Path) -> None:
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-m", "baseline"],
                   check=True, capture_output=True)


def _candidate(vault, state, sid=SID, body="a candidate\n"):
    return _run(
        ["session", "candidate", "--session-id", sid, "--kind", "spec", "--phase", "Plan"],
        vault=vault, state_dir=state, stdin_text=body,
        env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""},
    )


def _flush(vault, state, sid=SID):
    return _run(["flush", "--session-id", sid], vault=vault, state_dir=state,
                env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""})


class TestFlushSkillCliPath:
    """The flush skill documents `lore flush`; this exercises the real CLI path."""

    def test_flush_cli_flips_dirty_session_to_clean(self, tmp_path):
        """The real `lore flush` CLI runs against an isolated vault and flips dirty→clean.

        This exercises the actual CLI path the skill drives — not a string grep.
        """
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        r = _candidate(vault, state)
        assert r.returncode == 0, r.stderr
        _commit_baseline(vault)

        r = _flush(vault, state)
        assert r.returncode == 0, r.stderr

        import json
        sidecar = json.loads((vault / "session" / f"{SID}.json").read_text())
        assert sidecar["status"] == "clean", (
            f"flush must flip the session to clean; got {sidecar['status']!r}"
        )

    def test_flush_cli_no_op_on_clean_session(self, tmp_path):
        """The real `lore flush` CLI exits 0 and is a no-op on an already-clean session."""
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state).returncode == 0
        _commit_baseline(vault)
        # First flush: dirty → clean.
        assert _flush(vault, state).returncode == 0
        # Second flush: already clean → no-op.
        r = _flush(vault, state)
        assert r.returncode == 0, r.stderr
        combined = (r.stdout + r.stderr).lower()
        assert "clean" in combined and "nothing to flush" in combined
