#!/usr/bin/env python3
"""Ledger gate — certifies a spec's `## Slices` ledger append-only integrity,
the fourth member of the gate family, BEFORE `/craft:slice`'s reconcile lets
a slice loop rely on the ledger's coverage attestation.

Usage:
    lore record show spec/<name> | ledger_gate.py
    lore record show spec/<name> | ledger_gate.py --parent-coverage parents.json

The spec body arrives on stdin; there is no drafted-value flag — this gate
derives its answer from the document alone, the same convention
`candidate_set.py` and `observation_gate.py` use. The optional
`--parent-coverage` flag names a JSON file, read with the stdlib `json`
module, supplying the one fact this gate cannot derive from the spec body
alone: what each ledger entry's parent record actually declared. Omitting
the flag certifies structure only (duplicate task ids, an identifier
claimed both fully and partially, invisible task-id content) and says so on
stdout — the gate stays usable standalone, with the parent cross-check as
an additive, opt-in guarantee.

Per-entry structure is read via the sibling `candidate_set.py`'s
`parse_ledger_entries` — never re-derived here, so this gate and
`candidate_set.py` cannot disagree about where one ledger entry ends and
the next begins, about the CommonMark line grammar, or about which regions
(fenced code blocks, HTML comments) are masked. `parse_ledger_entries` folds
a canonical bullet only with its own indented continuation lines, so two
entries torn or interleaved by a concurrent append surface here as two
distinct entries rather than being merged into one — that is what makes
`duplicate-ledger-task-id` detectable at all.

`--parent-coverage`'s JSON schema: a top-level JSON object mapping a ledger
entry's bare task id (the identifier `parse_ledger_entries` reports as
`task_id` — e.g. `alpha` for a ledger line reading `` `task/alpha` ``, never
the `task/` prefix) to an object carrying that parent's own coverage
fields:

    {
      "alpha": {"covers": "AC1, AC2"},
      "beta": {"partially covers": "AC3"},
      "gamma": {"covers": "AC4", "partially covers": "AC5"}
    }

Either key may be omitted (a parent that only partially covers carries no
`"covers"` key); a key present must be a string matching
`^AC\\d+(, ?AC\\d+)*$`, the same grammar `covers_gate.parse_covers`
enforces on a drafted `--covers` value. A task id present in the ledger but
absent from this map — when the map is supplied — is `orphaned-ledger-entry`:
a line with no parent to have been reconciled from.

**The append-only invariant is monotonic**, and it is checked per field
(`covers` and `partially covers` independently), never as one combined
value: an entry's field that carries no token yet may still gain one later
(the legacy backfill precondition) and is never itself a violation; an
entry's field that already carries a token must match its parent's same
field exactly (as a set — order is not significant), or the entry was
edited after it was appended, or was fabricated by a writer with no
matching parent. Under this rule an operator cannot simply rewrite the
offending line, so every exit-1 message below states the sanctioned
recovery: correct the *parent* record and re-run the reconcile, which is
the only documented writer.

Every `reason:` line is built from vault-sourced spec text — a task id, an
identifier list, an offending parenthetical — so before being printed it is
run once, at this single output boundary, through
`criterion_gate._neutralize_control_chars` (raw non-printable code points
first, so a control byte cannot be used to split a credential pattern's
match and dodge the scrub that follows) and then
`criterion_gate._scrub_credential_shaped`. Reused rather than reimplemented:
two gates redacting the same pattern family independently is how a third
divergent variant creeps in.

Exit codes:
    0  certified — the token block below is on stdout. `parent-cross-check:
       checked` when `--parent-coverage` was supplied and every entry
       passed it; `parent-cross-check: skipped ...` when the flag was
       omitted and only structure was certified.
    1  integrity violation — every message names the offending task id or
       identifier and the sanctioned recovery. Stable `reason-code:` tokens:
         invisible-ledger-task-id  — a task id carries no character beyond
             whitespace and Unicode category Cf/Cc (the same non-empty bar
             `observation_gate._is_meaningfully_nonempty` applies to
             evidence text), so it is refused rather than certified even
             though it is technically well-formed and non-blank.
         duplicate-ledger-task-id — one task id names two ledger entries —
             under sole-writer plus append-only this indicates a rewrite
             that did not replace, a double reconcile, or a torn concurrent
             append.
         coverage-claimed-twice — one entry names the same identifier in
             both its `covers` and its `partially covers` token.
         orphaned-ledger-entry — a ledger entry's task id appears in no
             supplied parent map (only checked when `--parent-coverage` is
             given).
         coverage-contradicts-parent — an entry's `covers` or `partially
             covers` field is non-empty and differs (as a set) from its
             parent's same field (only checked when `--parent-coverage` is
             given).
    2  could not certify — fail-closed:
         empty-stdin, non-utf8-stdin, stdin-too-large (spec body over the
             256 KiB cap — never a silent truncation that certifies a
             partial document),
         duplicate-slices-heading (a second unmasked `## Slices` heading —
             raised by the shared `parse_ledger_entries` walk),
         unterminated-masked-region (the document ends inside an open
             fenced code block or an open HTML comment — a masked
             `## Slices` heading or entry might be hiding unseen),
         malformed-coverage-token (a `covers` or `partially covers` field in
             the ledger itself does not parse as an ACn identifier list —
             raised by the shared `parse_ledger_entries` walk),
         malformed-parent-coverage (the file named by `--parent-coverage` is
             absent, unreadable, not valid UTF-8, not a JSON object, or
             carries a value outside the schema above — fail closed rather
             than certify against a map this gate could not read).
       Every exit-2 case prints both a `reason:` and a `reason-code:` line,
       uniformly, with no carve-out. NEVER exits 0 when it could not
       actually certify.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from candidate_set import (  # noqa: E402
    LedgerEntry,
    MalformedCoverageTokenError,
    parse_ledger_entries,
)
from covers_gate import _COMMONMARK_LINE_RE, DuplicateHeadingError  # noqa: E402
from criterion_gate import (  # noqa: E402
    _neutralize_control_chars,
    _scrub_credential_shaped,
)
from observation_gate import _ends_inside_masked_region, _is_meaningfully_nonempty  # noqa: E402

_MALFORMED_TOKEN_REASON_CODE = "malformed-coverage-token"
_MALFORMED_PARENT_COVERAGE_REASON_CODE = "malformed-parent-coverage"
_UNTERMINATED_MASKED_REGION_REASON_CODE = "unterminated-masked-region"
_EMPTY_STDIN_REASON_CODE = "empty-stdin"
_NON_UTF8_STDIN_REASON_CODE = "non-utf8-stdin"
_STDIN_TOO_LARGE_REASON_CODE = "stdin-too-large"

_INVISIBLE_TASK_ID_REASON_CODE = "invisible-ledger-task-id"
_DUPLICATE_TASK_ID_REASON_CODE = "duplicate-ledger-task-id"
_COVERAGE_CLAIMED_TWICE_REASON_CODE = "coverage-claimed-twice"
_ORPHANED_ENTRY_REASON_CODE = "orphaned-ledger-entry"
_CONTRADICTS_PARENT_REASON_CODE = "coverage-contradicts-parent"

# Fail-closed backstop against a pathologically large vault record — a real
# spec body is kilobytes; 256 KiB is generous headroom while still bounding
# the work this gate does on a single stdin read. Matches the cap
# `criterion_gate.py` already established.
_MAX_STDIN_BYTES = 256 * 1024

_REMEDY = (
    "under the monotonic coverage rule a ledger line cannot be rewritten "
    "directly — correct the parent record and re-run the reconcile, the "
    "only documented writer"
)

_PARENT_COVERAGE_FIELD_KEYS = ("covers", "partially covers")
_COVERS_TOKEN_RE = re.compile(r"^AC\d+(, ?AC\d+)*$")


class LedgerGateViolation(ValueError):
    """An integrity violation over the ledger's entries — exit code 1, a
    `reason:` line naming the offending task id or identifier plus the
    sanctioned recovery, and a stable `reason-code:` token."""

    def __init__(self, message: str, reason_code: str):
        self.reason_code = reason_code
        super().__init__(message)


class MalformedParentCoverageError(ValueError):
    """The `--parent-coverage` file is absent, unreadable, not valid JSON,
    not a JSON object, or carries a value outside the schema this gate
    accepts — exit code 2, `reason-code: malformed-parent-coverage`."""

    reason_code = _MALFORMED_PARENT_COVERAGE_REASON_CODE


def _err(msg: str) -> None:
    print(f"ledger-gate: {msg}", file=sys.stderr)


def _safe(text: str) -> str:
    """Run vault-sourced text through control-character neutralization,
    then the credential-pattern scrub, at this gate's single point of
    output — never re-derived per call site, so a message added later
    cannot forget the pass. Neutralization runs first so a raw control byte
    cannot split a credential pattern's match and dodge the scrub."""
    return _scrub_credential_shaped(_neutralize_control_chars(text))


