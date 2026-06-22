"""One-time, **throwaway** legacy-vault → v1 transcode core (Slice 1).

This module is the bespoke, single-use reader + in-memory transcoder for the
2026-06 lore existing-vault migration. It is deliberately **pure**: `read_legacy`
parses bytes off disk (plus a `git log` provenance lookup against the original
path), and `transcode` produces an in-memory :class:`Transcoded` — **no writes,
no placement, no orchestration** (those are Slices 2/3). Both this module and its
test suite are deleted in the post-cutover commit (plan "Post-cutover").

Why bespoke (A8): there is no `yaml` module in the tree, and the in-tree
`frontmatter.py` is deliberately lossy. The migration must faithfully transcode
the real, messy live-vault shapes — block scalars, multi-line block maps, flow
sequences, quoted/unquoted scalars, a `type:` that disagrees with the directory —
without **silent** misparse. The failure mode designed out (Security/Reliability,
council Critical #3) is a wrong-but-valid sidecar: when a **known-sidecar key**
carries an unclassifiable value shape, the transcoder emits ``Flag.review`` rather
than guessing.

Design (expanded for the KU-2 live-vault findings):

- **Kind is the directory, not the `type:` field** — the legacy `type:` is often
  stale (e.g. `designs/_TEMPLATE.md` declares `type: design`). The top-level
  directory drives kind consolidation (``KIND_BY_DIR``).
- **Known-sidecar keys** map to explicit S1 fields; **every other key** is dumped
  wholesale into ``annotations["legacy/<key>"]`` (no flag). A strict allowlist
  would abort on nearly every record (the live vault has 200+ distinct keys).
- **`related` (all 5 variants + block map + `{}`)** is converted to the typed S1
  ``related`` map (``kind -> [names]``); the raw legacy form never survives, and a
  recursive scan over the transcoded sidecar finds no ``[[`` wikilink syntax.
- **Status mapping** normalizes legacy vocab per target kind; blob-dir statuses
  collapse to ``active`` (``STATUS_VOCAB["blob"] == ("active",)``); an
  unclassifiable base value flags review.
- **Provenance**: ``created-at`` from the legacy ``date``; ``created-by`` from
  ``git log`` against the **original** path (captured before any rename — A-side
  council finding), with the current git email as the genuine-absence fallback.
"""
# NOTE: deliberately no ``from __future__ import annotations`` — the lore test
# harness loads scripts via ``conftest.load_script`` (importlib without
# registering in ``sys.modules``), and string annotations make the stdlib
# ``@dataclass`` machinery look the module up in ``sys.modules`` and crash when
# absent (see record_model.py's matching note). Every annotation here is a valid
# runtime expression on 3.11+ with no forward references.

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The S1/S2/S3 in-process write APIs (reused, never the CLI subprocess). Imported
# at module level so tests patch them on this module's collaborators (the project's
# DI convention). They resolve because conftest.load_script puts scripts/ on path.
import index_store
import record_model
import record_store
import vault as vault_mod

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Flag:
    """A review-required (or informational) condition surfaced by transcode.

    ``kind`` is one of ``"review"`` (Slice 2's abort gate fires) or ``"drop"``
    (the record is intentionally not migrated). ``detail`` is a human-readable
    one-liner for the Slice 2 summary.
    """

    kind: str
    detail: str

    @staticmethod
    def review(detail: str) -> "Flag":
        return Flag("review", detail)

    @staticmethod
    def drop(detail: str) -> "Flag":
        return Flag("drop", detail)


@dataclass(frozen=True)
class LegacyRecord:
    """A parsed legacy record: original path + the parsed frontmatter + body.

    ``frontmatter`` values are the bespoke reader's classified Python values
    (str / list / dict / None). ``body`` is the markdown after the closing
    ``---``, verbatim. ``directory`` is the top-level legacy directory (drives
    kind consolidation).
    """

    path: str
    directory: str
    frontmatter: dict
    body: str
    has_frontmatter: bool


@dataclass
class Transcoded:
    """The in-memory result of `transcode`: target kind + name + sidecar + body."""

    kind: str
    name: str
    sidecar: dict
    body: str
    flags: list[Flag] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Mapping tables (A12 / A13)
# ---------------------------------------------------------------------------

