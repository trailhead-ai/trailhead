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

This scanner is a tripwire for ACCIDENTAL leakage in a `claude -p` transcript
— it is not, and cannot be, a defense against an agent (or an attacker
shaping the agent's output) that deliberately encodes a secret to evade a
regex. Any character-class or entropy heuristic can be defeated by an author
who controls the bytes being scanned: pick an alphabet, a chunking, a case
pattern, or a delimiter that the heuristic doesn't recognize, and the scan
passes. The controls for a deliberate exfiltration attempt are outside this
script entirely — the read-only tool grant given to the process producing
the transcript, and (on a host that provides one; absent on this Linux host)
an OS-level sandbox around it. Treat every exit-0 result here as "nothing
that looks like an accidental credential leak," never as "nothing was
exfiltrated."

Known false-positive: `/` is in the base64 alphabet, so the high-entropy
pattern also matches long slash-separated paths and record URLs. Exactly
two such shapes occur in this repository:

  - lore record URLs, e.g. `7313/records/harborlight/spec/dock` — a
    `records/<vault>/<kind>/<slug-fragment>` segment run
  - repository paths, e.g. `tools/craft/plugins/craft/skills/slice/SKILL`
    (printed without its extension — `.` is not in the base64 character
    class, so the match truncates at the first `.`)

A high-entropy-base64 match is reclassified as `path-or-url-shape` —
reported, not silently dropped, but not counted as a credential finding
either — only when it carries at least four `/` AND matches one of those
two shapes *structurally*:

  1. `_record_url_shape` — some `/`-separated segment is the literal word
     "records", followed by at least three more segments (vault, kind,
     slug-fragment). This keys on a fixed ASCII word actually occurring in
     the match, not on which characters make it up.
  2. `_resolves_to_repo_path` — walking the match's segments from the repo
     root, each one exactly names (or is a truncation-safe prefix of) a
     real directory or file, ending on a real filesystem entry. This keys
     on the repository's actual file tree, not on an alphabet property.

An earlier version of this rule instead asked whether any segment mixed
uppercase, lowercase, AND a digit, on the premise that a path/URL segment
is "a word" and a base64 run "routinely" is not. That is an alphabet-shape
heuristic, and alphabet shape is exactly the property an author of the
scanned content chooses: chunking a real secret into segments that are
each single-case (all of one letter case, or digits only — base32,
hex-with-per-chunk-case, or decimal encodings all do this trivially) passed
every segment through unchanged, regardless of chunk boundary. That
character-class condition is retired; the two structural checks above key
on recognizable path/URL structure instead, which is not a property the
scanned content's author gets to shape by choice of alphabet.

Both structural checks still key on `/`-separated segmentation, so this
rule remains scoped by an entropy measurement, not a claim of exhaustive
coverage: measured over 160,000 generated `base64.b64encode(os.urandom(32))`
tokens (four independently seeded runs of 40,000), zero matched either
structural shape — the record-url shape needs the literal word "records" to
occur verbatim as a segment, and the repo-path shape needs several
consecutive segments to name real, adjacent entries in this repository's
actual directory tree, neither of which a randomly generated token
produces by chance in any observed run. That measurement describes
randomly generated tokens only; it says nothing about a token an author
deliberately shapes to mimic one of these two structures, which remains
possible in principle — a determined evasion of a regex-based scan can
still make it target either shape on purpose. Every committed real-world
path/URL fixture this scanner must keep classifying safely satisfies one
of the two structural checks, so neither retirement nor tightening
misclassifies a known-safe fixture.

Usage:
  capture_scan.py <tree-or-file> [<tree-or-file> ...]

Exit codes:
  0  clean — no credential-class finding in the scanned tree(s). A
     known-safe-class line (e.g. `path-or-url-shape`) is still printed to
     stdout on this exit — 0 means "nothing that counts as a credential
     finding", not "nothing printed". Callers must check the exit code, not
     whether stdout is empty. A trailer line on stderr states the
     known-safe match count explicitly, so a human skimming terminal
     output during the manual eval protocol does not mistake N printed
     known-safe lines for N unaddressed findings.
  1  finding(s) — prints `relpath:lineno:class:token` per hit, commit blocked
  2  error — fail-closed: a given path does not exist, or exists but cannot
     be read (a file or directory this process lacks permission to open).
     A read failure must never fall through as an empty result: for a
     control whose whole purpose is to be fail-closed before raw
     transcripts reach a shared repo, "I could not read it" must never
     render as "it is fine".
"""

from __future__ import annotations

import argparse
import os
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


class ScanError(Exception):
    """A path exists but could not be read. The caller must fail closed (exit
    2) rather than let this resolve into an empty — and therefore falsely
    clean — result.
    """


# The coarse gate before either structural check runs — a path/URL fixture
# this scanner must classify safely never carries fewer than this many `/`
# (see the module docstring). This bounds the cost of the repo-path walk; it
# is not itself the discriminator.
_PATH_SLASH_THRESHOLD = 4

# scripts/capture_scan.py lives at <repo-root>/tools/craft/plugins/craft/scripts.
_REPO_ROOT = Path(__file__).resolve().parents[5]


def _record_url_shape(segments: list[str]) -> bool:
    """True when some segment is the literal word "records", followed by at
    least three more segments — the `records/<vault>/<kind>/<slug-fragment>`
    shape every lore record URL takes. Keys on a fixed word actually present
    in the match, not on the alphabet of the surrounding characters.
    """
    for i, segment in enumerate(segments):
        if segment == "records" and len(segments) - i - 1 >= 3:
            return True
    return False


def _resolves_to_repo_path(segments: list[str]) -> bool:
    """True when `segments`, walked from the repo root, name a real file or
    directory at every step. The base64 character class excludes `.`, `-`,
    and other path-legal characters, so a real path involving them (e.g.
    `skills/slice/SKILL.md`, `evals/ritual-deliverable-.../runs`) prints
    truncated — a segment is accepted when it exactly names an entry, or is
    an unambiguous prefix of exactly one entry, at that level.
    """
    current = _REPO_ROOT
    for segment in segments:
        if not segment:
            return False
        try:
            entries = [e.name for e in current.iterdir()]
        except OSError:
            return False
        if segment in entries:
            current = current / segment
            continue
        prefix_matches = [e for e in entries if e.startswith(segment)]
        if len(prefix_matches) != 1:
            return False
        current = current / prefix_matches[0]
    return True


def _reclassify(cls: str, matched: str) -> str:
    if cls != "high-entropy-base64" or matched.count("/") < _PATH_SLASH_THRESHOLD:
        return cls
    segments = matched.split("/")
    if _record_url_shape(segments) or _resolves_to_repo_path(segments):
        return KNOWN_SAFE_CLASS
    return cls


def is_credential_class(cls: str) -> bool:
    return cls != KNOWN_SAFE_CLASS


def scan_text(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for cls, pattern in _PATTERNS:
            for m in pattern.finditer(line):
                findings.append(Finding(lineno, _reclassify(cls, m.group(0)), m.group(0)))
    return findings


def scan_file(path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ScanError(f"cannot read {path}: {exc.strerror or exc}") from exc
    return scan_text(text)


def _skip_dir(name: str) -> bool:
    return name.startswith("__pycache__") or name == ".git"


def _iter_files(tree: Path):
    if tree.is_file():
        yield tree
        return

    def _onerror(exc: OSError) -> None:
        raise ScanError(f"cannot read {exc.filename}: {exc.strerror or exc}") from exc

    for dirpath, dirnames, filenames in os.walk(tree, onerror=_onerror):
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
        for name in sorted(filenames):
            yield Path(dirpath) / name


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
    known_safe_total = 0
    try:
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
                    else:
                        known_safe_total += 1
    except ScanError as exc:
        _err(str(exc))
        return 2
    if total:
        _err(f"{total} credential finding(s) — commit blocked")
        return 1
    _err(f"{known_safe_total} known-safe match(es), 0 credential findings — clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
