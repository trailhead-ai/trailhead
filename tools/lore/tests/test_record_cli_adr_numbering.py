"""Tests for ``lore record create --kind adr``'s per-vault sequence numbering.

Covers the test contract:

  - First adr in an empty vault gets ADR-001; second gets ADR-002.
  - Highest existing slug ``adr-007-*`` (with ``adr-003`` missing) yields
    ADR-008 — gaps are preserved, a dropped number is never reused.
  - A pre-seeded collision on the computed number refuses the write with a
    named error: nonzero exit, no file and no index row written
    (transactionality).
  - A user-supplied already-numbered title ("ADR-9: foo") is overridden — the
    CLI's computed number wins, deliberately.
  - Non-adr kinds are unaffected (regression: naming/collision-suffix
    behavior unchanged).
  - Concurrency: two racing creates targeting the identical computed stem —
    exactly one wins, the loser gets the named refusal, and the surviving
    body is intact (no clobber, no partial write).

The scan/rewrite/regression/collision tests run the CLI as a subprocess (the
conftest pattern used across the record CLI suite) so they exercise the exact
wiring an agent invokes. The concurrency test drives ``record_store``'s
``place_record``/``validate_and_write`` directly — the same production
functions the CLI's ``--kind adr`` create branch calls — synchronized with a
``threading.Barrier`` so both writers reach the exclusive-write call at the
same instant. A real filesystem race needs that instant to be shared;
subprocess launch timing cannot guarantee it deterministically.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from conftest import (  # noqa: F401
    load_script,
    make_vault as _make_vault,
    run_cli as _run,
    write_default_config,
)


def _find_sidecar(vault: Path, record_id: str) -> dict:
    kind, name = record_id.split("/", 1)
    path = vault / kind / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _create_adr(vault, state, title, stdin_text=""):
    return _run(
        ["record", "create", "--kind", "adr", "--title", title],
        vault=vault,
        state_dir=state,
        stdin_text=stdin_text,
    )


# ---------------------------------------------------------------------------
# sequential numbering
# ---------------------------------------------------------------------------


def test_first_adr_in_empty_vault_gets_001(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _create_adr(vault, state, "Use widgets")
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    assert record_id == "adr/adr-001-use-widgets"
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["title"] == "ADR-001: Use widgets"


def test_second_adr_gets_002(tmp_path):
    vault, state = _make_vault(tmp_path)
    first = _create_adr(vault, state, "Use widgets")
    assert first.returncode == 0, first.stderr
    second = _create_adr(vault, state, "Use gadgets")
    assert second.returncode == 0, second.stderr
    record_id = second.stdout.strip()
    assert record_id == "adr/adr-002-use-gadgets"
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["title"] == "ADR-002: Use gadgets"


def test_gap_preserved_next_is_highest_plus_one(tmp_path):
    """``adr-007-*`` exists (``adr-003`` never created) → next create is ADR-008."""
    vault, state = _make_vault(tmp_path)
    adr_dir = vault / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "adr-001-first.md").write_text("body")
    (adr_dir / "adr-001-first.json").write_text("{}")
    (adr_dir / "adr-007-seventh.md").write_text("body")
    (adr_dir / "adr-007-seventh.json").write_text("{}")

    r = _create_adr(vault, state, "Eighth decision")
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    assert record_id == "adr/adr-008-eighth-decision"
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["title"] == "ADR-008: Eighth decision"


# ---------------------------------------------------------------------------
# collision refusal (transactional)
# ---------------------------------------------------------------------------


def test_precomputed_collision_refuses_write_transactionally(tmp_path):
    """A stray sidecar (no matching body) already occupies the exact stem the
    write-time claim will target → refusal, nothing new persisted.

    The scan only tallies existing ``.md`` stems (:func:`next_adr_number`), so
    an orphaned ``.json`` with no matching ``.md`` — the same interrupted-write
    shape ``_stem_occupied`` already treats as "occupied" elsewhere in this
    module — is invisible to the scan and so reproduces a genuine collision on
    the computed number without needing two real concurrent writers: the scan
    computes number 1 (nothing counted), the body claim succeeds (no ``.md``
    there yet), and then the sidecar claim collides with the orphan.
    """
    vault, state = _make_vault(tmp_path)
    adr_dir = vault / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "adr-001-collision-test.json").write_text('{"pre": "existing"}')

    r = _create_adr(vault, state, "Collision test")
    assert r.returncode != 0
    assert "lore:" in r.stderr

    # Nothing new persisted: the orphan sidecar is untouched, and the body
    # claimed mid-write was rolled back rather than left standing.
    assert (
        adr_dir / "adr-001-collision-test.json"
    ).read_text() == '{"pre": "existing"}'
    assert not (adr_dir / "adr-001-collision-test.md").exists()
    assert list(adr_dir.glob("*.tmp")) == []

    mod = load_script("lore.search.index")
    conn = mod.open_index(env={"XDG_STATE_HOME": str(state)})
    try:
        rows = conn.execute(
            "SELECT name FROM records WHERE vault=? AND kind=? AND name=?",
            (str(vault), "adr", "adr-001-collision-test"),
        ).fetchall()
    finally:
        conn.close()
    assert rows == []


# ---------------------------------------------------------------------------
# deliberate override of a user-supplied numbered title
# ---------------------------------------------------------------------------


def test_user_supplied_numbered_title_is_overridden(tmp_path):
    """A title that already looks numbered ("ADR-9: foo") is overridden — the
    CLI's computed number wins (deliberate; not a merge)."""
    vault, state = _make_vault(tmp_path)
    r = _create_adr(vault, state, "ADR-9: Some decision")
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    # First adr in an empty vault → 001, not the user-supplied 9.
    assert record_id == "adr/adr-001-some-decision"
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["title"] == "ADR-001: Some decision"


