"""Canonical-status validator for vault notes.

Each note type has a fixed `status:` vocabulary. The pre-commit guard and
the `lore set-status` command both validate against these sets so a note
can never carry an off-vocabulary status.

The canonical sets are the single source of truth for the whole plugin —
do not invent statuses; add them here and to the scaffolded glossary.
"""
from __future__ import annotations

# Canonical status sets per note type, keyed by the directory / plural name.
CANONICAL: dict[str, frozenset[str]] = {
    "plans": frozenset({"draft", "ready", "in-progress", "complete", "superseded", "dropped", "shelved"}),
    "specs": frozenset({"draft", "ready", "planned", "complete", "superseded", "dropped", "shelved"}),
    "sessions": frozenset({"active", "complete", "shelved", "finalized", "handoff"}),
    "deferred": frozenset({"open", "scheduled", "resolved", "dropped", "graduated", "resurfaced"}),
    "radar": frozenset({"active", "resolved", "dropped"}),
    "lessons": frozenset({"active", "superseded"}),
    "dead-ends": frozenset({"active", "archived"}),
}

# Statuses that are accepted (back-compat) but deprecated: a note carrying one
# validates clean yet emits a one-line migration notice. `finalized`/`handoff`
# are legacy session terminal statuses; the canonical terminal status is
# `complete`. Accepting them keeps existing notes valid while signalling drift.
DEPRECATED: dict[str, frozenset[str]] = {
    "sessions": frozenset({"finalized", "handoff"}),
}

# Note `type:` frontmatter is usually singular ("deferred", "session",
# "dead-end"); directory names are the keys above. Map the singular form to
# the canonical key so callers can pass either.
_TYPE_ALIASES: dict[str, str] = {
    "plan": "plans",
    "spec": "specs",
    "session": "sessions",
    "deferred": "deferred",
    "radar": "radar",
    "lesson": "lessons",
    "dead-end": "dead-ends",
}


def _canonical_key(note_type: str | None) -> str | None:
    """Return the CANONICAL key for a note type/dir name, or None if untracked."""
    if not note_type:
        return None
    nt = note_type.strip()
    if nt in CANONICAL:
        return nt
    return _TYPE_ALIASES.get(nt)


def permitted_statuses(note_type: str | None) -> frozenset[str] | None:
    """Return the canonical status set for a note type, or None if untracked."""
    key = _canonical_key(note_type)
    if key is None:
        return None
    return CANONICAL[key]


def deprecated_statuses(note_type: str | None) -> frozenset[str]:
    """Return the deprecated-but-accepted status set for a note type (possibly empty)."""
    key = _canonical_key(note_type)
    if key is None:
        return frozenset()
    return DEPRECATED.get(key, frozenset())


def is_deprecated_status(note_type: str | None, status: str) -> bool:
    """True if `status` is accepted for `note_type` but flagged for migration."""
    return status in deprecated_statuses(note_type)


def is_valid_status(note_type: str | None, status: str) -> bool:
    """Return True if `status` is canonical for `note_type`.

    Untracked note types (outside the validated vocabulary) are
    unconstrained and always return True.
    """
    permitted = permitted_statuses(note_type)
    if permitted is None:
        return True
    return status in permitted


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: validate status frontmatter for each given file.

    Usage: status_validator.py <file.md> [<file.md> ...]

    Reads `type` and `status` frontmatter from each file. Exits non-zero if
    any file has an off-vocabulary status for its type. Prints the offending
    file+value to stderr. Exits 0 when all files pass (or no files given).
    Untracked note types are unconstrained and always pass.
    """
    import sys
    from pathlib import Path

    # Import frontmatter relative to this file so it works from repo or installed.
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    import frontmatter as _fm  # noqa: PLC0415

    args = argv if argv is not None else sys.argv[1:]
    violations: list[str] = []
    deprecations: list[str] = []

    for path_str in args:
        path = Path(path_str)
        if not path.exists():
            violations.append(
                f"  {path}: path does not exist — guard internal inconsistency; reinstall with `lore init`"
            )
            continue
        try:
            meta = _fm.parse_frontmatter(path)
        except Exception:
            continue
        note_type = meta.get("type")
        status = meta.get("status")
        if status is None:
            continue
        if not is_valid_status(note_type, status):
            violations.append(
                f"  {path}: type={note_type!r} status={status!r} — not in canonical set"
            )
        elif is_deprecated_status(note_type, status):
            deprecations.append(
                f"  {path}: status={status!r} accepted (deprecated — migrate to 'complete' before cutover)"
            )

    if deprecations:
        print("status-validator: deprecated status value(s):", file=sys.stderr)
        for d in deprecations:
            print(d, file=sys.stderr)

    if violations:
        print("status-validator: invalid status value(s):", file=sys.stderr)
        for v in violations:
            print(v, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
