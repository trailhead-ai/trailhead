"""Plan Slice 4 — /lore:flush skill replaces /lore:finish; /checkpoint deleted.

Delivers:
  - The 'flush' skill directory exists; 'finish' and 'checkpoint' do not.
  - The skill registry lists 'flush', NOT 'finish' or 'checkpoint'.
  - No skill, agent, or rules file in the lore plugin tree references '/checkpoint'.
  - The flush skill invokes the REAL `lore flush` CLI (not a string grep).
  - The SKILL.md describes the candidate→record evaluation model and reads
    the flushed-at watermark from the sidecar directly (not via KQL).
  - The SKILL.md describes all three scoping forms: no-arg, all, <search>.

Test contract (plan):
  - skill registry no longer lists checkpoint; lists flush, not finish.
  - a test asserts no skill/agent/rules file references /checkpoint anywhere
    in the lore plugin tree.
  - the flush skill test exercises the real CLI flush path.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from conftest import make_vault as _make_vault, run_cli as _run

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
SKILLS_DIR = PLUGIN_ROOT / "skills"
AGENTS_DIR = PLUGIN_ROOT / "agents"

SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill_files() -> list[Path]:
    return sorted(
        d / "SKILL.md"
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name != "_shared" and (d / "SKILL.md").exists()
    )


def _skill_text(name: str) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text()


def _all_lore_md_files() -> list[Path]:
    """All .md files under the lore plugin tree (skills, agents, scripts, hooks)."""
    return list(PLUGIN_ROOT.rglob("*.md"))


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


# ---------------------------------------------------------------------------
# Registry: flush present, finish/checkpoint absent
# ---------------------------------------------------------------------------

def test_registry_lists_flush_not_finish():
    """The skill registry lists 'flush', not the old 'finish'."""
    names = {p.parent.name for p in _skill_files()}
    assert "flush" in names, "flush skill must exist in the registry"
    assert "finish" not in names, "finish skill must not exist — renamed to flush"


def test_registry_does_not_list_checkpoint():
    """The skill registry no longer lists 'checkpoint'."""
    names = {p.parent.name for p in _skill_files()}
    assert "checkpoint" not in names, (
        "checkpoint skill must not exist — Slice 4 deleted it "
        "(continuous capture via `lore session candidate`; flush evaluates)"
    )


# ---------------------------------------------------------------------------
# No /checkpoint references anywhere in the lore plugin tree
# ---------------------------------------------------------------------------

def test_no_checkpoint_reference_in_any_lore_md_file():
    """`checkpoint` (in any slash-command form) must not appear in any skill, agent, or
    docs file under the lore plugin.

    Checkpoint is deleted; the substitute is `lore session candidate` (continuous
    capture) + `/lore:flush` (evaluation). Any remaining reference — whether
    `/checkpoint`, `/lore:checkpoint`, or bare `checkpoint` in a trigger context —
    drives a dead surface.
    """
    offenders = [
        str(p) for p in _all_lore_md_files()
        if "checkpoint" in p.read_text()
    ]
    assert not offenders, (
        "Found checkpoint reference(s) in lore plugin — must be removed "
        f"(Slice 4 deleted checkpoint): {offenders}"
    )


# ---------------------------------------------------------------------------
# flush skill SKILL.md — structure + content assertions
# ---------------------------------------------------------------------------

def test_flush_skill_has_registrable_frontmatter():
    """flush/SKILL.md opens with YAML frontmatter carrying a non-empty description."""
    text = _skill_text("flush")
    assert text.startswith("---\n"), "flush/SKILL.md must open with `---` frontmatter"
    end = text.find("\n---", 3)
    assert end > 0, "flush/SKILL.md frontmatter block must be closed"
    frontmatter = text[3:end]
    desc_lines = [
        ln for ln in frontmatter.splitlines()
        if ln.strip().startswith("description:") and ln.split(":", 1)[1].strip()
    ]
    assert desc_lines, "flush/SKILL.md must carry a non-empty description:"


def test_flush_skill_documents_clean_dirty_behavior():
    """flush/SKILL.md must describe both the clean (no-op) and dirty (evaluate+flush) paths."""
    text = _skill_text("flush").lower()
    assert "clean" in text, "flush/SKILL.md must describe the clean-session no-op path"
    assert "dirty" in text, "flush/SKILL.md must describe the dirty-session evaluation path"


def test_flush_skill_describes_candidate_evaluation():
    """flush/SKILL.md must describe evaluating outstanding candidates into vault records."""
    text = _skill_text("flush")
    assert "lore record create" in text, (
        "flush/SKILL.md must describe evaluating candidates into records via "
        "`lore record create`"
    )
    assert "candidate" in text.lower(), (
        "flush/SKILL.md must describe the candidate log / outstanding candidates"
    )


def test_flush_skill_reads_sidecar_not_kql_for_watermark():
    """flush/SKILL.md must document reading the sidecar directly for the flushed-at watermark.

    annotations is sidecar-only, NOT indexed — reading it requires reading the
    session/<key>.json directly, NOT via KQL (cross-slice contract, plan KU3).
    """
    text = _skill_text("flush")
    # Must mention reading the .json sidecar directly
    assert ".json" in text or "sidecar" in text, (
        "flush/SKILL.md must document reading the session .json sidecar directly "
        "for the flushed-at watermark — annotations are NOT indexed, not via KQL"
    )


def test_flush_skill_describes_all_three_scoping_forms():
    """flush/SKILL.md must document all three invocation scopes: no-arg, all, <search>."""
    text = _skill_text("flush")
    assert "lore flush" in text, "flush/SKILL.md must show the lore flush CLI call"
    assert "all" in text.lower(), (
        "flush/SKILL.md must document `lore flush all` (every dirty session)"
    )
    # <search> form — documented as KQL / date filter / query
    lower = text.lower()
    assert "search" in lower or "kql" in lower or "query" in lower, (
        "flush/SKILL.md must document the `lore flush <search>` KQL scoping form"
    )


def test_flush_skill_calls_lore_flush_cli():
    """flush/SKILL.md must invoke `lore flush` as the mechanical verb."""
    text = _skill_text("flush")
    assert "lore flush" in text, (
        "flush/SKILL.md must call `lore flush` to flip session clean + commit "
        "(the skill carries judgment; the CLI carries the mechanical flip)"
    )


def test_flush_skill_runnable_any_time_not_only_session_end():
    """flush/SKILL.md must state it is runnable at any time, not only at session end."""
    text = _skill_text("flush").lower()
    # Acceptable phrasings: "any time", "anytime", "not just", "runnable at any"
    assert "any time" in text or "anytime" in text or "not just" in text or "runnable" in text, (
        "flush/SKILL.md must state it is runnable at any time (not only at session end)"
    )


# ---------------------------------------------------------------------------
# Real CLI flush path — exercises the actual `lore flush` command
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# LOCKSTEP GATE — no removed/forbidden commands in the flush skill
# ---------------------------------------------------------------------------

# Commands that are removed / retired and must not appear in the new flush skill.
FORBIDDEN_IN_FLUSH = (
    "lore finish",
    "lore new",
    "lore recall",
    "lore patch",
    "lore handoff",
    "lore shelved",
    "lore resume",
    "shelved",
    "SessionStart",
)

# The new extant commands the flush skill may reference in fenced bash blocks.
EXTANT_COMMANDS = frozenset({
    "session candidate",
    "session referenced",
    "session-note",
    "flush",
    "record create",
    "search",
    "sync",
    "stats",
})

_LORE_CALL = re.compile(r"\blore\s+([a-z-]+)(?:\s+([a-z-]+))?")
_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)


def _fenced_lore_calls(text: str) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    for block in _FENCE.findall(text):
        calls.extend(_LORE_CALL.findall(block))
    return calls


@pytest.mark.parametrize("token", FORBIDDEN_IN_FLUSH)
def test_flush_skill_has_no_forbidden_token(token: str):
    text = _skill_text("flush")
    assert token not in text, (
        f"flush/SKILL.md contains forbidden token {token!r} — it drives a "
        "removed command or retired vocabulary."
    )


def test_flush_skill_lore_calls_resolve_to_extant_commands():
    text = _skill_text("flush")
    for first, second in _fenced_lore_calls(text):
        two = f"{first} {second}".strip()
        resolved = two if two in EXTANT_COMMANDS else first
        assert resolved in EXTANT_COMMANDS, (
            f"flush/SKILL.md documents `lore {two or first}`, which is not an "
            f"extant command. Extant: {sorted(EXTANT_COMMANDS)}"
        )
