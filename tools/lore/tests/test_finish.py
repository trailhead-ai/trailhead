"""Slice 1 (S6) tests: `lore finish` is finalize + commit ONLY.

Re-homes the surviving finalize behaviors from the retired harvest tests
(`test_harvest_expand.py`) so the finalize + single-commit + explicit-paths
path stays covered after the harvest-expansion flow is removed:

- finalize (`status: complete` + `ended:`) of the GUID capture file lands
  exactly ONE commit.
- the commit stages explicit paths only — an unrelated dirty/untracked file
  in the vault is NOT swept in.
- a vault carrying a populated `harvest-pending.md` is left UNTOUCHED by
  finish: the file is being retired, not consumed (no expansion, no new
  kind-notes, harvest-pending neither rewritten nor committed).

ALL fixtures SYNTHETIC — zero private tokens (public repo).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


def run_cli(args, env=None, cwd=None, input_text=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, cwd=cwd, input=input_text,
    )


def load_script(name: str):
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    for cached in (name, "vault", "frontmatter", "status_validator", "sessions"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "sessions").mkdir(parents=True)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@e.st"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Tester"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "commit.gpgsign", "false"],
                   check=True, capture_output=True)
    return vault


def _commit_baseline(vault: Path):
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-m", "baseline"],
                   check=True, capture_output=True)


_GUID = "11111111-2222-4333-8444-555555555555"


def _candidate(vault: Path, guid: str = _GUID):
    """Write a body-only GUID capture file via the real capture path."""
    return run_cli(
        ["session", "candidate", "--session-id", guid,
         "--kind", "lesson", "--phase", "Build"],
        env={"LORE_VAULT": str(vault)},
        input_text="a lesson captured during the session\n",
    )


def _committed_files_at_head(vault: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(vault), "show", "--name-only", "--pretty=format:", "HEAD"],
        capture_output=True, text=True,
    ).stdout


def _commit_count(vault: Path) -> int:
    out = subprocess.run(
        ["git", "-C", str(vault), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    return int(out) if out else 0


# ---------------------------------------------------------------------------
# finalize + single commit of explicit paths only
# ---------------------------------------------------------------------------

class TestFinalizeSingleCommit:
    def test_finish_lands_exactly_one_commit(self, tmp_path):
        vault = _git_vault(tmp_path)
        assert _candidate(vault).returncode == 0
        _commit_baseline(vault)
        before = _commit_count(vault)

        r = run_cli(["finish", "--session-id", _GUID], env={"LORE_VAULT": str(vault)})
        assert r.returncode == 0, r.stderr

        after = _commit_count(vault)
        assert after == before + 1, f"expected exactly one new commit ({before} -> {after})"
        # The body-only .md was committed in the baseline and is now untouched by
        # finalize (A-sidecar), so the finalize commit carries the metadata
        # sidecar — the unit that actually changed.
        committed = _committed_files_at_head(vault)
        assert f"session/{_GUID}.json" in committed

    def test_finish_stamps_status_complete_and_ended(self, tmp_path):
        vault = _git_vault(tmp_path)
        assert _candidate(vault).returncode == 0
        capture = vault / "session" / f"{_GUID}.md"
        before = capture.read_text()
        _commit_baseline(vault)

        r = run_cli(["finish", "--session-id", _GUID], env={"LORE_VAULT": str(vault)})
        assert r.returncode == 0, r.stderr

        # Session metadata lands in the .json sidecar, NOT .md frontmatter.
        sidecar = vault / "session" / f"{_GUID}.json"
        assert sidecar.exists()
        obj = json.loads(sidecar.read_text())
        assert obj["type"] == "session"
        assert obj["status"] == "complete"
        assert obj["ended"]
        assert capture.read_text() == before  # body-only .md untouched


# ---------------------------------------------------------------------------
# explicit paths only — unrelated dirty file is NOT swept into the commit
# ---------------------------------------------------------------------------

class TestExplicitPathsOnly:
    def test_unrelated_dirty_file_not_swept_into_commit(self, tmp_path):
        vault = _git_vault(tmp_path)
        # a tracked decisions/ dir so the stray file shows individually in status
        decisions = vault / "decisions"
        decisions.mkdir(parents=True, exist_ok=True)
        (decisions / ".keep").write_text("")
        assert _candidate(vault).returncode == 0
        _commit_baseline(vault)
        # an unrelated dirty file present at finish time
        stray_file = decisions / "unrelated-scratch.md"
        stray_file.write_text("scratch work, not part of the finish\n")

        r = run_cli(["finish", "--session-id", _GUID], env={"LORE_VAULT": str(vault)})
        assert r.returncode == 0, r.stderr

        assert "unrelated-scratch.md" not in _committed_files_at_head(vault)
        status = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout
        assert "unrelated-scratch.md" in status, "stray file must remain untracked, not consumed"


# ---------------------------------------------------------------------------
# harvest-pending.md is RETIRED — finish leaves it untouched, expands nothing
# ---------------------------------------------------------------------------

_PENDING_BODY = (
    "# Harvest pending\n"
    "\n"
    "Staging area.\n"
    "\n"
    "## 2026-06-04T10:00:00Z — some-agent — widget-worktree\n"
    "\n"
    "- deferred: rewrite the gizmo loader. Trigger to revisit: when the gizmo "
    "count exceeds 100.  <!-- h:aaaaaaaaaaaa -->\n"
    "- decision: chose the ring buffer over a linked list. Reversibility: hard.  "
    "<!-- h:bbbbbbbbbbbb -->\n"
)


class TestHarvestPendingRetired:
    def test_populated_pending_left_untouched_and_no_notes_created(self, tmp_path):
        vault = _git_vault(tmp_path)
        for d in ("deferred", "decisions", "dead-ends", "follow-ups", "lessons"):
            (vault / d).mkdir(parents=True, exist_ok=True)
        pending = vault / "harvest-pending.md"
        pending.write_text(_PENDING_BODY)
        assert _candidate(vault).returncode == 0
        _commit_baseline(vault)

        r = run_cli(["finish", "--session-id", _GUID], env={"LORE_VAULT": str(vault)})
        assert r.returncode == 0, r.stderr

        # the pending file is byte-for-byte unchanged — not consumed
        assert pending.read_text() == _PENDING_BODY
        # no kind-notes were synthesized from the pending entries
        for sub in ("deferred", "decisions", "dead-ends", "follow-ups", "lessons"):
            notes = sorted((vault / sub).glob("**/*.md"))
            assert notes == [], f"finish must not create notes in {sub}/: {notes}"
        # harvest-pending is not part of the finalize commit
        assert "harvest-pending.md" not in _committed_files_at_head(vault)

    def test_no_harvest_or_gotcha_language_in_finish_output(self, tmp_path):
        vault = _git_vault(tmp_path)
        pending = vault / "harvest-pending.md"
        pending.write_text(
            _PENDING_BODY
            + "- gotcha: the flux capacitor resets on reconnect. Where it bit: "
            "flux.py:88.  <!-- h:ffffffffffff -->\n"
        )
        assert _candidate(vault).returncode == 0
        _commit_baseline(vault)

        r = run_cli(["finish", "--session-id", _GUID], env={"LORE_VAULT": str(vault)})
        assert r.returncode == 0, r.stderr
        combined = (r.stdout + r.stderr).lower()
        assert "harvest" not in combined, f"finish must not mention harvest: {combined!r}"
        assert "gotcha" not in combined, f"finish must not surface gotchas: {combined!r}"


# ---------------------------------------------------------------------------
# the harvest module + starter protocol are gone — retirement is structural
# ---------------------------------------------------------------------------

class TestHarvestModuleRetired:
    def test_harvest_script_no_longer_exists(self):
        assert not (SCRIPTS_DIR / "harvest.py").exists()

    def test_harvest_protocol_starter_no_longer_exists(self):
        assert not (PLUGIN_ROOT / "starter" / "harvest-protocol.md").exists()

    def test_importing_harvest_module_fails(self):
        spec = importlib.util.find_spec  # local alias to avoid import-time side effects
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        sys.modules.pop("harvest", None)
        try:
            mod = spec("harvest")
        except ModuleNotFoundError:
            mod = None
        assert mod is None, "the harvest module must no longer be importable"
