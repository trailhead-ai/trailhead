#!/usr/bin/env python3
"""capture_scan.py — credential-pattern scan over committed eval captures.

Committing a `claude -p` transcript verbatim to a team-shared repo needs a
mechanical control: a scan for the credential-pattern classes named in
`tools/craft/plugins/craft/skills/_shared/security.md` ("Credential-pattern
scrub"). This is the committed implementation of that scan — no capture batch
should land in `evals/**/runs/` without having been run through it.

Seven pattern classes, taken verbatim from that document:
  key-like            secret|token|passwd|password|api[_-]?key followed by = or :
  vendor-prefix       issuer-shaped tokens (AKIA…, ghp_…, xox[baprs]-…, …)
  bearer              `bearer <token>`
  api-key-shape       `api_key="..."` / `api-key: '...'`
  high-entropy-base64 32+ base64-alphabet chars
  high-entropy-hex    40+ hex chars
  pem-private-key     a PEM private-key header

Known false-positive: `/` is in the base64 alphabet, so the high-entropy
pattern also matches long slash-separated paths and record URLs (e.g.
`7313/records/trailhead/task/both-slice-...`). A high-entropy-base64 match
is reclassified as `path-or-url-shape` — reported, not silently dropped,
but not counted as a credential finding either — only when it satisfies
BOTH of two independent conditions:

  1. the match contains **four or more** `/`
  2. no `/`-separated segment of the match mixes uppercase, lowercase, AND
     a digit — a path or URL segment is a word (`records`, `harborlight`,
     `SKILL`, `7313`) and never mixes all three character classes, while a
     base64 run routinely does

Each condition alone is only ever a probabilistic proxy — `/` occurs in
the base64 alphabet with probability 1/64, so a genuine random secret can
by chance carry any number of slashes, including four or more, and can by
chance land on a segmentation that happens not to combine character classes.
An earlier version of this rule used slash count alone at a threshold of
two, on the (false, unmeasured) premise that "real secrets essentially
never" carry more than one `/` in a 32+ char run — measured over 20,000
freshly generated `base64.b64encode(os.urandom(32))` tokens, 13.9% carried
two or more `/` and were silently waved through as path-shaped. Raising
the slash-only threshold to four (the lowest slash count any committed
real-world path/URL fixture carries — record URLs like
`7313/records/trailhead/task/...`, repository paths like
`tools/craft/plugins/craft/skills/slice/SKILL`) brought the false-negative
rate down to ~0.4%, but slash count alone only ever buys a smaller
probability, never a different kind of separation, because it is still
the same alphabet-frequency argument.

Conjoining the structural condition — no segment mixing all three
character classes — is independent of slash count and drives the combined
rate much lower: measured over 160,000 generated
`base64.b64encode(os.urandom(32))` tokens (four independently seeded runs
of 40,000), slash-count-alone (>=4) false-negatives at 0.394%, the
structural condition alone at 0.074%, and the two conjoined at 0.0019%
(3/160,000) — roughly 200x lower than slash count alone. It is not zero: a
sufficiently rare random secret can still land on four-or-more slashes
with no mixed segment. Every committed real-world path/URL fixture this
scanner must keep classifying safely still passes both conditions (a path
or URL segment is a word, never a three-class blend), so the conjunction
does not misclassify any known-safe fixture.

Usage:
  capture_scan.py <tree-or-file> [<tree-or-file> ...]

Exit codes:
  0  clean — no credential-class finding in the scanned tree(s). A
     known-safe-class line (e.g. `path-or-url-shape`) is still printed to
     stdout on this exit — 0 means "nothing that counts as a credential
     finding", not "nothing printed". Callers must check the exit code, not
     whether stdout is empty.
  1  finding(s) — prints `relpath:lineno:class:token` per hit, commit blocked
  2  error — fail-closed: a given path does not exist
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

KNOWN_SAFE_CLASS = "path-or-url-shape"

# Ordered (name, pattern) pairs — each name is one of the seven classes named
# in security.md's credential-pattern scrub list. All patterns are evaluated
# independently per line; a line may carry findings from more than one class.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "vendor-prefix",
        re.compile(
            r"(?i)\b(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}"
            r"|glpat-[A-Za-z0-9_-]{20}|xox[baprs]-[A-Za-z0-9-]+|sk_live_[A-Za-z0-9]+"
            r"|AIza[0-9A-Za-z_-]{35})\b"
        ),
    ),
    (
        "key-like",
        re.compile(r"(?i)(secret|token|passwd|password|api[_-]?key)[A-Za-z0-9_-]*\s*[=:]\s*\S+"),
    ),
    (
        "api-key-shape",
        re.compile(r"(?i)api[_-]?key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{16,}"),
    ),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")),
    ("high-entropy-hex", re.compile(r"\b[A-Fa-f0-9]{40,}\b")),
    ("high-entropy-base64", re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b")),
]


class Finding(NamedTuple):
    lineno: int
    cls: str
    text: str


# Measured over 160,000 generated base64(os.urandom(32)) tokens (four
# independently seeded runs of 40,000) — see the module docstring for the
# full rationale and the conjoined false-negative rate this threshold and
# the structural condition below together measure.
_PATH_SLASH_THRESHOLD = 4


def _no_segment_mixes_upper_lower_digit(matched: str) -> bool:
    """True when no `/`-separated segment of `matched` mixes uppercase,
    lowercase, AND a digit. A path or URL segment is a word and never mixes
    all three character classes; a base64 run routinely does.
    """
    for segment in matched.split("/"):
        has_upper = any(c.isupper() for c in segment)
        has_lower = any(c.islower() for c in segment)
        has_digit = any(c.isdigit() for c in segment)
        if has_upper and has_lower and has_digit:
            return False
    return True


def _reclassify(cls: str, matched: str) -> str:
    if (
        cls == "high-entropy-base64"
        and matched.count("/") >= _PATH_SLASH_THRESHOLD
        and _no_segment_mixes_upper_lower_digit(matched)
    ):
        return KNOWN_SAFE_CLASS
    return cls


def is_credential_class(cls: str) -> bool:
    return cls != KNOWN_SAFE_CLASS


def scan_text(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        seen: set[tuple[int, str, tuple[int, int]]] = set()
        for cls, pattern in _PATTERNS:
            for m in pattern.finditer(line):
                actual_cls = _reclassify(cls, m.group(0))
                key = (lineno, actual_cls, m.span())
                if key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(lineno, actual_cls, m.group(0)))
    return findings


def scan_file(path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return scan_text(text)


def _iter_files(tree: Path):
    if tree.is_file():
        yield tree
        return
    for p in sorted(tree.rglob("*")):
        if p.is_file() and not any(
            part.startswith("__pycache__") or part == ".git" for part in p.parts
        ):
            yield p


def _err(msg: str) -> None:
    print(f"capture-scan: {msg}", file=sys.stderr)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Credential-pattern scan over eval captures.")
    ap.add_argument("trees", nargs="+", metavar="path", help="one or more files/directories")
    args = ap.parse_args(argv)

    trees = [Path(t) for t in args.trees]
    for t in trees:
        if not t.exists():
            _err(f"path does not exist: {t}")
            return 2

    total = 0
    for t in trees:
        base = t if t.is_dir() else t.parent
        base = base.resolve()
        for f in _iter_files(t):
            for finding in scan_file(f):
                try:
                    rel = f.resolve().relative_to(base)
                except ValueError:
                    rel = f
                print(f"{rel}:{finding.lineno}:{finding.cls}:{finding.text}")
                if is_credential_class(finding.cls):
                    total += 1
    if total:
        _err(f"{total} credential finding(s) — commit blocked")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