def _load_parent_coverage(path_str: str) -> dict[str, tuple[list[str], list[str]]]:
    """Return {task_id: (covers, partially-covers)} parsed from the JSON
    file at `path_str`. Raises MalformedParentCoverageError on any
    departure from the documented schema — absent/unreadable file, invalid
    JSON, a non-object top level, a per-task value that is not an object, or
    a `covers`/`partially covers` value that is not a string matching
    `^AC\\d+(, ?AC\\d+)*$`."""
    path = Path(path_str)
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise MalformedParentCoverageError(
            f"--parent-coverage file {path_str!r} could not be read: {e}"
        ) from e

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise MalformedParentCoverageError(
            f"--parent-coverage file {path_str!r} is not valid UTF-8: {e}"
        ) from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise MalformedParentCoverageError(
            f"--parent-coverage file {path_str!r} is not valid JSON: {e}"
        ) from e

    if not isinstance(data, dict):
        raise MalformedParentCoverageError(
            f"--parent-coverage file {path_str!r} must be a JSON object mapping "
            "task id to parent coverage fields, found "
            f"{type(data).__name__}"
        )

    result: dict[str, tuple[list[str], list[str]]] = {}
    for task_id, value in data.items():
        if not isinstance(value, dict):
            raise MalformedParentCoverageError(
                f"--parent-coverage entry for task id {task_id!r} must be a JSON "
                f"object, found {type(value).__name__}"
            )
        fields: list[list[str]] = []
        for key in _PARENT_COVERAGE_FIELD_KEYS:
            field_value = value.get(key)
            if field_value is None:
                fields.append([])
                continue
            if not isinstance(field_value, str) or not _COVERS_TOKEN_RE.match(field_value):
                raise MalformedParentCoverageError(
                    f"--parent-coverage entry for task id {task_id!r} carries a "
                    f"{key!r} value outside the ACn identifier-list shape: "
                    f"{field_value!r}"
                )
            fields.append([t.lstrip(" ") for t in field_value.split(",")])
        result[task_id] = (fields[0], fields[1])
    return result


