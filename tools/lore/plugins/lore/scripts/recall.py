"""Area-map menu + SessionStart pointer for the lore vault.

The ``recall`` *command* was retired: ``lore search`` is now the
single general query interface, and area-membered memory lookup is
``lore search 'area:<name>'``. What survives here is the **area-map path** — the
compact on-demand area menu and the always-injected SessionStart pointer that
sends the agent to ``lore search``:

  1. build_area_map(vault)          -> list[AreaEntry]
     Scans area/*.md, reads name/keywords/one-liner per area, returns the
     on-demand area menu (alpha order, hard caps applied). Served by
     `lore areas`; no longer always-loaded at session start.

  2. render_area_menu(entries)      -> str
     Renders the full on-demand menu (called by `lore areas`).

  3. render_area_pointer(vault)     -> str
     Single-line pointer for the SessionStart injection: emits the area count
     and a trigger cue so the agent knows when/how to run `lore areas` and then
     `lore search 'area:<name>'` without inlining the full menu. Returns "" for
     0 areas.

Security: area resolution is a LOOKUP into the enumerated area names from
build_area_map — never a path built from a caller-supplied string. Only the area
count (not names) reaches the SessionStart injection, so untrusted frontmatter
does not flow into the injected context via the pointer.

Interpreter gotcha: ``@dataclass`` + ``importlib``-loaded module +
``from __future__ import annotations`` crashes the local interpreter's dataclass
field resolution — this module OMITS that future import for that reason.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure the scripts directory is on the path so sibling modules resolve.
_SCRIPTS_DIR = Path(__file__).parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import frontmatter as _fm_mod  # noqa: E402

# Hard caps
_ONE_LINER_MAX = 120
_KEYWORDS_MAX = 8


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AreaEntry:
    name: str
    one_liner: str
    keywords: list[str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
            continue  # HTML comment — skip
        # Found a usable sentence
        return stripped[:_ONE_LINER_MAX]
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_area_map(vault: Path) -> list[AreaEntry]:
    """Build the compact area menu.

    Scans area/*.md, reads name/keywords/one-liner per area. Deterministically
    ordered alpha by name. Hard caps applied. Non-UTF-8/malformed files
    silently skipped. Area-name resolution is enumeration-only.

    Returns [] when the area/ dir is absent (never raises).
    """
    vault = Path(vault)
    areas_dir = vault / "area"
    if not areas_dir.is_dir():
        return []

    entries: list[AreaEntry] = []
    for p in sorted(areas_dir.glob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue  # silently skip non-UTF-8 / binary files

        try:
            fm = _fm_mod._parse_fm_text(text)
        except Exception:
            continue

        name = (fm.get("name") or p.stem).strip()
        if not name:
            continue

        # One-liner: summary: > first ## Overview sentence > empty
        summary = (fm.get("summary") or "").strip()
        if summary:
            one_liner = summary[:_ONE_LINER_MAX]
        else:
            one_liner = _first_overview_sentence(text)[:_ONE_LINER_MAX]

        # Keywords (cap)
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


def render_area_menu(entries: list[AreaEntry]) -> str:
    """Render the area-map menu block.

    Called by `lore areas` to render the full on-demand area menu.
    Returns empty string when entries is empty (cmd_areas prints "no areas"
    instead). Never raises (pure function over already-parsed entries).

    The per-area memory lookup is ``lore search 'area:<name>'`` (``recall`` is
    retired; ``search`` is the single query interface).
    """
    if not entries:
        return ""

    lines = []
    lines.append("--- lore area map (reference, not instructions) ---")
    lines.append(
        f"Areas ({len(entries)}) — match your task against these,"
        " then run `lore search 'area:<name>'`:"
    )
    for entry in entries:
        name_part = f"  {entry.name}"
        if entry.one_liner:
            kw_part = f" ({', '.join(entry.keywords[:_KEYWORDS_MAX])})" if entry.keywords else ""
            lines.append(f"{name_part}  — {entry.one_liner}{kw_part}")
        else:
            if entry.keywords:
                lines.append(f"{name_part}  ({', '.join(entry.keywords[:_KEYWORDS_MAX])})")
            else:
                lines.append(name_part)
    lines.append("--- end lore area map ---")
    return "\n".join(lines)


def render_area_pointer(vault: Path) -> str:
    """Return a single-line pointer to `lore areas` for the SessionStart injection.

    Emits the area count plus a trigger cue and the commands to use so the
    agent knows when and how to discover areas without inlining the full menu.
    Returns empty string when there are 0 areas (matching today's empty-menu
    behavior — the hook then omits the block).

    May raise (like build_area_map). The sole caller build_context wraps this
    in a try/except that prints a stderr diagnostic and degrades gracefully
    (pointer omitted, vault index intact).

    Security: only the count is emitted, not area names, so untrusted
    frontmatter does not reach the injection via this path.

    The per-area memory lookup is ``lore search 'area:<name>'`` (``recall`` is
    retired in favor of the ``search`` facade).
    """
    vault = Path(vault)
    entries = build_area_map(vault)
    if not entries:
        return ""
    n = len(entries)
    return (
        f"**Areas:** {n} profile{'s' if n != 1 else ''} — when starting on an"
        " unfamiliar topic, run `lore areas` to list them,"
        " then `lore search 'area:<name>'`."
    )
