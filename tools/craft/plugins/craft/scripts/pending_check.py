#!/usr/bin/env python3
"""Pending-check writer — closes a slice whose manual check waits on the operator.

Usage:
    pending_check.py --spec <n> --parent <n> --entries <path> [--vault <v>]
    pending_check.py verify --spec <n> --parent <n> [--vault <v>]
    pending_check.py describe --spec <n> --criteria AC2,AC5 [--vault <v>]
    pending_check.py stuck --spec <n> --parent <n> [--vault <v>]

`--entries` names a JSON list of `{criterion, check, how, changed_behaviour}` objects
(`changed_behaviour` a bool, judged by the closing session). The file is the caller's;
the writer never deletes it.

Write mode runs in this order, and the order is the invariant: the spec's
`## Waiting` entry lands first, and only after it has landed (or was already there)
is the slice parent touched.

    1. `<spec>`, `<parent>` and `<vault>` are shape-checked (`followup_gather._shape_ok`)
       and every entry is validated before any `lore` call. A `check` / `how` value
       with a line break, a leading `#` or a leading `- ` is refused
       (`multiline-entry-text`); the waiting gate's raw-line checks stay the second
       line of defence.
    2. The parent is read: it must be `in-progress`, carry the `craft/slice-parent`
       label and be related to the spec (`not-a-slice-parent`), and every entry's
       criterion must be an unconfirmed manual check on `**Covers:**`
       (`unconfirmed_manual_checks`: a `manual-check` observation and no operator
       attestation, or no observation and a spec trailer that is manual check), or
       already on `**Partially covers:**` with a `pending-check` observation (a re-run)
       (`not-an-unconfirmed-manual-check`).
    3. The spec is read fresh. An entry whose key (criterion, `task/<parent>`) is
       present with latest status `waiting` counts as already written; present with
       any other status it is refused (`entry-already-answered`). Missing entries are
       appended (creating `## Waiting` at the end of the body), and the proposed body
       is certified in-process by `waiting_gate.certify` against the body it was
       composed from; a refusal exits with the gate's own code and writes nothing.
    4. The spec is written with `lore record update --base <revision read>`; a lore that
       rejects `--base` as an unrecognised argument is `lore-too-old` (exit 2), not a failed write. After a failed or
       timed-out write the spec is read again: when every entry this run proposed is
       present under its key with status `waiting`, the write is treated as landed and
       the parent is finished. Otherwise the revision decides: changed -> `stale-spec`,
       unchanged -> `spec-write-failed`, and the parent is never touched.
    5. The parent is read again (never the first read's body or revision), each
       pending identifier moves from `**Covers:**` to `**Partially covers:**` (the
       `**Covers:**` line is dropped when it empties), the two lists are certified by
       `covers_gate.main` run in-process against the spec body now on the vault, each
       `manual-check` line becomes `pending-check` with evidence
       `waiting entry on spec/<n>; <original evidence>` (a criterion with no observation
       gets a new line, evidence `waiting entry on spec/<n>; no observation recorded
       before close`), and the parent is written with
       `--base <revision of this read>`. Every refusal here exits 2 and says the spec
       entry already landed: `stale-parent` / `parent-write-failed` / `lore-too-old` for a write that did
       not take, `parent-not-finished` for any other refusal (the parent no longer
       qualifies, cannot be read, or its covers lists are refused). A re-run completes
       the close.

Stdout on success: one `pending: ACn` per entry, then `spec: written|already-present`
and `parent: updated|already-updated`. Entry text is never echoed in a `reason:` line.

Idempotency: a re-run after full success writes nothing; after a spec write that landed
and a parent write that did not, it skips the spec and writes the parent.

`verify` exits 0 (`verified: ACn, ...` or `verified: none`) only when every
`pending-check` observation on the parent has a `waiting` entry on the spec keyed
(criterion, `task/<parent>`); otherwise exit 1 `pending-check-without-entry` naming the
identifiers. `describe` prints `ACn: <criterion words>` for each requested criterion,
cleaned with the check list's display cleaner (`checklist_gather._clean`).

`stuck` decides, from vault state alone, whether the parent's build is finished and its only
unmet close condition is one or more unconfirmed manual checks. Exit 0 prints
`stuck: ACn, ...` (numeric order): the identifiers a close would move to pending checks,
including an identifier an earlier close already moved to `**Partially covers:**` with a
`pending-check` line while the parent never reached `done` (the writer's re-run finishes it).
Exit 1 prints one of `STUCK_REASON_CODES`: not-a-slice-parent (not labelled, not related to
the spec, or a status other than in-progress / blocked), parent-blocked (a labelled, related
parent whose status is blocked: only an in-progress parent is certified), push-failed (the parent carries
`craft/push=failed`), build-unfinished (a child not terminal — `done`, `dropped` or `superseded`, as lore's
completion guard counts them — or an `## End Phases` checklist that is not Phases 1-5 all present
and ticked beside exactly one unticked Phase 6, or no such checklist), close-gap-not-a-manual-check
(a covered identifier with no observation whose spec trailer is not manual check),
no-unconfirmed-manual-check (nothing to move), lore-too-old (`record update --help` does not
accept `--base`; probed first, after the name shape check, and nothing else is called).
Exit 2 is a read failure, which includes a spec that is not found, ambiguous or not a spec when
no `--vault` is given; every exit 1 carries a code from `STUCK_REASON_CODES`, and every caller
treats exit 2 as "not stuck". No criterion or record
text appears in a `reason:` line. `unconfirmed_manual_checks` is the eligibility rule: a
covered identifier is an unconfirmed manual check when it has a `manual-check` observation and
no attestation, or has no observation and the spec's `*Verified by:*` trailer for it
normalizes to manual check (`criterion_gate._check_trailer`).

Exit codes: 0 done; 1 refused (stable `reason-code:`); 2 could not complete.
Reason codes of this script: invalid-name, invalid-arguments, invalid-entries,
invalid-criteria, multiline-entry-text, not-a-slice-parent,
not-an-unconfirmed-manual-check, entry-already-answered, pending-check-without-entry,
spec-not-found, parent-not-found, spec-write-failed, stale-spec, parent-write-failed,
stale-parent, parent-not-finished, unreadable-parent, covers-refused, plus `waiting_gate`'s, `covers_gate`'s
and `followup_gather`'s own codes (lore-missing, lore-timeout, lore-failed, ...).

The shared layer is refused (`shared-layer-prior`): `show` XML-escapes its bodies, so a
body composed from it would write escaped text.

Known boundary: this is craft's write path; a raw `lore record update` skips it, as it
skips the waiting gate.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from contextlib import redirect_stderr
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import checklist_gather  # noqa: E402
import covers_gate  # noqa: E402
import criterion_gate  # noqa: E402
import followup_gather as gather  # noqa: E402
import markdown_section  # noqa: E402
import observation_gate as obs  # noqa: E402
import spec_write as sw  # noqa: E402
import waiting  # noqa: E402
import waiting_gate  # noqa: E402
from gate_plumbing import Refusal, emit_refusal  # noqa: E402

lore_runner = gather.lore_runner

PROG = "pending-check"
_CRITERION_RE = re.compile(r"AC[1-9][0-9]{0,8}")
_ENTRY_KEYS = {"criterion", "check", "how", "changed_behaviour"}
_LINE_BREAKS = "\n\r\x0b\x0c\x85\u2028\u2029"
_PENDING = "pending-check"
_MANUAL = "manual-check"
_PARENT_WRITE_CODES = ("stale-parent", "parent-write-failed", "lore-too-old")
_NO_OBSERVATION = "no observation recorded before close"
STUCK_REASON_CODES = (
    "not-a-slice-parent",
    "build-unfinished",
    "close-gap-not-a-manual-check",
    "no-unconfirmed-manual-check",
    "push-failed",
    "lore-too-old",
    "parent-blocked",
)
# The statuses lore's parent-completion guard treats as settled (`TERMINAL_STATUSES` in
# lore's record/graph.py); mirrored because craft does not import lore.
_TERMINAL_STATUSES = frozenset({"done", "dropped", "superseded"})
_END_PHASES_HEADING = "## End Phases"
_DUPLICATE_END_PHASES_CODE = "duplicate-end-phases-section"
_PHASE_LINE_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+Phase\s+([0-9]+)\b")
_CLOSE_PHASE = 6
_BUILD_PHASES = range(1, _CLOSE_PHASE)
_HISTORY_WORDS = "closed with this check not yet carried out"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise Refusal("invalid arguments — see the usage in the script docstring", "invalid-arguments", 2)


def _load_entries(path: str) -> list[dict]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        raise Refusal("--entries could not be read as a JSON list", "invalid-entries", 2)
    if not isinstance(raw, list) or not raw:
        raise Refusal("--entries must be a non-empty JSON list", "invalid-entries", 2)
    seen: set[str] = set()
    for item in raw:
        ok = (
            isinstance(item, dict)
            and set(item) == _ENTRY_KEYS
            and isinstance(item["criterion"], str)
            and _CRITERION_RE.fullmatch(item["criterion"])
            and isinstance(item["check"], str)
            and isinstance(item["how"], str)
            and isinstance(item["changed_behaviour"], bool)
            and item["criterion"] not in seen
        )
        if not ok:
            raise Refusal(
                "--entries must hold objects with exactly criterion (an ACn, once), check, "
                "how and changed_behaviour (a bool)",
                "invalid-entries",
                2,
            )
        seen.add(item["criterion"])
    return raw


def _refuse_structural_text(entries: list[dict]) -> None:
    for item in entries:
        for field in ("check", "how"):
            value = item[field]
            lead = value.lstrip()
            if any(ch in value for ch in _LINE_BREAKS) or lead.startswith("#") or lead.startswith("- "):
                raise Refusal(
                    f"the {field} for {item['criterion']} carries a line break, a leading '#' "
                    "or a leading '- ' — write it as one plain line",
                    "multiline-entry-text",
                    1,
                )


def _pending_line(criterion: str, spec: str, evidence: str) -> str:
    return f"- **{criterion}** — {_PENDING} — waiting entry on spec/{spec}; {evidence}"


class _Parent:
    """A parent body read through observation_gate's own parsers."""

    def __init__(self, body: str):
        self.body = body
        try:
            self.lines, self.masked, self.covers, self.partial = obs.read_covers_fields(body)
            self.observations = obs.parse_observations(self.lines, self.masked)
            self.attestations = obs.parse_attestations(self.lines, self.masked)
            self.obs_lines = self._observation_lines()
        except ValueError:
            raise Refusal(
                "the parent's covers fields or observations cannot be read unambiguously",
                "unreadable-parent",
                2,
            )

    def _observation_lines(self) -> dict[str, int]:
        start = covers_gate._find_unique_heading(
            self.lines, self.masked, obs._OBSERVATIONS_HEADING_RE, obs._OBSERVATIONS_HEADING,
            obs._DUPLICATE_OBSERVATIONS_SECTION_REASON_CODE,
        )
        if start is None:
            return {}
        found = obs._scan_section(self.lines, self.masked, start, obs._OBSERVATION_LINE_RE)
        return {identifier: idx for identifier, (_rest, idx) in found.items()}

    def _field_index(self, field_re) -> int | None:
        for i, line in enumerate(self.lines):
            if not self.masked[i] and field_re.match(line):
                return i
        return None

    def classify(self, criteria: list[str], spec_body: str) -> list[str]:
        """The criteria still to move; the rest are already pending. Refuses any
        criterion that is neither. Eligibility is `unconfirmed_manual_checks`'s."""
        unconfirmed = set(unconfirmed_manual_checks(self.body, spec_body)[0])
        todo = []
        for criterion in criteria:
            method = self.observations.get(criterion, ("", ""))[0]
            if criterion in self.covers and criterion in unconfirmed:
                todo.append(criterion)
            elif criterion in self.partial and method == _PENDING:
                continue
            else:
                raise Refusal(
                    f"{criterion} is not an unconfirmed manual check on this parent's "
                    "'**Covers:**' (or an already pending one on '**Partially covers:**')",
                    "not-an-unconfirmed-manual-check",
                    1,
                )
        return todo

    def moved(self, moving: list[str], spec: str) -> tuple[str, list[str], list[str]]:
        """(new body, covers left, partially covers now)."""
        lines = list(self.lines)
        unrecorded: list[str] = []
        for criterion in moving:
            if criterion in self.obs_lines:
                evidence = self.observations[criterion][1]
                lines[self.obs_lines[criterion]] = _pending_line(criterion, spec, evidence)
            else:
                unrecorded.append(_pending_line(criterion, spec, _NO_OBSERVATION))
        covers = [c for c in self.covers if c not in moving]
        partial = self.partial + moving
        covers_idx = self._field_index(obs._COVERS_FIELD_RE)
        partial_idx = self._field_index(obs._PARTIALLY_COVERS_FIELD_RE)
        partial_line = f"**Partially covers:** {', '.join(partial)}"
        if partial_idx is not None:
            lines[partial_idx] = partial_line
            if covers:
                lines[covers_idx] = f"**Covers:** {', '.join(covers)}"
            else:
                del lines[covers_idx]
        elif covers:
            lines[covers_idx] = f"**Covers:** {', '.join(covers)}"
            lines.insert(covers_idx + 1, partial_line)
        else:
            lines[covers_idx] = partial_line
        body = "\n".join(lines)
        if unrecorded:
            body = sw.append_to_section(
                body, obs._OBSERVATIONS_HEADING, obs._DUPLICATE_OBSERVATIONS_SECTION_REASON_CODE, unrecorded
            )
        return body, covers, partial


