#!/usr/bin/env python3
"""Check-list gather: every waiting entry on every ready spec in every
configured lore vault, as the text view `/craft:checks` repeats. Read-only.

Usage:
    checklist_gather.py

Takes no arguments (`--help` / `-h` prints usage and exits 0).

Specs are found with `lore search --json 'kind:spec status:ready'`, paged with
`--limit`/`--offset` until `total` hits are read, and each is read with
`lore record show --json --vault <vault> spec/<name>`. The shown sidecar's
status decides readiness, not the search hit's. A spec's `## Waiting` section
is read by `waiting.parse_waiting`; an entry is listed when its latest history
line is `waiting`. A criterion's own words come from
`covers_gate.parse_criteria_with_text`; an `ACn` the spec does not declare is
shown as `(criterion text not found)`.

Stdout on exit 0 is the text view fixed by
docs/design/show-the-check-list-across-every-vault-read-only.md: a count line
(`Nothing is waiting on you.` alone when every spec was read and nothing waits),
one `Could not read:` line per vault, spec or search problem, then one numbered
block per entry, ordered by vault, spec, then the `set` date (the latest
`waiting` line), oldest first. Exit 0 whenever the gather completed, even with
unreadable entries. An unreadable item is named with a reason from REASONS,
never lore's own output, and is never dropped:
- `unparseable`: the waiting section is malformed, or the spec has a duplicate
  `## Acceptance Criteria` heading.
- `show-failed`: `record show` failed or returned no JSON body and status.
- `unsafe-name`: a vault or spec name failed the safe-value shape.
- `not-configured`: a search hit's vault path matches zero, or more than one,
  configured vault.
- `stale`: lore reported `stale` or `config_stale`.
- `cut-off`: paging ended with `truncated` still set, fewer hits than `total`,
  or a search reply that could not be read.

A shared-layer spec's body comes back from lore escaped (`&`, `<`, `>` only);
it is un-escaped exactly, before anything is parsed or cleaned, so such a spec
is read as its author wrote it. Its sidecar status needs no un-escaping: no
escaped string equals `ready` unless the original did.

Exit 2 prints nothing on stdout and one `reason-code:` line on stderr, one of
REASON_CODES: `lore-missing` (no `lore` on PATH), `timeout` (a `lore` call timed
out), `lore-failed` (`lore vault ls` failed), `no-vaults` (it listed none),
`invalid-arguments`, `internal-error` (any unexpected exception).

Invariants and security posture:
- Every `lore` call is an argv list run without a shell, with a timeout and no
  stdin, through one injectable runner (`main(argv, runner)`).
- `lore`'s stdout and stderr are vault-derived and never printed.
- Every vault-sourced string is cleaned by `followup_gather.sanitize_text`
  (control and format characters stripped, length-bounded, credential spans
  redacted) before it reaches the output.
- A vault or spec name failing `followup_gather._shape_ok` never enters an argv.

Import coupling: this script imports `lore_runner`, `_call`, `_shape_ok` and
`sanitize_text` from `followup_gather.py`; a change to them changes this script.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from covers_gate import DuplicateHeadingError, parse_criteria_with_text  # noqa: E402
from followup_gather import (  # noqa: E402
    GatherError,
    _call,
    _shape_ok,
    lore_runner,
    sanitize_text,
)
from waiting import WaitingParseError, parse_waiting  # noqa: E402

REASONS = {
    "unparseable": "its waiting section could not be parsed",
    "show-failed": "lore could not show it",
    "unsafe-name": "its name is not a safe value",
    "not-configured": "it is not a configured vault",
    "stale": "its index is stale, so some ready specs may be missing; run lore reindex",
    "cut-off": "its results were cut off, so some ready specs are missing",
}

REASON_CODES = frozenset(
    {
        "lore-missing",
        "timeout",
        "lore-failed",
        "no-vaults",
        "invalid-arguments",
        "internal-error",
    }
)

SEARCH_LABEL = "lore's search"
SEARCH_QUERY = "kind:spec status:ready"
PAGE_SIZE = 50
MAX_PAGES = 200
MAX_TEXT_CHARS = 1000
NOT_FOUND = "(criterion text not found)"

_RUNNER_REASONS = {
    "lore-missing": "lore-missing",
    "lore-timeout": "timeout",
    "lore-failed": "lore-failed",
}
_CRITERION_PREFIX_RE = re.compile(r"^-\s+\*\*AC\d+\.\*\*\s*")


class GatherFailed(Exception):
    """A failure that maps to a stable `reason-code:`; carries no vault text."""

    def __init__(self, reason: str):
        assert reason in REASON_CODES, reason
        super().__init__(reason)
        self.reason = reason


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise GatherFailed("invalid-arguments")


def _lore(runner, argv: list[str]) -> subprocess.CompletedProcess:
    try:
        return _call(runner, argv)
    except GatherError as err:
        raise GatherFailed(_RUNNER_REASONS[err.reason]) from None


def _clean(text: str) -> str:
    return sanitize_text(" ".join(text.split()), MAX_TEXT_CHARS)


def _unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _vaults(runner) -> list[tuple[str, str | None]]:
    done = _lore(runner, ["vault", "ls"])
    if done.returncode != 0:
        raise GatherFailed("lore-failed")
    rows = [[c.strip() for c in line.split("\t")] for line in done.stdout.splitlines()]
    vaults = [(r[0], r[2].rstrip("/") if len(r) > 2 and r[2] else None) for r in rows if r[0]]
    if not vaults:
        raise GatherFailed("no-vaults")
    return vaults


def _json_object(done: subprocess.CompletedProcess) -> dict | None:
    if done.returncode != 0:
        return None
    try:
        doc = json.loads(done.stdout)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _search(runner, unreadable: set) -> list:
    """Every ready-spec hit, paged until `total` is read; a gap adds `cut-off`."""
    hits: list = []
    total = None
    complete = False
    for _ in range(MAX_PAGES):
        argv = ["search", "--json", "--limit", str(PAGE_SIZE), "--offset", str(len(hits)),
                SEARCH_QUERY]
        page = _json_object(_lore(runner, argv))
        page_hits = page.get("hits") if page else None
        total = page.get("total") if page else None
        if not isinstance(page_hits, list) or not isinstance(total, int) or isinstance(total, bool):
            break
        if page.get("stale") or page.get("config_stale"):
            unreadable.add((SEARCH_LABEL, "stale"))
        hits.extend(page_hits)
        if len(hits) >= total:
            complete = not page.get("truncated")
            break
        if not page_hits:
            break
    if not complete:
        unreadable.add((SEARCH_LABEL, "cut-off"))
    return hits


def _criteria(body: str) -> dict[str, str]:
    try:
        found = parse_criteria_with_text(body)
    except DuplicateHeadingError:
        raise
    except ValueError:
        return {}
    words: dict[str, str] = {}
    for identifier, text in found:
        if identifier is not None and identifier not in words:
            words[identifier] = _CRITERION_PREFIX_RE.sub("", text, count=1)
    return words


def _read_spec(runner, vault: str, name: str, unreadable: set, rows: list) -> None:
    label = _clean(f"{vault}/{name}")
    done = _lore(runner, ["record", "show", "--json", "--vault", vault, f"spec/{name}"])
    doc = _json_object(done)
    body = doc.get("body") if doc else None
    sidecar = doc.get("sidecar") if doc else None
    status = sidecar.get("status") if isinstance(sidecar, dict) else None
    if not isinstance(body, str) or not isinstance(status, str):
        unreadable.add((label, "show-failed"))
        return
    if doc.get("layer") == "shared":
        body = _unescape(body)
    if status != "ready":
        return
    try:
        waiting = [e for e in parse_waiting(body) if e.status == "waiting"]
        words = _criteria(body) if waiting else {}
    except (WaitingParseError, DuplicateHeadingError):
        unreadable.add((label, "unparseable"))
        return
    for entry in waiting:
        rows.append((vault, name, entry, words.get(entry.criterion_id)))


def gather(runner) -> tuple[list, set]:
    """(entries as (vault, spec, WaitingEntry, criterion words or None), unreadable as
    (label, reason key))."""
    unreadable: set[tuple[str, str]] = set()
    rows: list = []
    vaults = _vaults(runner)
    by_path: dict[str, list[str]] = {}
    for name, path in vaults:
        if not _shape_ok(name):
            unreadable.add((_clean(f"vault {name}"), "unsafe-name"))
        elif path:
            by_path.setdefault(path, []).append(name)
    unsafe_paths = {path for name, path in vaults if path and not _shape_ok(name)}
    for hit in _search(runner, unreadable):
        hit_id = hit.get("id") if isinstance(hit, dict) else None
        hit_path = hit.get("vault") if isinstance(hit, dict) else None
        if not isinstance(hit_id, str) or not isinstance(hit_path, str):
            unreadable.add((SEARCH_LABEL, "cut-off"))
            continue
        hit_path = hit_path.rstrip("/")
        if hit_path in unsafe_paths and len(by_path.get(hit_path, [])) == 0:
            continue
        owners = by_path.get(hit_path, [])
        if len(owners) != 1:
            unreadable.add((_clean(f"vault {hit_path.rsplit('/', 1)[-1]}"), "not-configured"))
            continue
        prefix = f"{hit_path}/spec/"
        name = hit_id[len(prefix):] if hit_id.startswith(prefix) else None
        if name is None or not _shape_ok(name):
            tail = name if name is not None else hit_id.rsplit("/", 1)[-1]
            unreadable.add((_clean(f"{owners[0]}/{tail}"), "unsafe-name"))
            continue
        _read_spec(runner, owners[0], name, unreadable, rows)
    return rows, unreadable


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _count_line(rows: list) -> str:
    checks = sum(1 for _, _, e, _ in rows if e.kind == "pending check")
    aside = len(rows) - checks
    parts = []
    if checks:
        parts.append(_plural(checks, "check", "checks"))
    if aside:
        parts.append(_plural(aside, "set-aside criterion", "set-aside criteria"))
    specs = len({(v, s) for v, s, _, _ in rows})
    where = "in 1 spec" if specs == 1 else f"across {specs} specs"
    return f"{' and '.join(parts)} waiting, {where}."


def _block(index: int, total: int, vault: str, spec: str, entry, words: str | None) -> list[str]:
    head = _clean(words) if words is not None else NOT_FOUND
    lines = [f"[{index}/{total}] {head} ({entry.criterion_id})",
             f"  spec: {_clean(f'{vault}/{spec}')}"]
    if entry.kind == "pending check":
        lines.append(f"  built by: task/{_clean(entry.building_slice)}")
    lines.append(f"  set: {entry.set_date}")
    if entry.kind == "pending check":
        lines += [f"  check: {_clean(entry.check)}", f"  how: {_clean(entry.how)}",
                  f"  changed existing behaviour: {'yes' if entry.changed_behaviour else 'no'}"]
    else:
        lines.append(f"  waits on: {_clean(entry.waits_on)}")
    return lines


def _sort_key(row):
    vault, spec, entry, _ = row
    return (vault, spec, entry.set_date, int(entry.criterion_id[2:]))


def render(rows: list, unreadable: set) -> str:
    if not rows and not unreadable:
        return "Nothing is waiting on you.\n"
    rows = sorted(rows, key=_sort_key)
    lines = [_count_line(rows) if rows else "Nothing waiting in the specs that could be read."]
    lines += [f"Could not read: {label} — {REASONS[key]}" for label, key in sorted(unreadable)]
    for i, (vault, spec, entry, words) in enumerate(rows, 1):
        lines.append("")
        lines += _block(i, len(rows), vault, spec, entry, words)
    return "\n".join(lines) + "\n"


def _refuse(reason: str) -> int:
    assert reason in REASON_CODES, reason
    print(f"reason-code: {reason}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None, runner=None) -> int:
    try:
        parser = _Parser(
            prog="checklist_gather.py",
            description="Print every waiting check across every configured lore vault.",
        )
        try:
            parser.parse_args(argv)
            text = render(*gather(runner or lore_runner))
        except GatherFailed as err:
            return _refuse(err.reason)
        sys.stdout.write(text)
        return 0
    except Exception:
        return _refuse("internal-error")


if __name__ == "__main__":
    sys.exit(main())
