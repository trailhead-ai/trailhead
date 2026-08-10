"""Tests for ``lore record create --kind adr``'s per-vault sequence numbering.

Covers the test contract:

  - First adr in an empty vault gets ADR-001; second gets ADR-002.
  - Highest existing slug ``adr-007-*`` (with ``adr-003`` missing) yields
    ADR-008 — gaps are preserved, a dropped number is never reused.
  - An orphaned half of an interrupted write still consumes its number.
  - A number already carried by some other title refuses the write with a named
    error: no file and no index row written (transactionality), and the CLI maps
    that error onto a ``lore:`` refusal line.
  - The per-number lock sidecar is not a record: it neither consumes a number
    nor wedges one after a crash.
  - A user-supplied already-numbered title ("ADR-9: foo") is overridden — the
    CLI's computed number wins, deliberately.
  - Non-adr kinds are unaffected (regression: naming/collision-suffix
    behavior unchanged).
  - Concurrency: two racing creates that computed the same number — whether
    they carry the identical title (identical stem) or different titles
    (different stems) — yield exactly one surviving record for that number,
    the loser gets the named refusal, and the surviving body is intact (no
    clobber, no partial write).

The scan/rewrite/regression/collision tests run the CLI as a subprocess (the
conftest pattern used across the record CLI suite) so they exercise the exact
wiring an agent invokes. The concurrency tests drive ``record_store``'s
``place_record``/``validate_and_write`` directly — the same production
functions the CLI's ``--kind adr`` create branch calls — synchronized with a
``threading.Barrier`` so both writers reach the exclusive-write call at the
same instant. A real filesystem race needs that instant to be shared;
subprocess launch timing cannot guarantee it deterministically.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from conftest import (  # noqa: F401
    load_script,
    make_vault as _make_vault,
    run_cli as _run,
    write_default_config,
)


def adr_records(vault: Path) -> list[Path]:
    """Every record artifact in the vault's ``adr/`` dir (locks excluded)."""
    return [p for p in (vault / "adr").glob("*") if p.suffix in (".md", ".json")]


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


def test_orphaned_sidecar_still_occupies_its_number(tmp_path):
    """An orphaned ``adr-001-*.json`` (no matching ``.md``) still consumes number
    1, so the next create is ADR-002 rather than colliding on 1.

    The interrupted-write shape ``_stem_occupied`` already treats as "occupied"
    elsewhere: a crash between the body claim and the sidecar claim can strand
    either half. A scan that tallied only ``.md`` stems would hand number 1 out
    again and the write would then refuse on a collision the scan could have
    seen for itself.
    """
    vault, state = _make_vault(tmp_path)
    adr_dir = vault / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "adr-001-interrupted.json").write_text('{"pre": "existing"}')

    r = _create_adr(vault, state, "Next decision")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "adr/adr-002-next-decision"
    # The orphan is left exactly as it was — the scan reads, never repairs.
    assert (adr_dir / "adr-001-interrupted.json").read_text() == '{"pre": "existing"}'


def test_stranded_number_lock_does_not_wedge_the_number(tmp_path):
    """A lock sidecar left behind by an earlier write never blocks a later one.

    The lock's exclusivity is the kernel-held ``flock``, not the file's
    existence, so an already-present ``.adr-<n>.lock`` — the normal steady state
    once a number has been issued, and what a crashed write leaves — is simply
    re-locked. Pins the reason a released-on-close lock was chosen over an
    unlink-on-exit claim artifact, which would have wedged its number on a crash.

    The lock lives under ``$XDG_STATE_HOME/lore/locks/<vault>/adr/``, not
    inside the vault tree — a stranded lock is seeded there, matching where a
    real crashed write would leave it.
    """
    vault, state = _make_vault(tmp_path)
    lock_dir = state / "lore" / "locks" / vault.name / "adr"
    lock_dir.mkdir(parents=True)
    (lock_dir / ".adr-1.lock").write_text("")

    r = _create_adr(vault, state, "First decision")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "adr/adr-001-first-decision"