def _is_slice_parent(sidecar: dict, spec: str, statuses: tuple[str, ...]) -> bool:
    """A task in one of `statuses`, labelled a slice parent, related to `spec`."""
    related = sidecar.get("related")
    specs = related.get("spec") if isinstance(related, dict) else None
    linked = {v.removeprefix("spec/") for v in specs if isinstance(v, str)} if isinstance(specs, list) else set()
    return (
        sidecar.get("kind") == "task"
        and sidecar.get("status") in statuses
        and gather.SLICE_LABEL in gather._labels(sidecar)
        and spec in linked
    )


def _check_parent(sidecar: dict, spec: str) -> None:
    if not _is_slice_parent(sidecar, spec, ("in-progress",)):
        raise Refusal(
            "the parent is not an in-progress slice parent related to this spec",
            "not-a-slice-parent",
            1,
        )


def _entry_lines(item: dict, parent: str, today: str) -> list[str]:
    return [
        f"- **{item['criterion']}.** pending check — task/{parent}",
        f"  - check: {item['check']}",
        f"  - how: {item['how']}",
        f"  - changed behaviour: {'yes' if item['changed_behaviour'] else 'no'}",
        f"  - {today} waiting: {_HISTORY_WORDS}",
    ]


def _with_entries(body: str, items: list[dict], parent: str) -> str:
    today = waiting_gate._today().isoformat()
    block: list[str] = []
    for item in items:
        if block:
            block.append("")
        block += _entry_lines(item, parent, today)
    return sw.append_to_section(body, waiting.HEADING, waiting.DUPLICATE_HEADING_REASON_CODE, block, separated=True)


