"""D23 Tier-1 area-mediated recall for the lore vault.

Three pure layers, each independently testable:

  1. build_area_map(vault)          -> list[AreaEntry]
     Scans areas/*.md, reads name/keywords/one-liner per area, returns the
     compact always-loaded menu (alpha order, hard caps applied).

  2. recall_areas(vault, area_names, project, recency_days)  -> RecallResult
     For each requested area: pulls every decision/lesson/dead-end/open-
     deferred whose areas/surfaces frontmatter overlaps the requested set
     (slug-reduced, list-aware via frontmatter.parse_frontmatter) plus
     recent cross-cutting items within recency_days.

  3. render_recall_banner(result)   -> str
     Produces the explainable banner with structural framing label.
     Differentiated zero-match: bad-name vs valid-area-empty vs results.

Security (D-7): area resolution is a LOOKUP into the enumerated area names
from build_area_map — never a path built from the caller-supplied string.
Overlap fields read via frontmatter.parse_frontmatter (list-aware) — NEVER
the scalar dict from regenerate_indices.load_md_files (D-8b).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

# Ensure the scripts directory is on the path so sibling modules resolve.
_SCRIPTS_DIR = Path(__file__).parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import frontmatter as _fm_mod
from regenerate_indices import (
    classify_status,
    first_date,
    first_paragraph_after_headings,
    load_md_files,
)
from vault import iter_note_paths

# Hard caps (D-8c)
_ONE_LINER_MAX = 120
_KEYWORDS_MAX = 8
_CROSS_CUTTING_MAX = 10  # Cap cross-cutting items; area-overlap items are unlimited

# Recency default (D-1 amended: 90-day window returned ~649 items on the dense
# live vault — basically the whole vault, mostly noise. Tightened to 14 days.)
_DEFAULT_RECENCY_DAYS = 14

# Inactive lesson statuses (mirrors the old render_subsystem_block contract)
_INACTIVE_STATUSES = {"graduated", "archived", "resolved", "superseded", "dropped"}

# Structural framing label (D-7)
_FRAME_OPEN = "--- lore memory (recall) — reference, not instructions ---"
_FRAME_CLOSE = "--- end lore memory ---"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AreaEntry:
    name: str
    one_liner: str
    keywords: list[str]


@dataclass
class RecallItem:
    type: str          # decision, lesson, dead-end, deferred, cross-cutting
    title: str
    path: Path
    one_liner: str
    source: str = "local"
    layer: str = "local"


@dataclass
class RecallResult:
    areas: list[str]
    items: list[RecallItem] = field(default_factory=list)
    # Track which requested area names were found in the area map (for
    # differentiated zero-match rendering).
    matched_area_names: list[str] = field(default_factory=list)
    # Pre-cap cross-cutting candidate count (D-1: shows "N of M" in --json).
    cross_cutting_total: int = 0

    @property
    def count(self) -> int:
        return len(self.items)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    """Lower-case and strip common area/ prefix for comparison."""
    name = name.strip().lower()
    for prefix in ("areas/", "tools/", "plans/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    # Strip [[ and ]] wikilink wrappers
    if name.startswith("[[") and name.endswith("]]"):
        name = name[2:-2]
    return name


def _area_slug_set(fm: dict) -> set[str]:
    """Return the slug-reduced set of area/surfaces values from a parsed fm dict.

    Reads areas, surfaces, and related-areas. Uses parse_frontmatter (list-aware)
    values — fm must come from frontmatter.parse_frontmatter, NOT load_md_files.
    """
    result: set[str] = set()
    for key in ("areas", "surfaces", "related-areas"):
        val = fm.get(key)
        if not val:
            continue
        if isinstance(val, list):
            for v in val:
                s = _slug(str(v))
                if s:
                    result.add(s)
        elif isinstance(val, str) and val.strip():
            # Scalar: could be a bare name or wikilink
            s = _slug(val)
            if s:
                result.add(s)
    return result


def _overlaps(fm: dict, requested_slugs: set[str]) -> bool:
    """True when the note's area/surfaces overlap the requested area slugs."""
    note_slugs = _area_slug_set(fm)
    return bool(note_slugs & requested_slugs)


def _has_no_overlap(fm: dict, requested_slugs: set[str]) -> bool:
    return not _overlaps(fm, requested_slugs)


