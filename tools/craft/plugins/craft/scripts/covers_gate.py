#!/usr/bin/env python3
"""Covers gate — certifies a drafted `--covers` and/or `--partial-covers`
identifier list against a spec's declared acceptance criteria, BEFORE a
slice parent record is written.

Usage:
    lore record show spec/<name> | covers_gate.py --covers "AC2, AC5"
    lore record show spec/<name> | covers_gate.py --partial-covers "AC7"
    lore record show spec/<name> | covers_gate.py --covers "AC2" --partial-covers "AC5"

The spec body arrives on stdin; the drafted identifier list(s) are `--covers`
and/or `--partial-covers` arguments composed by the caller from identifiers
alone, never from vault prose. At least one of the two flags is required — a
gate invoked with neither fails closed rather than certifying an empty
claim. Both flags share the same grammar and go through the same
`parse_covers`, so they can never disagree about what a valid identifier
list looks like. An identifier named in both lists is rejected: a slice
cannot coherently claim it both fully and partially delivers the same
criterion. The gate reads no parent record and writes no temp file — it
runs while the parent body is still a string in hand.

The `--covers` / `--partial-covers` grammar is exact and identifier-only:
one or more `ACn` tokens, comma-separated, nothing else. This is what
discharges "copies no criterion prose" — prose cannot enter a field whose
grammar admits only identifiers — so no similarity heuristic appears
anywhere in this gate.

One parser, `parse_criteria`, turns a spec body into its ordered list of
criterion identifiers: top-level `- ` bullets under a `## Acceptance
Criteria` heading, each prefixed `**ACn.**`. A `###` sub-heading groups
criteria without being one; a nested sub-bullet qualifies its parent rather
than forming its own criterion. The heading is anchored at line start (case
-insensitive — `## Acceptance criteria` satisfies it too), so an inline
mid-paragraph mention of `## Acceptance Criteria` does not satisfy it. The
parser is fence-aware: a fenced code block (``` or ~~~, with or without an
info string) is invisible to both the heading search and the criterion
scan, so a worked example anywhere in the spec body can neither forge a
heading anchor nor contribute a fabricated criterion. It is equally
comment-aware: a CommonMark HTML comment (`<!-- ... -->`) is invisible the
same way, to end of document if never closed — a comment renders as
nothing at all in every rendered view of the document, so structure hidden
inside one is structure a human reviewer never sees, and it must stay
invisible to the parser too. The heading search also rejects a second
unmasked occurrence of the heading it anchors on: a first-match-wins scan
would silently certify against whichever occurrence came first, so a
visible duplicate heading is a fail-closed error rather than a silent
substitution.

Lines are split on the CommonMark line grammar only — `\r\n`, `\r`, `\n` —
never on Python's broader `str.splitlines()` set (U+2028, U+2029, NEL, `\v`,
`\f`, ...). None of those extra characters render as a line break to a human
or a CommonMark reader, so a heading or bullet hidden behind one inside an
ordinary prose paragraph is ordinary line content here too, not structure.

Exit codes:
    0  clean   — every identifier in every list given names a real spec
       criterion, both lists' grammar is valid, and the two lists share no
       identifier.
    1  violation — bad grammar in either list, an unknown identifier in
       either list, a duplicate identifier within a list, or an identifier
       claimed in both `--covers` and `--partial-covers` (prints a
       `reason:` line to stderr).
    2  error   — fail-closed: neither `--covers` nor `--partial-covers`
       given (`reason-code: no-coverage-list-given` — the caller's own
       invocation is what needs fixing here, not the spec), empty or
       non-UTF-8 stdin, no `## Acceptance Criteria` heading
       in the spec body, a spec declaring zero criterion identifiers under
       that heading (the legacy shape, distinct reason line plus a stable
       `reason-code: zero-criterion-identifiers` line unique to this path —
       the caller's machine-readable carve-out discriminator), or a second
       unmasked `## Acceptance Criteria` heading
       (`reason-code: duplicate-acceptance-criteria-heading` — a
       first-match-wins scan would silently certify against whichever
       occurrence it saw first). NEVER exits 0 when it could not actually
       certify the drafted list(s).
"""

from __future__ import annotations