#: Legacy top-level directory → consolidated v1 kind. ``None`` means DROP.
KIND_BY_DIR: dict[str, str | None] = {
    "dead-ends": "lesson",
    "gotchas": "lesson",
    "lessons": "lesson",
    "lesson": "lesson",
    "deferred": "backlog",
    "follow-ups": "backlog",
    "tracking": "backlog",
    "inbox": "backlog",
    "backlog": "backlog",
    "tools": "area",
    "areas": "area",
    "decisions": "decision",
    "decision": "decision",
    "plans": "plan",
    "sessions": "session",
    "specs": "spec",
    "collaboration": "collaboration",
    "designs": "blob",
    "audits": "blob",
    "reviews": "blob",
    "ops": "blob",
    "briefings": None,
}

#: Directories whose mere non-emptiness must abort Phase A (manual extraction).
ABORT_GATE_DIRS: frozenset[str] = frozenset({"post-merge-incidents"})

#: The ~20 known-sidecar keys (everything else → annotations wholesale). These
#: are consumed by explicit transcode logic; they never land in annotations.
KNOWN_SIDECAR_KEYS: frozenset[str] = frozenset(
    {
        "type",
        "group",
        "date",
        "areas",
        "phases",
        "related",
        "raised-in",
        "source-spec",
        "source-plan",
        "last-reviewed",
        "severity",
        "closure-reason",
        "status",
        "revive-condition",
        "session_id",
        "worktree",
        "branch",
        "started",
        "ended",
        "phase",
    }
)

#: Per-kind status vocabulary (mirrors record_model.STATUS_VOCAB; the first
#: element is the kind's default). Duplicated here rather than imported because
#: this throwaway module must remain importable in isolation.
STATUS_VOCAB: dict[str, tuple[str, ...]] = {
    "area": ("active",),
    "backlog": ("open", "tracking", "dropped"),
    "blob": ("active",),
    "collaboration": ("active",),
    "decision": ("active", "superseded", "dropped"),
    "lesson": ("active", "conditional"),
    "plan": ("draft", "ready", "in-progress", "complete", "superseded", "dropped"),
    "session": ("active", "complete"),
    "spec": ("draft", "ready", "planned", "complete", "superseded", "dropped"),
}

#: Legacy status base value → v1 status, applied only when the value is not
#: already in the target kind's vocab. ``shelved`` is kind-sensitive (handled in
#: ``_map_status``).
_STATUS_REMAP: dict[str, str] = {
    "execute-active": "active",
    "plan-active": "active",
    "spec-active": "active",
    "review-active": "active",
    "accepted": "active",
    "final": "active",
    "proposed": "active",
    "skeleton": "active",
    "reference": "active",
    "disabled": "active",
    "enabled": "active",
    "graduated": "active",
    "iterating": "active",
    "resolved": "active",
    "stub": "draft",
    "": "active",
}


# ---------------------------------------------------------------------------
# Bespoke frontmatter reader
# ---------------------------------------------------------------------------

_FM_DELIM = "---"


