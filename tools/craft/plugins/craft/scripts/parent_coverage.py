#!/usr/bin/env python3
"""A slice parent's coverage lists, read and rewritten through the observation gate's own
parsers. The writers that edit a parent's `**Covers:**`, `**Partially covers:**` and
`## Criterion observations` lines (`pending_check.py`, `set_aside.py`) share this module. It
has no CLI.

    ParentCoverage(body)             the parent's `lines`, `masked`, `covers`, `partial`,
                                     `observations`, `attestations` and `obs_lines`
                                     ({identifier: line index} of its observation lines);
                                     refused `unreadable-parent` when the lists or
                                     observations cannot be read unambiguously
    ParentCoverage.rewrite_lists(lines, covers, partial)
                                     `lines` with the two coverage lines set to `covers` and
                                     `partial`; an emptied list's line is dropped
    is_slice_parent(sidecar, spec, statuses)
                                     a task in one of `statuses`, labelled a slice parent,
                                     related to `spec`
    require_slice_parent(sidecar, spec)
                                     refuses (`not-a-slice-parent`) a parent that is not an
                                     in-progress slice parent related to `spec`
    write_parent(runner, vault, record_id, revision, body, note="")
                                     the parent written with `--base revision`; a write that
                                     does not take is `stale-parent` when the revision moved,
                                     else `parent-write-failed`, each message ending in `note`
    certify_covers(spec_body, covers, partial)
                                     the lists certified by `covers_gate` in-process against
                                     the spec body (a `Refusal` carrying the gate's own code)
"""

from __future__ import annotations

import io
import re
import sys
from contextlib import redirect_stderr
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import covers_gate  # noqa: E402
import followup_gather as gather  # noqa: E402
import observation_gate as obs  # noqa: E402
import spec_write as sw  # noqa: E402
from gate_plumbing import Refusal  # noqa: E402


class ParentCoverage:
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

    def rewrite_lists(self, lines: list[str], covers: list[str], partial: list[str]) -> list[str]:
        """`lines` (this parent's lines, possibly with other edits) with the coverage lines set
        to `covers` and `partial`; the line of an emptied list is dropped. The indices are this
        parent's own, so `lines` must not have gained or lost lines above them."""
        lines = list(lines)
        covers_idx = self._field_index(obs._COVERS_FIELD_RE)
        partial_idx = self._field_index(obs._PARTIALLY_COVERS_FIELD_RE)
        covers_line = f"**Covers:** {', '.join(covers)}"
        partial_line = f"**Partially covers:** {', '.join(partial)}"
        drop: list[int] = []
        if covers_idx is not None:
            if covers:
                lines[covers_idx] = covers_line
            else:
                drop.append(covers_idx)
        if partial_idx is not None:
            if partial:
                lines[partial_idx] = partial_line
            else:
                drop.append(partial_idx)
        elif partial:
            if covers_idx is not None and covers:
                lines.insert(covers_idx + 1, partial_line)
            elif covers_idx is not None:
                lines[covers_idx] = partial_line
                drop.remove(covers_idx)
            else:
                lines.append(partial_line)
        for i in sorted(drop, reverse=True):
            del lines[i]
        return lines


def is_slice_parent(sidecar: dict, spec: str, statuses: tuple[str, ...]) -> bool:
    related = sidecar.get("related")
    specs = related.get("spec") if isinstance(related, dict) else None
    linked = {v.removeprefix("spec/") for v in specs if isinstance(v, str)} if isinstance(specs, list) else set()
    return (
        sidecar.get("kind") == "task"
        and sidecar.get("status") in statuses
        and gather.SLICE_LABEL in gather._labels(sidecar)
        and spec in linked
    )


def require_slice_parent(sidecar: dict, spec: str) -> None:
    if not is_slice_parent(sidecar, spec, ("in-progress",)):
        raise Refusal(
            "the parent is not an in-progress slice parent related to this spec", "not-a-slice-parent", 1
        )


def write_parent(runner, vault: str, record_id: str, revision: str, body: str, *, note: str = "") -> None:
    done = sw.write_record(runner, sw.update_argv(record_id, vault, revision), body)
    if done is not None and done.returncode == 0:
        return
    sw.refuse_if_base_rejected(done)
    if sw.revision_changed(runner, vault, record_id, revision, "parent-not-found"):
        raise Refusal(f"{record_id} changed after it was read{note}", "stale-parent", 2)
    raise Refusal(f"lore could not write {record_id}{note}", "parent-write-failed", 2)


def certify_covers(spec_body: str, covers: list[str], partial: list[str]) -> None:
    argv = []
    if covers:
        argv += ["--covers", ", ".join(covers)]
    if partial:
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