import argparse
import re
import sys

_AC_HEADING = "## Acceptance Criteria"
_AC_HEADING_RE = re.compile(r"^## Acceptance Criteria$", re.IGNORECASE)
_CRITERION_RE = re.compile(r"^-\s+\*\*(AC\d+)\.\*\*")
_COVERS_RE = re.compile(r"\AAC\d+(,\ ?AC\d+)*\Z")
_FENCE_START_RE = re.compile(r"^(`{3,}|~{3,})")
_HTML_COMMENT_START_RE = re.compile(r"^<!--")
_ZERO_CRITERIA_REASON_CODE = "zero-criterion-identifiers"
_DUPLICATE_AC_HEADING_REASON_CODE = "duplicate-acceptance-criteria-heading"
_NO_COVERAGE_LIST_GIVEN_REASON_CODE = "no-coverage-list-given"
# CommonMark line endings only — \r\n, \r, \n — and nothing else. Python's
# str.splitlines() additionally breaks on \v, \f, \x1c-\x1e, NEL (\x85),
# U+2028 LINE SEPARATOR, and U+2029 PARAGRAPH SEPARATOR: none of those render
# as a line break in any CommonMark viewer, so a line split on the broader
# set can be tricked into anchoring a heading or bullet that is invisible to
# a human reader of the same spec body.
_COMMONMARK_LINE_RE = re.compile(r"\r\n|\r|\n")


def _err(msg: str) -> None:
    print(f"covers-gate: {msg}", file=sys.stderr)


def _mask_fenced_lines(lines: list[str]) -> list[bool]:
    """Return one boolean per line — True where the line is invisible to a
    human reader of the rendered document: a fence marker or a line inside a
    fenced code block (``` or ~~~, any info string), or an HTML comment
    marker or a line inside a CommonMark HTML comment (`<!-- ... -->`). Both
    gates that scan a spec body for structure share this one masker so they
    can never disagree about what counts as document structure.

    A fence closes only on a same-character marker at least as long as the
    one that opened it, per CommonMark. A comment closes on the first line
    containing `-->` at or after the line that opened it (same line included)
    — an unclosed comment masks every remaining line to end of document,
    exactly as a CommonMark renderer drops it. While inside either a fence or
    a comment, the other's start marker is not recognized — a fenced code
    block eats a literal `<!--` as ordinary code text, and an HTML comment
    eats a literal ``` as ordinary comment text, matching how a CommonMark
    reader parses each in isolation.
    """
    masked = [False] * len(lines)
    fence_char: str | None = None
    fence_len = 0
    in_comment = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if in_comment:
            masked[i] = True
            if "-->" in line:
                in_comment = False
            continue
        if fence_char is None:
            if _HTML_COMMENT_START_RE.match(stripped):
                masked[i] = True
                if "-->" not in stripped:
                    in_comment = True
                continue
            m = _FENCE_START_RE.match(stripped)
            if m:
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                masked[i] = True
            continue
        masked[i] = True
        m = _FENCE_START_RE.match(stripped)
        if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len:
            fence_char = None
    return masked


class DuplicateHeadingError(ValueError):
    """A second unmasked occurrence of a heading a gate anchors on was
    found — the section it names is not uniquely determined, so certifying
    against the first occurrence found would silently trust whichever one a
    first-match-wins scan happened to see first."""

    def __init__(self, heading: str, reason_code: str):
        self.reason_code = reason_code
        super().__init__(
            f"a second unmasked {heading!r} heading was found — the section "
            "it names is not uniquely determined"
        )


def _find_unique_heading(
    lines: list[str],
    masked: list[bool],
    heading_re: re.Pattern[str],
    heading: str,
    reason_code: str,
) -> int | None:
    """Return the index of the line following the sole unmasked occurrence
    of `heading_re`, or None if it does not occur at all. Raises
    DuplicateHeadingError if a second unmasked occurrence exists anywhere in
    the document, even one that comes after content already scanned under
    the first."""
    start: int | None = None
    for i, line in enumerate(lines):
        if masked[i]:
            continue
        if heading_re.match(line):
            if start is not None:
                raise DuplicateHeadingError(heading, reason_code)
            start = i + 1
    return start


