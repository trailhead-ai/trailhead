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
import sys
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
    #
    # The co-occurrence window (``.{0,60}``) runs with re.DOTALL so hard-wrapped
    # prose that splits the two halves across a line break (e.g. "... recall\n
    # fires at SessionStart") still matches — this pattern is applied to the
    # whole-document text (see _scan below), not per-line, so the window can
    # span the newline.
    (
        re.compile(
            r"(?:recall|fires?)\b.{0,60}\bSessionStart"
            r"|\bSessionStart\b.{0,60}\b(?:recall|fires?|injection|pointer)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "SessionStart recall/injection claim (no push hook is installed)",
    ),
]


def _scan() -> list[str]:
    """Return ``relpath:lineno: <label> — <line>`` for every drifted claim found.

    Runs each denylist pattern against the **whole-document text**, not a
    per-line loop — a per-line loop cannot see a match whose two halves are
    split across a hard-wrapped line break (the SessionStart co-occurrence
    pattern above relies on this: its ``.{0,60}`` window runs with
    ``re.DOTALL`` precisely so it can cross a newline). The reported line
    number is derived from the match's offset into the full text, and the
    reported "line" text is the first line the match starts on (a
    cross-line match's second half is elided from the report, but the
    citation still points a reader at the right place).
    """
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
        for pattern, label in _DENYLIST:
            for match in pattern.finditer(text):
                lineno = text.count("\n", 0, match.start()) + 1
                line_text = text.splitlines()[lineno - 1].strip() if text.splitlines() else ""
                offenders.append(f"{rel}:{lineno}: {label} — {line_text}")
    return offenders


def test_agent_facing_docs_have_no_capability_drift():
    offenders = _scan()
    assert not offenders, (
        "Agent-facing docs claim a capability that was removed in the fully-pull "
        "refactor — an agent trusting this prose will assert something false. "
        "Correct the prose (or, if the capability shipped again, drop it from "
        "this denylist):\n  " + "\n  ".join(offenders)
    )


def test_scan_catches_session_start_split_across_hard_wrap(tmp_path, monkeypatch):
    """Meta-test: the SessionStart co-occurrence catch spans a hard-wrapped line break.

    A per-line-only scan would miss "recall" and "SessionStart" split across a
    newline within the existing 60-char co-occurrence window — regressing to a
    line-scoped ``.{0,60}`` (no ``re.DOTALL``, matched per-line) would silently
    pass this test's setup without ever finding the offender.
    """
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "hooks.md").write_text(
        "lore configures a hook that triggers recall\n"
        "at SessionStart to seed the agent's context.\n"
    )
    plugin_dir = tmp_path / "plugins" / "lore"
    plugin_dir.mkdir(parents=True)

    monkeypatch.setattr(sys.modules[__name__], "LORE_ROOT", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "DOCS_ROOT", docs_dir)
    monkeypatch.setattr(sys.modules[__name__], "PLUGIN_ROOT", plugin_dir)

    offenders = _scan()
    assert any("SessionStart recall/injection claim" in o for o in offenders), (
        "widened cross-line matching did not catch a 'recall' / 'SessionStart' "
        "pair split across a hard-wrapped line break: " + repr(offenders)
    )
