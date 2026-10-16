#!/usr/bin/env python3
"""Follow-up gathering: resolve a spec across the configured lore vaults,
decide whether its slice loop has ended, and gather its open follow-ups.

Usage:
    followup_gather.py <spec-name>

`<spec-name>` is the bare name of a spec record (the part after `spec/`) and
must match `^[A-Za-z0-9._/-]+$`, must not start with `-`, and must not
contain a `..` path segment; anything else is refused before `lore` is ever
invoked. A name starting with `-` is only reachable after a `--` separator,
since otherwise it parses as an option.

The spec is looked up with `lore vault ls` followed by `lore record show
--json --vault <vault> spec/<name>` in each listed vault. Exactly one vault
must hold it. Its slice loop has ended when it carries the label
`craft/slice-loop=complete` (entry `complete-marker`), carries
`craft/slice-loop=stopped` (entry `stopped-marker`), or has status `complete`
(entry `complete-status`), checked in that order.

Stdout on exit 0 is one JSON object:
- `spec` (the name given), `vault` (the vault holding it) and `entry` (how the
  loop ended);
- `followups`: `{name, title, status}` items sorted by `name`. `name` is the
  bare task slug (no `task/` prefix) and has passed the shape check above, so
  it can be substituted into `lore record show --json --vault <vault>
  task/<name>`; it is a record slug and is not credential-scrubbed. `title`
  has control characters and invisible format characters (Unicode categories
  Cf, Zl, Zp: zero-width, bidi and tag characters, line and paragraph
  separators) stripped, is truncated to MAX_TITLE_CHARS characters with a
  trailing `…` marker when longer (the credential scan is quadratic on
  hostile input, so the bound applies before it), and has credential spans
  redacted;
- `distilled`: true when the spec's sidecar `annotations` carry `distilled`,
  whatever its value (`adr`, `zero-adr` or `rejected`), else false;
- `followup-parents`: the sorted bare names of tasks in the spec's vault that
  carry `craft/followup-parent`, are linked to the spec (either side names the
  other) and have status `open`, `ready`, `in-progress` or `blocked`. Each name
  has passed the shape check above;
- the basis: `slice-basis` (`labelled` or `linked-parents`), `slices` (their
  names), `child-count`, `candidates-read`, `kept`, `excluded-by-label`
  (`{count, names}`) and `skipped-edges`.

A follow-up is a task in the spec's vault that is linked to the spec, to a
slice or to a direct child of a slice, whose status is `open`, `ready` or
`blocked`, and that has no `parent` and carries neither `craft/slice-parent`
nor `craft/followup-parent`. Linked means either side names the other: the
task's own `related` edges, or an outbound `related.task` edge of the spec,
slice or child. Slices are the tasks carrying `craft/slice-parent` that relate
to the spec; when there are none, the spec's linked parent tasks that are not
marked `craft/followup-parent` stand in (`linked-parents`). Children come from
the live `lore task list`. Links are discovered with `lore search` using
`related-spec:(...)` and `related-task:(...)` disjunctions of at most
QUERY_BATCH_SIZE quoted values (a longer one overflows SQLite's parser), and
a search hit is kept only when its own complete `record show` read names the
spec, a slice or a child, or when an outbound edge read from the spec, a slice
or a child names it; a hit with neither is dropped (visible as `candidates-read`
exceeding `kept`). A `related` value or record name that fails the shape check,
or an edge naming a task that does not exist, is skipped and counted in
`skipped-edges`. A vault name that fails the shape check is skipped, never
queried and never emitted.

Exit codes:
    0  the slice loop has ended; the JSON object is on stdout.
    1  refused: the spec exists but its slice loop has not ended. stderr
       carries `reason-code: slice-loop-not-ended`.
    2  could not certify or gather; nothing is written to stdout. stderr
       carries one `reason-code:` line, one of REASON_CODES: invalid-spec-name,
       invalid-arguments, no-vaults, spec-not-found, spec-ambiguous,
       not-a-spec, malformed-sidecar, empty-sidecar, lore-missing,
       lore-timeout, lore-failed, lore-output-not-json, search-truncated,
       internal-error.

Invariants and security posture:
- Every `lore` call is an argv list run without a shell, with a timeout and
  no stdin.
- `lore`'s stdout and stderr are vault-derived and are never printed: a
  failure prints its `reason-code:` and nothing parsed from `lore`.
- Any unexpected exception exits 2 with `reason-code: internal-error`. Python's
  default uncaught-exception exit is 1, which would read as the refusal.
- A sidecar that is valid JSON but lacks `kind` or `status` is a failed
  lookup, never a verdict.

`--help` / `-h` prints usage and exits 0.

`lore_runner` is the single seam through which every `lore` call is made, and
`resolve_spec` takes the runner as an argument, so callers and tests can
substitute it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

REASON_CODES = frozenset(
    {
        "invalid-spec-name",
        "invalid-arguments",
        "no-vaults",
        "spec-not-found",
        "spec-ambiguous",
        "not-a-spec",
        "malformed-sidecar",
        "empty-sidecar",
        "lore-missing",
        "lore-timeout",
        "lore-failed",
        "lore-output-not-json",
        "search-truncated",
        "internal-error",
        "slice-loop-not-ended",
    }
)

NAME_RE = re.compile(r"[A-Za-z0-9._/-]+")
LORE_TIMEOUT_SECONDS = 30
LOOP_LABEL = "craft/slice-loop"
SLICE_LABEL = "craft/slice-parent"
FOLLOWUP_PARENT_LABEL = "craft/followup-parent"
OPEN_STATUSES = frozenset({"open", "ready", "blocked"})
LIVE_PARENT_STATUSES = frozenset({"open", "ready", "in-progress", "blocked"})
DISTILLED_ANNOTATION = "distilled"
QUERY_BATCH_SIZE = 40
SEARCH_LIMIT = "100000"
REDACTED = "[REDACTED]"
MAX_TITLE_CHARS = 300
TRUNCATION_MARKER = "\u2026"
# How far past a cut the credential scan reaches, so a token straddling the
# bound is matched whole before the cut drops its tail.
_REDACT_MARGIN = 4096
_CONTROL_RE = re.compile("[\x00-\x1f\x7f-\x9f]")
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Zl", "Zp"})
MARKER_ENTRIES = {"complete": "complete-marker", "stopped": "stopped-marker"}


class GatherError(Exception):
    """A failure that maps to a stable `reason-code:`; carries no vault text."""

    def __init__(self, reason: str):
        assert reason in REASON_CODES, reason
        super().__init__(reason)
        self.reason = reason


def lore_runner(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    """Run `lore <argv>` with no shell and a timeout. `stdin` is piped as the body
    when given; otherwise stdin is closed, since a metadata-only `lore record
    update` errors on a stdin that is open but silent. May raise
    FileNotFoundError (no `lore` on PATH) or subprocess.TimeoutExpired."""
    kwargs = {"stdin": subprocess.DEVNULL} if stdin is None else {"input": stdin}
    return subprocess.run(
        ["lore", *argv],
        capture_output=True,
        text=True,
        timeout=LORE_TIMEOUT_SECONDS,
        shell=False,
        **kwargs,
    )


def _call(runner, argv: list[str]) -> subprocess.CompletedProcess:
    try:
        return runner(argv)
    except FileNotFoundError:
        raise GatherError("lore-missing") from None
    except subprocess.TimeoutExpired:
        raise GatherError("lore-timeout") from None


def _vault_rows(runner) -> list[list[str]]:
    done = _call(runner, ["vault", "ls"])
    if done.returncode != 0:
        raise GatherError("lore-failed")
    rows = [[c.strip() for c in line.split("\t")] for line in done.stdout.splitlines()]
    rows = [r for r in rows if r[0] and _shape_ok(r[0])]
    if not rows:
        raise GatherError("no-vaults")
    return rows


def list_vaults(runner) -> list[str]:
    return [row[0] for row in _vault_rows(runner)]


def vault_path(runner, vault: str) -> str:
    for row in _vault_rows(runner):
        if row[0] == vault and len(row) > 2 and row[2]:
            return row[2]
    raise GatherError("lore-failed")


def _parse_sidecar(stdout: str) -> dict:
    try:
        doc = json.loads(stdout)
    except ValueError:
        raise GatherError("lore-output-not-json") from None
    if not isinstance(doc, dict):
        raise GatherError("lore-output-not-json")
    sidecar = doc.get("sidecar")
    if not sidecar:
        raise GatherError("empty-sidecar")
    if not isinstance(sidecar, dict) or "kind" not in sidecar or "status" not in sidecar:
        raise GatherError("malformed-sidecar")
    return sidecar


def _show(runner, vault: str, record_id: str) -> dict | None:
    """The sidecar of `record_id` in `vault`, or None when it is not there."""
    done = _call(runner, ["record", "show", "--json", "--vault", vault, record_id])
    if done.returncode != 0:
        if "record not found" in done.stderr:
            return None
        raise GatherError("lore-failed")
    return _parse_sidecar(done.stdout)


def resolve_spec(runner, name: str) -> tuple[str, dict]:
    """Return (vault, sidecar) for `spec/<name>`, found in exactly one vault."""
    found: list[tuple[str, dict]] = []
    for vault in list_vaults(runner):
        sidecar = _show(runner, vault, f"spec/{name}")
        if sidecar is not None:
            found.append((vault, sidecar))
    if not found:
        raise GatherError("spec-not-found")
    if len(found) > 1:
        raise GatherError("spec-ambiguous")
    vault, sidecar = found[0]
    if sidecar["kind"] != "spec":
        raise GatherError("not-a-spec")
    return vault, sidecar


def _labels(sidecar: dict) -> dict:
    labels = sidecar.get("labels")
    return labels if isinstance(labels, dict) else {}


def entry_verdict(sidecar: dict) -> str | None:
    """How the spec's slice loop ended, or None when it has not."""
    marker = _labels(sidecar).get(LOOP_LABEL)
    if marker in MARKER_ENTRIES:
        return MARKER_ENTRIES[marker]
    if sidecar["status"] == "complete":
        return "complete-status"
    return None


