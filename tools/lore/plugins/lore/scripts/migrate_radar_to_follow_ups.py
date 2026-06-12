"""One-shot vault migration: radar/ → follow-ups/ (Slice 6).

Moves every note under ``<vault>/radar/`` to ``<vault>/follow-ups/`` (preserving
any ``YYYY-MM/`` substructure), rewrites the ``type: radar`` frontmatter anchor
to ``type: follow-up`` (frontmatter block ONLY — body prose is never touched),
fixes the single known off-vocab ``status: closed`` outlier to ``status:
dropped`` (so ``status_validator`` accepts it post-move), deletes the stale
auto-generated ``radar/_index.md`` (regenerated separately), and writes a
pre-migration manifest ``follow-ups-migration-manifest.json`` of every
``{old → new}`` path so a reverse is trivially scriptable.

The migration is the one irreversible step in the rename. Run ``--dry-run``
first to preview the planned moves + rewrites without touching disk.

Properties:
  - **Filesystem-derived**: the manifest is built from the live ``radar/`` tree,
    never a hardcoded count.
  - **Idempotent**: a second run is a no-op once ``radar/`` is gone.
  - **Path-safe**: refuses to operate on any note whose resolved source or
    destination escapes the vault root (no ``../`` traversal / symlink escape).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_INDEX_NAME = "_index.md"
_MANIFEST_NAME = "follow-ups-migration-manifest.json"

# Off-vocab status outlier → canonical replacement (status_validator rejects
# "closed"; the canonical radar/follow-up vocab is active|resolved|dropped).
_STATUS_FIXES = {"closed": "dropped"}


@dataclass
class Move:
    old: Path
    new: Path
    type_rewrite: bool
    status_fix: tuple[str, str] | None  # (old_status, new_status) or None


def _is_within(child: Path, parent: Path) -> bool:
    """True if ``child`` (resolved) is inside ``parent`` (resolved)."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _rewrite_frontmatter(text: str) -> tuple[str, bool, tuple[str, str] | None]:
    """Rewrite the leading YAML frontmatter block ONLY.

    Returns (new_text, type_rewritten, status_fix). Lines after the closing
    ``---`` (the body) are never inspected, so body prose containing the word
    "radar" is left byte-identical.
    """
    if not text.startswith("---\n"):
        return text, False, None

    # Locate the closing fence as a FULL line equal to '---' (anchored), never a
    # loose substring — so a body markdown rule ('---' / '----') or a body line
    # that merely starts with '---' can't be mistaken for the fence and pull body
    # prose into the rewrite region.
    lines = text.splitlines(keepends=True)
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n") == "---":
            close_idx = i
            break
    if close_idx is None:
        return text, False, None

    head = "".join(lines[:close_idx])   # opening '---\n' .. last fm line (exclusive of fence)
    rest = "".join(lines[close_idx:])   # the closing '---' fence onward (body untouched)

    type_rewritten = False
    status_fix: tuple[str, str] | None = None
    out_lines: list[str] = []
    for line in head.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if stripped == "type: radar":
            out_lines.append(line.replace("type: radar", "type: follow-up", 1))
            type_rewritten = True
            continue
        if stripped.startswith("status: "):
            value = stripped[len("status: "):].strip()
            if value in _STATUS_FIXES:
                new_value = _STATUS_FIXES[value]
                out_lines.append(line.replace(f"status: {value}", f"status: {new_value}", 1))
                status_fix = (value, new_value)
                continue
        out_lines.append(line)

    return "".join(out_lines) + rest, type_rewritten, status_fix


def _plan_moves(vault: Path, radar_dir: Path, follow_dir: Path) -> list[Move]:
    """Build the move plan from the filesystem. Excludes the auto-gen index."""
    moves: list[Move] = []
    for src in sorted(radar_dir.rglob("*.md")):
        if src.name == _INDEX_NAME:
            continue
        rel = src.relative_to(radar_dir)
        dst = follow_dir / rel
        text = src.read_text(encoding="utf-8")
        _, type_rewrite, status_fix = _rewrite_frontmatter(text)
        moves.append(Move(old=src, new=dst, type_rewrite=type_rewrite, status_fix=status_fix))
    return moves