def _iter_criterion_entries(spec_body: str):
    """Yield `(identifier_or_None, bullet_text)` for every top-level `- `
    bullet under the spec's `## Acceptance Criteria` heading. This is the one
    walk: `parse_criteria` and `parse_criteria_with_text` both call it rather
    than each re-deriving the walk from the masking primitives, so the two
    can never disagree about what a criterion is. Raises ValueError if the
    heading is not present at line start, or DuplicateHeadingError if a
    second unmasked occurrence exists.

    A `###` sub-heading is skipped as a grouping marker, and an indented
    sub-bullet is skipped as a criterion of its own — it qualifies its
    parent criterion instead, so it never yields its own identifier. The
    heading match is case-insensitive but still anchored at line start.
    Every fenced code block and every HTML comment is invisible to both the
    heading search and the bullet scan, so a worked example or a
    commented-out draft cannot forge a heading anchor or contribute a
    fabricated criterion.

    `bullet_text` joins the bullet's own line with every indented line that
    follows it — wrapped continuation prose and any nested `- ` sub-bullet
    (plus that sub-bullet's own wrapped continuation), since a sub-bullet
    qualifies its parent and its text belongs to the criterion it qualifies.
    The block ends at the next unmasked top-level (unindented) `- ` bullet, a
    `##`/`###` heading, or a blank line. A bullet with no `**ACn.**` prefix
    yields identifier `None` rather than being silently dropped —
    `parse_criteria` is the one that drops it; this walk does not.
    """
    lines = _COMMONMARK_LINE_RE.split(spec_body)
    masked = _mask_fenced_lines(lines)

    start = _find_unique_heading(
        lines, masked, _AC_HEADING_RE, _AC_HEADING, _DUPLICATE_AC_HEADING_REASON_CODE
    )
    if start is None:
        raise ValueError(f"spec body has no {_AC_HEADING!r} heading")

    n = len(lines)
    i = start
    while i < n:
        if masked[i]:
            i += 1
            continue
        line = lines[i]
        if line.startswith("## "):
            break
        if not line.startswith("- "):
            i += 1
            continue
        block = [line]
        j = i + 1
        while j < n:
            if masked[j]:
                j += 1
                continue
            nxt = lines[j]
            if nxt.startswith("## ") or nxt.startswith("### ") or nxt.startswith("- "):
                break
            stripped = nxt.strip()
            if stripped == "":
                break
            if nxt[:1] in (" ", "\t"):
                # An indented line here is either a wrapped continuation of
                # the bullet's own prose or a nested sub-bullet (and that
                # sub-bullet's own wrapped continuation) — both qualify the
                # parent criterion rather than forming a criterion of their
                # own, so both fold into this block's text.
                block.append(nxt)
                j += 1
                continue
            break
        m = _CRITERION_RE.match(line)
        identifier = m.group(1) if m else None
        yield identifier, "\n".join(block)
        i = j


def parse_criteria(spec_body: str) -> list[str]:
    """Return the ordered criterion identifiers declared under the spec's
    top-level `## Acceptance Criteria` heading. Raises ValueError if that
    heading is not present at line start.

    This is the one parser: a `###` sub-heading is skipped as a grouping
    marker, and an indented sub-bullet is skipped as a qualifier of its
    parent criterion rather than a criterion of its own. The heading match
    is case-insensitive but still anchored at line start. Every fenced code
    block and every HTML comment is invisible to both the heading search
    and the criterion scan, so a worked example or a commented-out draft
    cannot forge a heading anchor or contribute a fabricated criterion. A
    second unmasked occurrence of the heading raises DuplicateHeadingError
    rather than silently anchoring on the first.
    """
    return [
        identifier
        for identifier, _text in _iter_criterion_entries(spec_body)
        if identifier is not None
    ]


def parse_criteria_with_text(spec_body: str) -> list[tuple[str | None, str]]:
    """Sibling accessor to `parse_criteria`, sharing its exact walk via
    `_iter_criterion_entries`. Returns every top-level bullet under the
    criteria heading as `(identifier_or_None, bullet_text)`, in document
    order — including an unprefixed bullet (`identifier` is `None`), which
    `parse_criteria` discards. `bullet_text` is the bullet's own line plus
    every indented line that follows it, including a nested sub-bullet's
    text — a sub-bullet qualifies its parent criterion, so its text belongs
    to the criterion it qualifies. Raises the same errors as `parse_criteria`.
    """
    return list(_iter_criterion_entries(spec_body))