def read_legacy(path: str | Path) -> LegacyRecord:
    """Parse a legacy ``<dir>/<file>.md`` into a :class:`LegacyRecord`.

    The bespoke stdlib reader copes with the messy live-vault frontmatter shapes:
    scalars, block scalars (``>-`` fold / ``|`` literal), flow sequences/maps, and
    multi-line block maps (``related:`` then indented sub-keys). A bare ``key:``
    parses to ``None``. Unparseable structure is recorded as ``has_frontmatter``
    True but leaves the offending value out; ``transcode`` decides whether that is
    review-worthy.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    directory = _legacy_directory(path)

    fm_text, body = _split_frontmatter(text)
    if fm_text is None:
        return LegacyRecord(
            path=str(path),
            directory=directory,
            frontmatter={},
            body=text,
            has_frontmatter=False,
        )
    frontmatter = _parse_frontmatter(fm_text)
    return LegacyRecord(
        path=str(path),
        directory=directory,
        frontmatter=frontmatter,
        body=body,
        has_frontmatter=True,
    )


def _legacy_directory(path: Path) -> str:
    """Resolve the legacy kind-directory for a record path.

    Live records sit under ``<kind-dir>/YYYY-MM/<file>.md`` (date buckets) or
    directly under ``<kind-dir>/<file>.md``. Walk the parents and return the
    first segment recognized as a legacy directory; fall back to the immediate
    parent so an unmapped directory still surfaces (and flags review).
    """
    parents = list(path.parts[:-1])
    for part in parents:
        if part in KIND_BY_DIR or part in ABORT_GATE_DIRS:
            return part
    return parents[-1] if parents else ""


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return ``(frontmatter_text, body)``; ``(None, text)`` when no ``---`` block."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != _FM_DELIM:
        return None, text
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n") == _FM_DELIM:
            fm_text = "".join(lines[1:i])
            body = "".join(lines[i + 1 :])
            return fm_text, body
    return None, text


def _parse_frontmatter(fm_text: str) -> dict:
    """Parse the frontmatter block into a dict of classified Python values."""
    result: dict[str, Any] = {}
    lines = fm_text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # Skip blank lines and any stray indented lines not consumed by a parent.
        if not line.strip() or line[:1] in (" ", "\t"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()

        # Block scalar: `key: >-` (fold) or `key: |` (literal).
        if raw in (">", ">-", "|", "|-"):
            value, i = _collect_block_scalar(lines, i + 1, fold=raw.startswith(">"))
            result[key] = value
            continue

        # Bare key (`key:`): either an empty value or a multi-line block map.
        if raw == "":
            block_map, next_i = _collect_block_map(lines, i + 1)
            if block_map is not None:
                result[key] = block_map
                i = next_i
                continue
            result[key] = None
            i += 1
            continue

        result[key] = _classify_scalar(raw)
        i += 1
    return result


def _collect_block_scalar(lines: list[str], start: int, *, fold: bool) -> tuple[str, int]:
    """Collect indented continuation lines into a single string from ``start``."""
    collected: list[str] = []
    i = start
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.strip() == "":
            collected.append("")
            i += 1
            continue
        if line[:1] not in (" ", "\t"):
            break
        collected.append(line.strip())
        i += 1
    # Drop trailing blank lines.
    while collected and collected[-1] == "":
        collected.pop()
    if fold:
        value = " ".join(part for part in collected if part != "")
    else:
        value = "\n".join(collected)
    return value, i


def _collect_block_map(lines: list[str], start: int) -> tuple[dict | None, int]:
    """Collect an indented ``  subkey: value`` block map from ``start``.

    Returns ``(None, start)`` when the next line is not an indented ``key: value``
    pair (so the parent key is a bare/empty value, not a map).
    """
    if start >= len(lines):
        return None, start
    first = lines[start]
    if first[:1] not in (" ", "\t") or ":" not in first:
        # Could be an indented list (`  - item`) — not a block map for our needs.
        return None, start
    block: dict[str, Any] = {}
    i = start
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue
        if line[:1] not in (" ", "\t"):
            break
        if ":" not in line:
            i += 1
            continue
        sub_key, _, sub_raw = line.partition(":")
        block[sub_key.strip()] = _classify_scalar(sub_raw.strip())
        i += 1
    return (block or None), i


def _classify_scalar(raw: str) -> Any:
    """Classify a single-line value into str / list / dict / None."""
    if raw == "":
        return None
    if raw == "{}":
        return {}
    if raw == "[]":
        return []
    # A bare wikilink `[[target]]` also starts/ends with brackets — classify it
    # as a scalar string (not a flow sequence) so the wikilink survives intact.
    if raw.startswith("[[") and raw.endswith("]]"):
        return raw
    if raw.startswith("{") and raw.endswith("}"):
        return _parse_flow_map(raw)
    if raw.startswith("[") and raw.endswith("]"):
        return _parse_flow_sequence(raw)
    return _unquote(raw)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_flow_sequence(raw: str) -> list:
    inner = raw[1:-1].strip()
    if inner == "":
        return []
    return [_unquote(item.strip()) for item in _split_top_level(inner)]


def _parse_flow_map(raw: str) -> dict:
    inner = raw[1:-1].strip()
    out: dict[str, Any] = {}
    if inner == "":
        return out
    for part in _split_top_level(inner):
        sub_key, _, sub_val = part.partition(":")
        out[_unquote(sub_key.strip())] = _classify_scalar(sub_val.strip())
    return out


def _split_top_level(inner: str) -> list[str]:
    """Split on commas not nested inside ``[]``/``{}``/quotes."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    buf: list[str] = []
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch in ("[", "{"):
            depth += 1
        elif ch in ("]", "}"):
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