def _certify_covers(spec_body: str, covers: list[str], partial: list[str]) -> None:
    argv = []
    if covers:
        argv += ["--covers", ", ".join(covers)]
    argv += ["--partial-covers", ", ".join(partial)]
    saved = sys.stdin
    sys.stdin = io.TextIOWrapper(io.BytesIO(spec_body.encode("utf-8")), encoding="utf-8")
    captured = io.StringIO()
    try:
        with redirect_stderr(captured):
            rc = covers_gate.main(argv)
    finally:
        sys.stdin = saved
    if rc == 0:
        return
    codes = re.findall(r"reason-code: (\S+)", captured.getvalue())
    raise Refusal(
        "covers_gate refused the moved identifier lists", codes[-1] if codes else "covers-refused",
        rc if rc in (1, 2) else 2,
    )


def _precertify_covers(state: _Parent, moving: list[str], spec: str, spec_body: str) -> None:
    """The lists the parent will carry, certified before the spec entry is written so a
    criterion the spec does not declare leaves nothing behind."""
    if moving:
        _, covers, partial = state.moved(moving, spec)
        _certify_covers(spec_body, covers, partial)


def _finish_parent(runner, vault, spec, parent, criteria, spec_body) -> str:
    sidecar, body, revision, _ = sw.read_record(runner, vault, f"task/{parent}", "parent-not-found")
    _check_parent(sidecar, spec)
    state = _Parent(body)
    todo = state.classify(criteria, spec_body)
    if not todo:
        return "already-updated"
    new_body, covers, partial = state.moved(todo, spec)
    _certify_covers(spec_body, covers, partial)
    done = sw.write_record(runner, sw.update_argv(f"task/{parent}", vault, revision), new_body)
    if done is not None and done.returncode == 0:
        return "updated"
    sw.refuse_if_base_rejected(done)
    if sw.revision_changed(runner, vault, f"task/{parent}", revision, "parent-not-found"):
        raise Refusal(f"task/{parent} changed after it was read", "stale-parent", 2)
    raise Refusal(f"lore could not write task/{parent}", "parent-write-failed", 2)