def is_distilled(sidecar: dict) -> bool:
    """Whether the spec carries a `distilled` annotation, whatever its value."""
    annotations = sidecar.get("annotations")
    return isinstance(annotations, dict) and DISTILLED_ANNOTATION in annotations


def _call_json(runner, argv: list[str]):
    done = _call(runner, argv)
    if done.returncode != 0:
        raise GatherError("lore-failed")
    try:
        return json.loads(done.stdout)
    except ValueError:
        raise GatherError("lore-output-not-json") from None


def sanitize_text(text: str, limit: int) -> str:
    """Vault text made safe to print: control and invisible format characters
    stripped, credential spans redacted, then cut to `limit` characters with a
    trailing marker. Redaction runs before the cut, over a window reaching
    `_REDACT_MARGIN` characters past it, so a credential straddling the bound
    is matched whole and no fragment of it survives the cut; the window keeps
    the scan bounded on hostile oversized input."""
    import capture_scan

    clean = "".join(
        c
        for c in _CONTROL_RE.sub("", text)
        if unicodedata.category(c) not in _INVISIBLE_CATEGORIES
    )
    over = len(clean) > limit
    clean = clean[: limit + _REDACT_MARGIN]
    spans = {
        f.text
        for f in capture_scan.scan_text(clean)
        if capture_scan.is_credential_class(f.cls)
    }
    for span in sorted(spans, key=len, reverse=True):
        clean = clean.replace(span, REDACTED)
    if over:
        clean = clean[:limit] + TRUNCATION_MARKER
    return clean


