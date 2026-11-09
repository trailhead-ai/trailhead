#!/usr/bin/env python3
"""Pending-check writer — closes a slice whose manual check waits on the operator.

Usage:
    pending_check.py --spec <n> --parent <n> --entries <path> [--vault <v>]
    pending_check.py verify --spec <n> --parent <n> [--vault <v>]
    pending_check.py describe --spec <n> --criteria AC2,AC5 [--vault <v>]

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
       criterion must be on `**Covers:**` with a `manual-check` observation and no
       operator attestation, or already on `**Partially covers:**` with a
       `pending-check` observation (a re-run) (`not-an-unconfirmed-manual-check`).
    3. The spec is read fresh. An entry whose key (criterion, `task/<parent>`) is
       present with latest status `waiting` counts as already written; present with
       any other status it is refused (`entry-already-answered`). Missing entries are
       appended (creating `## Waiting` at the end of the body), and the proposed body
       is certified in-process by `waiting_gate.certify` against the body it was
       composed from; a refusal exits with the gate's own code and writes nothing.
    4. The spec is written with `lore record update --base <revision read>`. After a failed or
       timed-out write the spec is read again: when every entry this run proposed is
       present under its key with status `waiting`, the write is treated as landed and
       the parent is finished. Otherwise the revision decides: changed -> `stale-spec`,
       unchanged -> `spec-write-failed`, and the parent is never touched.
    5. The parent is read again (never the first read's body or revision), each
       pending identifier moves from `**Covers:**` to `**Partially covers:**` (the
       `**Covers:**` line is dropped when it empties), the two lists are certified by
       `covers_gate.main` run in-process against the spec body now on the vault, each
       `manual-check` line becomes `pending-check` with evidence
       `waiting entry on spec/<n>; <original evidence>`, and the parent is written with
       `--base <revision of this read>`. Every refusal here exits 2 and says the spec
       entry already landed: `stale-parent` / `parent-write-failed` for a write that did
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
import subprocess
import sys
from contextlib import redirect_stderr
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import checklist_gather  # noqa: E402
import covers_gate  # noqa: E402
import followup_gather as gather  # noqa: E402
import markdown_section  # noqa: E402
import observation_gate as obs  # noqa: E402
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
_PARENT_WRITE_CODES = ("stale-parent", "parent-write-failed")
_HISTORY_WORDS = "closed with this check not yet carried out"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise Refusal("invalid arguments — see the usage in the script docstring", "invalid-arguments", 2)


def _check_names(*names: str | None) -> None:
    for name in names:
        if name is not None and not gather._shape_ok(name):
            raise Refusal(
                "a spec, parent or vault name has a shape that cannot be used",
                "invalid-name",
                2,
            )


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


def _refusal_from(err: gather.GatherError, exit_code: int = 2) -> Refusal:
    return Refusal(f"lore could not be read: {err.reason}", err.reason, exit_code)


def _read(runner, vault: str, record_id: str, missing_code: str, *, personal_only: bool = True):
    """(sidecar, body, revision, layer) of `record_id`."""
    try:
        done = gather._call(runner, ["record", "show", "--json", "--vault", vault, record_id])
        if done.returncode != 0:
            if "record not found" in done.stderr:
                raise Refusal(f"{record_id} was not found in the vault", missing_code, 1)
            raise Refusal(f"lore could not show {record_id}", "lore-failed", 2)
        sidecar = gather._parse_sidecar(done.stdout)
    except gather.GatherError as err:
        raise _refusal_from(err)
    doc = json.loads(done.stdout)
    body, revision, layer = doc.get("body"), doc.get("revision"), doc.get("layer")
    if not isinstance(body, str) or not isinstance(revision, str) or not revision:
        raise Refusal(f"lore's reply for {record_id} is malformed", "malformed-sidecar", 2)
    if personal_only and layer != "personal":
        raise Refusal(
            f"{record_id} is not in a personal-layer vault: in a shared vault 'show' "
            "XML-escapes the body, so a body composed from it would write escaped text",
            "shared-layer-prior",
            2,
        )
    return sidecar, body, revision, layer


def _resolve_vault(runner, spec: str, vault: str | None) -> str:
    if vault is not None:
        return vault
    try:
        return gather.resolve_spec(runner, spec)[0]
    except gather.GatherError as err:
        raise _refusal_from(err, 1 if err.reason in ("spec-not-found", "spec-ambiguous", "not-a-spec") else 2)


def _spec_entries(body: str) -> dict[tuple[str, str | None], waiting.WaitingEntry]:
    try:
        return {e.key: e for e in waiting.parse_waiting(body)}
    except (waiting.WaitingParseError, covers_gate.DuplicateHeadingError):
        raise Refusal(
            "the spec's own '## Waiting' section does not parse — repair it with a direct "
            "edit before writing again",
            "malformed-prior-section",
            2,
        )


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

    def classify(self, criteria: list[str]) -> list[str]:
        """The criteria still to move; the rest are already pending. Refuses any
        criterion that is neither."""
        todo = []
        for criterion in criteria:
            method = self.observations.get(criterion, ("", ""))[0]
            attested = obs._is_meaningfully_nonempty(self.attestations.get(criterion, ""))
            if criterion in self.covers and method == _MANUAL and not attested:
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
        for criterion in moving:
            idx = self.obs_lines[criterion]
            evidence = self.observations[criterion][1]
            lines[idx] = f"- **{criterion}** — {_PENDING} — waiting entry on spec/{spec}; {evidence}"
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
        return "\n".join(lines), covers, partial


def _check_parent(sidecar: dict, spec: str) -> None:
    related = sidecar.get("related")
    specs = related.get("spec") if isinstance(related, dict) else None
    linked = {v.removeprefix("spec/") for v in specs if isinstance(v, str)} if isinstance(specs, list) else set()
    if (
        sidecar.get("kind") != "task"
        or sidecar.get("status") != "in-progress"
        or gather.SLICE_LABEL not in gather._labels(sidecar)
        or spec not in linked
    ):
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
    lines, masked = markdown_section.split_body(body)
    located = markdown_section.section_range(
        lines, masked, waiting.HEADING, waiting.DUPLICATE_HEADING_REASON_CODE
    )
    if located is None:
        return body.rstrip("\n") + "\n\n" + waiting.HEADING + "\n\n" + "\n".join(block) + "\n"
    start, end = located
    last = start
    for i in range(start + 1, end):
        if lines[i].strip():
            last = i
    lines[last + 1 : last + 1] = [""] + block
    return "\n".join(lines)


def _write(runner, argv: list[str], stdin: str):
    try:
        return runner(argv, stdin=stdin)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _update_argv(record_id: str, vault: str, revision: str) -> list[str]:
    return ["record", "update", record_id, "--vault", vault, "--base", revision]


def _revision_changed(runner, vault: str, record_id: str, revision: str, missing: str) -> bool:
    try:
        return _read(runner, vault, record_id, missing)[2] != revision
    except Refusal:
        return False


def _landed_body(runner, vault: str, spec_id: str, missing: list[dict], parent: str) -> str | None:
    """The spec body when a write that reported failure had in fact committed every
    entry this run proposed (each present under its key, still `waiting`), else None."""
    try:
        body = _read(runner, vault, spec_id, "spec-not-found")[1]
        present = _spec_entries(body)
    except Refusal:
        return None
    for item in missing:
        existing = present.get((item["criterion"], parent))
        if existing is None or existing.status != "waiting":
            return None
    return body


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
    sidecar, body, revision, _ = _read(runner, vault, f"task/{parent}", "parent-not-found")
    _check_parent(sidecar, spec)
    state = _Parent(body)
    todo = state.classify(criteria)
    if not todo:
        return "already-updated"
    new_body, covers, partial = state.moved(todo, spec)
    _certify_covers(spec_body, covers, partial)
    done = _write(runner, _update_argv(f"task/{parent}", vault, revision), new_body)
    if done is not None and done.returncode == 0:
        return "updated"
    if _revision_changed(runner, vault, f"task/{parent}", revision, "parent-not-found"):
        raise Refusal(f"task/{parent} changed after it was read", "stale-parent", 2)
    raise Refusal(f"lore could not write task/{parent}", "parent-write-failed", 2)


def write_mode(args, runner) -> int:
    _check_names(args.spec, args.parent, args.vault)
    entries = _load_entries(args.entries)
    _refuse_structural_text(entries)
    criteria = [item["criterion"] for item in entries]
    vault = _resolve_vault(runner, args.spec, args.vault)

    spec_id = f"spec/{args.spec}"
    parent_sidecar, parent_body, _, _ = _read(runner, vault, f"task/{args.parent}", "parent-not-found")
    _check_parent(parent_sidecar, args.spec)
    state = _Parent(parent_body)
    moving = state.classify(criteria)

    _, spec_body, spec_rev, _ = _read(runner, vault, spec_id, "spec-not-found")
    present = _spec_entries(spec_body)
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
        proposed = _with_entries(spec_body, missing, args.parent)
        waiting_gate.certify(spec_body, proposed)
        _precertify_covers(state, moving, args.spec, proposed)
        done = _write(runner, _update_argv(spec_id, vault, spec_rev), proposed)
        if done is None or done.returncode != 0:
            landed = _landed_body(runner, vault, spec_id, missing, args.parent)
            if landed is None:
                if _revision_changed(runner, vault, spec_id, spec_rev, "spec-not-found"):
                    raise Refusal(f"{spec_id} changed after it was read", "stale-spec", 2)
                raise Refusal(f"lore could not write {spec_id}", "spec-write-failed", 2)
            proposed = landed
        spec_body, spec_status = proposed, "written"

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
    _check_names(args.spec, args.parent, args.vault)
    vault = _resolve_vault(runner, args.spec, args.vault)
    _, parent_body, _, _ = _read(runner, vault, f"task/{args.parent}", "parent-not-found")
    pending = [i for i, (method, _e) in _Parent(parent_body).observations.items() if method == _PENDING]
    _, spec_body, _, _ = _read(runner, vault, f"spec/{args.spec}", "spec-not-found")
    present = _spec_entries(spec_body)
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
    _check_names(args.spec, args.vault)
    try:
        wanted = covers_gate.parse_covers(args.criteria, "--criteria")
    except ValueError:
        raise Refusal("--criteria must be ACn identifiers separated by commas", "invalid-criteria", 2)
    vault = _resolve_vault(runner, args.spec, args.vault)
    _, body, _, layer = _read(runner, vault, f"spec/{args.spec}", "spec-not-found", personal_only=False)
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


def _parse(argv: list[str]):
    mode = argv[0] if argv and argv[0] in ("verify", "describe") else "write"
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
        return {"write": write_mode, "verify": verify_mode, "describe": describe_mode}[mode](args, runner)
    except Refusal as r:
        return emit_refusal(PROG, r)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