def test_number_lock_is_not_counted_as_a_record(tmp_path):
    """The lock sidecar is not a record: it never consumes a sequence number and
    is not itself indexed or listed as an adr artifact."""
    vault, state = _make_vault(tmp_path)
    first = _create_adr(vault, state, "First decision")
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "adr/adr-001-first-decision"

    # No lock file lands inside the vault's adr/ dir at all — it lives under
    # the state dir now, so the vault listing is nothing but the record pair.
    assert [p.name for p in sorted(adr_records(vault))] == [
        "adr-001-first-decision.json",
        "adr-001-first-decision.md",
    ]
    assert list((vault / "adr").glob("*.lock")) == []
    second = _create_adr(vault, state, "Second decision")
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "adr/adr-002-second-decision"


def test_adr_lock_never_lands_inside_the_vault_adr_dir(tmp_path):
    """The per-number lock sidecar is created under the state dir, never under
    the vault's ``adr/`` directory — the leak this task fixes."""
    vault, state = _make_vault(tmp_path)
    r = _create_adr(vault, state, "First decision")
    assert r.returncode == 0, r.stderr

    assert list((vault / "adr").glob("*.lock")) == []
    lock_path = state / "lore" / "locks" / vault.name / "adr" / ".adr-1.lock"
    assert lock_path.is_file(), "lock sidecar was not created under the state dir"


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
# kebab-empty title (all-punctuation / non-Latin) still participates in scan + claim
# ---------------------------------------------------------------------------


def test_kebab_empty_title_still_gets_distinct_sequence_numbers(tmp_path):
    """A title that kebabs to nothing (e.g. all non-Latin) must still land on a
    stem the number scan and claim recognize — not an invisible ``adr-001`` that
    a second, differently-titled ADR can silently collide with.

    ``_kebab("中文")`` collapses to nothing and falls back to a stem with NO
    trailing hyphen after the number (``adr-001``, not ``adr-001-...``). A number
    regex requiring a trailing hyphen misses it entirely: ``next_adr_number``
    would not see it as occupying 1, so a second create also lands on 1.
    """
    vault, state = _make_vault(tmp_path)
    first = _create_adr(vault, state, "中文")
    assert first.returncode == 0, first.stderr
    second = _create_adr(vault, state, "Use widgets")
    assert second.returncode == 0, second.stderr

    first_id = first.stdout.strip()
    second_id = second.stdout.strip()
    assert first_id != second_id
    assert second_id == "adr/adr-002-use-widgets"


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


def _race_two_creates(rs, index_mod, vault: Path, state: Path, titles: tuple[str, str]):
    """Race two ``validate_and_write(require_new=True)`` adr creates.

    Both writers are released from a shared ``threading.Barrier`` so they reach
    the exclusive-claim syscall at the same instant. Returns the two outcomes as
    ``("ok", record_id)`` / ``("error", exception)`` tuples, in writer order.
    """
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

    def _racer(idx: int, title: str, body_text: str) -> None:
        location = rs.place_record(title, "adr", None, str(vault))
        sidecar = {
            "version": "v1",
            "kind": "adr",
            "title": title,
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

    t1 = threading.Thread(target=_racer, args=(1, titles[0], "body one\n"))
    t2 = threading.Thread(target=_racer, args=(2, titles[1], "body two\n"))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)
    return [results[1], results[2]]


def _assert_one_winner(rs, outcomes) -> None:
    """Exactly one writer succeeded; the loser got the named refusal."""
    oks = [o for o in outcomes if o[0] == "ok"]
    errors = [o for o in outcomes if o[0] == "error"]
    assert len(oks) == 1, outcomes
    assert len(errors) == 1, outcomes
    assert isinstance(errors[0][1], rs.RecordAlreadyExistsError), outcomes


def test_concurrent_creates_same_stem_exactly_one_winner(tmp_path, monkeypatch):
    """Two racing creates targeting the identical computed stem: one winner,
    one named refusal, no clobber."""
    rs = load_script("lore.record.store")
    index_mod = load_script("lore.search.index")

    vault, state = _make_vault(tmp_path)
    monkeypatch.setenv("LORE_EMAIL", "tester@example.com")

    outcomes = _race_two_creates(
        rs,
        index_mod,
        vault,
        state,
        ("ADR-001: Same Decision", "ADR-001: Same Decision"),
    )
    _assert_one_winner(rs, outcomes)

    body_path = vault / "adr" / "adr-001-same-decision.md"
    assert body_path.read_text() in ("body one\n", "body two\n")
    assert list((vault / "adr").glob("*.tmp")) == []


