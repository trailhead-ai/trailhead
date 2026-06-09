"""Lore review report generator.

Walks the lore vault and produces a structured markdown report that the
`lore review` ritual uses to drive an interactive review with the user.

Read-only: this module never mutates the vault. Write-back happens in the
skill flow after the user approves specific changes.

Sections reported:
- Activity since last review (git log with file-level changes)
- Action taxonomy drift (near-duplicate action names)
- Graduation candidates (collaboration notes aging past 30 days)
- Stale area profiles (last-touched older than 60 days)
- Open deferred items (for trigger-condition review)
- Dead-ends (for revive-condition review)
- Open radar items (for migration)
- Active lessons (for migration)
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
from pathlib import Path
from typing import Iterable

import frontmatter as fm_mod
from vault import iter_note_paths

GRADUATION_AGE_DAYS = 30
STALE_AREA_DAYS = 60


def _git(vault: Path, *args: str) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), *args],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


def last_review_date(vault: Path) -> dt.date | None:
    """Find the most recent file in <vault>/reviews/ and return its date.

    Returns None when no reviews exist yet.
    """
    reviews_dir = vault / "reviews"
    if not reviews_dir.is_dir():
        return None
    reviews = sorted(reviews_dir.glob("*.md"), reverse=True)
    if not reviews:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", reviews[0].name)
    if not m:
        return None
    try:
        return dt.date.fromisoformat(m.group(1))
    except Exception:
        return None


def resolve_since(vault: Path, arg: str | None) -> str:
    """Return a git-compatible --since argument.

    Priority: explicit arg (passed through) > last review date > "7 days ago".
    """
    if arg:
        return arg
    last = last_review_date(vault)
    if last:
        return last.isoformat()
    return "7 days ago"


def section_activity(vault: Path, since: str) -> list[str]:
    lines = ["## Activity since last review", ""]
    rc, log_out, _ = _git(
        vault, "log", f"--since={since}", "--pretty=format:%h %s", "--no-merges",
    )
    if rc != 0 or not log_out.strip():
        lines.append("_No commits in the window._")
        lines.append("")
        return lines

    rc, stat_out, _ = _git(
        vault, "log", f"--since={since}", "--pretty=format:", "--name-only", "--no-merges",
    )
    files: dict[str, int] = {}
    for f in (stat_out or "").splitlines():
        f = f.strip()
        if not f:
            continue
        files[f] = files.get(f, 0) + 1

    lines.append("### Commits")
    lines.append("```")
    lines.extend(log_out.splitlines())
    lines.append("```")
    lines.append("")

    if files:
        by_category: dict[str, int] = {}
        for f, n in files.items():
            top = f.split("/", 1)[0] if "/" in f else f
            by_category[top] = by_category.get(top, 0) + n
        lines.append("### Files touched (by top-level category)")
        for cat, count in sorted(by_category.items(), key=lambda kv: -kv[1]):
            lines.append(f"- **{cat}** — {count} edits")
        lines.append("")

    return lines


def _collect_actions(vault: Path) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    # dead-ends is a date-bucketed living folder (recurse); collaboration stays flat.
    for d, recursive in (("collaboration", False), ("dead-ends", True)):
        dir_path = vault / d
        if not dir_path.is_dir():
            continue
        for p in iter_note_paths(dir_path, recursive=recursive):
            note_fm = fm_mod.parse_frontmatter(p)
            if note_fm.get("status") in ("graduated", "obsolete"):
                continue
            actions = note_fm.get("actions")
            if isinstance(actions, str):
                actions = [actions]
            if not isinstance(actions, list):
                continue
            for a in actions:
                if isinstance(a, str) and a:
                    index.setdefault(a, []).append(p)
    return index


def _tokens(s: str) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", s.lower())) - {""}


def section_drift(vault: Path) -> list[str]:
    lines = ["## Action taxonomy drift", ""]
    actions = _collect_actions(vault)
    if not actions:
        lines.append("_No action-tagged notes yet._")
        lines.append("")
        return lines

    names = sorted(actions.keys())
    near: list[tuple[str, str, float]] = []
    for i, a in enumerate(names):
        ta = _tokens(a)
        for b in names[i + 1:]:
            tb = _tokens(b)
            if not ta or not tb:
                continue
            overlap = len(ta & tb) / max(len(ta | tb), 1)
            if overlap >= 0.5:
                near.append((a, b, overlap))

    if not near:
        lines.append(f"All {len(names)} action names look distinct ({', '.join(f'`{n}`' for n in names)}).")
        lines.append("")
        return lines

    lines.append("Possible duplicates — consider merging:")
    lines.append("")
    for a, b, score in sorted(near, key=lambda t: -t[2]):
        lines.append(f"- **`{a}`** vs **`{b}`** — token overlap {score:.0%}")
    lines.append("")
    return lines


def section_graduation_candidates(vault: Path) -> list[str]:
    lines = ["## Graduation candidates", ""]
    dir_path = vault / "collaboration"
    if not dir_path.is_dir():
        lines.append("_No collaboration dir._")
        lines.append("")
        return lines

    today = dt.date.today()
    candidates: list[tuple[Path, int]] = []
    for p in sorted(dir_path.glob("*.md")):
        note_fm = fm_mod.parse_frontmatter(p)
        if note_fm.get("type") != "collaboration":
            continue
        if note_fm.get("status") != "active":
            continue
        date_str = note_fm.get("date")
        if not isinstance(date_str, str):
            continue
        try:
            note_date = dt.date.fromisoformat(date_str)
        except Exception:
            continue
        age = (today - note_date).days
        if age >= GRADUATION_AGE_DAYS:
            candidates.append((p, age))

    if not candidates:
        lines.append(f"_No active collaboration notes older than {GRADUATION_AGE_DAYS} days._")
        lines.append("")
        return lines

    lines.append(
        f"Active collaboration notes past the {GRADUATION_AGE_DAYS}-day graduation threshold — "
        "consider promoting stable patterns to memory feedback rules:"
    )
    lines.append("")
    for p, age in sorted(candidates, key=lambda t: -t[1]):
        lines.append(f"- [[{p.relative_to(vault)}]] — {age} days old")
    lines.append("")
    return lines


def section_stale_areas(vault: Path) -> list[str]:
    lines = ["## Stale area profiles", ""]
    dir_path = vault / "areas"
    if not dir_path.is_dir():
        lines.append("_No areas dir._")
        lines.append("")
        return lines

    today = dt.date.today()
    stale: list[tuple[Path, int]] = []
    for p in sorted(dir_path.glob("*.md")):
        note_fm = fm_mod.parse_frontmatter(p)
        if note_fm.get("type") != "area":
            continue
        last_touched = note_fm.get("last-touched")
        if not isinstance(last_touched, str):
            continue
        try:
            lt_date = dt.date.fromisoformat(last_touched)
        except Exception:
            continue
        age = (today - lt_date).days
        if age >= STALE_AREA_DAYS:
            stale.append((p, age))

    if not stale:
        lines.append(f"_No area profiles older than {STALE_AREA_DAYS} days._")
        lines.append("")
        return lines

    lines.append(
        f"Profiles with `last-touched` older than {STALE_AREA_DAYS} days — "
        "either they're inactive and fine, or they need a refresh pass:"
    )
    lines.append("")
    for p, age in sorted(stale, key=lambda t: -t[1]):
        lines.append(f"- [[{p.relative_to(vault)}]] — {age} days stale")
    lines.append("")
    return lines


def section_open_deferred(vault: Path) -> list[str]:
    lines = ["## Open deferred items", ""]
    dir_path = vault / "deferred"
    if not dir_path.is_dir() or not any(iter_note_paths(dir_path, recursive=True)):
        lines.append("_No open deferred items._")
        lines.append("")
        return lines

    hits: list[tuple[Path, dict]] = []
    for p in sorted(iter_note_paths(dir_path, recursive=True)):
        note_fm = fm_mod.parse_frontmatter(p)
        if note_fm.get("type") == "deferred" and note_fm.get("status") in ("open", "watching", "resurfaced"):
            hits.append((p, note_fm))

    if not hits:
        lines.append("_All deferred items are resolved or dropped._")
        lines.append("")
        return lines

    lines.append("Review each trigger condition — has it become true? If so, promote/act:")
    lines.append("")
    for p, note_fm in hits:
        surfaces = note_fm.get("surfaces") or []
        trig = note_fm.get("next-check", "")
        lines.append(f"- [[{p.relative_to(vault)}]] — surfaces: {surfaces}, revisit when: {trig}")
    lines.append("")
    return lines


def section_dead_ends(vault: Path) -> list[str]:
    lines = ["## Dead-ends (for revive-condition check)", ""]
    dir_path = vault / "dead-ends"
    if not dir_path.is_dir() or not any(iter_note_paths(dir_path, recursive=True)):
        lines.append("_No dead-ends recorded yet._")
        lines.append("")
        return lines

    hits: list[tuple[Path, dict]] = []
    for p in sorted(iter_note_paths(dir_path, recursive=True)):
        note_fm = fm_mod.parse_frontmatter(p)
        if note_fm.get("type") == "dead-end":
            hits.append((p, note_fm))

    if not hits:
        lines.append("_No dead-ends recorded yet._")
        lines.append("")
        return lines

    lines.append("Review each revive condition — has it become true? If so, retrying may be worth it:")
    lines.append("")
    for p, note_fm in hits:
        revive = note_fm.get("revive-condition", "")
        lines.append(f"- [[{p.relative_to(vault)}]] — revive when: {revive}")
    lines.append("")
    return lines


def section_open_radar(vault: Path) -> list[str]:
    lines = ["## Open radar items", ""]
    dir_path = vault / "radar"
    if not dir_path.is_dir() or not any(iter_note_paths(dir_path, recursive=True)):
        lines.append("_No radar items yet._")
        lines.append("")
        return lines

    hits: list[tuple[Path, dict]] = []
    for p in sorted(iter_note_paths(dir_path, recursive=True)):
        note_fm = fm_mod.parse_frontmatter(p)
        if note_fm.get("type") == "radar" and note_fm.get("status") in ("open", "watching"):
            hits.append((p, note_fm))

    if not hits:
        lines.append("_No open radar items._")
        lines.append("")
        return lines

    lines.append("Review each — still worth watching?")
    lines.append("")
    for p, note_fm in hits:
        revisit = note_fm.get("revisit-after", "")
        lines.append(f"- [[{p.relative_to(vault)}]] — revisit: {revisit}")
    lines.append("")
    return lines


def section_active_lessons(vault: Path) -> list[str]:
    lines = ["## Active lessons", ""]
    dir_path = vault / "lessons"
    if not dir_path.is_dir() or not any(iter_note_paths(dir_path, recursive=True)):
        lines.append("_No active lessons._")
        lines.append("")
        return lines

    hits: list[tuple[Path, dict]] = []
    for p in sorted(iter_note_paths(dir_path, recursive=True)):
        note_fm = fm_mod.parse_frontmatter(p)
        status = note_fm.get("status", "active")
        if status == "active":
            hits.append((p, note_fm))

    if not hits:
        lines.append("_No active lessons._")
        lines.append("")
        return lines

    lines.append("Review each — is the prevention check still meaningful, or has it been superseded by tooling?")
    lines.append("")
    for p, note_fm in hits:
        areas = note_fm.get("areas") or []
        lines.append(f"- [[{p.relative_to(vault)}]] — areas: {areas}")
    lines.append("")
    return lines


def build_report(vault: Path, since: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    header = [
        f"# Lore review — {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"**Window:** since `{since}`",
        f"**Generated:** {now_iso}",
        "",
    ]
    out: list[str] = header
    out.extend(section_activity(vault, since))
    out.extend(section_drift(vault))
    out.extend(section_graduation_candidates(vault))
    out.extend(section_stale_areas(vault))
    out.extend(section_open_deferred(vault))
    out.extend(section_dead_ends(vault))
    out.extend(section_open_radar(vault))
    out.extend(section_active_lessons(vault))
    return "\n".join(out)
