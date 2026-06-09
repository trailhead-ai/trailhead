"""Deterministic helpers for the `/forge:pickup` dev ritual.

`/forge:pickup` resumes work shelved by `/forge:handoff`: it surfaces the
recorded git state + pickup hints and (on the lore backend) flips the shelved
session note back to active. The SKILL.md orchestrates; the testable logic
lives here:

  - ``most_recent_handoff``  symmetric degraded read — locate the newest
    forge handoff file (or one named by slug) under the handoff dir.
  - ``parse_pickup_hints``   extract the ``## Pickup hints`` section body from
    a handoff file or session note.

PICKUP NEVER RESTORES CODE. It surfaces recorded (possibly stale) state only —
the recorded branch / ahead-count / commit list is a snapshot taken at handoff
time, not live git state. Restoring the working tree is the user's job.

The degraded read is SYMMETRIC with handoff's degraded write: handoff writes
``~/.forge/handoffs/<slug>.md`` (out of any repo), and pickup reads from the
same location so a lore-absent handoff is never orphaned.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class HandoffFile:
    path: Path
    content: str


def _default_handoff_dir() -> Path:
    return Path.home() / ".forge" / "handoffs"


def most_recent_handoff(
    handoff_dir: Path | None = None,
    slug: str | None = None,
) -> HandoffFile | None:
    """Return the relevant forge handoff file, or None.

    With a *slug*, targets ``<handoff_dir>/<slug>.md`` specifically. Without a
    slug, returns the most-recently-modified ``*.md`` in *handoff_dir* — the
    symmetric counterpart to handoff's degraded write, so a lore-absent handoff
    always has a guaranteed path back.

    Returns None when the directory is missing/empty or the named slug file
    does not exist. Never raises.
    """
    handoff_dir = Path(handoff_dir) if handoff_dir is not None else _default_handoff_dir()
    if not handoff_dir.is_dir():
        return None

    if slug is not None:
        candidate = handoff_dir / f"{slug}.md"
        # Containment guard (symmetric with the lore-side resume guard): a slug
        # like "../escape" must not read outside handoff_dir, even though it's a
        # read-only, user-supplied value.
        try:
            candidate.resolve().relative_to(handoff_dir.resolve())
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        try:
            return HandoffFile(path=candidate, content=candidate.read_text())
        except Exception:
            return None

    files = [p for p in handoff_dir.glob("*.md") if p.is_file()]
    if not files:
        return None
    # Most-recently-modified first; filename as a stable secondary key so the
    # choice is deterministic when mtimes tie.
    newest = sorted(files, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)[0]
    try:
        return HandoffFile(path=newest, content=newest.read_text())
    except Exception:
        return None


def parse_pickup_hints(text: str) -> str:
    """Extract the body under a ``## Pickup hints`` heading.

    Returns the lines between the heading and the next ``## `` heading (or EOF),
    stripped. Returns an empty string when no such section exists. Never raises.
    """
    lines = text.splitlines()
    collected: list[str] = []
    in_hints = False
    for line in lines:
        if line.strip().lower().startswith("## pickup hints"):
            in_hints = True
            continue
        if in_hints:
            if line.strip().startswith("## "):
                break
            collected.append(line)
    return "\n".join(collected).strip()


# ---------------------------------------------------------------------------
# CLI entrypoint (thin — the SKILL.md orchestrates; this exposes the helpers)
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="pickup_resume", description=__doc__)
    ap.add_argument(
        "--handoff-dir",
        default=str(_default_handoff_dir()),
        help="directory holding forge handoff files (default: ~/.forge/handoffs)",
    )
    ap.add_argument("--slug", default=None, help="target a specific handoff slug")
    args = ap.parse_args(argv)

    found = most_recent_handoff(Path(args.handoff_dir).expanduser(), slug=args.slug)
    if found is None:
        print("nothing to resume — no forge handoff file found")
        return 0

    print(found.path)
    hints = parse_pickup_hints(found.content)
    if hints:
        print()
        print("## Pickup hints")
        print()
        print(hints)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
