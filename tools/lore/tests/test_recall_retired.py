"""Slice 5 (S3) — `recall` command retired, call sites rewired to `lore search`.

The destructive cutover: the `recall` COMMAND surface is gone (`cmd_recall`, the
`recall` subparser, and the recall-command machinery in `recall.py`). The
unknown-command hint machinery redirects an agent that types the old command to
`lore search`. Every executable `lore recall` reference in `hooks/` + `cli/` is
removed, and the SessionStart area-pointer path now points at `lore search`.

The area-map path (`cmd_areas`, `build_area_map`, `render_area_pointer`,
`render_area_menu`, `AreaEntry`) is KEPT — it serves `lore areas` and the
SessionStart pointer, not the recall command — and is covered by
`test_recall_core.py` / `test_subsystem_recall.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

CONFTEST_DIR = Path(__file__).parent
sys.path.insert(0, str(CONFTEST_DIR))
from conftest import CLI_PATH, load_script, make_vault, run_cli  # noqa: E402

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
CLI_DIR = PLUGIN_ROOT / "cli"


# ---------------------------------------------------------------------------
# `lore recall …` is gone → non-zero exit + "did you mean 'lore search'?" hint
# ---------------------------------------------------------------------------

class TestRecallCommandRetired:
    def test_bare_recall_is_unknown_command_nonzero(self, tmp_path):
        vault, state = make_vault(tmp_path)
        r = run_cli(["recall"], vault=vault, state_dir=state)
        assert r.returncode != 0, (
            "`lore recall` must exit non-zero — the command is gone, not a no-op.\n"
            f"stdout={r.stdout!r} stderr={r.stderr!r}"
        )

    def test_recall_emits_did_you_mean_search_hint(self, tmp_path):
        vault, state = make_vault(tmp_path)
        r = run_cli(["recall"], vault=vault, state_dir=state)
        assert "did you mean 'lore search'?" in r.stderr, (
            "`lore recall` must redirect to `lore search` via the dispatch hint.\n"
            f"stderr={r.stderr!r}"
        )

    def test_recall_with_areas_flag_still_redirects(self, tmp_path):
        """The flagged form (`lore recall --areas …`) the old call sites used must
        also resolve to the hint, not silently parse."""
        vault, state = make_vault(tmp_path)
        r = run_cli(["recall", "--areas", "penny"], vault=vault, state_dir=state)
        assert r.returncode != 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
        assert "did you mean 'lore search'?" in r.stderr, r.stderr


# ---------------------------------------------------------------------------
# Grep-guard: NO remaining executable `lore recall` reference in hooks/ + cli/
# ---------------------------------------------------------------------------

class TestNoRecallReferencesInExecutables:
    # Flagged forms the old call sites used — the hard guard must catch all of them.
    _FORBIDDEN = [
        "lore recall",
        "lore recall --areas",
        "lore recall --json",
        "lore recall --limit",
    ]

    def _executable_files(self):
        files = []
        for d in (HOOKS_DIR, CLI_DIR):
            if d.is_dir():
                files.extend(p for p in d.rglob("*") if p.is_file())
        return files

    def test_no_lore_recall_in_hooks_or_cli(self):
        offenders = []
        for f in self._executable_files():
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for needle in self._FORBIDDEN:
                if needle in text:
                    offenders.append(f"{f}: contains {needle!r}")
        assert not offenders, (
            "Executable `lore recall` references survive in hooks/ or cli/ — the "
            "recall command is gone; rewire to `lore search`:\n" + "\n".join(offenders)
        )


# ---------------------------------------------------------------------------
# Call-site smoke test: SessionStart hook runs and points at `lore search`
# ---------------------------------------------------------------------------

class TestSessionStartCallSite:
    def test_area_pointer_references_search_not_recall(self, tmp_path):
        """The kept area-pointer (used by the SessionStart hook) must point at
        `lore search`, not the removed `lore recall`."""
        recall = load_script("recall")
        vault = tmp_path / "vault"
        (vault / "areas").mkdir(parents=True)
        (vault / "areas" / "penny.md").write_text(
            "---\nname: penny\nsummary: the penny worker\n---\n## Overview\nPenny.\n"
        )
        pointer = recall.render_area_pointer(vault)
        assert pointer, "area pointer must be non-empty when areas exist"
        assert "lore search" in pointer, (
            f"area pointer must reference `lore search`; got: {pointer!r}"
        )
        assert "lore recall" not in pointer, (
            f"area pointer must NOT reference the removed `lore recall`; got: {pointer!r}"
        )

    def test_session_start_hook_runs_and_emits_search_pointer(self, tmp_path):
        """The whole SessionStart hook path runs without error and surfaces the
        rewired `lore search` pointer — a regressed area-pointer would surface a
        visible error, not a silent empty pointer."""
        import json
        import os
        import subprocess

        vault = tmp_path / "vault"
        (vault / "areas").mkdir(parents=True)
        (vault / "sessions").mkdir(parents=True)
        (vault / "areas" / "penny.md").write_text(
            "---\nname: penny\nsummary: the penny worker pipeline\n---\n"
            "## Overview\nPenny worker.\n"
        )
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        hook = HOOKS_DIR / "session-context.py"
        env = dict(os.environ)
        env["LORE_VAULT"] = str(vault)
        env["LORE_EMAIL"] = "tester@example.com"
        env["CLAUDE_PROJECT_DIR"] = str(worktree)
        r = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({"session_id": "smoke-test"}),
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, f"hook crashed: stderr={r.stderr!r}"
        payload = json.loads(r.stdout)
        context = payload.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "lore search" in context, (
            "SessionStart context must point at `lore search` (rewired pointer).\n"
            f"stderr={r.stderr!r} context={context!r}"
        )
        assert "lore recall" not in context, (
            f"SessionStart context still references the removed `lore recall`: {context!r}"
        )


# ---------------------------------------------------------------------------
# The recall COMMAND path is gutted from recall.py (kept: area-map functions)
# ---------------------------------------------------------------------------

class TestRecallModuleGutted:
    def test_command_path_symbols_removed(self):
        recall = load_script("recall")
        for name in (
            "recall_areas",
            "_recall_areas_layered",
            "render_recall_banner",
            "RecallItem",
            "RecallResult",
            "_pull_folder",
            "_pull_deferred",
            "_pull_dead_ends",
            "_pull_lessons",
            "_pull_decisions",
            "_pull_cross_cutting",
        ):
            assert not hasattr(recall, name), (
                f"recall.{name} is part of the removed recall-command path and must "
                "be gone (Slice 5)."
            )

    def test_area_map_path_kept(self):
        recall = load_script("recall")
        for name in (
            "build_area_map",
            "render_area_pointer",
            "render_area_menu",
            "AreaEntry",
        ):
            assert hasattr(recall, name), (
                f"recall.{name} serves `lore areas` + the SessionStart pointer and "
                "must be KEPT (Slice 5 keep-vs-remove boundary)."
            )