def _print_plan(vault: Path, moves: list[Move], radar_dir: Path) -> None:
    print(f"Planned migration for vault: {vault}")
    print(f"  {len(moves)} note(s) to move  radar/ → follow-ups/")
    for m in moves:
        old_rel = m.old.relative_to(vault).as_posix()
        new_rel = m.new.relative_to(vault).as_posix()
        print(f"  MOVE  {old_rel}  →  {new_rel}")
        if m.type_rewrite:
            print("        rewrite frontmatter: type: radar → type: follow-up")
        if m.status_fix is not None:
            old_s, new_s = m.status_fix
            print(f"        fix off-vocab status: status: {old_s} → status: {new_s}")
    index = radar_dir / _INDEX_NAME
    if index.exists():
        print(f"  DELETE {index.relative_to(vault).as_posix()} (stale auto-generated index)")


def migrate(vault: Path, *, dry_run: bool) -> int:
    vault = vault.expanduser().resolve()
    radar_dir = vault / "radar"
    follow_dir = vault / "follow-ups"

    if not radar_dir.exists():
        print(f"Nothing to migrate: no radar/ directory under {vault}")
        return 0

    # Path-safety: radar_dir must resolve inside the vault root (refuse symlink /
    # ../ escape). resolve() follows symlinks, so an escaping radar/ is caught.
    if not _is_within(radar_dir, vault):
        print(
            f"error: radar/ resolves outside the vault root ({radar_dir.resolve()} "
            f"escapes {vault}) — refusing to migrate.",
            file=sys.stderr,
        )
        return 1

    moves = _plan_moves(vault, radar_dir, follow_dir)

    # Path-safety: every destination must stay inside the vault root.
    for m in moves:
        if not _is_within(m.new.parent if not m.new.exists() else m.new, vault):
            print(
                f"error: destination {m.new} escapes the vault root {vault} — refusing.",
                file=sys.stderr,
            )
            return 1

    if not moves:
        print(f"Nothing to migrate: radar/ under {vault} has no notes.")
        return 0

    if dry_run:
        _print_plan(vault, moves, radar_dir)
        print("\n(dry-run — nothing was changed.)")
        return 0

    # Write the pre-migration manifest FIRST (so a reverse is scriptable even if
    # a later step is interrupted). Write-once: if a manifest already exists (a
    # prior interrupted run), refuse to clobber it — re-planning from the live
    # filesystem would otherwise shrink the reverse record to the not-yet-moved
    # subset and lose the already-moved entries.
    manifest_path = vault / _MANIFEST_NAME
    if manifest_path.exists():
        print(
            f"error: {manifest_path} already exists (prior run?) — refusing to "
            f"overwrite the pre-migration manifest. Remove it to re-migrate.",
            file=sys.stderr,
        )
        return 1
    manifest = {
        "vault": str(vault),
        "from": "radar",
        "to": "follow-ups",
        "moves": [
            {
                "old": m.old.relative_to(vault).as_posix(),
                "new": m.new.relative_to(vault).as_posix(),
                "type_rewrite": m.type_rewrite,
                "status_fix": list(m.status_fix) if m.status_fix else None,
            }
            for m in moves
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    for m in moves:
        text = m.old.read_text(encoding="utf-8")
        new_text, _, _ = _rewrite_frontmatter(text)
        m.new.parent.mkdir(parents=True, exist_ok=True)
        m.new.write_text(new_text, encoding="utf-8")
        m.old.unlink()

    # Delete the stale auto-generated index (regenerated separately).
    index = radar_dir / _INDEX_NAME
    if index.exists():
        index.unlink()

    # Remove the now-empty radar/ tree (deepest dirs first).
    for d in sorted(radar_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    if radar_dir.exists() and not any(radar_dir.iterdir()):
        radar_dir.rmdir()

    print(f"Migrated {len(moves)} note(s): radar/ → follow-ups/ under {vault}")
    print(f"Manifest written: {manifest_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate vault radar/ notes to follow-ups/.")
    parser.add_argument("--vault", required=True, help="Path to the lore vault root.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned moves + frontmatter rewrites without touching disk.",
    )
    args = parser.parse_args(argv)
    return migrate(Path(args.vault), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
