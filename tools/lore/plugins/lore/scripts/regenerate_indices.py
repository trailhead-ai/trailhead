#!/usr/bin/env python3
"""Regenerate per-folder _index.md files in the lore vault.

Folders covered:
  - deferred/  → sorted by value/effort/date (open), then closed section
  - radar/     → sorted by revisit-after (then added, last-checked) descending
  - lessons/   → sorted by date descending; grouped by primary area
  - plans/     → grouped by status (in-progress / ready / completed / unknown)
  - specs/     → grouped by status
  - designs/   → grouped by status

Within each plan/spec/design status bucket, items are sorted by `updated`
then `created` (then filename) descending.

Frontmatter is parsed with a small hand-rolled YAML reader (only the fields
we care about — no PyYAML dependency). Files without recognized frontmatter
land in an "Uncategorized" bucket so they're visible.

Invoked from the vault pre-commit hook before every vault commit.
Idempotent — safe to run any time.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

VAULT = Path(os.environ.get("LORE_VAULT", Path.home() / "lore"))

# ---- Status bucketing for plans/specs/designs --------------------------------

IN_PROGRESS = {"in-progress", "draft", "active"}
SHELVED = {"shelved"}
READY = {"ready", "planned"}
COMPLETED = {"complete", "superseded", "dropped", "archived", "graduated", "resolved"}

BUCKET_ORDER = ["In progress", "Shelved", "Ready", "Completed", "Uncategorized"]


def classify_status(raw: str | None) -> str:
    if not raw:
        return "Uncategorized"
    s = raw.strip().strip("'\"").lower()
    s = s.split("#", 1)[0].strip()
    if "|" in s:
        return "Uncategorized"
    if s in IN_PROGRESS:
        return "In progress"
    if s in SHELVED:
        return "Shelved"
    if s in READY:
        return "Ready"
    if s in COMPLETED:
        return "Completed"
    return "Uncategorized"


# ---- Frontmatter parsing -----------------------------------------------------

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
SCALAR_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$")


def parse_frontmatter(text: str) -> dict:
    """Tiny YAML-ish reader for the scalar fields we need.

    Handles:
      - simple `key: value` lines
      - `key: >-` / `key: >` folded blocks (joins the indented continuation)
      - quoted values
      - inline comments after the value (stripped)
    Ignores nested mappings, lists, and anchors — we don't need them.
    """
    m = FM_RE.match(text)
    if not m:
        return {}
    body = m.group(1)
    lines = body.split("\n")
    out: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line.startswith(" ") or line.startswith("\t") or line.startswith("-"):
            i += 1
            continue
        sm = SCALAR_RE.match(line)
        if not sm:
            i += 1
            continue
        key, val = sm.group(1), sm.group(2)
        if val in (">", ">-", "|", "|-"):
            i += 1
            collected: list[str] = []
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t") or not lines[i].strip()):
                stripped = lines[i].strip()
                if stripped:
                    collected.append(stripped)
                i += 1
            out[key] = " ".join(collected)
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
        i += 1
    return out


# ---- Helpers -----------------------------------------------------------------

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def first_date(*candidates: str | None) -> str:
    """Return the first ISO-ish date found in the candidate strings, or ''."""
    for c in candidates:
        if not c:
            continue
        m = DATE_RE.search(c)
        if m:
            return m.group(0)
    return ""


def first_h1(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# ") and not line.startswith("# ---"):
            return line[2:].strip()
    return ""


BOLD_LABEL_RE = re.compile(r"^\*\*[^*]+:\*\*")


def first_paragraph_after_headings(text: str, max_chars: int = 140) -> str:
    """Grab a one-line summary — first non-heading, non-metadata line of body."""
    body = FM_RE.sub("", text, count=1).lstrip()
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith(">"):
            continue
        if BOLD_LABEL_RE.match(line):
            continue
        if line.startswith("|") or line.startswith("---"):
            continue
        line = re.sub(r"\*\*", "", line)
        line = line.replace("|", "\\|")
        if len(line) > max_chars:
            line = line[: max_chars - 1].rstrip() + "…"
        return line
    return ""


def _iter_md_paths(folder: Path, recursive: bool):
    """Yield note paths in *folder*, skipping `_`-prefixed files.

    Flat by default. When *recursive*, also descends exactly one level into
    `YYYY-MM/` month buckets (the date-bucketed archive layout), skipping
    `_`-prefixed subdirs (`_archive/`, `_test/`). Bounded to one level — never
    rglob — so deeper or special dirs are never descended.
    """
    for p in folder.glob("*.md"):
        if not p.name.startswith("_"):
            yield p
    if recursive:
        for sub in folder.iterdir():
            if not sub.is_dir() or sub.name.startswith("_"):
                continue
            for p in sub.glob("*.md"):
                if not p.name.startswith("_"):
                    yield p


def load_md_files(folder: Path, recursive: bool = False) -> list[tuple[Path, str, dict]]:
    out = []
    for p in sorted(_iter_md_paths(folder, recursive)):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        out.append((p, text, parse_frontmatter(text)))
    return out


def link(p: Path) -> str:
    """Emit a resolvable vault-relative wikilink.

    For a flat note `plans/foo.md` → `[[plans/foo]]`; for a bucketed note
    `plans/2026-06/foo.md` → `[[plans/2026-06/foo]]`. Using the full
    vault-relative path (not just `p.parent.name`) keeps the link resolvable
    after the date-bucketing move — `[[2026-06/foo]]` would not resolve.
    """
    try:
        rel = p.relative_to(VAULT).with_suffix("")
        return f"[[{rel.as_posix()}]]"
    except ValueError:
        return f"[[{p.parent.name}/{p.stem}]]"


def link_with_title(p: Path, title: str) -> str:
    """Link to the note. Append a human title only if it adds info beyond the slug."""
    base = link(p)
    if not title:
        return base
    norm_title = re.sub(r"[^a-z0-9]+", "", title.lower())
    norm_stem = re.sub(r"[^a-z0-9]+", "", p.stem.lower())
    if norm_title == norm_stem:
        return base
    return f"{base} — {title}"


# ---- Per-folder generators ---------------------------------------------------

VALUE_RANK = {"high": 0, "medium": 1, "low": 2}
EFFORT_RANK = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4}
TERMINAL_STATUSES = {"resolved", "dropped", "graduated", "archived", "superseded"}


def _neg_date(d: str) -> str:
    """Return a sort key that reverses date ordering (newer = lower key)."""
    if not d:
        return "~"
    return "".join(chr(255 - ord(c)) for c in d)


def render_dated_index(folder_name: str, folder: Path, date_keys: list[str]) -> str:
    """For radar: list sorted by chosen date desc.

    Radar is a date-bucketed living folder, so the scan recurses one level into
    `YYYY-MM/` buckets while still listing any still-flat top-level notes.
    """
    items = []
    for p, text, fm in load_md_files(folder, recursive=True):
        d = first_date(*[fm.get(k) for k in date_keys])
        title = first_h1(text) or p.stem
        summary = first_paragraph_after_headings(text)
        status = (fm.get("status") or "").strip().strip("'\"")
        items.append((d, p, title, summary, status))

    items.sort(key=lambda it: (it[0] or "", it[1].name), reverse=True)

    lines = [
        f"# {folder_name.capitalize()} — index",
        "",
        f"_Auto-generated by `plugins/lore/scripts/regenerate_indices.py`. Sorted by follow-up date (descending). Edit source files, not this index._",
        "",
        f"**Total:** {len(items)}",
        "",
    ]
    if not items:
        lines.append("_(empty)_")
        return "\n".join(lines) + "\n"

    primary_key = date_keys[0]
    lines.append(f"| {primary_key} | item | status | summary |")
    lines.append("| --- | --- | --- | --- |")
    for d, p, title, summary, status in items:
        d_disp = d or "—"
        st_disp = status or "—"
        lines.append(f"| {d_disp} | {link_with_title(p, title)} | {st_disp} | {summary} |")
    return "\n".join(lines) + "\n"


def render_deferred_index(folder_name: str, folder: Path) -> str:
    """Deferred: sort by value (high>med>low), then effort (XS first), then date desc.

    Items with the same `consolidation-group` cluster together within their
    (value, effort) tier. Closed items (status in TERMINAL_STATUSES) sink to
    a bottom section.
    """
    open_items: list[tuple] = []
    closed_items: list[tuple] = []
    for p, text, fm in load_md_files(folder, recursive=True):
        d = first_date(fm.get("revisit-after"), fm.get("raised"), p.name)
        title = first_h1(text) or p.stem
        summary = first_paragraph_after_headings(text)
        status = (fm.get("status") or "open").strip().strip("'\"").lower()
        value = (fm.get("value") or "").strip().strip("'\"").lower() or "—"
        effort = (fm.get("effort") or "").strip().strip("'\"").upper() or "—"
        group = (fm.get("consolidation-group") or "").strip().strip("'\"")
        revisit = first_date(fm.get("revisit-after")) or "—"
        row = (value, effort, group, d, p, title, summary, status, revisit)
        if status in TERMINAL_STATUSES:
            closed_items.append(row)
        else:
            open_items.append(row)

    def sort_key(it):
        value, effort, group, d, p, *_ = it
        v_rank = VALUE_RANK.get(value, 99)
        e_rank = EFFORT_RANK.get(effort, 99)
        g_key = (0, group) if group else (1, "")
        return (v_rank, e_rank, g_key, _neg_date(d or ""), p.name)

    open_items.sort(key=sort_key)
    closed_items.sort(key=lambda it: (it[3] or "", it[4].name), reverse=True)

    total = len(open_items) + len(closed_items)
    lines = [
        f"# {folder_name.capitalize()} — index",
        "",
        f"_Auto-generated by `plugins/lore/scripts/regenerate_indices.py`. Sorted by value (high → low), then effort (XS → XL), then date (newest first). Items in the same `consolidation-group` cluster within their tier. Edit source files, not this index._",
        "",
        f"**Total:** {total}  ·  **Open:** {len(open_items)}  ·  **Closed:** {len(closed_items)}",
        "",
    ]
    if not open_items and not closed_items:
        lines.append("_(empty)_")
        return "\n".join(lines) + "\n"

    if open_items:
        lines.append("## Open")
        lines.append("")
        lines.append("| value | effort | group | item | revisit-after | summary |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for value, effort, group, d, p, title, summary, status, revisit in open_items:
            g_disp = group or "—"
            lines.append(
                f"| {value} | {effort} | {g_disp} | {link_with_title(p, title)} | {revisit} | {summary} |"
            )
        lines.append("")

    if closed_items:
        lines.append(f"## Closed ({len(closed_items)})")
        lines.append("")
        lines.append("| status | item | summary |")
        lines.append("| --- | --- | --- |")
        for value, effort, group, d, p, title, summary, status, revisit in closed_items:
            lines.append(f"| {status} | {link_with_title(p, title)} | {summary} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_lessons_index(folder_name: str, folder: Path) -> str:
    """For lessons: list grouped by area (alpha), then date desc within group.

    Lessons are a date-bucketed living folder, so the scan recurses one level
    into `YYYY-MM/` buckets while still listing any still-flat top-level notes.
    """
    active: list[tuple[str, Path, str, str, str, str]] = []
    superseded: list[tuple[str, Path, str, str, str, str]] = []
    for p, text, fm in load_md_files(folder, recursive=True):
        d = first_date(fm.get("date"), p.name)
        title = first_h1(text) or p.stem
        summary = first_paragraph_after_headings(text)
        severity = (fm.get("severity") or "—").strip().strip("'\"") or "—"
        status = (fm.get("status") or "active").strip().strip("'\"")
        sub_match = re.search(r"^areas:\s*\[([^\]]*)\]", text, re.MULTILINE)
        if sub_match:
            subs = [s.strip().strip("'\"") for s in sub_match.group(1).split(",") if s.strip()]
        else:
            subs = []
        primary = subs[0] if subs else "(no area)"
        row = (d, p, title, summary, severity, primary)
        if status == "active":
            active.append(row)
        else:
            superseded.append(row)

    lines = [
        f"# {folder_name.capitalize()} — index",
        "",
        f"_Auto-generated by `plugins/lore/scripts/regenerate_indices.py`. Grouped by primary area; newest first within each group. Edit source files, not this index._",
        "",
        f"**Total active:** {len(active)}  ·  **Superseded:** {len(superseded)}",
        "",
        "## What this is",
        "",
        "Lessons are mistakes (process, judgment, coordination, technical) with concrete prevention checks. Surfaced at SessionStart for matched areas alongside dead-ends. Consult during /planning, /brainstorm, /council-session.",
        "",
    ]

    if not active and not superseded:
        lines.append("_(empty)_")
        return "\n".join(lines) + "\n"

    if active:
        groups: dict[str, list] = {}
        for row in active:
            groups.setdefault(row[5], []).append(row)
        for sub_name in sorted(groups.keys()):
            items = groups[sub_name]
            items.sort(key=lambda it: (it[0] or "", it[1].name), reverse=True)
            lines.append(f"## {sub_name} ({len(items)})")
            lines.append("")
            lines.append("| date | severity | item | summary |")
            lines.append("| --- | --- | --- | --- |")
            for d, p, title, summary, severity, _sub in items:
                d_disp = d or "—"
                lines.append(f"| {d_disp} | {severity} | {link_with_title(p, title)} | {summary} |")
            lines.append("")

    if superseded:
        lines.append(f"## Superseded ({len(superseded)})")
        lines.append("")
        lines.append("| date | item | summary |")
        lines.append("| --- | --- | --- |")
        superseded.sort(key=lambda it: (it[0] or "", it[1].name), reverse=True)
        for d, p, title, summary, _sev, _sub in superseded:
            d_disp = d or "—"
            lines.append(f"| {d_disp} | {link_with_title(p, title)} | {summary} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_status_index(folder_name: str, folder: Path) -> str:
    """For plans/specs/designs: grouped by status bucket.

    These are in-scope for date-bucketing, so the scan recurses one level into
    `YYYY-MM/` buckets while still listing any still-flat top-level notes.
    """
    buckets: dict[str, list] = {b: [] for b in BUCKET_ORDER}
    for p, text, fm in load_md_files(folder, recursive=True):
        bucket = classify_status(fm.get("status"))
        title = first_h1(text) or p.stem
        summary = first_paragraph_after_headings(text)
        updated = first_date(fm.get("updated"), fm.get("created"), p.name)
        raw_status = (fm.get("status") or "").strip().strip("'\"") or "—"
        raw_status = raw_status.split("#", 1)[0].strip() or "—"
        buckets[bucket].append((updated, p, title, summary, raw_status))

    for b in buckets:
        buckets[b].sort(key=lambda it: (it[0] or "", it[1].name), reverse=True)

    total = sum(len(v) for v in buckets.values())
    lines = [
        f"# {folder_name.capitalize()} — index",
        "",
        f"_Auto-generated by `plugins/lore/scripts/regenerate_indices.py`. Grouped by status; within each bucket, newest first. Edit source files, not this index._",
        "",
        f"**Total:** {total}  ·  "
        + "  ·  ".join(f"{b}: {len(buckets[b])}" for b in BUCKET_ORDER if buckets[b]),
        "",
    ]
    for b in BUCKET_ORDER:
        items = buckets[b]
        if not items:
            continue
        lines.append(f"## {b} ({len(items)})")
        lines.append("")
        lines.append("| updated | item | status | summary |")
        lines.append("| --- | --- | --- | --- |")
        for updated, p, title, summary, raw_status in items:
            d_disp = updated or "—"
            lines.append(f"| {d_disp} | {link_with_title(p, title)} | {raw_status} | {summary} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---- Driver ------------------------------------------------------------------

JOBS = [
    ("deferred", "deferred", None),
    ("radar", "dated", ["revisit-after", "added", "last-checked"]),
    ("lessons", "lessons", None),
    ("plans", "status", None),
    ("specs", "status", None),
    ("designs", "status", None),
]


def main() -> int:
    if not VAULT.is_dir():
        print(f"vault not found: {VAULT}", file=sys.stderr)
        return 2

    written: list[Path] = []
    for folder_name, kind, args in JOBS:
        folder = VAULT / folder_name
        if not folder.is_dir():
            continue
        if kind == "dated":
            content = render_dated_index(folder_name, folder, args)
        elif kind == "deferred":
            content = render_deferred_index(folder_name, folder)
        elif kind == "lessons":
            content = render_lessons_index(folder_name, folder)
        else:
            content = render_status_index(folder_name, folder)
        out = folder / "_index.md"
        existing = out.read_text(encoding="utf-8") if out.exists() else None
        if existing != content:
            out.write_text(content, encoding="utf-8")
            written.append(out)

    if written:
        for p in written:
            print(p.relative_to(VAULT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