def sanitize_title(title: str) -> str:
    return sanitize_text(title, MAX_TITLE_CHARS)


def _shape_ok(name: str) -> bool:
    return (
        NAME_RE.fullmatch(name) is not None
        and not name.startswith("-")
        and ".." not in name.split("/")
    )


def _related(sidecar: dict, kind: str) -> set[str]:
    related = sidecar.get("related")
    values = related.get(kind) if isinstance(related, dict) else None
    return {
        v.removeprefix(f"{kind}/") for v in values if isinstance(v, str)
    } if isinstance(values, list) else set()


def _names_any(sidecar: dict, spec: str, anchors: list[str]) -> bool:
    """Whether the task's own `related` edges name the spec or any anchor task."""
    return spec in _related(sidecar, "spec") or bool(_related(sidecar, "task") & set(anchors))


class _Gatherer:
    def __init__(self, runner, vault: str, vault_dir: str):
        self.runner = runner
        self.vault = vault
        self.vault_dir = vault_dir
        self.skipped: set[str] = set()
        self._sidecars: dict[str, dict] = {}
        self.listing: dict[str, dict] = {}

    def load_listing(self) -> None:
        doc = _call_json(self.runner, ["task", "list", "--vault", self.vault, "--json"])
        if not isinstance(doc, list):
            raise GatherError("lore-output-not-json")
        for item in doc:
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str):
                raise GatherError("lore-output-not-json")
            if _shape_ok(name):
                self.listing[name] = item
            else:
                self.skipped.add(repr(name))

    def outbound(self, sidecar: dict) -> set[str]:
        """Tasks named by the sidecar's `related.task` values. Only a name in the
        shape-checked live listing reaches a query or a read; any other value
        cannot name a task in this vault and is counted as skipped."""
        related = sidecar.get("related")
        values = related.get("task") if isinstance(related, dict) else None
        names = set()
        for value in values if isinstance(values, list) else []:
            name = value.removeprefix("task/") if isinstance(value, str) else None
            if name in self.listing:
                names.add(name)
            else:
                self.skipped.add(repr(value))
        return names

    def search(self, kind: str, names: list[str]) -> set[str]:
        """Tasks in this vault whose `related.<kind>` names any of `names`, bare or
        `<kind>/`-qualified, in batches of at most QUERY_BATCH_SIZE values."""
        found: set[str] = set()
        per_batch = max(QUERY_BATCH_SIZE // 2, 1)
        for start in range(0, len(names), per_batch):
            values = [
                spelling
                for name in names[start : start + per_batch]
                for spelling in (name, f"{kind}/{name}")
            ]
            query = f"related-{kind}:(" + " OR ".join(f'"{v}"' for v in values) + ")"
            doc = _call_json(
                self.runner, ["search", query, "--json", "--limit", SEARCH_LIMIT]
            )
            hits = doc.get("hits") if isinstance(doc, dict) else None
            if not isinstance(hits, list):
                raise GatherError("lore-output-not-json")
            if doc.get("truncated"):
                raise GatherError("search-truncated")
            for h in hits:
                name = self._hit_task(h)
                if name is not None:
                    found.add(name)
        return found

    def _hit_task(self, hit) -> str | None:
        if not isinstance(hit, dict):
            raise GatherError("lore-output-not-json")
        hit_id, hit_vault = hit.get("id"), hit.get("vault")
        if not isinstance(hit_id, str) or not isinstance(hit_vault, str):
            raise GatherError("lore-output-not-json")
        if hit_vault != self.vault_dir or not hit_id.startswith(hit_vault + "/"):
            return None
        kind, _, slug = hit_id[len(hit_vault) + 1 :].partition("/")
        if kind != "task":
            return None
        if not _shape_ok(slug):
            self.skipped.add(repr(slug))
            return None
        return slug

    def spec_linked(self, spec: str, spec_sidecar: dict) -> dict[str, dict]:
        """Sidecars of the tasks linked to the spec (either side names the other),
        read in name order."""
        names = self.search("spec", [spec]) | self.outbound(spec_sidecar)
        return {name: self.read(name) for name in sorted(names)}

    def read(self, name: str) -> dict:
        if name not in self._sidecars:
            sidecar = _show(self.runner, self.vault, f"task/{name}")
            if sidecar is None:
                raise GatherError("lore-failed")
            if sidecar["kind"] != "task":
                raise GatherError("malformed-sidecar")
            self._sidecars[name] = sidecar
        return self._sidecars[name]


def live_parent_names(linked: dict[str, dict]) -> list[str]:
    """Sorted names among the spec-linked sidecars that carry `craft/followup-parent`
    with a live status."""
    return sorted(
        n
        for n, sc in linked.items()
        if FOLLOWUP_PARENT_LABEL in _labels(sc) and sc["status"] in LIVE_PARENT_STATUSES
    )


def gather_followups(runner, vault: str, spec: str, spec_sidecar: dict) -> dict:
    g = _Gatherer(runner, vault, vault_path(runner, vault))
    g.load_listing()
    children_of: dict[str, list[str]] = {}
    for name, item in g.listing.items():
        parent = item.get("parent")
        if isinstance(parent, str):
            children_of.setdefault(parent, []).append(name)

    linked = g.spec_linked(spec, spec_sidecar)
    slices = sorted(n for n, sc in linked.items() if SLICE_LABEL in _labels(sc))
    basis = "labelled"
    if not slices:
        basis = "linked-parents"
        slices = sorted(
            n
            for n, sc in linked.items()
            if n in children_of and FOLLOWUP_PARENT_LABEL not in _labels(sc)
        )
    children = sorted({c for s in slices for c in children_of.get(s, [])})

    pool = set(linked)
    pool |= g.search("task", [*slices, *children])
    for name in [*slices, *children]:
        pool |= g.outbound(g.read(name))
    pool -= set(slices) | set(children)

    anchors = [*slices, *children]
    named_by_edge = g.outbound(spec_sidecar)
    for name in anchors:
        named_by_edge |= g.outbound(g.read(name))

    followups, excluded = [], []
    for name in sorted(pool):
        sc = g.read(name)
        if name not in named_by_edge and not _names_any(sc, spec, anchors):
            continue
        if sc["status"] not in OPEN_STATUSES or sc.get("parent"):
            continue
        if SLICE_LABEL in _labels(sc) or FOLLOWUP_PARENT_LABEL in _labels(sc):
            excluded.append(name)
            continue
        title = sc.get("title")
        if not isinstance(title, str):
            raise GatherError("malformed-sidecar")
        followups.append(
            {"name": name, "title": sanitize_title(title), "status": sc["status"]}
        )
    return {
        "distilled": is_distilled(spec_sidecar),
        "followup-parents": live_parent_names(linked),
        "followups": followups,
        "slice-basis": basis,
        "slices": slices,
        "child-count": len(children),
        "candidates-read": len(pool),
        "kept": len(followups),
        "excluded-by-label": {"count": len(excluded), "names": excluded},
        "skipped-edges": len(g.skipped),
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise GatherError("invalid-arguments")


def _refuse(code: int, reason: str) -> int:
    assert reason in REASON_CODES, reason
    print(f"reason-code: {reason}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None, runner=None) -> int:
    try:
        parser = _Parser(
            prog="followup_gather.py", description="Decide whether a spec's slice loop has ended."
        )
        parser.add_argument("spec_name", help="bare spec record name (the part after spec/)")
        runner = runner or lore_runner
        try:
            name = parser.parse_args(argv).spec_name
            if not _shape_ok(name):
                raise GatherError("invalid-spec-name")
            vault, sidecar = resolve_spec(runner, name)
            entry = entry_verdict(sidecar)
            if entry is None:
                return _refuse(1, "slice-loop-not-ended")
            gathered = gather_followups(runner, vault, name, sidecar)
        except GatherError as err:
            return _refuse(2, err.reason)
        print(json.dumps({"spec": name, "vault": vault, "entry": entry, **gathered}))
        return 0
    except Exception:
        return _refuse(2, "internal-error")


if __name__ == "__main__":
    sys.exit(main())