# ---------------------------------------------------------------------------
# Transcode
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def transcode(legacy: LegacyRecord) -> Transcoded:
    """Transcode a :class:`LegacyRecord` into a :class:`Transcoded` (in-memory).

    Applies kind consolidation, status mapping, 5-variant wikilink conversion,
    provenance synthesis, lossy-key rehome, the revive-condition→status rule,
    breadcrumb folding, and session naming. Emits ``Flag``s for review-required
    and DROP conditions; writes nothing.
    """
    flags: list[Flag] = []
    fm = legacy.frontmatter
    directory = legacy.directory
    body_notes: list[str] = []

    # --- DROP and abort-gate dispositions. ----------------------------------
    if directory in ABORT_GATE_DIRS:
        flags.append(Flag.review(f"{legacy.path}: pending {directory}/ extraction (abort gate)"))
        return Transcoded(kind="", name="", sidecar={}, body=legacy.body, flags=flags)
    if directory in KIND_BY_DIR and KIND_BY_DIR[directory] is None:
        flags.append(Flag.drop(f"{legacy.path}: {directory}/ records are dropped"))
        return Transcoded(kind="", name="", sidecar={}, body=legacy.body, flags=flags)

    kind = KIND_BY_DIR.get(directory)
    if kind is None:
        flags.append(Flag.review(f"{legacy.path}: unmapped directory {directory!r}"))
        kind = "blob"

    sidecar: dict[str, Any] = {"version": "v1", "kind": kind, "keywords": []}
    annotations: dict[str, str] = {}

    # --- Title (S1 required). Synthesize from the filename stem. -------------
    sidecar["title"] = _title_from_path(legacy.path)

    # --- Status. ------------------------------------------------------------
    status_value = fm.get("status")
    status, status_prose, status_flag = _map_status(status_value, kind)
    if status_flag is not None:
        flags.append(Flag.review(f"{legacy.path}: {status_flag}"))
    sidecar["status"] = status
    if status_prose:
        body_notes.append(status_prose)

    # --- Provenance: created-at (legacy date) + created-by (git log). --------
    created_at = _iso_datetime(fm.get("date"))
    if created_at:
        sidecar["created-at"] = created_at
    sidecar["created-by"] = _git_author(legacy.path)

    # --- related: 5-variant wikilink conversion. ----------------------------
    related, unresolved, related_flag = _convert_related(fm.get("related"))
    if related_flag is not None:
        flags.append(Flag.review(f"{legacy.path}: {related_flag}"))
    # Single-target wikilink keys (raised-in / source-plan / source-spec).
    for legacy_key, rel_kind in (("raised-in", "session"), ("source-plan", "plan"), ("source-spec", "spec")):
        target = _extract_single_target(fm.get(legacy_key))
        if target:
            related.setdefault(rel_kind, []).append(target)
    if related:
        sidecar["related"] = related

    # --- revive-condition → lesson status + body line. ----------------------
    if kind == "lesson":
        revive = fm.get("revive-condition")
        if isinstance(revive, str) and revive.strip() and revive.strip().lower() != "never":
            sidecar["status"] = "conditional"
            body_notes.append(f"**Revisit when:** {revive.strip()}")

    # --- Lossy-key rehome (severity / closure-reason). ----------------------
    for lossy in ("severity", "closure-reason"):
        val = fm.get(lossy)
        if isinstance(val, str) and val.strip():
            annotations[f"legacy/{lossy}"] = val

    # --- Unknown keys → annotations wholesale (no flag). --------------------
    for key, value in fm.items():
        if key in KNOWN_SIDECAR_KEYS:
            continue
        rendered = _render_annotation(value)
        if rendered is not None:
            annotations[f"legacy/{key}"] = rendered

    if annotations:
        sidecar["annotations"] = annotations

    # --- Session naming (name == session_id; missing → review). -------------
    if kind == "session":
        session_id = fm.get("session_id")
        if isinstance(session_id, str) and session_id.strip():
            name = session_id.strip()
        else:
            name = ""
            flags.append(Flag.review(f"{legacy.path}: session missing session_id"))
    else:
        name = _name_from_path(legacy.path)

    # --- Breadcrumb folding (unresolved links + status prose). --------------
    for target in unresolved:
        body_notes.append(f"**Migration note:** unresolved related target `{target}`")

    body = _fold_breadcrumb(legacy.body, body_notes)

    return Transcoded(kind=kind, name=name, sidecar=sidecar, body=body, flags=flags)


