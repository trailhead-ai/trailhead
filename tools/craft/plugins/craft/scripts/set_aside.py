#!/usr/bin/env python3
"""Set-aside writer — sets a candidate criterion aside from a slice pass or a build session.

Usage:
    set_aside.py --spec <n> --entries <path> [--vault <v>]
    set_aside.py --spec <n> --parent <n> --entries <path> [--vault <v>]

`--entries` names a JSON list of `{criterion, waits_on}` objects: `criterion` an `ACn` (once
each), `waits_on` one plain line naming the outside thing and what unblocks it, at most
`MAX_WAITS_ON_CHARS` characters. The file is the caller's; the writer never deletes it.

Each entry lands on the spec's `## Waiting` section in the set-aside shape:

    - **AC17.** set aside
      - waits on: <waits_on>
      - <today> waiting: <the writer's fixed wording, never the caller's text>

The order is: names and entries are checked before any lore call (`invalid-name`,
`invalid-arguments`, `invalid-entries`; `waits_on` with a line break, a leading `#` or a
leading `- ` is `multiline-entry-text`; over the cap is `waits-on-too-long`); the spec is read
fresh with its revision; an entry that already has a set-aside entry is `already-present` when
its status is `waiting` and `entry-exists` under any other status; every other criterion must be
a current candidate by the candidate set's own derivation, run in-process over the spec body and
`lore record criteria` (`not-a-candidate`: undeclared, fully covered, removed or closed). The
proposed body is certified by `waiting_gate.certify` against the body it was composed from (its
refusals exit with the gate's own code) and written with `lore record update --base <revision
read>`; a failed or timed-out write is decided by re-reading (landed, `stale-spec` or
`spec-write-failed`), as `spec_write.certify_and_write` does for every `## Waiting` writer.
A re-run whose entries are all present with status `waiting` writes nothing. The history line's
words are the writer's own, one fixed text per mode (a slice pass or a build session).

With `--parent` (a build session): the parent must be an `in-progress` slice parent related to
the spec (`not-a-slice-parent`; a parent that is not found is `parent-not-found`), every
criterion must be on its `**Covers:**` or `**Partially covers:**` (`not-on-this-slice`), and the
set-aside must leave it covering something (`would-empty-slice`); all three are refused before
anything is written. Then the spec entry lands first, exactly as above. Then the parent is read
again (never the first read): each criterion is taken off whichever coverage list holds it (an
emptied line is dropped) and its `## Criterion observations` line is removed (a leftover one
makes the observation gate refuse the close), the remaining lists are certified by
`covers_gate` in-process against the spec now on the vault, and the parent is written with
`--base` of that re-read. A parent write that does not take is `stale-parent` or
`parent-write-failed` (exit 2) and says the spec entry already landed: a re-run finds the entry
present and finishes the parent. A criterion already off the parent at the re-read leaves it
unwritten (`parent: already-updated`).

Stdout on success: one `set aside: ACn` per entry, then `spec: written|already-present`, and
with `--parent` `parent: updated|already-updated`. Entry text is never echoed in a `reason:`
line.

Exit codes: 0 done; 1 refused (stable `reason-code:`); 2 could not complete.
`REASON_CODES` declares the codes this script raises itself; the waiting gate's and lore's own
codes (credential-shaped-text, lore-missing, lore-failed, ...) pass through.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import candidate_set  # noqa: E402
import followup_gather as gather  # noqa: E402
import parent_coverage  # noqa: E402
import spec_write as sw  # noqa: E402
import waiting  # noqa: E402
import waiting_gate  # noqa: E402
from gate_plumbing import Refusal, emit_refusal  # noqa: E402

PROG = "set-aside"
MAX_WAITS_ON_CHARS = 300
REASON_CODES = frozenset(
    {
        "invalid-name",
        "invalid-arguments",
        "invalid-entries",
        "multiline-entry-text",
        "waits-on-too-long",
        "not-a-candidate",
        "entry-exists",
        "spec-not-found",
        "stale-spec",
        "spec-write-failed",
        "lore-too-old",
        "malformed-prior-section",
        "shared-layer-prior",
        "candidates-unreadable",
        "parent-not-found",
        "not-a-slice-parent",
        "not-on-this-slice",
        "would-empty-slice",
        "unreadable-parent",
        "stale-parent",
        "parent-write-failed",
    }
)
_SLICE_WORDS = "set aside by a slice pass until the outside thing it waits on is done"
_BUILD_WORDS = "set aside by a build session until the outside thing it waits on is done"
_CRITERION_RE = re.compile(r"AC[1-9][0-9]{0,8}")
_ENTRY_KEYS = {"criterion", "waits_on"}
_LINE_BREAKS = "\n\r\x0b\x0c\x85  "
_SET_ASIDE = "set aside"
lore_runner = gather.lore_runner


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
    for entry in raw:
        ok = (
            isinstance(entry, dict)
            and set(entry) == _ENTRY_KEYS
            and isinstance(entry["criterion"], str)
            and _CRITERION_RE.fullmatch(entry["criterion"])
            and isinstance(entry["waits_on"], str)
            and entry["waits_on"].strip()
            and entry["criterion"] not in seen
        )
        if not ok:
            raise Refusal(
                "--entries must hold objects with exactly criterion (an ACn, once) and waits_on "
                "(a non-empty string)",
                "invalid-entries",
                2,
            )
        seen.add(entry["criterion"])
    return raw


def _refuse_unfit_text(entries: list[dict]) -> None:
    for entry in entries:
        value = entry["waits_on"]
        lead = value.lstrip()
        if any(ch in value for ch in _LINE_BREAKS) or lead.startswith("#") or lead.startswith("- "):
            raise Refusal(
                f"the waits_on for {entry['criterion']} carries a line break, a leading '#' "
                "or a leading '- ' — write it as one plain line",
                "multiline-entry-text",
                1,
            )
        if len(value) > MAX_WAITS_ON_CHARS:
            raise Refusal(
                f"the waits_on for {entry['criterion']} is over {MAX_WAITS_ON_CHARS} characters — "
                "name the outside thing and what unblocks it in fewer words",
                "waits-on-too-long",
                1,
            )


def _entry_lines(entry: dict, today: str, words: str) -> list[str]:
    return [
        f"- **{entry['criterion']}.** {_SET_ASIDE}",
        f"  - waits on: {entry['waits_on']}",
        f"  - {today} waiting: {words}",
    ]


def _with_entries(body: str, entries: list[dict], words: str) -> str:
    today = waiting_gate._today().isoformat()
    block: list[str] = []
    for entry in entries:
        if block:
            block.append("")
        block += _entry_lines(entry, today, words)
    return sw.append_to_section(body, waiting.HEADING, waiting.DUPLICATE_HEADING_REASON_CODE, block, separated=True)


def _candidates(runner, vault: str, spec_id: str, spec_body: str) -> set[str]:
    """The spec's candidate set, by `candidate_set.main` run in-process over `spec_body` and the
    spec's `lore record criteria` state."""
    try:
        done = gather._call(runner, ["record", "criteria", spec_id, "--vault", vault])
    except gather.GatherError as err:
        raise sw.refusal_from(err)
    if done.returncode != 0:
        raise Refusal(f"lore could not read the criteria of {spec_id}", "lore-failed", 2)
    fd, state_path = tempfile.mkstemp(prefix="set-aside-state-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(done.stdout)
        saved = sys.stdin
        sys.stdin = io.TextIOWrapper(io.BytesIO(spec_body.encode("utf-8")), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rc = candidate_set.main(["--criteria-state", state_path])
        finally:
            sys.stdin = saved
    finally:
        os.unlink(state_path)
    if rc != 0:
        codes = re.findall(r"reason-code: (\S+)", err.getvalue())
        raise Refusal(
            "the candidate set could not be derived for the spec", codes[-1] if codes else "candidates-unreadable", 2
        )
    for line in out.getvalue().splitlines():
        key, _, value = line.partition(": ")
        if key == candidate_set.STDOUT_LINE_KEYS[2]:
            return set() if value == "none" else set(value.split(", "))
    raise Refusal("the candidate set printed no candidates line", "candidates-unreadable", 2)


def set_aside_in_spec(runner, vault: str, spec: str, entries: list[dict], words: str) -> tuple[str, str]:
    """Land `entries` on `spec`'s `## Waiting` section, their history line reading `words`; returns (the spec body now on the vault,
    `written` or `already-present`). Refusals are `Refusal`s and leave the spec untouched."""
    spec_id = f"spec/{spec}"
    _, spec_body, spec_rev, _ = sw.read_record(runner, vault, spec_id, "spec-not-found")
    present = sw.spec_entries(spec_body)
    missing = []
    for entry in entries:
        existing = next(
            (e for key, e in present.items() if key[0] == entry["criterion"] and e.kind == _SET_ASIDE), None
        )
        if existing is None:
            missing.append(entry)
        elif existing.status != "waiting":
            raise Refusal(
                f"{entry['criterion']} already has a set-aside entry on the spec that is no longer waiting",
                "entry-exists",
                1,
            )
    if not missing:
        return spec_body, "already-present"
    candidates = _candidates(runner, vault, spec_id, spec_body)
    for entry in missing:
        if entry["criterion"] not in candidates:
            raise Refusal(
                f"{entry['criterion']} is not a current candidate of the spec "
                "(undeclared, fully covered, removed, closed or already waiting)",
                "not-a-candidate",
                1,
            )
    drafted = _with_entries(spec_body, missing, words)
    body = sw.certify_and_write(
        runner, vault, spec_id, spec_body, spec_rev, drafted, [(e["criterion"], None) for e in missing]
    )
    return body, "written"


def write_mode(args, runner) -> int:
    sw.check_names(args.spec, args.vault)
    entries = _load_entries(args.entries)
    _refuse_unfit_text(entries)
    vault = sw.resolve_vault(runner, args.spec, args.vault)
    _, status = set_aside_in_spec(runner, vault, args.spec, entries, _SLICE_WORDS)
    for entry in entries:
        print(f"set aside: {entry['criterion']}")
    print(f"spec: {status}")
    return 0


def _read_parent(sidecar: dict, body: str, spec: str) -> parent_coverage.ParentCoverage:
    if not parent_coverage.is_slice_parent(sidecar, spec, ("in-progress",)):
        raise Refusal(
            "the parent is not an in-progress slice parent related to this spec", "not-a-slice-parent", 1
        )
    try:
        return parent_coverage.ParentCoverage(body)
    except ValueError:
        raise Refusal(
            "the parent's covers fields or observations cannot be read unambiguously", "unreadable-parent", 2
        )


def _lists_without(state: parent_coverage.ParentCoverage, criteria: list[str]) -> tuple[list[str], list[str]]:
    drop = set(criteria)
    return [c for c in state.covers if c not in drop], [c for c in state.partial if c not in drop]


def _refuse_unfit_claim(state: parent_coverage.ParentCoverage, criteria: list[str]) -> None:
    on_parent = set(state.covers) | set(state.partial)
    off = [c for c in criteria if c not in on_parent]
    if off:
        raise Refusal(
            f"{', '.join(off)} is not on this parent's '**Covers:**' or '**Partially covers:**'",
            "not-on-this-slice",
            1,
        )
    if not any(_lists_without(state, criteria)):
        raise Refusal(
            "setting these aside would leave the parent covering nothing — drop the slice instead",
            "would-empty-slice",
            1,
        )


def _narrow_parent(runner, vault: str, spec: str, parent: str, criteria: list[str], spec_body: str) -> str:
    landed = "the spec entry already landed; re-run to finish the parent"
    record = f"task/{parent}"
    sidecar, body, revision, _ = sw.read_record(runner, vault, record, "parent-not-found")
    state = _read_parent(sidecar, body, spec)
    on_parent = set(state.covers) | set(state.partial)
    todo = [c for c in criteria if c in on_parent]
    if not todo:
        return "already-updated"
    covers, partial = _lists_without(state, todo)
    if not covers and not partial:
        raise Refusal(
            f"the parent changed since it was read and setting these aside would now leave it covering "
            f"nothing; {landed}",
            "would-empty-slice",
            1,
        )
    lines: list[str | None] = list(state.lines)
    for criterion in todo:
        if criterion in state.obs_lines:
            lines[state.obs_lines[criterion]] = None
    lines = state.rewrite_lists(lines, covers, partial)
    parent_coverage.certify_covers(spec_body, covers, partial)
    new_body = "\n".join(line for line in lines if line is not None)
    done = sw.write_record(runner, sw.update_argv(record, vault, revision), new_body)
    if done is not None and done.returncode == 0:
        return "updated"
    sw.refuse_if_base_rejected(done)
    if sw.revision_changed(runner, vault, record, revision, "parent-not-found"):
        raise Refusal(f"{record} changed after it was read; {landed}", "stale-parent", 2)
    raise Refusal(f"lore could not write {record}; {landed}", "parent-write-failed", 2)


def build_mode(args, runner) -> int:
    sw.check_names(args.spec, args.parent, args.vault)
    entries = _load_entries(args.entries)
    _refuse_unfit_text(entries)
    criteria = [entry["criterion"] for entry in entries]
    vault = sw.resolve_vault(runner, args.spec, args.vault)
    sidecar, body, _, _ = sw.read_record(runner, vault, f"task/{args.parent}", "parent-not-found")
    _refuse_unfit_claim(_read_parent(sidecar, body, args.spec), criteria)
    spec_body, spec_status = set_aside_in_spec(runner, vault, args.spec, entries, _BUILD_WORDS)
    parent_status = _narrow_parent(runner, vault, args.spec, args.parent, criteria, spec_body)
    for criterion in criteria:
        print(f"set aside: {criterion}")
    print(f"spec: {spec_status}")
    print(f"parent: {parent_status}")
    return 0


def _parse(argv: list[str]):
    ap = _Parser(prog="set_aside.py", description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--parent")
    ap.add_argument("--vault")
    ap.add_argument("--entries", required=True)
    return ap.parse_args(argv)


def main(argv: list[str], runner=None) -> int:
    runner = runner or lore_runner
    try:
        args = _parse(argv)
        return (build_mode if args.parent is not None else write_mode)(args, runner)
    except Refusal as r:
        return emit_refusal(PROG, r)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
