"""The `recall` command is retired; call sites are rewired to `lore search`.

The destructive cutover: the `recall` COMMAND surface is gone (`cmd_recall`, the
`recall` subparser, and the recall-command machinery in `recall.py`). The
unknown-command hint machinery redirects an agent that types the old command to
`lore search`. Every executable `lore recall` reference in `hooks/` + `cli/` is
removed, and the SessionStart hook itself has been retired.

The area-map path (`cmd_areas`, `build_area_map`, `render_area_pointer`,
`render_area_menu`, `AreaEntry`) is KEPT — it serves `lore areas`, not the
recall command — and is covered by `test_area_map_core.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

CONFTEST_DIR = Path(__file__).parent
sys.path.insert(0, str(CONFTEST_DIR))
from conftest import make_vault, run_cli  # noqa: E402


# ---------------------------------------------------------------------------
# `lore recall …` is gone → non-zero exit + "did you mean 'lore search'?" hint
# ---------------------------------------------------------------------------


class TestRecallCommandRetired:
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