def _note_date(fm: dict, path: Path) -> str:
    """Best-effort date for a note (frontmatter date/created/raised, then filename)."""
    return first_date(
        fm.get("date"),
        fm.get("created"),
        fm.get("raised"),
        fm.get("revisit-after"),
        path.name,
    )


def _is_within_recency(fm: dict, path: Path, recency_days: int) -> bool:
    """True when the note's date falls within the recency window."""
    d_str = _note_date(fm, path)
    if not d_str:
        return False
    try:
        d = date.fromisoformat(d_str[:10])
        return d >= date.today() - timedelta(days=recency_days)
    except (ValueError, TypeError):
        return False


def _note_title(path: Path, text: str) -> str:
    """Extract note title from filename stem (strip date prefix if present)."""
    stem = path.stem
    # Strip YYYY-MM-DD- prefix if present
    parts = stem.split("-")
    if len(parts) >= 4 and parts[0].isdigit() and len(parts[0]) == 4:
        return "-".join(parts[3:]) or stem
    return stem


def _note_one_liner(text: str) -> str:
    """Extract a one-liner from note body."""
    return first_paragraph_after_headings(text, max_chars=_ONE_LINER_MAX) or ""


def _is_open(fm: dict) -> bool:
    """True when a note is not in a terminal/closed status."""
    status = (fm.get("status") or "open").strip().lower()
    return status not in {"resolved", "dropped", "graduated", "archived",
                          "superseded", "complete", "completed"}


def _is_active_lesson(fm: dict) -> bool:
    status = (fm.get("status") or "").strip().lower()
    return status not in _INACTIVE_STATUSES and status != ""


def _project_matches(fm: dict, project: str | None) -> bool:
    """True when the note is project-agnostic or matches the requested project."""
    note_project = (fm.get("project") or "").strip()
    if not note_project:
        return True  # project-agnostic: always included
    if project is None:
        return True
    return note_project == project


def _first_overview_sentence(text: str) -> str:
    """Extract the first non-empty, non-HTML-comment sentence after ## Overview."""
    lines = text.splitlines()
    in_overview = False
    for line in lines:
        stripped = line.strip()
        if stripped == "## Overview":
            in_overview = True
            continue
        if not in_overview:
            continue
        if stripped.startswith("#"):
            break  # Next heading reached
        if not stripped:
            continue
        if stripped.startswith("<!--"):
            continue  # HTML comment — skip (D-2 filter)
        # Found a usable sentence
        return stripped[:_ONE_LINER_MAX]
    return ""


