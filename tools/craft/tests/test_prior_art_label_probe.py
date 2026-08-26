"""EPHEMERAL assumption probe — NOT durable coverage, delete after use.

Resolves the unknown blocking task/prove-the-craft-prior-art-label-key-end-to-end:
is the label key ``craft/prior-art`` writable, queryable, and legal to name
literally inside a shipped SKILL.md?

Claims 1-3 (write + query round-trip) are probed here against a throwaway
vault in tmp_path, using the config-env / CLI-subprocess helper pattern from
tools/lore/tests/conftest.py (never the developer's real vault — Axiom 6).
Claim 4 (audit legality) is a separate probe run manually via bash against
tools/lore/tests/test_reserved_label_key_audit.py with a scratch SKILL.md
edit, reverted before commit — see the assumption-prover's report.
"""

from __future__ import annotations

import sys
from pathlib import Path

LORE_TESTS_DIR = Path(__file__).parent.parent.parent / "lore" / "tests"
sys.path.insert(0, str(LORE_TESTS_DIR))

from conftest import make_vault, run_cli  # noqa: E402


def test_craft_prior_art_label_round_trips_through_create_and_search(tmp_path):
    """Claims 1-3 in one round trip:

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
    assert create.returncode == 0, create.stderr  # Claim 1: write-time guard accepted it
    record_id = create.stdout.strip()
    assert record_id.startswith("decision/"), f"expected decision/<name>, got {record_id!r}"
    name = record_id.split("/", 1)[1]

    exists_search = run_cli(
        ["search", "has:label.craft.prior-art"],
        vault=vault,
        state_dir=state,
    )
    assert exists_search.returncode == 0, exists_search.stderr
    assert name in exists_search.stdout, (  # Claim 2
        f"expected {name!r} in search output for has:label.craft.prior-art, "
        f"got: {exists_search.stdout!r}"
    )

    eq_search = run_cli(
        ["search", "label.craft.prior-art:widget-cache"],
        vault=vault,
        state_dir=state,
    )
    assert eq_search.returncode == 0, eq_search.stderr
    assert name in eq_search.stdout, (  # Claim 3
        f"expected {name!r} in search output for label.craft.prior-art:widget-cache, "
        f"got: {eq_search.stdout!r}"
    )