def write_mode(args, runner) -> int:
    sw.check_names(args.spec, args.parent, args.vault)
    entries = _load_entries(args.entries)
    _refuse_structural_text(entries)
    criteria = [item["criterion"] for item in entries]
    vault = sw.resolve_vault(runner, args.spec, args.vault)

    spec_id = f"spec/{args.spec}"
    parent_sidecar, parent_body, _, _ = sw.read_record(runner, vault, f"task/{args.parent}", "parent-not-found")
    _check_parent(parent_sidecar, args.spec)
    _, spec_body, spec_rev, _ = sw.read_record(runner, vault, spec_id, "spec-not-found")
    state = _Parent(parent_body)
    moving = state.classify(criteria, spec_body)
    present = sw.spec_entries(spec_body)
    missing = []
    for item in entries:
        existing = present.get((item["criterion"], args.parent))
        if existing is None:
            missing.append(item)
        elif existing.status != "waiting":
            raise Refusal(
                f"{item['criterion']} already has an entry on the spec that was answered",
                "entry-already-answered",
                1,
            )

    spec_status = "already-present"
    if missing:
        drafted = _with_entries(spec_body, missing, args.parent)
        spec_body = sw.certify_and_write(
            runner, vault, spec_id, spec_body, spec_rev, drafted,
            [(item["criterion"], args.parent) for item in missing],
            before_write=lambda: _precertify_covers(state, moving, args.spec, drafted),
        )
        spec_status = "written"

    try:
        parent_status = _finish_parent(runner, vault, args.spec, args.parent, criteria, spec_body)
    except Refusal as r:
        code, exit_code = r.reason_code, r.exit_code
        if code not in _PARENT_WRITE_CODES:
            code, exit_code = "parent-not-finished", 2
        raise Refusal(
            f"{r} — the spec entry already landed on {spec_id}; a re-run completes the close",
            code,
            exit_code,
        )
    for criterion in criteria:
        print(f"pending: {criterion}")
    print(f"spec: {spec_status}")
    print(f"parent: {parent_status}")
    return 0


