"""Drift guard: agent-facing docs must not claim removed capabilities are shipped.

The June 2026 fully-pull refactor stripped every lore hook (SessionStart,
PostToolUse, WorktreeRemove — ``hooks/hooks.json`` is ``{"hooks": {}}``, pinned by
``test_manifest_validity.py``) and retired the branch-keyword recall path
(``test_recall_retired.py``). Code and tests were updated for the refactor; prose
was not, and a prior session twice asserted a false capability to the user because
``ROADMAP.md`` still described it as shipped.

This is a *mechanical* guard so a future capability removal cannot silently leave
agent-facing prose behind the way this one did: a refactor that removes a
capability must treat prose as part of its blast radius.

Scope — agent-facing prose and source, **excluding** ``tests/``: the shipped
plugin (``plugins/lore``) plus the top-level docs adopters and agents read
(``ROADMAP.md``, ``README.md``, ``MANUAL-SMOKE.md``, ``docs/``). Test files are
deliberately out of scope: they legitimately describe these removed capabilities
*by name* to assert their absence (e.g. "harvest-candidates.py is absent and
hooks.json carries no PostToolUse entry") or narrate the refactor's history (e.g.
"`lore flush` replaces `lore finish`") — a content guard over that prose would
punish the tests that pin the removal, not catch a drift regression. None of the
capabilities named below are ever legitimately claimed as *current* behavior
outside tests/, so the denylist needs no allowlist within its scanned scope.
"""

from __future__ import annotations

import re
from pathlib import Path

# tests/ -> tools/lore
LORE_ROOT = Path(__file__).resolve().parent.parent
DOCS_ROOT = LORE_ROOT / "docs"
PLUGIN_ROOT = LORE_ROOT / "plugins" / "lore"

# This file necessarily spells the forbidden tokens out (to define the denylist
# and to explain the convention), so it must never scan itself.
SELF = Path(__file__).resolve()

_SCANNED_SUFFIXES = {".py", ".md", ".toml", ".json"}

# Each entry: (compiled pattern, human label). These are the removed-capability
# strings named in the task that filed this guard.
_DENYLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bbranch-keyword\b"), "branch-keyword recall (removed)"),
    (re.compile(r"\bharvest-candidates\b"), "harvest-candidates PostToolUse hook (removed)"),
    (re.compile(r"\blore finish\b"), "'lore finish' command (renamed to 'lore flush')"),
    (re.compile(r"\bRECALL_CLASSIFIER_ENABLED\b"), "RECALL_CLASSIFIER_ENABLED flag (never defined)"),
    (re.compile(r"\bbuild_context\b"), "build_context function (never defined)"),
    # Bare "SessionStart" has legitimate non-lore-recall uses (a generic hook-name
    # example, a user-installable project hook, prose describing its absence) —
    # only flag it co-occurring with recall/fires/injection/pointer, the shape of
    # the false "lore pushes recall at SessionStart" claim this guard exists for.
    (
        re.compile(
            r"(?:recall|fires?)\b.{0,60}\bSessionStart|\bSessionStart\b.{0,60}\b(?:recall|fires?|injection|pointer)\b",
            re.IGNORECASE,
        ),
        "SessionStart recall/injection claim (no push hook is installed)",
    ),
]


def _scan() -> list[str]:
    """Return ``relpath:lineno: <label> — <line>`` for every drifted claim found."""
    offenders: list[str] = []
    candidates: list[Path] = []
    if DOCS_ROOT.exists():
        candidates.extend(DOCS_ROOT.rglob("*"))
    if PLUGIN_ROOT.exists():
        candidates.extend(PLUGIN_ROOT.rglob("*"))
    # Top-level docs (ROADMAP.md, README.md, MANUAL-SMOKE.md, capabilities.toml, …)
    # without recursing into docs/, plugins/, or tests/ (each handled separately
    # or deliberately excluded).
    candidates.extend(p for p in LORE_ROOT.iterdir() if p.is_file())

    for path in sorted(set(candidates)):
        if not path.is_file():
            continue
        if path.resolve() == SELF:
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix not in _SCANNED_SUFFIXES:
            continue
        rel = path.relative_to(LORE_ROOT)
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


def test_agent_facing_docs_have_no_capability_drift():
    offenders = _scan()
    assert not offenders, (
        "Agent-facing docs claim a capability that was removed in the fully-pull "
        "refactor — an agent trusting this prose will assert something false. "
        "Correct the prose (or, if the capability shipped again, drop it from "
        "this denylist):\n  " + "\n  ".join(offenders)
    )
