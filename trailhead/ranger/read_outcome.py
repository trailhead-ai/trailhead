"""Judge a ritual session's captured output against the record it names.

The grammar is craft's (``tools/craft/plugins/craft/skills/_shared/unattended.md``, "The outcome
line"). The output file is a model's session text, so it is read as hostile: a bounded tail of a
regular file, decoded leniently, and nothing from it reaches the verdict except an id that matched
the name shape exactly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from trailhead.ranger.drive_groups import _UNSAFE_VAULT_NAME

VERDICT_FIELDS = ("verdict", "record", "reason_code")
REASON_CODES = (
    "no-outcome",
    "malformed-outcome",
    "record-outside-spec",
    "record-unreadable",
    "record-mismatch",
    "ritual-stuck",
)
OUTCOME_KINDS = (
    "plan-ready",
    "slice-chosen",
    "pr-ready",
    "stuck",
    "needs-a-decision",
    "waiting",
    "spec-complete",
    "refused",
)
RECORD_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
SLICE_PARENT_LABEL = "craft/slice-parent"

_BLOCKLESS_KINDS = frozenset({"refused", "spec-complete", "waiting"})
_TAIL_BYTES = 1 << 20
_TAIL_LINES = 200
_MAX_LINE_CHARS = 8192
_LORE_TIMEOUT_SECONDS = 60

_NAME_RE = re.compile(RECORD_NAME_PATTERN)
_OUTCOME_RE = re.compile(r"outcome: ([a-z-]+) (task|spec)/([a-z0-9][a-z0-9-]*) parked=[0-9]{1,6}(?: — .*)?")
_HEADING_RE = re.compile(r"#{1,6} ")
_BLOCK_HEADING = "## Unattended run"
_OUTCOME_FIELD_RE = re.compile(r"\*\*Outcome:\*\*[ \t]*(\S+)[ \t]*")

Show = Callable[[str, "str | None"], "dict | None"]


def valid_spec(spec: str) -> bool:
    return _NAME_RE.fullmatch(spec) is not None


def valid_vault(vault: str) -> bool:
    return _UNSAFE_VAULT_NAME.search(vault) is None


def lore_show(env: dict[str, str]) -> Show:
    """A record lookup through the ``lore`` CLI; vault ``None`` scans every configured vault."""

    def show(record_id: str, vault: str | None) -> dict | None:
        lore = shutil.which("lore", path=env.get("PATH", ""))
        if lore is None:
            return None
        argv = [lore, "record", "show", record_id, "--json"]
        if vault is not None:
            argv.append(f"--vault={vault}")
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                env=env,
                timeout=_LORE_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                return None
            record = json.loads(proc.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    return show


def _verdict(verdict: str, record: str | None = None, reason: str | None = None) -> dict:
    return {VERDICT_FIELDS[0]: verdict, VERDICT_FIELDS[1]: record, VERDICT_FIELDS[2]: reason}


def _stuck(reason: str, record: str | None = None) -> dict:
    return _verdict("stuck", record, reason)


def _tail_lines(path: Path) -> list[str] | None:
    """The last lines of the file, or None when it cannot be read as one (a directory, a link, a pipe)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        offset = max(0, info.st_size - _TAIL_BYTES)
        os.lseek(fd, offset, os.SEEK_SET)
        data = os.read(fd, _TAIL_BYTES)
    except OSError:
        return None
    finally:
        os.close(fd)
    lines = data.decode("utf-8", errors="replace").split("\n")
    if offset > 0:
        lines = lines[1:]
    if lines and lines[-1] == "":
        lines.pop()
    return lines[-_TAIL_LINES:]


def _last_run_outcome(body: str) -> str | None:
    """The ``**Outcome:**`` value of the body's last ``## Unattended run`` block, outside code fences."""
    in_fence = False
    in_block = False
    outcome: str | None = None
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _HEADING_RE.match(line):
            in_block = line.rstrip() == _BLOCK_HEADING
            if in_block:
                outcome = None
            continue
        if in_block and (field := _OUTCOME_FIELD_RE.fullmatch(line)):
            outcome = field.group(1)
    return outcome


def _judge_task(kind: str, record_id: str, spec: str, vault: str, show: Show) -> dict:
    record = show(record_id, vault)
    if record is None:
        found_elsewhere = show(record_id, None) is not None
        return _stuck("record-outside-spec" if found_elsewhere else "record-unreadable", record_id)
    body, sidecar = record.get("body"), record.get("sidecar")
    if not isinstance(body, str) or not isinstance(sidecar, dict):
        return _stuck("record-unreadable", record_id)
    labels, related = sidecar.get("labels"), sidecar.get("related")
    specs = related.get("spec") if isinstance(related, dict) else None
    is_slice_parent = isinstance(labels, dict) and SLICE_PARENT_LABEL in labels
    if not is_slice_parent or not isinstance(specs, list) or spec not in specs:
        return _stuck("record-outside-spec", record_id)
    if _last_run_outcome(body) != kind:
        return _stuck("record-mismatch", record_id)
    return _verdict(kind, record_id)


def judge(spec: str, vault: str, output_file: Path, *, show: Show) -> dict:
    """The verdict for the session output at ``output_file`` on the drive of ``spec`` in ``vault``."""
    lines = _tail_lines(Path(output_file))
    marked = [line for line in lines or () if "outcome:" in line]
    if not marked:
        return _stuck("no-outcome")
    line = marked[-1].strip()
    parsed = _OUTCOME_RE.fullmatch(line) if len(line) <= _MAX_LINE_CHARS else None
    if parsed is None or parsed.group(1) not in OUTCOME_KINDS:
        return _stuck("malformed-outcome")
    kind, record_kind, name = parsed.groups()
    record_id = f"{record_kind}/{name}"
    if kind == "stuck":
        return _stuck("ritual-stuck", record_id)
    if record_kind == "spec":
        if name == spec and (kind in _BLOCKLESS_KINDS or kind == "needs-a-decision"):
            return _verdict(kind, record_id)
        return _stuck("record-outside-spec", record_id)
    if kind in _BLOCKLESS_KINDS:
        return _stuck("record-outside-spec", record_id)
    return _judge_task(kind, record_id, spec, vault, show)
