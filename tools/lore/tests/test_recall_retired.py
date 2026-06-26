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
from conftest import load_script, make_vault, run_cli  # noqa: E402


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
# Call-site: area pointer references search not recall
# ---------------------------------------------------------------------------


class TestAreaPointerCallSite:
    def test_area_pointer_references_search_not_recall(self, tmp_path):
        """The area-pointer (serve `lore areas` / recall flows) must point at
        `lore search`, not the removed `lore recall`.
        Slice 7: area profiles live under area/ (singular), not areas/."""
        recall = load_script("recall")
        vault = tmp_path / "vault"
        (vault / "area").mkdir(parents=True)
        (vault / "area" / "penny.md").write_text(
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