def verify_mode(args, runner) -> int:
    sw.check_names(args.spec, args.parent, args.vault)
    vault = sw.resolve_vault(runner, args.spec, args.vault)
    _, parent_body, _, _ = sw.read_record(runner, vault, f"task/{args.parent}", "parent-not-found")
    pending = [i for i, (method, _e) in _Parent(parent_body).observations.items() if method == _PENDING]
    _, spec_body, _, _ = sw.read_record(runner, vault, f"spec/{args.spec}", "spec-not-found")
    present = sw.spec_entries(spec_body)
    unbacked = [
        i for i in pending
        if (i, args.parent) not in present or present[(i, args.parent)].status != "waiting"
    ]
    if unbacked:
        raise Refusal(
            f"pending-check on {', '.join(unbacked)} has no waiting entry on spec/{args.spec} "
            f"keyed task/{args.parent}",
            "pending-check-without-entry",
            1,
        )
    print(f"verified: {', '.join(pending) if pending else 'none'}")
    return 0


def describe_mode(args, runner) -> int:
    sw.check_names(args.spec, args.vault)
    try:
        wanted = covers_gate.parse_covers(args.criteria, "--criteria")
    except ValueError:
        raise Refusal("--criteria must be ACn identifiers separated by commas", "invalid-criteria", 2)
    vault = sw.resolve_vault(runner, args.spec, args.vault)
    _, body, _, layer = sw.read_record(runner, vault, f"spec/{args.spec}", "spec-not-found", personal_only=False)
    if layer == "shared":
        body = checklist_gather._unescape(body)
    try:
        words = checklist_gather._criteria(body)
    except covers_gate.DuplicateHeadingError as err:
        raise Refusal(str(err), err.reason_code, 2)
    for criterion in wanted:
        text = words.get(criterion)
        shown = checklist_gather._clean(text) if text is not None else checklist_gather.NOT_FOUND
        print(f"{criterion}: {shown}")
    return 0


