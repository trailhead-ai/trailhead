#!/usr/bin/env python3
"""Set-aside writer — sets a candidate criterion aside from a slice pass.

Usage:
    set_aside.py --spec <n> --entries <path> [--vault <v>]

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
A re-run whose entries are all present with status `waiting` writes nothing.

Stdout on success: one `set aside: ACn` per entry, then `spec: written|already-present`. Entry
text is never echoed in a `reason:` line.

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
    }
)
_WORDS = "set aside by a slice pass until the outside thing it waits on is done"
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


def _entry_lines(entry: dict, today: str) -> list[str]:
    return [
        f"- **{entry['criterion']}.** {_SET_ASIDE}",
        f"  - waits on: {entry['waits_on']}",
        f"  - {today} waiting: {_WORDS}",
    ]


def _with_entries(body: str, entries: list[dict]) -> str:
    today = waiting_gate._today().isoformat()
    block: list[str] = []
    for entry in entries:
        if block:
            block.append("")
        block += _entry_lines(entry, today)
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


def set_aside_in_spec(runner, vault: str, spec: str, entries: list[dict]) -> tuple[str, str]:
    """Land `entries` on `spec`'s `## Waiting` section; returns (the spec body now on the vault,
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
    drafted = _with_entries(spec_body, missing)
    body = sw.certify_and_write(
        runner, vault, spec_id, spec_body, spec_rev, drafted, [(e["criterion"], None) for e in missing]
    )
    return body, "written"


def write_mode(args, runner) -> int:
    sw.check_names(args.spec, args.vault)
    entries = _load_entries(args.entries)
    _refuse_unfit_text(entries)
    vault = sw.resolve_vault(runner, args.spec, args.vault)
    _, status = set_aside_in_spec(runner, vault, args.spec, entries)
    for entry in entries:
        print(f"set aside: {entry['criterion']}")
    print(f"spec: {status}")
    return 0


def _parse(argv: list[str]):
    ap = _Parser(prog="set_aside.py", description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--vault")
    ap.add_argument("--entries", required=True)
    return ap.parse_args(argv)


def main(argv: list[str], runner=None) -> int:
    runner = runner or lore_runner
    try:
        return write_mode(_parse(argv), runner)
    except Refusal as r:
        return emit_refusal(PROG, r)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