def _title_from_path(path: str) -> str:
    return Path(path).stem


def _name_from_path(path: str) -> str:
    return Path(path).stem


def _iso_datetime(value: Any) -> str | None:
    """Coerce a legacy ``date`` value into an ISO-8601 UTC string, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    # Already a full timestamp (`...T...Z` or with offset) — pass through.
    if "T" in raw:
        return raw
    # Date-only (`2026-06-04`) → midnight UTC.
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return f"{raw}T00:00:00Z"
    return raw


def _git_author(path: str) -> str:
    """Resolve the historical author email via `git log --follow` on ``path``.

    Falls back to the repo's configured ``user.email`` when the path has no
    history (a genuinely new/unstaged record). The fallback is deliberately the
    LAST resort (council Security finding): running git on the post-rename path
    would silently collapse all authorship to it.
    """
    p = Path(path)
    cwd = str(p.parent)
    out = _run_git(["log", "--follow", "--format=%ae", "-n", "1", "--", p.name], cwd)
    if out:
        return out
    return _run_git(["config", "user.email"], cwd)


def _run_git(args: list[str], cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return ""
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""


def _map_status(value: Any, kind: str) -> tuple[str, str | None, str | None]:
    """Map a legacy status value → ``(status, body_prose, flag_detail)``.

    Compound/prose-suffixed values (``base | suffix — prose``) keep the base and
    move the prose to a breadcrumb. Blob-dir kinds collapse everything to the only
    vocab value. An unclassifiable base flags review.
    """
    vocab = STATUS_VOCAB[kind]
    default = vocab[0]

    if value is None:
        return default, None, None
    if not isinstance(value, str):
        return default, None, f"status has unrecognizable shape {value!r}"

    raw = value.strip()
    if raw == "":
        return default, None, None

    # Compound: split base off the `|` and capture the em-dash prose tail.
    base = re.split(r"\s*\|", raw, maxsplit=1)[0].strip()
    prose: str | None = None
    if "—" in raw:
        prose = raw.split("—", 1)[1].strip()
    if prose:
        prose = f"**Migration note:** prior status — {prose}"

    # blob has a single vocab value; everything semantically means "it exists".
    if kind == "blob":
        return default, prose, None

    if base in vocab:
        return base, prose, None

    if base == "shelved":
        if kind in ("plan", "spec"):
            mapped = "superseded"
        elif kind == "session":
            mapped = "complete"
        else:
            mapped = default
        return (mapped if mapped in vocab else default), prose, None

    remapped = _STATUS_REMAP.get(base)
    if remapped is not None and remapped in vocab:
        return remapped, prose, None
    if remapped is not None:
        # Remap target not valid for this kind → fall to default, no flag.
        return default, prose, None

    # One-shot / scheduled prefixes (ops dir, blob) already returned above.
    return default, prose, f"unmapped status base {base!r} for kind {kind!r}"


def _convert_related(value: Any) -> tuple[dict, list[str], str | None]:
    """Convert the legacy ``related`` value into a typed ``kind -> [names]`` map.

    Handles: ``None``/``{}`` (no refs), flow map (variant 2), flow sequence
    (variant 3), block map (variant 5), and a bare/aliased scalar wikilink
    (variants 1 & 4). An unclassifiable shape returns a flag.
    """
    if value is None or value == {} or value == []:
        return {}, [], None

    related: dict[str, list[str]] = {}

    if isinstance(value, dict):
        # Variants 2 & 5: sub-key is a (dir-mapped) kind, value is wikilink(s).
        for sub_key, sub_val in value.items():
            rel_kind = _dir_or_kind(sub_key)
            for target in _extract_targets(sub_val):
                related.setdefault(rel_kind, []).append(target)
        return related, [], None

    if isinstance(value, list):
        # Variant 3: flow sequence of wikilink strings → infer kind from target.
        for item in value:
            for target in _extract_targets(item):
                related.setdefault(_kind_from_target(target), []).append(target)
        return related, [], None

    if isinstance(value, str):
        # Variants 1 & 4: pipe-aliased or bare scalar.
        targets = _extract_targets(value)
        for target in targets:
            related.setdefault(_kind_from_target(target), []).append(target)
        return related, [], None

    return {}, [], f"unrecognizable related shape {value!r}"


def _extract_targets(value: Any) -> list[str]:
    """Extract wikilink targets (alias-stripped) from a scalar/list value."""
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_extract_targets(item))
        return out
    if not isinstance(value, str):
        return []
    raw = value.strip()
    if not raw:
        return []
    matches = _WIKILINK_RE.findall(raw)
    if matches:
        return [_strip_alias(m) for m in matches]
    # Bare scalar target (variant 4) — no brackets.
    return [_strip_alias(raw)]


def _extract_single_target(value: Any) -> str | None:
    targets = _extract_targets(value)
    return targets[0] if targets else None


def _strip_alias(target: str) -> str:
    return target.split("|", 1)[0].strip()


#: Singular `related:` sub-key forms used in the live vault (the block-map keys
#: are the singular legacy kind name, e.g. ``dead-end:``) → consolidated v1 kind.
_RELATED_SUBKEY_KIND: dict[str, str] = {
    "dead-end": "lesson",
    "gotcha": "lesson",
    "lesson": "lesson",
    "deferred": "backlog",
    "follow-up": "backlog",
    "tool": "area",
    "area": "area",
    "decision": "decision",
    "plan": "plan",
    "session": "session",
    "spec": "spec",
    "collaboration": "collaboration",
    "blob": "blob",
    "backlog": "backlog",
}


def _dir_or_kind(sub_key: str) -> str:
    """Map a `related:` sub-key (a legacy dir name or a kind) to a v1 kind."""
    if sub_key in _RELATED_SUBKEY_KIND:
        return _RELATED_SUBKEY_KIND[sub_key]
    if sub_key in KIND_BY_DIR and KIND_BY_DIR[sub_key] is not None:
        return KIND_BY_DIR[sub_key]  # type: ignore[return-value]
    if sub_key in STATUS_VOCAB:
        return sub_key
    # Unknown sub-key — keep it as a kind-ish bucket; S1 will reject if invalid,
    # but the dominant live forms are all dir/kind names.
    return sub_key


def _kind_from_target(target: str) -> str:
    """Infer the related kind from a wikilink target's leading directory."""
    first = target.split("/", 1)[0]
    mapped = KIND_BY_DIR.get(first)
    if mapped:
        return mapped
    if first in STATUS_VOCAB:
        return first
    return "blob"