def test_concurrent_creates_same_number_different_titles_exactly_one_winner(
    tmp_path, monkeypatch
):
    """Two racing creates that both computed number 001 but carry DIFFERENT
    titles: exactly one ADR-001 survives, the loser gets the named refusal.

    Different titles mean different stems, so a stem-scoped claim lets both
    writers through and two records ship carrying the same number. The claim
    has to be scoped to the NUMBER for this race to have a single winner.
    """
    rs = load_script("lore.record.store")
    index_mod = load_script("lore.search.index")

    vault, state = _make_vault(tmp_path)
    monkeypatch.setenv("LORE_EMAIL", "tester@example.com")

    outcomes = _race_two_creates(
        rs,
        index_mod,
        vault,
        state,
        ("ADR-001: Decision A", "ADR-001: Decision B"),
    )
    _assert_one_winner(rs, outcomes)

    adr_dir = vault / "adr"
    bodies = sorted(p.name for p in adr_dir.glob("adr-001-*.md"))
    sidecars = sorted(p.name for p in adr_dir.glob("adr-001-*.json"))
    assert len(bodies) == 1, bodies
    assert len(sidecars) == 1, sidecars
    assert Path(bodies[0]).stem == Path(sidecars[0]).stem
    assert (adr_dir / bodies[0]).read_text() in ("body one\n", "body two\n")
    assert list(adr_dir.glob("*.tmp")) == []


def test_number_occupied_by_other_title_refuses_transactionally(tmp_path, monkeypatch):
    """An existing ``adr-001-*`` record refuses a second write on number 001
    even under a different title — nothing written, no index row.

    The single-writer form of the race above: the CLI's scan cannot hand out an
    already-occupied number, so this drives the store directly to pin the
    write-time guard the scan relies on.
    """
    rs = load_script("lore.record.store")
    index_mod = load_script("lore.search.index")

    vault, state = _make_vault(tmp_path)
    monkeypatch.setenv("LORE_EMAIL", "tester@example.com")
    adr_dir = vault / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "adr-001-decision-a.md").write_text("first body")
    (adr_dir / "adr-001-decision-a.json").write_text('{"pre": "existing"}')

    location = rs.place_record("ADR-001: Decision B", "adr", None, str(vault))
    conn = index_mod.open_index(env={"XDG_STATE_HOME": str(state)})
    try:
        with pytest.raises(rs.RecordAlreadyExistsError):
            rs.validate_and_write(
                location=location,
                sidecar={
                    "version": "v1",
                    "kind": "adr",
                    "title": "ADR-001: Decision B",
                    "status": "draft",
                },
                body="second body",
                conn=conn,
                require_new=True,
            )
    finally:
        conn.close()

    assert (adr_dir / "adr-001-decision-a.md").read_text() == "first body"
    assert not (adr_dir / "adr-001-decision-b.md").exists()
    assert not (adr_dir / "adr-001-decision-b.json").exists()
    assert list(adr_dir.glob("*.tmp")) == []

    conn = index_mod.open_index(env={"XDG_STATE_HOME": str(state)})
    try:
        rows = conn.execute("SELECT COUNT(*) FROM records").fetchone()
    finally:
        conn.close()
    assert rows[0] == 0


def test_refusal_reaches_the_operator_as_a_named_lore_line(capsys):
    """The refusal surfaces as a ``lore:`` line + nonzero exit, not a traceback.

    Once the sequence scan sees both stems, a number the scan hands out is never
    already occupied, so this refusal is reachable only from a genuine race —
    which a single subprocess cannot stage. The CLI's mapping of the store's
    typed error onto the operator-facing clean-refusal convention is asserted
    directly instead.
    """
    record_cli = load_script("lore.cli.record")
    rs = load_script("lore.record.store")

    exc = rs.RecordAlreadyExistsError("an adr already carries number 1")
    code = record_cli._handle_write_error(exc, "create")

    assert code == 1
    captured = capsys.readouterr()
    assert captured.err == "lore: an adr already carries number 1\n"
    # Nothing on stdout: no RECORD_ID is printed for a write that did not happen.
    assert captured.out == ""
