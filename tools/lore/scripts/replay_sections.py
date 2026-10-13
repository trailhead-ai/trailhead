#!/usr/bin/env python3
"""Read-only replay of a vault's git history through ``authored_content``.

For every commit that changed a spec or task body, derive the expected class
from the diff alone (does every changed non-blank line sit inside a naive
``## Slices`` / ``## Driver run`` region of its own side?) and compare it with
what ``authored_content`` says (equal before and after = pipeline-only). The
ground-truth region finder here is deliberately naive (no fence or comment
masking) so it shares no logic with the function under test; any disagreement
is printed for a human to adjudicate.

Only ``git log`` / ``git show`` are run against the vault.

    replay_sections.py <vault-path> [--dump-dir DIR]
"""
from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "lore"))
from lore.record.sections import authored_content  # noqa: E402

NAIVE_HEADINGS = {"spec": re.compile(r"## Slices", re.I), "task": re.compile(r"## Driver run", re.I)}
ANY_HEADING = re.compile(r"#{1,6}[ \t]|#{1,6}$")
LINE_SPLIT = re.compile(r"\r\n|\r|\n")


def git(vault: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", vault, *args], check=True, capture_output=True, text=True, errors="surrogateescape"
    ).stdout


def git_show(vault: str, rev: str, path: str) -> str | None:
    p = subprocess.run(["git", "-C", vault, "show", f"{rev}:{path}"], capture_output=True)
    if p.returncode != 0:
        return None
    return p.stdout.decode("utf-8", errors="surrogateescape")


def naive_regions(kind: str, lines: list[str]) -> set[int]:
    region: set[int] = set()
    i = 0
    while i < len(lines):
        if NAIVE_HEADINGS[kind].fullmatch(lines[i]):
            j = i + 1
            while j < len(lines) and not ANY_HEADING.match(lines[j]):
                j += 1
            region.update(range(i, j))
            i = j
        else:
            i += 1
    return region


def blank_is_boundary(lines: list[str], region: set[int], idx: int) -> bool:
    """A blank line separating content from a region, or trailing the document."""
    j = idx
    while j < len(lines) and not lines[j].strip():
        j += 1
    return j == len(lines) or j in region


def expected_class(kind: str, old: str, new: str) -> str:
    a, b = LINE_SPLIT.split(old), LINE_SPLIT.split(new)
    ra, rb = naive_regions(kind, a), naive_regions(kind, b)
    changed = False
    pipeline = True
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        for lines, region, lo, hi in ((a, ra, i1, i2), (b, rb, j1, j2)):
            for i in range(lo, hi):
                if lines[i].strip():
                    changed = True
                    pipeline &= i in region
                elif i not in region and not blank_is_boundary(lines, region, i):
                    changed = True
                    pipeline = False
    if not changed:
        return "noop"
    return "pipeline" if pipeline else "authored"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("vault")
    ap.add_argument("--dump-dir", help="write each misclassified before/after pair here")
    args = ap.parse_args()
    out = git(args.vault, "log", "--no-merges", "--format=%x00%H", "--name-status", "--", "spec", "task")
    counts: Counter[tuple[str, str]] = Counter()
    commits = 0
    mismatches = []
    for chunk in out.split("\x00")[1:]:
        sha, *rows = chunk.strip().splitlines()
        commits += 1
        for row in rows:
            status, _, path = row.partition("\t")
            kind = path.split("/")[0]
            if kind not in NAIVE_HEADINGS or not path.endswith(".md") or status[0] not in "AM":
                continue
            old = "" if status[0] == "A" else git_show(args.vault, f"{sha}^", path)
            new = git_show(args.vault, sha, path)
            if old is None or new is None:
                counts[(kind, "skipped")] += 1
                continue
            exp = expected_class(kind, old, new)
            got = "noop" if old == new else (
                "pipeline" if authored_content(kind, old) == authored_content(kind, new) else "authored"
            )
            if exp == "noop":
                got = "noop" if got != "authored" else "authored"
            counts[(kind, exp)] += 1
            if exp != got:
                mismatches.append((sha, path, exp, got))
                if args.dump_dir:
                    d = Path(args.dump_dir) / f"{sha[:10]}-{Path(path).stem[:40]}"
                    d.mkdir(parents=True, exist_ok=True)
                    (d / "old.md").write_text(old, errors="surrogateescape")
                    (d / "new.md").write_text(new, errors="surrogateescape")
    print(f"commits walked (touching spec/ or task/): {commits}")
    for (kind, cls), n in sorted(counts.items()):
        print(f"  {kind:5s} {cls:9s} {n}")
    print(f"misclassifications: {len(mismatches)}")
    for m in mismatches:
        print("  MISMATCH", *m)
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