def _render_annotation(value: Any) -> str | None:
    """Render a legacy value into a string for annotations; None drops it."""
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items())
    return str(value)


def _fold_breadcrumb(body: str, notes: list[str]) -> str:
    """Prepend migration breadcrumb note lines to the body (never as frontmatter)."""
    if not notes:
        return body
    block = "\n".join(notes)
    return f"{block}\n\n{body.lstrip(chr(10))}"


# ---------------------------------------------------------------------------
# Slice 2 — write orchestrator + pre-write summary + abort gate
#
# The cutover runs as two strictly-ordered phases so the abort gate can never
# leave the vault half-written:
#   Preflight  — vault git tree clean + committer email resolves (else abort).
#   Phase A    — walk + transcode + place + validate EVERY record; write NOTHING.
#   Summary    — review-required items first, then informational counts; an
#                aborting run ends with a numbered "What to do next" block.
#   Abort gate — any review-required item (validation failure / Flag.review /
#                non-empty post-merge-incidents/) exits non-zero, writes nothing.
#                DROP records (Flag.drop) are itemized for acknowledgment but do
#                NOT abort (git is the safety net for the destructive drop, A11).
#   Phase B    — write each non-DROP record in place via validate_and_write;
#                print `wrote N of M`; on any mid-pass raise, print the split-
#                state recovery banner to stderr and exit non-zero. After a clean
#                pass, rebuild the index in-process.
#
# This whole module (and its tests) is deleted in the post-cutover commit.
# ---------------------------------------------------------------------------


@dataclass
class _Planned:
    """One record's Phase-A plan: the transcode + its computed write location."""

    source_path: str
    transcoded: Transcoded
    location: Any  # record_store.RecordLocation
    validation_errors: list[str]


@dataclass
class _Plan:
    """The full Phase-A result: what to write, what to drop, what blocks the run."""

    to_write: list[_Planned] = field(default_factory=list)
    drops: list[Flag] = field(default_factory=list)
    drop_paths: list[str] = field(default_factory=list)
    review_flags: list[Flag] = field(default_factory=list)
    validation_failures: list[tuple[str, list[str]]] = field(default_factory=list)
    incident_count: int = 0
    lossy_count: int = 0
    kind_moves: int = 0

    @property
    def blocked(self) -> bool:
        return bool(self.review_flags or self.validation_failures or self.incident_count)