def _end_phases_finished(state: _Parent) -> bool:
    """Phases 1-5 all present and every line of them ticked, and exactly one Phase 6 line,
    unticked."""
    try:
        located = markdown_section.section_range(
            state.lines, state.masked, _END_PHASES_HEADING, _DUPLICATE_END_PHASES_CODE
        )
    except covers_gate.DuplicateHeadingError:
        raise Refusal("the parent has more than one '## End Phases' section", "unreadable-parent", 2)
    if located is None:
        return False
    start, end = located
    phases: dict[int, list[bool]] = {}
    for i in range(start + 1, end):
        m = None if state.masked[i] else _PHASE_LINE_RE.match(state.lines[i])
        if m:
            phases.setdefault(int(m.group(2)), []).append(m.group(1) != " ")
    before = [ticked for n, marks in phases.items() if n < _CLOSE_PHASE for ticked in marks]
    return (
        all(n in phases for n in _BUILD_PHASES)
        and all(before)
        and phases.get(_CLOSE_PHASE) == [False]
    )


def _criterion_methods(spec_body: str) -> dict[str, str | None]:
    """Each declared criterion's trailer method (None when its trailer is not one method)."""
    try:
        entries = covers_gate.parse_criteria_with_text(spec_body)
    except (ValueError, RecursionError):
        raise Refusal("the spec's criteria cannot be read unambiguously", "unreadable-spec", 2)
    return {
        identifier: criterion_gate._check_trailer(text)[1]
        for identifier, text in entries
        if identifier is not None
    }


def unconfirmed_manual_checks(parent_body: str, spec_body: str) -> tuple[list[str], list[str]]:
    """(unconfirmed manual checks, close gaps) among the parent's covered identifiers, each
    in numeric order. A covered identifier is an unconfirmed manual check when it has a
    `manual-check` observation and no attestation, or no observation and its spec trailer
    normalizes to manual check; one already moved to `**Partially covers:**` under a
    `pending-check` observation counts too (a close that did not finish). A covered
    identifier with no observation and any other trailer is a close gap."""
    state = _Parent(parent_body)
    methods = _criterion_methods(spec_body)
    unconfirmed: list[str] = []
    gaps: list[str] = []
    for criterion in state.covers:
        observed = state.observations.get(criterion)
        if observed is None:
            (unconfirmed if methods.get(criterion) == _MANUAL else gaps).append(criterion)
        elif observed[0] == _MANUAL and not obs._is_meaningfully_nonempty(
            state.attestations.get(criterion, "")
        ):
            unconfirmed.append(criterion)
    unconfirmed += [c for c in state.partial if state.observations.get(c, ("", ""))[0] == _PENDING]
    return sorted(unconfirmed, key=_criterion_number), sorted(gaps, key=_criterion_number)


def _criterion_number(criterion: str) -> int:
    return int(criterion[2:])


