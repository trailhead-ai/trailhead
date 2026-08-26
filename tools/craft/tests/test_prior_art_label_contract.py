"""The ``craft/prior-art`` label key craft's prior-art survey block prescribes.

The survey block shipped in ``skills/brainstorm/SKILL.md`` and ``skills/plan/SKILL.md``
instructs a session to write ``--label craft/prior-art=<capability-slug>`` and to read
those calls back with ``lore search 'has:label.craft.prior-art'``. This pins that round
trip, so a change to lore's write-time reserved-key guard, label handling, or KQL
parsing fails here rather than silently turning the shipped instruction into a query
that returns empty — a result the block's zero-result protocol reads as "nothing
recorded yet", making the breakage invisible at the surface.

The round trip runs against a throwaway vault in ``tmp_path`` via the config-env /
CLI-subprocess helpers in ``tools/lore/tests/conftest.py``, never the developer's real
vault. Legality of the literal ``--label craft/prior-art=<capability-slug>`` example
inside a shipped SKILL.md is held separately by
``tools/lore/tests/test_reserved_label_key_audit.py``, whose scope is every shipped
``SKILL.md`` and agent definition under ``tools/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

LORE_TESTS_DIR = Path(__file__).parent.parent.parent / "lore" / "tests"
sys.path.insert(0, str(LORE_TESTS_DIR))

from conftest import make_vault, run_cli  # noqa: E402


def test_craft_prior_art_label_round_trips_through_create_and_search(tmp_path):
    """Three properties in one round trip:

    1. `record create --kind decision --label craft/prior-art=<slug>` is
       accepted by the write-time reserved-key guard.
    2. `search 'has:label.craft.prior-art'` returns that record.
    3. `search 'label.craft.prior-art:<slug>'` matches it exactly.
    """
    vault, state = make_vault(tmp_path)

    create = run_cli(
        [
            "record",
            "create",
            "--kind",
            "decision",
            "--title",
            "Prior Art Probe Decision",
            "--keyword",
            "probe",
            "--label",
            "craft/prior-art=widget-cache",
        ],
        vault=vault,
        state_dir=state,
        stdin_text="body\n",
    )
    assert create.returncode == 0, create.stderr  # write-time reserved-key guard accepted it
    record_id = create.stdout.strip()
    assert record_id.startswith("decision/"), f"expected decision/<name>, got {record_id!r}"
    name = record_id.split("/", 1)[1]

    exists_search = run_cli(
        ["search", "has:label.craft.prior-art"],
        vault=vault,
        state_dir=state,
    )
    assert exists_search.returncode == 0, exists_search.stderr
    assert name in exists_search.stdout, (  # existence lookup
        f"expected {name!r} in search output for has:label.craft.prior-art, "
        f"got: {exists_search.stdout!r}"
    )

    eq_search = run_cli(
        ["search", "label.craft.prior-art:widget-cache"],
        vault=vault,
        state_dir=state,
    )
    assert eq_search.returncode == 0, eq_search.stderr
    assert name in eq_search.stdout, (  # exact-value lookup
        f"expected {name!r} in search output for label.craft.prior-art:widget-cache, "
        f"got: {eq_search.stdout!r}"
    )