def parse_covers(value: str, flag: str = "--covers") -> list[str]:
    """Parse the `--covers` grammar: one or more `ACn` tokens separated by
    `,` or `, `, nothing else. Raises ValueError naming the reason on any
    violation, including a duplicate identifier. `flag` names the
    command-line flag `value` was drafted for in the raised message — it
    defaults to `--covers`, but a caller parsing a `--partial-covers` value
    (or a ledger token derived from neither flag) passes its own name so the
    violation text names the site that actually produced the value, not
    whichever flag this function happens to default to."""
    if not value:
        raise ValueError(f"{flag} value is empty")
    if not _COVERS_RE.match(value):
        raise ValueError(f"{flag} value is not a valid identifier list: {value!r}")
    identifiers = [t.lstrip(" ") for t in value.split(",")]
    seen: set[str] = set()
    for identifier in identifiers:
        if identifier in seen:
            raise ValueError(f"{flag} value repeats identifier {identifier!r}")
        seen.add(identifier)
    return identifiers


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Certify a drafted --covers and/or --partial-covers identifier "
            "list against a spec's acceptance criteria."
        )
    )
    ap.add_argument("--covers", help="comma-separated ACn identifiers fully covered, e.g. 'AC2, AC5'")
    ap.add_argument(
        "--partial-covers",
        help="comma-separated ACn identifiers partially covered, e.g. 'AC2, AC5'",
    )
    args = ap.parse_args(argv)

    if args.covers is None and args.partial_covers is None:
        _err("reason: at least one of --covers or --partial-covers is required")
        _err(f"reason-code: {_NO_COVERAGE_LIST_GIVEN_REASON_CODE}")
        _err("failing closed — cannot certify an empty claim")
        return 2

    try:
        spec_body = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError as e:
        _err(f"spec body on stdin is not valid UTF-8: {e}")
        return 2
    if not spec_body.strip():
        _err("spec body on stdin is empty")
        return 2

    try:
        criteria = parse_criteria(spec_body)
    except DuplicateHeadingError as e:
        _err(f"reason: {e}")
        _err(f"reason-code: {e.reason_code}")
        return 2
    except ValueError as e:
        _err(str(e))
        _err("failing closed — cannot certify membership without a criteria list")
        return 2

    if not criteria:
        _err(
            "reason: spec declares no criterion identifiers under "
            f"{_AC_HEADING!r} — this is the legacy shape a spec predating the "
            "**ACn.** convention carries"
        )
        _err(f"reason-code: {_ZERO_CRITERIA_REASON_CODE}")
        return 2

    # Both drafted lists go through one grammar pass, then one membership
    # pass, in that order — a flag omitted contributes an empty list rather
    # than a separate code path, so the two flags cannot drift apart in what
    # they accept or in how they report a rejection.
    drafted: list[list[str]] = []
    for value, flag in ((args.covers, "--covers"), (args.partial_covers, "--partial-covers")):
        if value is None:
            drafted.append([])
            continue
        try:
            drafted.append(parse_covers(value, flag))
        except ValueError as e:
            _err(f"reason: {e}")
            return 1
    covers, partial_covers = drafted

    # Both lists are checked before reporting, not short-circuited on the
    # first, so a caller supplying both flags sees every unknown identifier
    # in one pass instead of a second round trip per flag.
    unknown_by_flag = []
    for identifiers, flag in ((covers, "--covers"), (partial_covers, "--partial-covers")):
        unknown = [c for c in identifiers if c not in criteria]
        if unknown:
            unknown_by_flag.append(f"{flag} names unknown identifier(s): {', '.join(unknown)}")
    if unknown_by_flag:
        _err(f"reason: {'; '.join(unknown_by_flag)}")
        return 1

    overlap = [c for c in covers if c in partial_covers]
    if overlap:
        _err(
            "reason: identifier(s) claimed both fully and partially "
            f"covered: {', '.join(overlap)}"
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