def _children_statuses(runner, vault: str, parent: str) -> list[str]:
    try:
        rows = gather._call_json(runner, ["task", "list", "--vault", vault, "--json"])
    except gather.GatherError as err:
        raise sw.refusal_from(err)
    named = (
        {r["name"]: r for r in rows if isinstance(r, dict) and isinstance(r.get("name"), str)}
        if isinstance(rows, list)
        else {}
    )
    row = named.get(parent)
    children = row.get("children") if row else None
    if not isinstance(children, list):
        raise Refusal(f"lore's task list does not hold task/{parent}", "malformed-sidecar", 2)
    statuses = []
    for child in children:
        status = named.get(child, {}).get("status") if isinstance(child, str) else None
        if not isinstance(status, str):
            raise Refusal("lore's task list does not describe every child of the parent", "malformed-sidecar", 2)
        statuses.append(status)
    return statuses


def _refuse_stuck(reason: str, code: str) -> Refusal:
    assert code in STUCK_REASON_CODES, code
    return Refusal(reason, code, 1)


def _read_or_fail(runner, vault: str, record_id: str, missing_code: str, **kwargs):
    try:
        return sw.read_record(runner, vault, record_id, missing_code, **kwargs)
    except Refusal as r:
        if r.reason_code == missing_code:
            raise Refusal(str(r), r.reason_code, 2)
        raise


def stuck_mode(args, runner) -> int:
    sw.check_names(args.spec, args.parent, args.vault)
    try:
        probe = gather._call(runner, ["record", "update", "--help"])
    except gather.GatherError as err:
        raise sw.refusal_from(err)
    if probe.returncode != 0:
        raise Refusal("lore could not describe 'record update'", "lore-failed", 2)
    if "--base" not in probe.stdout:
        raise _refuse_stuck("this lore's 'record update' has no --base, so no close could write", "lore-too-old")
    vault = sw.resolve_vault(runner, args.spec, args.vault, refusal_exit=2)
    sidecar, parent_body, _, _ = _read_or_fail(runner, vault, f"task/{args.parent}", "parent-not-found")
    if _is_slice_parent(sidecar, args.spec, ("blocked",)):
        raise _refuse_stuck("the parent is blocked; only an in-progress parent is certified", "parent-blocked")
    if not _is_slice_parent(sidecar, args.spec, ("in-progress",)):
        raise _refuse_stuck("the parent is not an in-progress slice parent related to this spec", "not-a-slice-parent")
    if gather._labels(sidecar).get("craft/push") == "failed":
        raise _refuse_stuck("the parent's last push failed and needs settling by hand", "push-failed")
    if any(status not in _TERMINAL_STATUSES for status in _children_statuses(runner, vault, args.parent)) or not (
        _end_phases_finished(_Parent(parent_body))
    ):
        raise _refuse_stuck("the parent's build is not finished", "build-unfinished")
    _, spec_body, _, layer = _read_or_fail(
        runner, vault, f"spec/{args.spec}", "spec-not-found", personal_only=False
    )
    if layer == "shared":
        spec_body = checklist_gather._unescape(spec_body)
    unconfirmed, gaps = unconfirmed_manual_checks(parent_body, spec_body)
    if gaps:
        raise _refuse_stuck(
            "a covered criterion has no observation and is not a manual check", "close-gap-not-a-manual-check"
        )
    if not unconfirmed:
        raise _refuse_stuck("no covered criterion is an unconfirmed manual check", "no-unconfirmed-manual-check")
    print(f"stuck: {', '.join(unconfirmed)}")
    return 0


def _parse(argv: list[str]):
    mode = argv[0] if argv and argv[0] in ("verify", "describe", "stuck") else "write"
    ap = _Parser(prog="pending_check.py", description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--vault")
    if mode != "describe":
        ap.add_argument("--parent", required=True)
    if mode == "write":
        ap.add_argument("--entries", required=True)
    if mode == "describe":
        ap.add_argument("--criteria", required=True)
    args = ap.parse_args(argv[1:] if mode != "write" else argv)
    return mode, args


def main(argv: list[str], runner=None) -> int:
    runner = runner or lore_runner
    try:
        mode, args = _parse(argv)
        modes = {"write": write_mode, "verify": verify_mode, "describe": describe_mode, "stuck": stuck_mode}
        return modes[mode](args, runner)
    except Refusal as r:
        return emit_refusal(PROG, r)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