def run_migration(vault_root: str, *, dry_run: bool = False) -> int:
    """Migrate a legacy vault in place to the v1 body + JSON sidecar model.

    Main entry point; returns an exit code (0 = success, 1 = abort/error). Runs
    the preflight + Phase A (plan/validate, no writes) + the pre-write summary +
    the abort gate, then — only if Phase A is clean and ``dry_run`` is False —
    Phase B (write each non-DROP record in place) followed by an in-process index
    rebuild. ``dry_run`` stops after the summary (used by tests to exercise the
    gate without touching the vault).
    """
    root = Path(vault_root)

    # --- Preflight 1: vault git working tree must be clean. -----------------
    dirty = _git_porcelain(root)
    if dirty is None:
        print(f"abort: {root} is not a git repository (git is the only safety net)")
        return 1
    if dirty:
        print(
            f"abort: vault working tree at {root} is dirty — commit or stash first "
            "(git reset --hard cannot recover uncommitted edits)"
        )
        return 1

    # --- Preflight 2: committer email must resolve (A5) before Phase B. -----
    email = vault_mod.resolve_committer_email()
    if not email:
        print(
            "abort: committer email is unset — set $LORE_EMAIL or "
            "`git config --global user.email` before migrating "
            "(provenance is required and cannot be defaulted)"
        )
        return 1

    # --- Phase A: plan + validate; write NOTHING. --------------------------
    plan = _plan_phase_a(root)

    # --- Pre-write summary (review-required first, then informational). -----
    _print_summary(plan, root)

    # --- Abort gate. -------------------------------------------------------
    if plan.blocked:
        return 1

    if dry_run:
        return 0

    # --- Phase B: write in place, then rebuild the index. ------------------
    return _write_phase_b(plan, root)