def _certify_entries(
    entries: list[LedgerEntry],
    parent_map: dict[str, tuple[list[str], list[str]]] | None,
) -> None:
    """Raise LedgerGateViolation on the first integrity violation found, in
    a fixed order: invisible task-id content, a duplicated task id,
    same-entry double-claimed coverage, then — only when `parent_map` is
    supplied — an orphaned entry or a field that contradicts its parent."""
    for entry in entries:
        if not _is_meaningfully_nonempty(entry.task_id):
            raise LedgerGateViolation(
                f"ledger entry's task id carries no visible content: {entry.task_id!r} — "
                f"{_REMEDY}",
                _INVISIBLE_TASK_ID_REASON_CODE,
            )

    seen: dict[str, LedgerEntry] = {}
    for entry in entries:
        if entry.task_id in seen:
            raise LedgerGateViolation(
                f"task id {entry.task_id!r} names two ledger entries — {_REMEDY}",
                _DUPLICATE_TASK_ID_REASON_CODE,
            )
        seen[entry.task_id] = entry

    for entry in entries:
        overlap = [i for i in entry.covers if i in entry.partial]
        if overlap:
            raise LedgerGateViolation(
                f"ledger entry for task id {entry.task_id!r} claims identifier(s) "
                f"{', '.join(overlap)} both fully and partially covered — {_REMEDY}",
                _COVERAGE_CLAIMED_TWICE_REASON_CODE,
            )

    if parent_map is None:
        return

    for entry in entries:
        if entry.task_id not in parent_map:
            raise LedgerGateViolation(
                f"ledger entry for task id {entry.task_id!r} has no corresponding "
                f"entry in the supplied parent coverage map — {_REMEDY}",
                _ORPHANED_ENTRY_REASON_CODE,
            )
        parent_covers, parent_partial = parent_map[entry.task_id]
        for label, entry_field, parent_field in (
            ("covers", entry.covers, parent_covers),
            ("partially covers", entry.partial, parent_partial),
        ):
            if entry_field and set(entry_field) != set(parent_field):
                raise LedgerGateViolation(
                    f"ledger entry for task id {entry.task_id!r} {label} "
                    f"{', '.join(entry_field)} but its parent's {label!r} field "
                    f"reads {', '.join(parent_field) if parent_field else 'none'} — "
                    f"{_REMEDY}",
                    _CONTRADICTS_PARENT_REASON_CODE,
                )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Certify a spec's '## Slices' ledger append-only integrity, "
            "optionally cross-checked against each entry's parent record."
        )
    )
    ap.add_argument(
        "--parent-coverage",
        help=(
            "path to a JSON file mapping task id to that parent's 'covers' "
            "and 'partially covers' fields"
        ),
    )
    args = ap.parse_args(argv)

    raw = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
    if len(raw) > _MAX_STDIN_BYTES:
        _err(
            f"reason: spec body on stdin exceeds the {_MAX_STDIN_BYTES}-byte "
            "cap — refusing rather than certifying a truncated document"
        )
        _err(f"reason-code: {_STDIN_TOO_LARGE_REASON_CODE}")
        return 2

    try:
        spec_body = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        _err(f"reason: spec body on stdin is not valid UTF-8: {e}")
        _err(f"reason-code: {_NON_UTF8_STDIN_REASON_CODE}")
        return 2
    if not spec_body.strip():
        _err("reason: spec body on stdin is empty")
        _err(f"reason-code: {_EMPTY_STDIN_REASON_CODE}")
        return 2

    lines = _COMMONMARK_LINE_RE.split(spec_body)
    if _ends_inside_masked_region(lines):
        _err(
            "reason: the document ends while still inside an open fenced code "
            "block or an open HTML comment — cannot certify what a masked "
            "'## Slices' heading or entry might carry"
        )
        _err(f"reason-code: {_UNTERMINATED_MASKED_REGION_REASON_CODE}")
        return 2

    try:
        entries = parse_ledger_entries(spec_body)
    except DuplicateHeadingError as e:
        _err(f"reason: {_safe(str(e))}")
        _err(f"reason-code: {e.reason_code}")
        return 2
    except (MalformedCoverageTokenError, ValueError) as e:
        # parse_ledger_entries documents raising MalformedCoverageTokenError
        # for a malformed covers/partially-covers grammar, but its
        # within-entry duplicate-identifier case (`covers AC1, AC1`) raises
        # a plain ValueError from the shared `parse_covers` instead — a
        # docstring/behavior gap in that frozen accessor. Caught broadly
        # here rather than re-deriving the distinction: both are the same
        # could-not-certify case from this gate's perspective.
        _err(f"reason: {_safe(str(e))}")
        _err(f"reason-code: {_MALFORMED_TOKEN_REASON_CODE}")
        return 2

    parent_map: dict[str, tuple[list[str], list[str]]] | None = None
    if args.parent_coverage is not None:
        try:
            parent_map = _load_parent_coverage(args.parent_coverage)
        except MalformedParentCoverageError as e:
            _err(f"reason: {_safe(str(e))}")
            _err(f"reason-code: {e.reason_code}")
            return 2

    try:
        _certify_entries(entries, parent_map)
    except LedgerGateViolation as e:
        _err(f"reason: {_safe(str(e))}")
        _err(f"reason-code: {e.reason_code}")
        return 1

    print(f"entries: {len(entries)}")
    for entry in entries:
        covers_str = ", ".join(entry.covers) if entry.covers else "none"
        partial_str = ", ".join(entry.partial) if entry.partial else "none"
        print(f"{entry.task_id}: covers={covers_str}, partial={partial_str}")
    if parent_map is not None:
        print("parent-cross-check: checked")
    else:
        print(
            "parent-cross-check: skipped — no --parent-coverage supplied; "
            "structure certified only"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
