#!/usr/bin/env python3
"""Generic, denylist-driven leak gate.

Scans a tree for any case-insensitive match of the regexes in a denylist file
and fails the commit if a private string would ship. The gate ships ZERO
private strings itself — every forbidden token lives in a machine-local
denylist (default: ~/.claude/leak-gate.denylist), which is never tracked in any
repo. That keeps the publishable plugin repos (forge, lore) free of the very
tokens they must not leak.

Usage:
    leak_gate.py <tree-path> [<tree-path> ...] [--denylist <path>]

Multiple trees may be scanned in one invocation (e.g. a plugin's shippable
surface AND its tests/ dir — both go public, but tests/ often sits outside a
single shippable-surface path). A missing path among them fails closed.

Denylist format:
    One Python regex per line. `#` starts a comment; blank lines ignored.
    Matching is case-insensitive. Use \\b anchors for word-boundary tokens
    (e.g. `\\bprojections\\b`) and `metric\\.[a-z_]+` for dotted metric names.

Exit codes:
    0  clean — no denylist token found in the tree
    1  leak  — at least one match (prints `relpath:lineno:token` per hit)
    2  error — fail-closed: denylist missing/unreadable/pattern-empty, or the
       tree path does not exist. NEVER exits 0 when it could not actually
       certify the tree as clean.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

DEFAULT_DENYLIST = Path.home() / ".claude" / "leak-gate.denylist"

# Text file types to scan. Extensionless files are scanned if they decode as
# UTF-8 (covers shell/cli scripts with no suffix).
_TEXT_EXTENSIONS = {
    ".md", ".py", ".sh", ".json", ".txt", ".toml", ".cfg", ".ini", ".yaml",
    ".yml", ".bash", ".zsh", ".fish",
}


def _err(msg: str) -> None:
    print(f"leak-gate: {msg}", file=sys.stderr)


def _load_denylist(path: Path) -> list[re.Pattern]:
    """Load and compile denylist regexes. Raises ValueError on any failure so
    the caller can fail closed (exit 2)."""
    if not path.exists():
        raise ValueError(f"denylist not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"denylist unreadable: {path} ({e})")
    patterns: list[re.Pattern] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            patterns.append(re.compile(stripped, re.IGNORECASE))
        except re.error as e:
            raise ValueError(f"invalid regex in denylist line {lineno}: {stripped!r} ({e})")
    if not patterns:
        raise ValueError(f"denylist has no patterns: {path}")
    return patterns


def _text_files(tree: Path):
    for path in sorted(tree.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith("__pycache__") or part == ".git" for part in path.parts):
            continue
        if path.suffix in _TEXT_EXTENSIONS:
            yield path
            continue
        if not path.suffix:
            try:
                path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            yield path


def scan(tree: Path, patterns: list[re.Pattern]) -> list[tuple[Path, int, str]]:
    hits: list[tuple[Path, int, str]] = []
    for f in _text_files(tree):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pat in patterns:
                m = pat.search(line)
                if m:
                    hits.append((f, lineno, m.group(0)))
                    break
    return hits


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Denylist-driven leak gate.")
    ap.add_argument("trees", nargs="+", metavar="tree", help="one or more directories to scan")
    ap.add_argument(
        "--denylist",
        default=os.environ.get("LEAK_GATE_DENYLIST", str(DEFAULT_DENYLIST)),
        help="path to denylist file (default: $LEAK_GATE_DENYLIST or ~/.claude/leak-gate.denylist)",
    )
    args = ap.parse_args(argv)

    trees = [Path(t) for t in args.trees]
    for t in trees:
        if not t.exists():
            _err(f"tree path does not exist: {t}")
            return 2

    try:
        patterns = _load_denylist(Path(args.denylist).expanduser())
    except ValueError as e:
        _err(str(e))
        _err("failing closed — cannot certify the tree clean without a denylist")
        return 2

    total = 0
    for t in trees:
        base = t.resolve()
        for f, lineno, token in scan(t, patterns):
            try:
                rel = f.resolve().relative_to(base)
            except ValueError:
                rel = f
            print(f"{rel}:{lineno}:{token}")
            total += 1
    if total:
        _err(f"{total} forbidden token(s) found — commit blocked")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