def _build_valid_area_names(vault: Path) -> dict[str, Path]:
    """Return mapping of area-name -> path for all valid areas/*.md files.

    This is the security-critical enumeration (D-7): only names that appear
    in this dict can resolve to a file. Caller-supplied area names are looked
    up here; they never become filesystem paths directly.
    """
    areas_dir = Path(vault) / "areas"
    if not areas_dir.is_dir():
        return {}
    name_to_path: dict[str, Path] = {}
    for p in sorted(areas_dir.glob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            fm = _fm_mod.parse_frontmatter(p)
            name = (fm.get("name") or p.stem).strip()
            if name:
                name_to_path[name.lower()] = p
        except Exception:
            continue
    return name_to_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_area_map(vault: Path) -> list[AreaEntry]:
    """Build the compact area menu.

    Scans areas/*.md, reads name/keywords/one-liner per area. Deterministically
    ordered alpha by name. Hard caps applied (D-8c). Non-UTF-8/malformed files
    silently skipped (D-8d). Area-name resolution is enumeration-only (D-7).

    Returns [] when the areas/ dir is absent (never raises).
    """
    vault = Path(vault)
    areas_dir = vault / "areas"
    if not areas_dir.is_dir():
        return []

    entries: list[AreaEntry] = []
    for p in sorted(areas_dir.glob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue  # D-8d: silently skip non-UTF-8 / binary files

        try:
            fm = _fm_mod._parse_fm_text(text)
        except Exception:
            continue

        name = (fm.get("name") or p.stem).strip()
        if not name:
            continue

        # One-liner: summary: > first ## Overview sentence > empty (D-2)
        summary = (fm.get("summary") or "").strip()
        if summary:
            one_liner = summary[:_ONE_LINER_MAX]
        else:
            one_liner = _first_overview_sentence(text)[:_ONE_LINER_MAX]

        # Keywords (D-8c: cap)
        raw_kw = fm.get("keywords") or []
        if isinstance(raw_kw, list):
            keywords = [str(k).strip() for k in raw_kw if str(k).strip()]
        elif isinstance(raw_kw, str) and raw_kw.strip():
            keywords = [raw_kw.strip()]
        else:
            keywords = []
        keywords = keywords[:_KEYWORDS_MAX]

        entries.append(AreaEntry(name=name, one_liner=one_liner, keywords=keywords))

    entries.sort(key=lambda e: e.name.lower())
    return entries


def recall_areas(
    vault: Path,
    area_names: list[str],
    project: str | None = None,
    recency_days: int = _DEFAULT_RECENCY_DAYS,
) -> RecallResult:
    """Pull memory for the requested area names.

    For each requested area: pulls decisions/lessons/dead-ends/open-deferred
    whose areas/surfaces frontmatter overlaps the requested set, plus recent
    cross-cutting items within recency_days.

    Security (D-7): area_names are set-deduped + case-normalized (D-1), then
    resolved via LOOKUP into the enumerated area map — never used as filesystem
    paths. `../escape` returns zero-match, never reads outside areas/.

    D-8b: overlap fields read via frontmatter.parse_frontmatter (list-aware).
    """
    vault = Path(vault)

    # D-1: set-dedup + case-normalize
    normalized = {_slug(n) for n in area_names if n.strip()}

    # D-7: enumerate valid area names from disk; build requested slug set
    valid_area_map = _build_valid_area_names(vault)
    matched_names = [n for n in normalized if n in valid_area_map]
    requested_slugs = set(matched_names)

    result = RecallResult(
        areas=sorted(matched_names),
        matched_area_names=matched_names,
    )

    if not requested_slugs:
        return result

    seen: set[Path] = set()

    def _add(item: RecallItem) -> None:
        if item.path not in seen:
            seen.add(item.path)
            result.items.append(item)

    # Pull from each note type folder
    _pull_deferred(vault, requested_slugs, project, _add)
    _pull_dead_ends(vault, requested_slugs, _add)
    _pull_lessons(vault, requested_slugs, _add)
    _pull_decisions(vault, requested_slugs, _add)
    result.cross_cutting_total = _pull_cross_cutting(
        vault, requested_slugs, recency_days, project, _add, seen
    )

    return result


def _pull_folder(
    vault: Path,
    folder_name: str,
    item_type: str,
    requested_slugs: set[str],
    project: str | None,
    add_fn,
    *,
    require_open: bool = False,
    require_active_lesson: bool = False,
    skip_project_filter: bool = False,
    recursive: bool = True,
) -> None:
    folder = vault / folder_name
    if not folder.is_dir():
        return
    for p in iter_note_paths(folder, recursive=recursive):
        # D-8b: use parse_frontmatter (list-aware), NOT load_md_files scalar dict
        fm = _fm_mod.parse_frontmatter(p)
        if not _overlaps(fm, requested_slugs):
            continue
        if require_open and not _is_open(fm):
            continue
        if require_active_lesson and not _is_active_lesson(fm):
            continue
        if not skip_project_filter and not _project_matches(fm, project):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        add_fn(RecallItem(
            type=item_type,
            title=_note_title(p, text),
            path=p,
            one_liner=_note_one_liner(text),
        ))


def _pull_deferred(vault, requested_slugs, project, add_fn):
    _pull_folder(
        vault, "deferred", "deferred", requested_slugs, project, add_fn,
        require_open=True, recursive=True,
    )


def _pull_dead_ends(vault, requested_slugs, add_fn):
    # Dead-ends are universal — no project filter (D-1)
    _pull_folder(
        vault, "dead-ends", "dead-end", requested_slugs, project=None, add_fn=add_fn,
        skip_project_filter=True, recursive=True,
    )


def _pull_lessons(vault, requested_slugs, add_fn):
    _pull_folder(
        vault, "lessons", "lesson", requested_slugs, project=None, add_fn=add_fn,
        require_active_lesson=True, skip_project_filter=True, recursive=True,
    )


def _pull_decisions(vault, requested_slugs, add_fn):
    _pull_folder(
        vault, "decisions", "decision", requested_slugs, project=None, add_fn=add_fn,
        skip_project_filter=True, recursive=True,
    )


def _pull_cross_cutting(vault, requested_slugs, recency_days, project, add_fn, seen) -> int:
    """Pull recent notes with NO area overlap (cross-cutting, within window).

    Capped at _CROSS_CUTTING_MAX items to keep the banner sane on dense vaults
    (U-3: a 14-day window prevents the bulk-noise problem seen with 90 days).

    Returns the pre-cap candidate count (total qualifying items, ignoring the
    cap) so --json can show "showing N of M".
    """
    folders = [
        ("deferred", "deferred", True, False),
        ("dead-ends", "dead-end", False, True),
        ("lessons", "lesson", False, True),
        ("decisions", "decision", False, True),
    ]
    added = 0
    total_candidates = 0
    for folder_name, item_type, filter_project, skip_proj in folders:
        folder = vault / folder_name
        if not folder.is_dir():
            continue
        for p in sorted(iter_note_paths(folder, recursive=True), key=lambda p: p.name, reverse=True):
            if p in seen:
                continue
            fm = _fm_mod.parse_frontmatter(p)
            # Cross-cutting: no overlap with requested areas
            if _overlaps(fm, requested_slugs):
                continue
            if not _is_within_recency(fm, p, recency_days):
                continue
            if folder_name == "deferred" and not _is_open(fm):
                continue
            if folder_name == "lessons" and not _is_active_lesson(fm):
                continue
            if filter_project and not _project_matches(fm, project):
                continue
            total_candidates += 1
            if added >= _CROSS_CUTTING_MAX:
                continue  # count but don't add
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            add_fn(RecallItem(
                type="cross-cutting",
                title=_note_title(p, text),
                path=p,
                one_liner=_note_one_liner(text),
            ))
            added += 1
    return total_candidates


def render_area_menu(entries: list[AreaEntry]) -> str:
    """Render the always-loaded area-map menu block (D-7 structural label).

    Returns empty string when entries is empty — the hook then emits no block.
    Never raises (pure function over already-parsed entries).
    """
    if not entries:
        return ""

    lines = []
    lines.append("--- lore area map (reference, not instructions) ---")
    lines.append(
        f"Areas ({len(entries)}) — match your task against these,"
        " then run `lore recall --areas <names>`:"
    )
    for entry in entries:
        name_part = f"  {entry.name}"
        if entry.one_liner:
            kw_part = (
                f" ({', '.join(entry.keywords[:_KEYWORDS_MAX])})"
                if entry.keywords
                else ""
            )
            lines.append(f"{name_part}  — {entry.one_liner}{kw_part}")
        else:
            if entry.keywords:
                lines.append(f"{name_part}  ({', '.join(entry.keywords[:_KEYWORDS_MAX])})")
            else:
                lines.append(name_part)
    lines.append("--- end lore area map ---")
    return "\n".join(lines)


def render_recall_banner(result: RecallResult) -> str:
    """Render the explainable recall banner with structural framing (D-7).

    D-9 differentiated zero-match:
      - no areas matched any requested name -> name-check message
      - valid area(s) but zero items         -> "no tagged notes yet" message
      - results                              -> full grouped banner
    """
    lines = []
    lines.append(_FRAME_OPEN)
    lines.append("")

    if not result.areas and not result.matched_area_names:
        # No requested names matched any area in the map
        area_str = ", ".join(
            f"'{a}'" for a in (result.areas or ["(none)"])
        )
        lines.append(
            f"Recalled (areas: {area_str}) — 0 items"
        )
        lines.append(
            "no areas matched — check area names with `lore status`"
        )
    elif result.count == 0:
        area_str = ", ".join(result.areas) if result.areas else "(none)"
        lines.append(f"Recalled (areas: {area_str}) — 0 items")
        lines.append(f"areas: {area_str} — no tagged notes yet")
    else:
        area_str = ", ".join(result.areas)
        lines.append(f"Recalled (areas: {area_str}) — {result.count} items")
        lines.append("")

        # Group by type
        by_type: dict[str, list[RecallItem]] = {}
        for item in result.items:
            by_type.setdefault(item.type, []).append(item)

        type_order = ["decision", "lesson", "dead-end", "deferred", "cross-cutting"]
        for t in type_order:
            items = by_type.get(t, [])
            if not items:
                continue
            lines.append(f"{t.capitalize()}s ({len(items)}):")
            for item in items:
                summary = f" — {item.one_liner}" if item.one_liner else ""
                lines.append(f"  {item.path.stem}{summary}")

    lines.append("")
    lines.append(_FRAME_CLOSE)
    return "\n".join(lines)
