"""Area-map menu for the lore vault.

This module provides the **area-map path** — the compact on-demand area menu
served by `lore areas`. ``lore search`` is the general query interface;
area-membered memory lookup is ``lore search 'area:<name>'``:

  1. build_area_map(vault)          -> list[AreaEntry]
     Scans area/*.md, reads name/keywords/one-liner per area, returns the
     on-demand area menu (alpha order, hard caps applied). Served on
     demand by `lore areas`.

  2. build_area_map_multi(vaults)   -> list[AreaEntry]
     Merges `build_area_map` across every vault root passed in, deduping
     same-named areas (first-in-input-order wins), then re-sorting the
     merged set alpha. Hard caps are already applied per entry by
     build_area_map. The multi-vault counterpart `cmd_areas` calls once it
     resolves every configured non-shared vault, rather than the
     `default`-scope vault alone. This dedupe mirrors `lore record show
     area/<name>`'s cross-vault resolution EXCEPT when a shared vault holds
     a same-named area earlier in config order: `record show`'s scan does
     not filter shared vaults, so it can resolve to a different vault's copy
     than this (shared-excluding) merge does. Parity holds only among
     non-shared vaults.

  3. render_area_menu(entries)      -> str
     Renders the full on-demand menu (called by `lore areas`).

  4. render_area_pointer(vault)     -> str
     Single-line pointer summarizing the area count and a trigger cue for
     `lore areas` / `lore search 'area:<name>'`. Not currently wired to any
     caller — lore has no push hook to inject it into; see its own docstring.
     Returns "" for 0 areas.

Security: area resolution is a LOOKUP into the enumerated area names from
build_area_map — never a path built from a caller-supplied string. Only the
area count (not names) would reach any future injection point, so untrusted
frontmatter cannot flow out through the pointer.

Interpreter gotcha: ``@dataclass`` + ``importlib``-loaded module +
``from __future__ import annotations`` crashes the local interpreter's dataclass
field resolution — this module OMITS that future import for that reason.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import frontmatter as _fm_mod

# Hard caps
_ONE_LINER_MAX = 120
_KEYWORDS_MAX = 8

# Strips ASCII control characters (incl. \n, \r, tab, ESC) from frontmatter-
# sourced strings before they reach the rendered menu. render_area_menu emits
# `name`/`one_liner`/keywords as undelimited plaintext lines with no escaping,
# so an embedded newline in area frontmatter can inject arbitrary extra
# lines — including a forged "--- end lore area map ---" delimiter followed
# by fabricated instruction text — into an AI agent's context. See module
# docstring's Security note.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _strip_control_chars(value: str) -> str:
    """Remove ASCII control characters from *value* (newlines, ESC, etc.)."""
    return _CONTROL_CHARS_RE.sub("", value)


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

        name = _strip_control_chars((fm.get("name") or p.stem).strip()).strip()
        if not name:
            continue

        # One-liner: summary: > first ## Overview sentence > empty
        summary = (fm.get("summary") or "").strip()
        if summary:
            one_liner = summary[:_ONE_LINER_MAX]
        else:
            one_liner = _first_overview_sentence(text)[:_ONE_LINER_MAX]
        one_liner = _strip_control_chars(one_liner)

        # Keywords (cap)
        raw_kw = fm.get("keywords") or []
        if isinstance(raw_kw, list):
            keywords = [str(k).strip() for k in raw_kw if str(k).strip()]
        elif isinstance(raw_kw, str) and raw_kw.strip():
            keywords = [raw_kw.strip()]
        else:
            keywords = []
        keywords = [
            k for k in (_strip_control_chars(k) for k in keywords[:_KEYWORDS_MAX]) if k
        ]

        entries.append(AreaEntry(name=name, one_liner=one_liner, keywords=keywords))

    entries.sort(key=lambda e: e.name.lower())
    return entries


def build_area_map_multi(
    vaults: list[Path], errors: list | None = None
) -> list[AreaEntry]:
    """Build the compact area menu spanning multiple vault roots.

    Calls :func:`build_area_map` once per root in ``vaults`` and merges the
    results. Same-named areas across roots collapse to a single entry — the
    first root (in input order, i.e. config order) to define the name wins,
    mirroring how ``lore record show area/<name>`` resolves the same
    cross-vault collision (with one exception — see below). The merged set is
    then re-sorted alpha by name, since per-vault ordering says nothing about
    the cross-vault order.

    **Caller precondition:** ``vaults`` must already exclude every
    ``shared: true`` root. This function has no visibility into vault scope
    and applies no filtering of its own — the area menu it renders is
    undelimited plaintext that reaches agent context directly, so a shared
    (untrusted) vault's areas must never reach this function's input in the
    first place. `cmd_areas` is the caller that enforces this today.

    The ``_ONE_LINER_MAX`` / ``_KEYWORDS_MAX`` caps are NOT re-applied here:
    they bound a single entry's one-liner and keyword list, not the menu as a
    whole, and every entry this function returns came from
    :func:`build_area_map`, which already capped it. Merging entries cannot
    push an already-capped entry over its cap, so the cap lives at its one
    source rather than being restated per caller.

    A root that yields no areas (absent ``area/`` dir, or every file
    unreadable) simply contributes nothing. A root whose ``build_area_map``
    call raises is likewise skipped rather than propagated — one bad vault
    root must not cost every other root its areas, extending
    ``build_area_map``'s own never-raise contract across the merge. Pass a
    list via ``errors`` to observe which roots (and exceptions) were skipped
    this way — callers that need to distinguish "legitimately empty" from
    "a root silently failed" (e.g. to decide whether to emit a degradation
    signal) read it after the call; it is left untouched (``None`` stays
    ``None``, an empty list stays empty) when nothing failed.

    Dedup only ever collapses a name across DIFFERENT roots. Two files
    colliding on the same frontmatter ``name`` within a single root are both
    kept — ``build_area_map`` on one root, called directly (the single-vault,
    pre-multi-vault path), never deduped its own output, and a single-root
    call through this function must stay byte-identical to that. The
    single-writer-wins collapse is specifically a cross-vault concern (the
    same name declared independently in two vaults), not a same-vault one.
    """
    seen_names: set[str] = set()
    entries: list[AreaEntry] = []
    for vault in vaults:
        try:
            vault_entries = build_area_map(vault)
        except Exception as exc:
            if errors is not None:
                errors.append((vault, exc))
            continue
        this_vault_names: set[str] = set()
        for entry in vault_entries:
            if entry.name in seen_names and entry.name not in this_vault_names:
                continue
            entries.append(entry)
            this_vault_names.add(entry.name)
        seen_names |= this_vault_names

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
    """Return a single-line pointer to `lore areas`.

    Emits the area count plus a trigger cue and the commands to use so the
    agent knows when and how to discover areas without inlining the full menu.
    Returns empty string when there are 0 areas (matching today's empty-menu
    behavior).

    Orphaned: lore installs no push hook, so nothing calls this today — its
    only caller is `tests/test_recall_retired.py`, which pins its output
    shape in case a future caller (e.g. a UserPromptSubmit hook, see
    ROADMAP.md) wires it in. May raise (like build_area_map); any future
    caller should wrap it and degrade gracefully rather than propagate.

    Security: only the count is emitted, not area names, so untrusted
    frontmatter would not reach any future caller through this path.

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