def _git_porcelain(root: Path) -> str | None:
    """Return ``git status --porcelain`` output for *root* (empty = clean).

    Returns ``None`` when *root* is not a git repo (no safety net to abort against).
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _plan_phase_a(root: Path) -> _Plan:
    """Walk every ``.md`` under *root*, transcode, place, and validate. No writes."""
    plan = _Plan()
    for md_path in sorted(root.rglob("*.md")):
        if ".git" in md_path.parts:
            continue
        legacy = read_legacy(md_path)
        transcoded = transcode(legacy)

        # Partition the transcode flags.
        is_drop = False
        for flag in transcoded.flags:
            if flag.kind == "drop":
                plan.drops.append(flag)
                plan.drop_paths.append(str(md_path))
                is_drop = True
            else:  # "review"
                plan.review_flags.append(flag)

        # Abort-gate directories (post-merge-incidents/) surface as review flags
        # and produce no kind/name — count them and move on (never placed).
        if not transcoded.kind:
            if legacy.directory in ABORT_GATE_DIRS:
                plan.incident_count += 1
            continue

        if is_drop:
            continue

        # Informational counts.
        if KIND_BY_DIR.get(legacy.directory) != legacy.directory:
            plan.kind_moves += 1
        annotations = transcoded.sidecar.get("annotations", {})
        if "legacy/severity" in annotations or "legacy/closure-reason" in annotations:
            plan.lossy_count += 1

        location = record_store.place_record(
            transcoded.name, transcoded.kind, scope=None, vault_root=str(root)
        )
        result = record_model.validate(transcoded.sidecar, kind=transcoded.kind)
        if result.errors:
            plan.validation_failures.append((str(md_path), list(result.errors)))
        plan.to_write.append(
            _Planned(
                source_path=str(md_path),
                transcoded=transcoded,
                location=location,
                validation_errors=list(result.errors),
            )
        )
    return plan


def _print_summary(plan: _Plan, root: Path) -> None:
    """Print the pre-write summary: review-required first, then informational."""
    print("=== lore vault migration — pre-write summary ===")
    print("")
    print("Review-required items:")
    if not (plan.validation_failures or plan.review_flags or plan.incident_count or plan.drops):
        print("  (none)")
    else:
        if plan.incident_count:
            print(
                f"  - {plan.incident_count} record(s) pending extraction in "
                f"post-merge-incidents/ (manual lesson-extraction backlog)"
            )
        if plan.validation_failures:
            print(f"  - {len(plan.validation_failures)} record(s) fail S1 validation:")
            for path, errors in plan.validation_failures:
                print(f"      {path}: {'; '.join(errors)}")
        if plan.review_flags:
            print(f"  - {len(plan.review_flags)} record(s) flagged for review:")
            for flag in plan.review_flags:
                print(f"      {flag.detail}")
        if plan.drops:
            print(
                f"  - {len(plan.drops)} record(s) will be DROPPED (destructive — "
                "recover via git if unintended):"
            )
            for flag in plan.drops:
                print(f"      {flag.detail}")

    print("")
    print("Informational counts:")
    print(f"  - records to migrate: {len(plan.to_write)}")
    print(f"  - kind moves (consolidated to a new kind): {plan.kind_moves}")
    print(f"  - lossy rehomes (severity/closure-reason → annotations): {plan.lossy_count}")
    print(f"  - planned relocation: in place at {root} (canonical move is a later step)")

    if plan.blocked:
        _print_next_steps(plan)


def _print_next_steps(plan: _Plan) -> None:
    """Numbered "What to do next" block, ordered by fix-before-rerun priority."""
    print("")
    print("ABORTED — write nothing. What to do next:")
    step = 1
    if plan.incident_count:
        print(
            f"  {step}. extract lessons from {plan.incident_count} incident(s) at "
            "post-merge-incidents/, then delete the raw reports"
        )
        step += 1
    sessions_missing = sum(1 for f in plan.review_flags if "session_id" in f.detail)
    if sessions_missing:
        print(f"  {step}. resolve {sessions_missing} session(s) missing session_id")
        step += 1
    other_flags = len(plan.review_flags) - sessions_missing
    if other_flags > 0:
        print(f"  {step}. resolve {other_flags} flagged record(s) (links / status / unmapped)")
        step += 1
    if plan.validation_failures:
        print(f"  {step}. fix {len(plan.validation_failures)} record(s) failing validation")
        step += 1
    print(f"  {step}. then re-run the migration")


def _write_phase_b(plan: _Plan, root: Path) -> int:
    """Phase B: write each planned record in place, then rebuild the index."""
    total = len(plan.to_write)
    conn = index_store.open_index()
    try:
        written = 0
        for planned in plan.to_write:
            try:
                record_store.validate_and_write(
                    planned.location, planned.transcoded.sidecar, planned.transcoded.body, conn
                )
                # The new flat record is durable — retire the legacy source so the
                # whole cutover is one reviewable rename in `git diff`.
                _retire_legacy_source(planned.source_path, planned.location.body_path)
            except Exception as exc:  # noqa: BLE001 — any raise means a split vault.
                conn.commit()
                print(
                    f"wrote {written} of {total}; vault is in a SPLIT state "
                    "(some records v1, some legacy); recover with: "
                    "git reset --hard <pre-migrate-commit>",
                    file=sys.stderr,
                )
                print(f"  cause: {exc}", file=sys.stderr)
                return 1
            written += 1
            print(f"wrote {written} of {total}")
        # DROP records are intentionally not migrated — remove the legacy source
        # (recoverable via the pre-migrate git commit; itemized in the summary).
        for drop_path in plan.drop_paths:
            _unlink_quietly(Path(drop_path))
        _prune_empty_dirs(root)
        # Clean write pass — rebuild the index in-process over the canonical tree.
        index_store.rebuild([str(root)], conn)
        conn.commit()
    finally:
        conn.close()
    return 0


def _retire_legacy_source(source_path: str, dest_body_path: Path) -> None:
    """Remove the legacy ``.md`` source unless it IS the new flat destination."""
    source = Path(source_path)
    if source.resolve() == Path(dest_body_path).resolve():
        return
    _unlink_quietly(source)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _prune_empty_dirs(root: Path) -> None:
    """Remove now-empty legacy directories (date buckets + kind dirs) under root.

    Walks bottom-up so a date bucket is removed before its parent kind dir. The
    vault root and ``.git`` are never removed.
    """
    for dir_path in sorted(
        (p for p in root.rglob("*") if p.is_dir() and ".git" not in p.parts),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        try:
            next(dir_path.iterdir())
        except StopIteration:
            dir_path.rmdir()
        except OSError:
            pass
