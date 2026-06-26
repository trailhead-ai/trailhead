"""Drift guard: the shipped camp tree carries no internal dev-process references.

The project convention (trailhead/CLAUDE.md) is that comments, docstrings, and
tests must stand on their own and must NOT reference internal planning artifacts:
development "slices", lettered "specs" / invariant tags (``D-1``, ``S-3``,
``D-E`` …), "known unknowns" (``U1``, ``KU2``), council-review roles, lesson
notes, or code-review finding IDs (``CR-7``). Those live in the project's working
notes, not in the shipped code or its tests.

This is a *mechanical* enforcement of that convention so the refs cannot silently
creep back in. camp picked these up once when its feature branch was collapsed
onto a ``main`` that had already scrubbed them; without this guard, a future
overlay or merge can reintroduce them.

Scope — the **whole camp tree**: the shipped plugin (``plugins/camp``), the
example group configs (``groups.example``), and this test suite (``tests/``).
camp has no legitimate vocabulary of this shape, so the denylist needs no
allowlist.

Both *content* and *filenames* are scanned. Filenames matter because test files
were once named after plan phases (``test_slice1_...``, ``test_slice3_...``): a
content-only guard scrubbed their bodies but left the phase prefix in the name.
``_NAME_DENYLIST`` closes that gap so a phase-named file cannot creep back in.

Why camp is guarded (like lore, unlike craft): craft ships "Slice" and "Known
Unknown" as *product* vocabulary (its plan template's ``### Slice N`` headings,
the council panel) and its tests legitimately assert on that content, so craft
cannot be guarded mechanically. camp ships none of that vocabulary — it is a
worktree-orchestration tool — so the denylist is safe here.
"""

from __future__ import annotations

import re
from pathlib import Path

# tests/ -> tools/camp
CAMP_ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = (
    CAMP_ROOT / "plugins" / "camp",
    CAMP_ROOT / "groups.example",
    CAMP_ROOT / "tests",
)

# This file necessarily spells the forbidden tokens out (to define the denylist
# and to explain the convention), so it must never scan itself.
SELF = Path(__file__).resolve()

# Files worth scanning: source, docs, and config that ship or describe behavior.
# The empty suffix covers extensionless entry points (cli/camp, bin/camp).
# Anything textual is read; undecodable (binary) files are skipped.
_SCANNED_SUFFIXES = {".py", ".md", ".toml", ".json", ".txt", ""}

# Each entry: (compiled pattern, human label). Patterns encode the convention's
# forbidden shapes. None of these occur anywhere in a clean camp tree.
_DENYLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bSlice \d"), "development 'slice' reference"),
    (re.compile(r"\b(?:pre|post|cross)-[Ss]lice"), "slice-phase reference"),
    (re.compile(r"\bS\d+ Slice"), "spec/slice reference (e.g. 'S6 Slice')"),
    (re.compile(r"\bPlan Slice"), "plan-document slice reference"),
    (re.compile(r"\bKU\d"), "'known unknown' tag (KU<n>)"),
    (re.compile(r"\bU\d+\b"), "'unknown' tag (U<n>)"),
    (re.compile(r"\b[Kk]nown [Uu]nknown"), "'known unknown' phrase"),
    (re.compile(r"\bAC\d|\bAC-[A-Z]"), "acceptance-criteria tag (AC<n>)"),
    (
        re.compile(r"[Cc]ouncil/(?:Security|Reliability|Advocate|Builder|Breaker|Attacker)"),
        "council-review role reference",
    ),
    (re.compile(r"\bumbrella decision\b"), "'umbrella decision' planning reference"),
    (re.compile(r"\bD-[A-I]\b"), "lettered decision tag (e.g. D-E, D-H)"),
    (re.compile(r"\([A-Z]-?\d+\)"), "lettered invariant/spec tag (e.g. (D-1), (C2))"),
    (re.compile(r"\bCR-?\d"), "code-review finding ID (CR-<n>)"),
    (re.compile(r"\bFIX \d"), "code-review fix ID (FIX <n>)"),
    (re.compile(r"\bsimplify #\d"), "simplify-item reference (#<n>)"),
    (re.compile(r"\blessons?:"), "lesson-note reference"),
    (re.compile(r"\b(?:de-?zenith|zenith|quarr)"), "zenith/quarry provenance reference"),
]

# Filename patterns. ``slice\d`` is the plan-phase test-file prefix
# (``test_slice1_...``, ``test_slice3_...``); ``_p\d`` is the lore-style
# plan-phase prefix, guarded here too so the shape cannot migrate in.
_NAME_DENYLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[Ss]lice\d"), "plan-phase filename prefix (e.g. test_slice3_...)"),
    (re.compile(r"_p\d"), "plan-phase filename prefix (e.g. test_p3_...)"),
]


def _scan() -> list[str]:
    """Return ``relpath:lineno: <label> — <line>`` for every dev-process ref found.

    Filename offenders use lineno ``0`` and report the name itself in place of a line.
    """
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
            rel = path.relative_to(CAMP_ROOT)
            for pattern, label in _NAME_DENYLIST:
                if pattern.search(path.name):
                    offenders.append(f"{rel}:0: {label} — {path.name}")
                    break
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for pattern, label in _DENYLIST:
                    if pattern.search(line):
                        offenders.append(f"{rel}:{lineno}: {label} — {line.strip()}")
                        break
    return offenders


def test_camp_tree_has_no_dev_process_refs():
    offenders = _scan()
    assert not offenders, (
        "Internal dev-process references found in the shipped camp tree — these "
        "must not appear in shipped code, docstrings, or tests (see the convention "
        "in trailhead/CLAUDE.md). Reword to describe the behavior directly:\n  "
        + "\n  ".join(offenders)
    )
