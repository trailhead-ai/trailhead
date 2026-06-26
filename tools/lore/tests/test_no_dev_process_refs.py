"""Drift guard: the shipped lore tree carries no internal dev-process references.

The project convention (trailhead/CLAUDE.md) is that comments, docstrings, and
tests must stand on their own and must NOT reference internal planning artifacts:
development "slices", lettered "specs" / invariant tags (``D-1``, ``S-3``,
``A-3`` …), "known unknowns", council reviews, or plan documents. Those live in
the project's working notes, not in the shipped code or its tests.

This is a *mechanical* enforcement of that convention so the refs cannot silently
creep back in. The earlier scrub was one-time; without this guard, routine work
on ``main`` kept reintroducing slice/KU/council refs into the lore test suite.

Scope — the **whole lore tree**: both the shipped plugin (``plugins/lore``) and
this test suite (``tests/``). lore has no legitimate vocabulary of this shape, so
the denylist needs no allowlist.

Why lore-only (craft is deliberately excluded): craft ships "Slice" and
"Known Unknown" as *product* vocabulary — the plan template's ``### Slice N``
headings, the plan/polish skills, the council panel — and craft tests legitimately
assert on that content. A regex cannot tell "Slice 5 cutover" (a dev-process leak)
from "Slice ordering" (an asserted product fixture), so guarding craft mechanically
would risk scrubbing the very features it ships. craft's tests are scrubbed by
hand; only lore is guarded here.
"""

from __future__ import annotations

import re
from pathlib import Path

# tests/ -> tools/lore
LORE_ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = (LORE_ROOT / "plugins" / "lore", LORE_ROOT / "tests")

# This file necessarily spells the forbidden tokens out (to define the denylist
# and to explain the convention), so it must never scan itself.
SELF = Path(__file__).resolve()

# Files worth scanning: source, docs, and config that ship or describe behavior.
# Anything textual is read; undecodable (binary) files are skipped.
_SCANNED_SUFFIXES = {".py", ".md", ".toml", ".json", ".txt", ""}

# Each entry: (compiled pattern, human label). Patterns encode the convention's
# forbidden shapes. None of these occur anywhere in a clean lore tree.
_DENYLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bSlice \d"), "development 'slice' reference"),
    (re.compile(r"\b(?:pre|post|cross)-[Ss]lice"), "slice-phase reference"),
    (re.compile(r"\bS\d+ Slice"), "spec/slice reference (e.g. 'S6 Slice')"),
    (re.compile(r"\bPlan Slice"), "plan-document slice reference"),
    (re.compile(r"\bKU\d"), "'known unknown' tag (KU<n>)"),
    (re.compile(r"\b[Kk]nown [Uu]nknown"), "'known unknown' phrase"),
    (re.compile(r"\bAC\d|\bAC-[A-Z]"), "acceptance-criteria tag (AC<n>)"),
    (
        re.compile(r"[Cc]ouncil/(?:Security|Reliability|Advocate|Builder|Breaker|Attacker)"),
        "council-review role reference",
    ),
    (re.compile(r"\bumbrella decision\b"), "'umbrella decision' planning reference"),
    (re.compile(r"\([A-Z]-?\d+\)"), "lettered invariant/spec tag (e.g. (D-1), (A-3), (F5))"),
]


def _scan() -> list[str]:
    """Return ``relpath:lineno: <label> — <line>`` for every dev-process ref found."""
    offenders: list[str] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.resolve() == SELF:
                continue
            if "__pycache__" in path.parts:
                continue
            if path.suffix not in _SCANNED_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel = path.relative_to(LORE_ROOT)
            for lineno, line in enumerate(text.splitlines(), start=1):
                for pattern, label in _DENYLIST:
                    if pattern.search(line):
                        offenders.append(f"{rel}:{lineno}: {label} — {line.strip()}")
                        break
    return offenders


def test_lore_tree_has_no_dev_process_refs():
    offenders = _scan()
    assert not offenders, (
        "Internal dev-process references found in the shipped lore tree — these "
        "must not appear in shipped code, docstrings, or tests (see the convention "
        "in trailhead/CLAUDE.md). Reword to describe the behavior directly:\n  "
        + "\n  ".join(offenders)
    )
