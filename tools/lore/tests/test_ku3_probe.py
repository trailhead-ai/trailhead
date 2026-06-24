"""KU3 assumption probe — flushed-at storage in the annotations map.

Resolves KU3: *The record frontmatter schema (FIELDS_V1) is a closed schema —
arbitrary top-level keys are rejected by the validator. The plan proposes storing
the flush watermark inside the sidecar's free-form annotations map (the same map
Slice 1 uses for last-referenced-at), so no schema change is needed; 'outstanding
candidates' = candidate body lines timestamped after flushed-at.*

Four sub-questions probed:

  1. The annotations map accepts an arbitrary flushed-at key without validator
     rejection — i.e. annotations IS free-form, NOT whitelisted/closed.
  2. Candidate body lines carry a comparable ISO-8601-UTC timestamp.  The ts
     string-sorts and datetime-parses consistently with a flushed-at value.
  3. Round-trip: setting annotations["flushed-at"], writing the sidecar, and
     reindexing leaves the value intact and the record still valid (status clean).
  4. Parse-failure fallback is well-defined: stdlib datetime.fromisoformat
     detect/reject a corrupted/missing/future flushed-at without dropping candidates.

This file is ephemeral — delete it after Slice 2 ships its own behavioral tests.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Script / conftest imports
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"
CLI_PATH = REPO_ROOT / "plugins" / "lore" / "cli" / "lore"

sys.path.insert(0, str(SCRIPTS_DIR))

from conftest import load_script, make_vault as _make_vault, run_cli as _run

SID = "22222222-3333-4444-8555-666666666666"

# ---------------------------------------------------------------------------
# Minimal valid session sidecar fixture
# ---------------------------------------------------------------------------

_BASE_SIDECAR = {
    "version": "v1",
    "kind": "session",
    "title": "ku3-probe",
    "status": "dirty",
    "created-at": "2026-06-24T10:00:00Z",
    "created-by": "tester@example.com",
    "updated-at": "2026-06-24T10:00:00Z",
    "updated-by": "tester@example.com",
}


def _sidecar(**extra_annotations) -> dict:
    """Return a valid session sidecar, optionally with annotations entries."""
    s = copy.deepcopy(_BASE_SIDECAR)
    if extra_annotations:
        s["annotations"] = dict(extra_annotations)
    return s


# ---------------------------------------------------------------------------
# Probe 1: annotations map accepts flushed-at without validator rejection
# ---------------------------------------------------------------------------

class TestAnnotationsFlushedAtAccepted:
    """Prove annotations['flushed-at'] passes the record_model validator."""

    def test_base_sidecar_is_valid(self):
        """Sanity: the base sidecar validates clean before we add anything."""
        rm = load_script("record_model")
        result = rm.validate(_base := _sidecar())
        assert result.errors == [], f"base sidecar has unexpected errors: {result.errors}"

    def test_flushed_at_in_annotations_passes_validator(self):
        """annotations['flushed-at'] is accepted — annotations IS free-form."""
        rm = load_script("record_model")
        ts = "2026-06-24T12:00:00Z"
        s = _sidecar(**{"flushed-at": ts})
        result = rm.validate(s)
        assert result.errors == [], (
            f"annotations['flushed-at'] = {ts!r} was rejected by record_model:\n"
            + "\n".join(result.errors)
        )

    def test_flushed_at_map_key_matches_internal_regex(self):
        """The key 'flushed-at' satisfies _MAP_KEY_RE (lowercase kebab, alphanumeric ends)."""
        # Reconstruct the regex the validator uses — this makes the contract explicit.
        _KEBAB_SEGMENT = r"[a-z0-9]([a-z0-9-]*[a-z0-9])?"
        _MAP_KEY_RE = re.compile(rf"^{_KEBAB_SEGMENT}(/{_KEBAB_SEGMENT})?\Z")
        assert _MAP_KEY_RE.match("flushed-at"), (
            "'flushed-at' does NOT match the annotations key regex — would be rejected"
        )
        # Also confirm last-referenced-at (already in use) matches, as a control.
        assert _MAP_KEY_RE.match("last-referenced-at"), (
            "'last-referenced-at' does NOT match — control assertion failed"
        )

    def test_annotations_top_level_key_is_rejected_by_validator(self):
        """Arbitrary keys OUTSIDE annotations are rejected — schema IS closed at top level."""
        rm = load_script("record_model")
        s = dict(_BASE_SIDECAR)
        s["flushed-at"] = "2026-06-24T12:00:00Z"  # top-level, not in annotations
        result = rm.validate(s)
        assert any("flushed-at" in e or "unsupported key" in e for e in result.errors), (
            "Expected 'flushed-at' as a top-level key to be rejected; got: "
            + str(result.errors)
        )

    def test_arbitrary_string_value_passes_in_annotations(self):
        """annotations values are free-form strings — any ISO-8601 string is acceptable."""
        rm = load_script("record_model")
        for ts in [
            "2026-06-24T00:00:00Z",
            "2026-12-31T23:59:59Z",
            "2026-01-01T00:00:00Z",
        ]:
            s = _sidecar(**{"flushed-at": ts})
            result = rm.validate(s)
            assert result.errors == [], (
                f"timestamp {ts!r} in annotations was unexpectedly rejected: {result.errors}"
            )


# ---------------------------------------------------------------------------
# Probe 2: candidate timestamp format is ISO-8601-UTC and lexicographically sortable
# ---------------------------------------------------------------------------

class TestCandidateTimestampFormat:
    """Prove candidate body lines carry a comparable ISO-8601-UTC timestamp."""

    # The CLI writes:  f"- candidate {now} kind={kind} phase={phase}"
    # where now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # The session_store._now_utc_z() uses the same format.
    _CAND_RE = re.compile(
        r"^- candidate (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) kind=\S+ phase=\S+"
    )

    def _extract_ts(self, line: str) -> str:
        m = self._CAND_RE.match(line)
        assert m, f"candidate line does not match expected format: {line!r}"
        return m.group(1)

    def test_candidate_ts_parses_as_iso8601_utc(self):
        """A candidate line timestamp parses as UTC with datetime.fromisoformat."""
        line = "- candidate 2026-06-24T10:15:30Z kind=decision phase=Build"
        ts = self._extract_ts(line)
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None, "parsed datetime has no tzinfo"
        assert parsed.utcoffset() == timedelta(0), "parsed datetime is not UTC"

    def test_flushed_at_and_candidate_ts_use_same_format(self):
        """flushed-at and candidate timestamps use the identical strftime format."""
        # Both produced by: strftime("%Y-%m-%dT%H:%M:%SZ")
        flushed_at = "2026-06-24T12:00:00Z"
        cand_ts    = "2026-06-24T10:15:30Z"  # before flush
        cand_ts2   = "2026-06-24T13:45:00Z"  # after flush

        flush_dt  = datetime.fromisoformat(flushed_at)
        cand_dt   = datetime.fromisoformat(cand_ts)
        cand_dt2  = datetime.fromisoformat(cand_ts2)

        assert cand_dt  < flush_dt,  "pre-flush candidate should compare < flushed-at"
        assert cand_dt2 > flush_dt,  "post-flush candidate should compare > flushed-at"

    def test_iso8601_utc_is_lexicographically_sortable(self):
        """ISO-8601-UTC strings (no fractional seconds) sort lexicographically == chronologically."""
        timestamps = [
            "2026-01-01T00:00:00Z",
            "2026-06-24T10:00:00Z",
            "2026-06-24T12:00:00Z",
            "2026-06-24T13:45:00Z",
            "2026-12-31T23:59:59Z",
        ]
        # Lexicographic sort must agree with datetime sort.
        lex_sorted  = sorted(timestamps)
        dt_sorted   = sorted(timestamps, key=lambda s: datetime.fromisoformat(s))
        assert lex_sorted == dt_sorted, (
            "Lex and dt sorts disagree — the 'lines after watermark' comparison is NOT reliable"
        )

    def test_outstanding_candidate_detection_contract(self):
        """Demonstrate the full 'outstanding = after flushed-at' comparison contract."""
        flushed_at = "2026-06-24T12:00:00Z"
        flush_dt   = datetime.fromisoformat(flushed_at)

        body_lines = [
            "- candidate 2026-06-24T10:00:00Z kind=decision phase=Plan",  # before flush
            "- candidate 2026-06-24T11:59:59Z kind=lesson phase=Build",   # before flush
            "- candidate 2026-06-24T12:00:01Z kind=spec phase=Review",    # after flush (outstanding)
            "- candidate 2026-06-24T14:00:00Z kind=backlog phase=Close",  # after flush (outstanding)
        ]

        outstanding = []
        for line in body_lines:
            m = self._CAND_RE.match(line)
            if m and datetime.fromisoformat(m.group(1)) > flush_dt:
                outstanding.append(line)

        assert len(outstanding) == 2, f"expected 2 outstanding candidates, got: {outstanding}"
        assert "2026-06-24T12:00:01Z" in outstanding[0]
        assert "2026-06-24T14:00:00Z" in outstanding[1]


# ---------------------------------------------------------------------------
# Probe 3: round-trip — set flushed-at, write sidecar, reindex, value survives
# ---------------------------------------------------------------------------

class TestFlushedAtRoundTrip:
    """Prove flushed-at survives a reindex and the record stays valid."""

    def test_flushed_at_survives_sidecar_write_and_reindex(self, tmp_path):
        """Set annotations['flushed-at'], write disk, reindex, read back — value intact."""
        vault, state = _make_vault(tmp_path)

        # Step 1: create a session record via the CLI (so the index + sidecar are real).
        r = _run(
            ["session", "candidate",
             "--session-id", SID, "--kind", "decision", "--phase", "Build"],
            vault=vault, state_dir=state, stdin_text="some candidate\n",
            env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""},
        )
        assert r.returncode == 0, f"candidate failed: {r.stderr}"

        sidecar_path = vault / "session" / f"{SID}.json"
        body_path    = vault / "session" / f"{SID}.md"

        # Step 2: stamp flushed-at + set status clean (simulating what lore flush will do).
        sidecar = json.loads(sidecar_path.read_text())
        flush_ts = "2026-06-24T15:00:00Z"
        sidecar.setdefault("annotations", {})["flushed-at"] = flush_ts
        sidecar["status"] = "clean"
        sidecar_path.write_text(json.dumps(sidecar, sort_keys=True))

        # Step 3: validate the on-disk sidecar passes record_model.
        rm = load_script("record_model")
        result = rm.validate(sidecar)
        assert result.errors == [], (
            f"sidecar with flushed-at fails validator after manual stamp:\n"
            + "\n".join(result.errors)
        )

        # Step 4: reindex via index_store.upsert_row (the same path lore flush uses).
        index_store = load_script("index_store")
        body = body_path.read_text()
        conn = index_store.open_index(env={"XDG_STATE_HOME": str(state)})
        try:
            index_store.upsert_row(conn, str(vault), "session", SID, sidecar, body)
            conn.commit()
        finally:
            conn.close()

        # Step 5: read the on-disk sidecar back — flushed-at must be intact.
        after = json.loads(sidecar_path.read_text())
        assert after.get("annotations", {}).get("flushed-at") == flush_ts, (
            f"flushed-at lost after reindex: {after.get('annotations')}"
        )
        assert after["status"] == "clean"

        # Step 6: confirm the index row reflects clean status (annotations not projected).
        conn = index_store.open_index(env={"XDG_STATE_HOME": str(state)})
        try:
            row = conn.execute(
                "SELECT status FROM records WHERE kind='session' AND name=?",
                (SID,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "session record not found in index after upsert_row"
        assert row[0] == "clean", f"index status should be 'clean', got {row[0]!r}"

    def test_annotations_not_projected_to_index(self, tmp_path):
        """Prove annotations (including flushed-at) are sidecar-only, not projected to the index.

        The index_store comment at line ~305 says:
          'annotations are sidecar-only and deliberately NOT projected.'
        This test confirms that: a sidecar with flushed-at lands in the index as
        a status/title row with NO annotations column, so flushed-at is invisible
        to KQL and can only be read from the .json sidecar.
        """
        vault, state = _make_vault(tmp_path)

        r = _run(
            ["session", "candidate",
             "--session-id", SID, "--kind", "spec", "--phase", "Plan"],
            vault=vault, state_dir=state, stdin_text="probe\n",
            env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""},
        )
        assert r.returncode == 0

        sidecar_path = vault / "session" / f"{SID}.json"
        body_path    = vault / "session" / f"{SID}.md"
        sidecar = json.loads(sidecar_path.read_text())
        sidecar.setdefault("annotations", {})["flushed-at"] = "2026-06-24T15:00:00Z"
        sidecar_path.write_text(json.dumps(sidecar))

        index_store = load_script("index_store")
        conn = index_store.open_index(env={"XDG_STATE_HOME": str(state)})
        try:
            index_store.upsert_row(conn, str(vault), "session", SID, sidecar,
                                   body_path.read_text())
            conn.commit()
            # The records table has no 'annotations' column — existence probe.
            col_names = [row[1] for row in conn.execute("PRAGMA table_info(records)")]
        finally:
            conn.close()

        assert "annotations" not in col_names, (
            "annotations should NOT be a column in records table — it is sidecar-only"
        )


# ---------------------------------------------------------------------------
# Probe 4: parse-failure fallback is well-defined via stdlib
# ---------------------------------------------------------------------------

class TestFlushedAtParseFailureFallback:
    """Prove a corrupted/missing/future flushed-at can be detected with stdlib."""

    def _safe_parse_flushed_at(self, raw: str | None) -> datetime | None:
        """Reference implementation of the Slice 2 reader contract.

        Returns a UTC datetime if raw is a valid ISO-8601 UTC string; None otherwise
        (triggering the 'no prior flush' fallback — re-evaluate all candidates).
        """
        if not raw or not isinstance(raw, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            return None
        return parsed

    def test_valid_flushed_at_parses_correctly(self):
        ts = "2026-06-24T12:00:00Z"
        result = self._safe_parse_flushed_at(ts)
        assert result is not None, "valid flushed-at should parse"
        assert result.utcoffset() == timedelta(0)

    def test_missing_flushed_at_returns_none(self):
        """annotations dict with no flushed-at key -> fallback."""
        annotations = {"last-referenced-at": "2026-06-24T10:00:00Z"}
        raw = annotations.get("flushed-at")  # None
        result = self._safe_parse_flushed_at(raw)
        assert result is None, "missing flushed-at must return None (re-evaluate all)"

    def test_empty_string_flushed_at_returns_none(self):
        result = self._safe_parse_flushed_at("")
        assert result is None, "empty string must return None"

    def test_corrupted_flushed_at_returns_none(self):
        """A non-ISO-8601 value in annotations['flushed-at'] falls back to None."""
        for bad in ["not-a-date", "2026/06/24", "June 24 2026", "12345", None, 12345]:
            raw = bad if isinstance(bad, (str, type(None))) else str(bad)
            result = self._safe_parse_flushed_at(raw)
            assert result is None, f"corrupted value {bad!r} should return None, got {result}"

    def test_naive_datetime_string_returns_none(self):
        """A naive datetime (no timezone suffix) must be rejected."""
        result = self._safe_parse_flushed_at("2026-06-24T12:00:00")
        assert result is None, "naive datetime must be rejected (no tzinfo)"

    def test_non_utc_offset_returns_none(self):
        """A non-UTC timezone offset must be rejected."""
        result = self._safe_parse_flushed_at("2026-06-24T12:00:00+05:30")
        assert result is None, "+05:30 offset must be rejected (not UTC)"

    def test_future_flushed_at_does_not_cause_error(self):
        """A future timestamp is valid ISO-8601 UTC and must parse (not error)."""
        # A 'future' flushed-at is an application-layer concern; the parser accepts it.
        far_future = "2099-12-31T23:59:59Z"
        result = self._safe_parse_flushed_at(far_future)
        assert result is not None, "future flushed-at must parse without error"

    def test_fallback_means_all_candidates_evaluated(self):
        """When flushed-at is None (fallback), ALL candidate lines are 'outstanding'."""
        _CAND_RE = re.compile(
            r"^- candidate (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) kind=\S+ phase=\S+"
        )
        body_lines = [
            "- candidate 2026-06-24T10:00:00Z kind=decision phase=Plan",
            "- candidate 2026-06-24T11:00:00Z kind=lesson phase=Build",
        ]
        flushed_at = None  # corrupted / missing → fallback
        # Contract: when flushed_at is None, cutoff = epoch → all candidates are outstanding.
        cutoff = datetime.fromtimestamp(0, tz=timezone.utc) if flushed_at is None else flushed_at

        outstanding = []
        for line in body_lines:
            m = _CAND_RE.match(line)
            if m and datetime.fromisoformat(m.group(1)) > cutoff:
                outstanding.append(line)

        assert len(outstanding) == 2, (
            "fallback (flushed_at=None) must treat ALL candidates as outstanding"
        )
