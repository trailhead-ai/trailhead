"""Drift guard: the shipped lore tree carries no internal dev-process references.

The project convention (trailhead/CLAUDE.md) is that comments, docstrings, and
tests must stand on their own and must NOT reference internal planning artifacts:
development "slices", lettered "specs" / invariant tags (``D-1``, ``S-3``,
``A-3`` …), "known unknowns", council reviews, or plan documents. Those live in
the project's working notes, not in the shipped code or its tests.

This is a *mechanical* enforcement of that convention so the refs cannot silently
creep back in. The earlier scrub was one-time; without this guard, routine work
on ``main`` kept reintroducing slice/KU/council refs into the lore test suite.

Scope — the **whole lore tree**: the shipped plugin (``plugins/lore``), this
test suite (``tests/``), and the top-level ``tools/lore`` docs (``docs/`` plus
top-level files like ``MANUAL-SMOKE.md``, ``README.md``, ``ROADMAP.md``) —
mirroring the top-level-file scan ``test_capability_doc_drift.py`` already runs.
Those top-level docs were previously unscanned, so a dev-process leak there
(e.g. ``MANUAL-SMOKE.md`` carrying ``Slice 5``/``F5``/``S6`` references) was
invisible to this guard. lore has no legitimate vocabulary of this shape, so
the denylist needs no allowlist.

Both *content* and *filenames* are scanned. Filenames matter because test files
were once named after plan phases (``test_p1a_...``, ``test_p3_...``): a
content-only guard scrubbed their bodies but left the plan-phase prefix in the
name. ``_NAME_DENYLIST`` closes that gap so a plan-phase-named file cannot creep
back in.

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
import sys
from pathlib import Path

# tests/ -> tools/lore
LORE_ROOT = Path(__file__).resolve().parent.parent
DOCS_ROOT = LORE_ROOT / "docs"
SCAN_ROOTS = (LORE_ROOT / "plugins" / "lore", LORE_ROOT / "tests", DOCS_ROOT)

# Top-level tools/lore files (MANUAL-SMOKE.md, README.md, ROADMAP.md, …) are not
# under any of SCAN_ROOTS above — mirror test_capability_doc_drift.py's
# LORE_ROOT.iterdir() pattern to reach them without recursing into the
# subtrees SCAN_ROOTS already covers (or into tools/lore's other subdirs, e.g.
# cli/, bin/, lore/, which are out of this guard's scope).

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

# Filename patterns. ``_p<digit>`` is the plan-phase test-file prefix
# (``test_p1a_...``, ``test_p3_...``): an underscore immediately before the ``p``
# avoids matching legitimate stems like ``test_step3`` or ``test_top3``.
_NAME_DENYLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"_p\d"), "plan-phase filename prefix (e.g. test_p3_...)"),
]


def _candidate_paths() -> list[Path]:
    """Every file under SCAN_ROOTS, plus non-recursive top-level LORE_ROOT files.

    The top-level files (``MANUAL-SMOKE.md``, ``README.md``, ``ROADMAP.md``, …)
    sit as siblings of ``plugins/``, ``tests/``, and ``docs/`` — none of which
    reach them via rglob — so they're gathered separately with a non-recursive
    ``iterdir()``, mirroring ``test_capability_doc_drift.py``'s ``LORE_ROOT.iterdir()``
    top-level-file pattern.
    """
    candidates: list[Path] = []
    for root in SCAN_ROOTS:
        if root.exists():
            candidates.extend(root.rglob("*"))
    if LORE_ROOT.exists():
        candidates.extend(p for p in LORE_ROOT.iterdir() if p.is_file())
    return sorted(set(candidates))


def _scan() -> list[str]:
    """Return ``relpath:lineno: <label> — <line>`` for every dev-process ref found.

    Filename offenders use lineno ``0`` and report the name itself in place of a line.
    """
    offenders: list[str] = []
    for path in _candidate_paths():
        if not path.is_file():
            continue
        if path.resolve() == SELF:
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix not in _SCANNED_SUFFIXES:
            continue
        rel = path.relative_to(LORE_ROOT)
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


def test_lore_tree_has_no_dev_process_refs():
    offenders = _scan()
    assert not offenders, (
        "Internal dev-process references found in the shipped lore tree — these "
        "must not appear in shipped code, docstrings, or tests (see the convention "
        "in trailhead/CLAUDE.md). Reword to describe the behavior directly:\n  "
        + "\n  ".join(offenders)
    )


def test_scan_reaches_docs_and_top_level_files(tmp_path, monkeypatch):
    """Meta-test: the widened roots actually reach docs/ and top-level files.

    Builds a tmp_path tree shaped like tools/lore (docs/ dir + a top-level file,
    both outside plugins/lore and tests/) and proves _scan() reports a
    denylisted token planted in each — regressing to the pre-widen SCAN_ROOTS
    (``plugins/lore``, ``tests`` only) would silently pass this test's setup
    without ever finding either offender.
    """
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "EXTENDING.md").write_text("See Slice 5 for the rollout plan.\n")
    (tmp_path / "MANUAL-SMOKE.md").write_text("Ran through Slice 5 of the smoke test.\n")
    # Keep the existing subtrees present (empty) so the function's normal
    # scan of them doesn't error on a missing path.
    (tmp_path / "plugins" / "lore").mkdir(parents=True)
    (tmp_path / "tests").mkdir()

    monkeypatch.setattr(sys.modules[__name__], "LORE_ROOT", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "DOCS_ROOT", docs_dir)
    monkeypatch.setattr(
        sys.modules[__name__],
        "SCAN_ROOTS",
        (tmp_path / "plugins" / "lore", tmp_path / "tests", docs_dir),
    )

    offenders = _scan()
    offender_paths = {o.split(":", 1)[0] for o in offenders}
    assert "docs/EXTENDING.md" in offender_paths, (
        "widened SCAN_ROOTS did not reach a docs/-shaped file: " + repr(offenders)
    )
    assert "MANUAL-SMOKE.md" in offender_paths, (
        "widened scan did not reach a top-level file: " + repr(offenders)
    )
