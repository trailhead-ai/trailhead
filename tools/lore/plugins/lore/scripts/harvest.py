"""Harvest-pending expansion.

`lore finish` reads `<vault>/harvest-pending.md` and the active session note's
`## Harvest candidates` block, then expands each typed one-liner of the five
in-scope types (deferred / decision / dead-end / radar / lesson) into a full
templated note in the matching vault directory.

Out of scope:
- `gotcha` entries are an area patch, not a standalone note. They are
  surfaced (returned for the finish report) and LEFT in `harvest-pending.md`.
- Malformed / unmarked lines are retained and warned, never silently consumed.

Idempotency: every entry carries a `<!-- h:<hash> -->` marker. A note is only
expanded if no note already records that hash (stamped into the rendered note's
frontmatter as `harvest-hash:`). Re-running is a no-op for already-expanded
hashes.

The caller (cmd_finish) is responsible for the atomic commit: this module only
writes notes to disk and reports which pending hashes were consumed. The pending
file is rewritten by the caller AS PART OF the same commit, so a failed commit
leaves the entries recoverable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from vault import bucket_dir, iter_note_paths

# Typed one-liner: `- <type>: <body>  <!-- h:<hash> -->` (marker optional here;
# unmarked typed lines are treated as malformed — they can't be deduped safely).
_ENTRY_RE = re.compile(
    r"^[ \t]*-[ \t]+(lesson|dead-end|deferred|radar|decision|gotcha):[ \t]*(.+?)"
    r"(?:[ \t]*<!--[ \t]*h:([a-f0-9]+)[ \t]*-->)?[ \t]*$"
)
_HASH_RE = re.compile(r"<!--[ \t]*h:([a-f0-9]+)[ \t]*-->")

# In-scope expandable types → vault directory.
_TYPE_DIRS: dict[str, str] = {
    "deferred": "deferred",
    "decision": "decisions",
    "dead-end": "dead-ends",
    "radar": "radar",
    "lesson": "lessons",
}

GOTCHA = "gotcha"


@dataclass
class Entry:
    kind: str
    body: str
    hash: str | None
    raw: str


@dataclass
class ExpansionResult:
    written: list[Path] = field(default_factory=list)
    consumed_hashes: set[str] = field(default_factory=set)
    gotchas: list[Entry] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---- parsing ---------------------------------------------------------------

def parse_entries(text: str) -> tuple[list[Entry], list[str]]:
    """Parse typed harvest one-liners out of a block of text.

    Returns (entries, malformed_lines). A line that starts like a list item
    (`- `) but does not match a typed-with-hash entry is malformed. Plain prose
    lines (headers, blank lines, the file preamble) are ignored, not malformed.
    A typed line missing its hash marker is malformed (can't be deduped safely).
    """
    entries: list[Entry] = []
    malformed: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        m = _ENTRY_RE.match(line)
        if not m:
            malformed.append(line)
            continue
        kind, body, h = m.group(1), m.group(2).strip(), m.group(3)
        if h is None:
            # Typed but unmarked — retain + warn rather than expand undeduped.
            malformed.append(line)
            continue
        entries.append(Entry(kind=kind, body=body, hash=h, raw=line))
    return entries, malformed


def session_candidates_block(note_text: str) -> str:
    """Return the body under a session note's `## Harvest candidates` heading."""
    lines = note_text.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.strip().lower().startswith("## harvest candidates"):
            capturing = True
            continue
        if capturing:
            if line.startswith("## "):
                break
            out.append(line)
    return "\n".join(out)


# ---- field extraction ------------------------------------------------------

def _split_body(body: str) -> tuple[str, dict[str, str]]:
    """Split a one-liner body into a leading clause + trailing labeled fields.

    The one-liner format is `<lead>. <Label>: <value>. <Label>: <value>.` —
    labeled fields are the `Word(s): value` segments at the tail. Returns the
    lead clause and a lowercased-label → value map. Parsing is best-effort:
    anything that doesn't look like a `Label: value` segment stays in the lead.
    """
    # Tokenize on sentence-ish boundaries while keeping label segments intact.
    segments = [s.strip() for s in re.split(r"(?<=\.)\s+", body) if s.strip()]
    lead_parts: list[str] = []
    fields: dict[str, str] = {}
    label_re = re.compile(r"^([A-Za-z][A-Za-z ]*?):\s*(.+?)\.?$")
    for seg in segments:
        m = label_re.match(seg)
        if m:
            fields[m.group(1).strip().lower()] = m.group(2).strip()
        else:
            lead_parts.append(seg)
    lead = " ".join(lead_parts).strip().rstrip(".")
    return lead, fields


def _title_from(lead: str, limit: int = 60) -> str:
    """Derive a short title from a lead clause."""
    title = lead.strip().rstrip(".")
    # Drop a leading verb-y prefix like "tried " / "chose " for readability.
    return title[:limit].strip() or "harvest-note"


# ---- rendering -------------------------------------------------------------

def _render(template_path: Path, subs: dict[str, str]) -> str:
    text = template_path.read_text()
    for key, value in subs.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _stamp_hash(rendered: str, h: str) -> str:
    """Insert `harvest-hash: <h>` into the note's frontmatter for dedup."""
    lines = rendered.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        # find closing fence
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines.insert(i, f"harvest-hash: {h}\n")
                return "".join(lines)
    return rendered


def _append_section(rendered: str, heading: str, text: str) -> str:
    """Append `text` as the first content line under an existing `## heading`."""
    if not text:
        return rendered
    lines = rendered.splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.strip().lower() == heading.strip().lower():
            out.append(text)
            inserted = True
    return "\n".join(out) + ("\n" if rendered.endswith("\n") else "")


def render_note(entry: Entry, templates_dir: Path, today: str, project: str) -> str:
    """Render a full templated note for an in-scope entry."""
    kind = entry.kind
    lead, fields = _split_body(entry.body)
    template = templates_dir / f"{kind}.md"

    if kind == "deferred":
        subs = {
            "project": project, "date": today, "status": "open",
            "surfaces": "[]", "next-check": "", "revisit-after": "",
        }
        rendered = _render(template, subs)
        rendered = _append_section(rendered, "## What", lead + ".")
        trigger = fields.get("trigger to revisit") or fields.get("trigger")
        rendered = _append_section(rendered, "## When to revisit", trigger or "")

    elif kind == "decision":
        subs = {"project": project, "date": today, "areas": "[]"}
        rendered = _render(template, subs)
        rendered = _append_section(rendered, "## Decision", lead + ".")
        # "chose X over Y because Z" — the reason is part of the lead; capture
        # any reversibility field into rationale.
        rationale = fields.get("reversibility")
        if rationale:
            rendered = _append_section(rendered, "## Rationale", f"Reversibility: {rationale}.")

    elif kind == "dead-end":
        subs = {
            "tried": today, "areas": "[]",
            "revive-condition": fields.get("revive if") or fields.get("revive") or "",
        }
        rendered = _render(template, subs)
        rendered = _append_section(rendered, "## What was tried", lead + ".")
        failed = fields.get("failed because")
        if failed:
            rendered = _append_section(rendered, "## Why it failed", failed + ".")
        revive = fields.get("revive if") or fields.get("revive")
        if revive:
            rendered = _append_section(rendered, "## What would make it worth retrying", revive + ".")

    elif kind == "radar":
        subs = {
            "project": project, "date": today,
            "source": "", "target": "",
            "check": fields.get("cadence") or "",
        }
        rendered = _render(template, subs)
        rendered = _append_section(rendered, "## What we're watching", lead + ".")
        why = fields.get("why")
        if why:
            rendered = _append_section(rendered, "## Why we care", why + ".")

    elif kind == "lesson":
        subs = {
            "project": project, "date": today,
            "areas": "[]", "severity": fields.get("confidence") or "",
        }
        rendered = _render(template, subs)
        rendered = _append_section(rendered, "## What we did wrong", lead + ".")
        why = fields.get("why it matters") or fields.get("why")
        if why:
            rendered = _append_section(rendered, "## How to prevent recurrence", why + ".")

    else:  # pragma: no cover - guarded by caller
        raise ValueError(f"not an in-scope type: {kind}")

    return _stamp_hash(rendered, entry.hash)


# ---- expansion -------------------------------------------------------------

def _kebab(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "harvest-note"


def _existing_hashes(vault: Path) -> set[str]:
    """Collect every harvest-hash already stamped into a vault note."""
    seen: set[str] = set()
    for sub in set(_TYPE_DIRS.values()):
        d = vault / sub
        if not d.is_dir():
            continue
        for p in iter_note_paths(d, recursive=True):
            try:
                head = p.read_text(encoding="utf-8")
            except Exception:
                continue
            m = re.search(r"^harvest-hash:\s*([a-f0-9]+)\s*$", head, re.MULTILINE)
            if m:
                seen.add(m.group(1))
    return seen


def _unique_path(directory: Path, stem: str) -> Path:
    candidate = directory / f"{stem}.md"
    if not candidate.exists():
        return candidate
    n = 2
    while (directory / f"{stem}-{n}.md").exists():
        n += 1
    return directory / f"{stem}-{n}.md"


def expand(
    vault: Path,
    pending_text: str,
    session_text: str,
    templates_dir: Path,
    today: str,
    project: str,
) -> ExpansionResult:
    """Expand in-scope entries from pending + the session candidates block.

    Writes notes to disk (idempotent by harvest-hash) and returns the result —
    consumed hashes, surfaced gotchas, malformed lines, and warnings. Does NOT
    touch the pending file or git; the caller owns the atomic commit.
    """
    result = ExpansionResult()

    pending_entries, pending_malformed = parse_entries(pending_text)
    session_entries, _ = parse_entries(session_candidates_block(session_text))

    result.malformed = pending_malformed
    for line in pending_malformed:
        result.warnings.append(f"harvest: retained malformed/unmarked line: {line.strip()}")

    already = _existing_hashes(vault)
    # Dedup across both sources by hash.
    seen_this_run: set[str] = set()

    for entry in pending_entries + session_entries:
        if entry.kind == GOTCHA:
            # Surface gotchas from both pending and the session note.
            # Pending gotchas are left in place (not consumed); session-note
            # gotchas live in the committed note verbatim — don't add to
            # consumed_hashes in either case.
            result.gotchas.append(entry)
            continue
        if entry.kind not in _TYPE_DIRS:
            continue
        h = entry.hash
        if h in already or h in seen_this_run:
            # Already expanded — but if it came from pending, still mark it
            # consumed so the stale pending line is cleared.
            if entry in pending_entries:
                result.consumed_hashes.add(h)
            seen_this_run.add(h)
            continue

        rendered = render_note(entry, templates_dir, today, project)
        lead, _ = _split_body(entry.body)
        stem = f"{today}-{_kebab(_title_from(lead))}"
        note_dir = bucket_dir(vault / _TYPE_DIRS[entry.kind], today)
        note_dir.mkdir(parents=True, exist_ok=True)
        path = _unique_path(note_dir, stem)
        path.write_text(rendered, encoding="utf-8")

        result.written.append(path)
        seen_this_run.add(h)
        if entry in pending_entries:
            result.consumed_hashes.add(h)

    return result


def rewrite_pending(pending_text: str, consumed_hashes: set[str]) -> str:
    """Return `pending_text` with every line carrying a consumed hash removed.

    Lines without a recognized hash marker (gotchas, malformed lines, headers,
    preamble) are preserved verbatim — only consumed in-scope entries go.
    """
    if not consumed_hashes:
        return pending_text
    kept: list[str] = []
    for line in pending_text.splitlines():
        m = _HASH_RE.search(line)
        if m and m.group(1) in consumed_hashes:
            continue
        kept.append(line)
    out = "\n".join(kept)
    if pending_text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out
