#!/usr/bin/env python3
"""The spec write path shared by the writers of a spec's `## Waiting` section
(`pending_check.py`, `set_aside.py`). This module has no CLI.

    check_names(*names)               refuses a spec, parent or vault name with an unusable shape
                                      (`invalid-name`) before any lore call
    read_record(runner, vault, id, missing_code, personal_only=True)
                                      (sidecar, body, revision, layer) of a record, read through
                                      `lore record show --json`; a shared-layer record is refused
                                      (`shared-layer-prior`) unless `personal_only` is False, because
                                      `show` XML-escapes shared bodies
    resolve_vault(runner, spec, vault, refusal_exit=1)
                                      `vault`, or the one vault holding `spec`
    spec_entries(body)                the spec's waiting entries by key (`malformed-prior-section`
                                      when the section does not parse)
    append_to_section(body, heading, duplicate_code, new_lines, separated=False)
                                      `new_lines` at the end of a section, creating it at the end
                                      of the body when absent
    certify_and_write(...)            certify the proposed body with the waiting gate, write it with
                                      `--base <revision read>`, and decide a failed or timed-out write
                                      by re-reading: every proposed entry present under its key with
                                      status `waiting` is treated as landed; otherwise a changed
                                      revision is `stale-spec` and an unchanged one `spec-write-failed`
    write_record, update_argv, refuse_if_base_rejected, revision_changed
                                      the write primitives a writer uses for its own second record

A lore that rejects `--base` as an unrecognised argument is `lore-too-old` (exit 2), not a failed
write. Every refusal is a `gate_plumbing.Refusal`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import covers_gate  # noqa: E402
import followup_gather as gather  # noqa: E402
import markdown_section  # noqa: E402
import waiting  # noqa: E402
import waiting_gate  # noqa: E402
from gate_plumbing import Refusal  # noqa: E402

_BASE_REJECTED_RE = re.compile(r"unrecogni[sz]ed arguments?:.*--base")


def check_names(*names: str | None) -> None:
    for name in names:
        if name is not None and not gather._shape_ok(name):
            raise Refusal(
                "a spec, parent or vault name has a shape that cannot be used",
                "invalid-name",
                2,
            )


def refusal_from(err: gather.GatherError, exit_code: int = 2) -> Refusal:
    return Refusal(f"lore could not be read: {err.reason}", err.reason, exit_code)


def read_record(runner, vault: str, record_id: str, missing_code: str, *, personal_only: bool = True):
    """(sidecar, body, revision, layer) of `record_id`."""
    try:
        done = gather._call(runner, ["record", "show", "--json", "--vault", vault, record_id])
        if done.returncode != 0:
            if "record not found" in done.stderr:
                raise Refusal(f"{record_id} was not found in the vault", missing_code, 1)
            raise Refusal(f"lore could not show {record_id}", "lore-failed", 2)
        sidecar = gather._parse_sidecar(done.stdout)
    except gather.GatherError as err:
        raise refusal_from(err)
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


def resolve_vault(runner, spec: str, vault: str | None, *, refusal_exit: int = 1) -> str:
    """`vault`, or the one vault holding `spec`; a spec that is missing, in several vaults
    or not a spec is refused with `refusal_exit`, any other failure with 2."""
    if vault is not None:
        return vault
    try:
        return gather.resolve_spec(runner, spec)[0]
    except gather.GatherError as err:
        raise refusal_from(
            err, refusal_exit if err.reason in ("spec-not-found", "spec-ambiguous", "not-a-spec") else 2
        )


def spec_entries(body: str) -> dict[tuple[str, str | None], waiting.WaitingEntry]:
    try:
        return {e.key: e for e in waiting.parse_waiting(body)}
    except (waiting.WaitingParseError, covers_gate.DuplicateHeadingError):
        raise Refusal(
            "the spec's own '## Waiting' section does not parse — repair it with a direct "
            "edit before writing again",
            "malformed-prior-section",
            2,
        )


def append_to_section(
    body: str, heading: str, duplicate_code: str, new_lines: list[str], *, separated: bool = False
) -> str:
    """`body` with `new_lines` after the last non-blank line of its `heading` section (after
    a blank line when `separated`), or under a new `heading` at the end of the body when it
    has none."""
    lines, masked = markdown_section.split_body(body)
    located = markdown_section.section_range(lines, masked, heading, duplicate_code)
    if located is None:
        return body.rstrip("\n") + "\n\n" + heading + "\n\n" + "\n".join(new_lines) + "\n"
    start, end = located
    last = start
    for i in range(start + 1, end):
        if lines[i].strip():
            last = i
    lines[last + 1 : last + 1] = ([""] if separated else []) + new_lines
    return "\n".join(lines)


def write_record(runner, argv: list[str], stdin: str):
    try:
        return runner(argv, stdin=stdin)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def update_argv(record_id: str, vault: str, revision: str) -> list[str]:
    return ["record", "update", record_id, "--vault", vault, "--base", revision]


def refuse_if_base_rejected(done) -> None:
    if done is not None and done.returncode != 0 and _BASE_REJECTED_RE.search(done.stderr or ""):
        raise Refusal("this lore's 'record update' has no --base, so nothing was written", "lore-too-old", 2)


def revision_changed(runner, vault: str, record_id: str, revision: str, missing: str) -> bool:
    try:
        return read_record(runner, vault, record_id, missing)[2] != revision
    except Refusal:
        return False


def _landed_body(runner, vault: str, spec_id: str, keys: list[tuple[str, str | None]]) -> str | None:
    """The spec body when a write that reported failure had in fact committed every
    entry this run proposed (each present under its key, still `waiting`), else None."""
    try:
        body = read_record(runner, vault, spec_id, "spec-not-found")[1]
        present = spec_entries(body)
    except Refusal:
        return None
    for key in keys:
        existing = present.get(key)
        if existing is None or existing.status != "waiting":
            return None
    return body


def certify_and_write(
    runner,
    vault: str,
    spec_id: str,
    spec_body: str,
    spec_rev: str,
    proposed: str,
    keys: list[tuple[str, str | None]],
    *,
    before_write=None,
) -> str:
    """Certify `proposed` against `spec_body` with the waiting gate, run `before_write()`
    (a writer's own further certification, so a refusal there leaves nothing behind), then
    write `proposed` with `--base spec_rev`. `keys` are the (criterion, building slice)
    keys of the entries `proposed` adds. Returns the spec body now on the vault."""
    waiting_gate.certify(spec_body, proposed)
    if before_write is not None:
        before_write()
    done = write_record(runner, update_argv(spec_id, vault, spec_rev), proposed)
    if done is None or done.returncode != 0:
        refuse_if_base_rejected(done)
        landed = _landed_body(runner, vault, spec_id, keys)
        if landed is None:
            if revision_changed(runner, vault, spec_id, spec_rev, "spec-not-found"):
                raise Refusal(f"{spec_id} changed after it was read", "stale-spec", 2)
            raise Refusal(f"lore could not write {spec_id}", "spec-write-failed", 2)
        return landed
    return proposed