# ---------------------------------------------------------------------------
# regression: non-adr kinds untouched
# ---------------------------------------------------------------------------


def test_non_adr_kind_naming_unchanged(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--kind", "spec", "--title", "My Record"],
        vault=vault,
        state_dir=state,
        stdin_text="",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    assert record_id == "spec/my-record"
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["title"] == "My Record"


def test_non_adr_kind_collision_still_suffixes(tmp_path):
    """A non-adr kind still gets the ``-2`` suffix on collision (unchanged)."""
    vault, state = _make_vault(tmp_path)
    first = _run(
        ["record", "create", "--kind", "spec", "--title", "My Record"],
        vault=vault,
        state_dir=state,
        stdin_text="",
    )
    assert first.returncode == 0, first.stderr
    second = _run(
        ["record", "create", "--kind", "spec", "--title", "My Record"],
        vault=vault,
        state_dir=state,
        stdin_text="",
    )
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "spec/my-record-2"


# ---------------------------------------------------------------------------
# concurrency: exactly one winner, no clobber
# ---------------------------------------------------------------------------


def test_concurrent_creates_same_stem_exactly_one_winner(tmp_path):
    """Two racing creates targeting the identical computed stem: one winner,
    one named refusal, no clobber."""
    rs = load_script("lore.record.store")
    index_mod = load_script("lore.search.index")

    vault = tmp_path / "vault"
    vault.mkdir()
    state = tmp_path / "state"
    state.mkdir()

    os.environ["LORE_EMAIL"] = "tester@example.com"

    barrier = threading.Barrier(2)
    results: dict[int, tuple] = {}

    # Warm the schema in the main thread first -- a sqlite3.Connection is
    # confined to the thread that opened it (check_same_thread), so each
    # racer opens its OWN connection inside its own thread below; doing the
    # first-time "CREATE TABLE IF NOT EXISTS" DDL here avoids two threads
    # racing over schema creation, which is a separate, unrelated race from
    # the one this test exercises.
    warm_conn = index_mod.open_index(env={"XDG_STATE_HOME": str(state)})
    warm_conn.close()

    def _racer(idx: int, body_text: str) -> None:
        location = rs.place_record("ADR-001: Same Decision", "adr", None, str(vault))
        sidecar = {
            "version": "v1",
            "kind": "adr",
            "title": "ADR-001: Same Decision",
            "status": "draft",
        }
        conn = index_mod.open_index(env={"XDG_STATE_HOME": str(state)})
        try:
            barrier.wait(timeout=10)
            record_id = rs.validate_and_write(
                location=location,
                sidecar=sidecar,
                body=body_text,
                conn=conn,
                require_new=True,
            )
            conn.commit()
            results[idx] = ("ok", record_id)
        except Exception as exc:  # noqa: BLE001 - captured for assertion below
            results[idx] = ("error", exc)
        finally:
            conn.close()

    t1 = threading.Thread(target=_racer, args=(1, "body one\n"))
    t2 = threading.Thread(target=_racer, args=(2, "body two\n"))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    outcomes = [results[1], results[2]]
    oks = [o for o in outcomes if o[0] == "ok"]
    errors = [o for o in outcomes if o[0] == "error"]
    assert len(oks) == 1, outcomes
    assert len(errors) == 1, outcomes
    assert isinstance(errors[0][1], rs.RecordAlreadyExistsError), outcomes

    body_path = vault / "adr" / "adr-001-same-decision.md"
    assert body_path.read_text() in ("body one\n", "body two\n")
    assert list((vault / "adr").glob("*.tmp")) == []
