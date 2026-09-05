#!/usr/bin/env python3
"""Criterion content gate — certifies that every criterion under a spec's
`## Acceptance Criteria` heading names no implementation identifier (AC3)
and declares exactly one sanctioned verification method (AC4), BEFORE the
spec advances past the gauntlet.

Usage:
    lore record show spec/<name> | criterion_gate.py

The spec body arrives on stdin; there is no flag — this gate derives its
answer from the document alone, the same convention `candidate_set.py` uses.

The criterion walk is `covers_gate.parse_criteria_with_text` — the sibling
accessor to `parse_criteria`, sharing its exact heading anchor, its fence-
and-HTML-comment masking, and its `###`-skip / nested-sub-bullet-skip rules.
This gate never re-derives that walk from the masking primitives; two gates
reading the same document must not be able to disagree about what a
criterion is. The sanctioned method vocabulary is
`observation_gate._SANCTIONED_METHODS`, imported rather than redeclared —
two gates naming the same three methods must not be able to disagree about
what they are.

AC3 classification is inline-code-span-scoped, allow-list first, then
refuse-list, and anything matching neither is allowed (precision over
recall — a path written without backticks is a false negative this gate
accepts):

    Allowed (product surface): slash-commands, CLI flags, CLI subcommands,
    record field labels (`**Covers:**`), section headings (`## Slices`).

    Refused (code location): a path carrying a file extension, a
    `path:line` reference, call syntax `name(...)`, an HTTP verb followed by
    a path, and a symbol or attribute-access name — snake_case, CamelCase
    (genuine case alternation only — an all-caps token like `DELETE`
    certifies), dotted, `::`-joined, or subscripted.

AC4's trailer (`*Verified by: <method>.*`) is normalized — whitespace
collapsed to hyphens — before comparison against the shared vocabulary, so
`automated assertion` and `automated-assertion` both certify. A trailer
naming zero or more than one method, or an unsanctioned token, refuses.

Every failing criterion is named in one pass — never fail-fast on the
first — and an offending span that looks credential-shaped (the same
pattern family `_shared/execute.md`'s Phase 5 credential-pattern scrub
guards) is redacted before it is echoed into the refusal text.

Exit codes:
    0  certified — every criterion carries no refused span and exactly one
       sanctioned method; stdout prints one `<identifier>: <method>` line
       per criterion.
    1  integrity violation — a refused code-location span, a missing or
       malformed verification trailer, or (in a section that declares at
       least one identifier) a bullet carrying none — prints a `reason:`
       line naming every fault found, not just the first.
    2  could not certify — fail-closed: empty stdin
       (`reason-code: empty-stdin`), stdin over the 256 KiB size cap
       (`reason-code: stdin-too-large` — never a silent truncation that
       certifies a partial document), non-UTF-8 stdin
       (`reason-code: non-utf8-stdin`), no `## Acceptance Criteria` heading,
       a spec declaring zero criterion identifiers under that heading — the
       legacy carve-out (`reason-code: zero-criterion-identifiers`), a
       second unmasked heading
       (`reason-code: duplicate-acceptance-criteria-heading` — inherited
       from the shared parser), or the document ending inside an open
       fenced code block or an open HTML comment
       (`reason-code: unterminated-masked-region`). NEVER exits 0 when it
       could not actually certify.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import covers_gate  # noqa: E402
from observation_gate import (  # noqa: E402
    _SANCTIONED_METHODS,
    _ends_inside_masked_region,
    _is_meaningfully_nonempty,
)

_AC_HEADING = "## Acceptance Criteria"
_ZERO_CRITERIA_REASON_CODE = "zero-criterion-identifiers"
_UNTERMINATED_MASKED_REGION_REASON_CODE = "unterminated-masked-region"
_EMPTY_STDIN_REASON_CODE = "empty-stdin"
_NON_UTF8_STDIN_REASON_CODE = "non-utf8-stdin"
_STDIN_TOO_LARGE_REASON_CODE = "stdin-too-large"

# Fail-closed backstop against a pathologically large vault record, ahead of
# an eight-agent dispatch that runs this gate first. A real spec body is
# kilobytes; 256 KiB is generous headroom over that while still bounding the
# work this gate (and the regex walk over it) will do on a single stdin read.
_MAX_STDIN_BYTES = 256 * 1024

# ---------------------------------------------------------------------------
# AC3 — inline code span classification
# ---------------------------------------------------------------------------

_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")

_ALLOW_SPAN_PATTERNS = [
    re.compile(r"^/[A-Za-z][\w:-]*$"),  # slash-command, e.g. /craft:slice
    re.compile(r"^--?[A-Za-z][\w-]*$"),  # CLI flag, e.g. --related, -v
    re.compile(r"^[a-z][a-z0-9]*(?:\s+[a-z][a-z0-9]*)+$"),  # CLI subcommand, e.g. lore areas
    re.compile(r"^\*\*[^*]+:\*\*$"),  # record field label, e.g. **Covers:**
    re.compile(r"^#{1,6}\s+\S.*$"),  # section heading, e.g. ## Slices
]

_REFUSE_SPAN_PATTERNS = [
    # path with extension — the 1-5-character extension must contain a
    # letter, so a purely numeric dotted literal (`2.0`, `v2.1`, `1.2.3`) is
    # not classified as a file path; a real extension (`.py`, `.md`, `.sql`,
    # `.ts`) always carries one.
    re.compile(r"^[\w./-]*[\w-]\.(?=[A-Za-z0-9]{1,5}$)[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*$"),
    re.compile(r"^[\w./-]+:\d+(?:-\d+)?$"),  # path:line or path:line-line
    re.compile(r"^[A-Za-z_][\w.]*\([^)]*\)$"),  # call syntax
    re.compile(r"^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/\S*$"),  # HTTP verb + path
    re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$"),  # snake_case
    # CamelCase — genuine case alternation required (at least one
    # upper-to-lower AND one lower-to-upper transition), per U1's binding
    # correction: an all-caps closed-vocabulary token (WHERE, KEEP, DELETE)
    # must certify, not refuse.
    re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+$"),
    re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$"),  # dotted
    re.compile(r"^[A-Za-z_]\w*(?:::[A-Za-z_]\w*)+$"),  # ::-joined
    re.compile(r"^[A-Za-z_]\w*\[[^\]]*\]$"),  # subscripted
]


def _classify_span(span: str) -> str | None:
    """Return "allow", "refuse", or None (matches neither — allowed by the
    precision-first default). Allow-list is checked first."""
    for pattern in _ALLOW_SPAN_PATTERNS:
        if pattern.match(span):
            return "allow"
    for pattern in _REFUSE_SPAN_PATTERNS:
        if pattern.match(span):
            return "refuse"
    return None


def _offending_spans(text: str) -> list[str]:
    return [s for s in _CODE_SPAN_RE.findall(text) if _classify_span(s) == "refuse"]


# ---------------------------------------------------------------------------
# Credential scrub — the same pattern family `_shared/execute.md`'s Phase 5
# credential-pattern scrub guards, applied here to any span text this gate
# is about to echo into a refusal. Reimplemented rather than imported: no
# importable Python module carries that prose regex list, and this gate is
# code, not the skill prose the scrub's "run it by reference" rule targets.
# ---------------------------------------------------------------------------

_CREDENTIAL_PATTERNS = [
    re.compile(r"(?i)(secret|token|passwd|password|api[_-]?key)[A-Za-z0-9_-]*\s*[=:]\s*\S+"),
    re.compile(
        r"(?i)\b(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|"
        r"glpat-[A-Za-z0-9_-]{20}|xox[baprs]-[A-Za-z0-9-]+|sk_live_[A-Za-z0-9]+|"
        r"AIza[0-9A-Za-z_-]{35})\b"
    ),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)api[_-]?key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{16,}"),
    re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b"),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

# A raw control byte this gate might scrub before neutralization runs (there
# is no such call site today, but nothing prevents one), or — the actual
# vulnerable case — the visible escaped form `_neutralize_control_chars` below
# produces when it runs first, as every current call site does. Both forms
# split a fixed-format pattern's match exactly as the raw byte would, for a
# pattern family whose character classes exclude `\`, `x`/`u`/`U`, and hex
# digits (AKIA, base64-shaped, hex-shaped, PEM headers). Collapsing both forms
# away before deciding whether a credential-shaped match exists closes that
# gap without changing the character range this module neutralizes. Each
# alternative is a fixed-width match (`\xHH`, `\uHHHH`, `\UHHHHHHHH` — the
# same three escape widths Python's own string literals use, keyed off the
# codepoint's own magnitude in `_neutralize_control_chars` below) so a literal
# hex digit immediately following a genuine escape is never swallowed into it.
_ESCAPED_CONTROL_CHAR_RE = re.compile(
    r"\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8}"
)


def _apply_credential_patterns(text: str) -> str:
    for pattern in _CREDENTIAL_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def _scrub_credential_shaped(text: str) -> str:
    """Redact every credential-shaped span in `text`. The match *decision* is
    made against a collapsed view with every raw control byte and every
    already-escaped `\\xNN` sequence removed — never against `text` itself —
    so a control byte (raw or pre-neutralized) cannot split a fixed-format
    secret's match and dodge the scrub by hiding the split from it. Only when
    that collapsed view actually contains a credential-shaped match does this
    return the collapsed, scrubbed result in place of `text`; otherwise `text`
    is scrubbed and returned unchanged in shape, exactly as before."""
    collapsed = _ESCAPED_CONTROL_CHAR_RE.sub("", _CONTROL_CHAR_RE.sub("", text))
    if collapsed != text:
        scrubbed_collapsed = _apply_credential_patterns(collapsed)
        if scrubbed_collapsed != collapsed:
            return scrubbed_collapsed
    return _apply_credential_patterns(text)


# ---------------------------------------------------------------------------
# Control-character neutralization — a separate concern from the credential
# scrub above, applied at the same single output-join site. Several refuse
# patterns (call syntax, HTTP verb + path, subscript) place no character
# restriction on part of their match, so a vault-sourced span carrying a raw
# ESC byte (or any other non-printable code point) is legal input. Echoing it
# unneutralized is terminal-escape injection into a human's terminal and, per
# the gauntlet skill's contract to re-quote a refused span verbatim,
# prompt injection into the operator agent reading this gate's own output.
# Every non-printable code point (C0 controls except tab/newline/CR, and the
# C1 range) is escaped to a visible `\xNN` form — legible as evidence, inert
# as a terminal control sequence. Unicode category Zl/Zp (U+2028 LINE
# SEPARATOR, U+2029 PARAGRAPH SEPARATOR — invisible to the CommonMark line
# grammar this gate parses with, but treated as line breaks by a caller's
# `str.splitlines()`, which is how a hostile span forges extra lines into
# this gate's own output) and category Cf (bidi/format controls — U+202E
# RIGHT-TO-LEFT OVERRIDE and U+202C POP DIRECTIONAL FORMATTING chief among
# them, the Trojan-Source visual-spoofing pair) are neutralized the same way.
# The escape width is keyed off the codepoint's own magnitude — `\xHH` (C0/C1,
# unchanged from before), `\uHHHH` (the rest of the Basic Multilingual Plane,
# where every Zl/Zp/Cf codepoint below U+10000 lives), `\UHHHHHHHH` (beyond
# it) — the same three widths Python's own string literals use, so a literal
# hex digit immediately following a genuine escape is never ambiguous with
# one more digit of it.
# ---------------------------------------------------------------------------

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_NEUTRALIZE_UNICODE_CATEGORIES = frozenset({"Cf", "Zl", "Zp"})


def _escape_one(ch: str) -> str:
    codepoint = ord(ch)
    if codepoint <= 0xFF:
        return f"\\x{codepoint:02x}"
    if codepoint <= 0xFFFF:
        return f"\\u{codepoint:04x}"
    return f"\\U{codepoint:08x}"


def _neutralize_control_chars(text: str) -> str:
    return "".join(
        _escape_one(ch)
        if _CONTROL_CHAR_RE.match(ch) or unicodedata.category(ch) in _NEUTRALIZE_UNICODE_CATEGORIES
        else ch
        for ch in text
    )


# ---------------------------------------------------------------------------
# AC4 — verification trailer
# ---------------------------------------------------------------------------

_TRAILER_RE = re.compile(r"\*\s*Verified\s+by:\s*([^*]+?)\.?\s*\*", re.IGNORECASE)
_METHOD_SPLIT_RE = re.compile(r"\s*,\s*|\s+and\s+", re.IGNORECASE)


def _normalize_method(token: str) -> str:
    return re.sub(r"\s+", "-", token.strip().lower())


def _check_trailer(text: str) -> tuple[str | None, str | None]:
    """Return (problem_message_or_None, normalized_method_or_None)."""
    matches = list(_TRAILER_RE.finditer(text))
    # A second unmasked trailer anywhere in the bullet is refused before a
    # single trailer's own token count is even considered — the same
    # find-every-occurrence discipline `observation_gate.find_covers_field`
    # applies to a duplicate `**Covers:**` line, so "exactly one" holds
    # across trailers, not just within the first one found.
    if len(matches) > 1:
        return (
            f"criterion carries {len(matches)} verification trailers; "
            "exactly one is required"
        ), None
    m = matches[0] if matches else None
    if not m or not _is_meaningfully_nonempty(m.group(1)):
        return "carries no verification trailer naming a sanctioned method", None

    # A wrapped trailer (`*Verified by: automated\n  assertion.*`) captures
    # its embedded newline and indentation raw — flatten internal
    # whitespace runs (including the newline) to a single space before
    # tokenizing, so a trailer split across a line wrap is not misread as
    # naming two methods.
    raw = " ".join(m.group(1).split())
    tokens = [t for t in _METHOD_SPLIT_RE.split(raw) if t.strip()]
    if len(tokens) != 1:
        return (
            "verification trailer must name exactly one sanctioned method, "
            f"found {len(tokens)}: {raw.strip()!r}"
        ), None

    normalized = _normalize_method(tokens[0])
    if normalized not in _SANCTIONED_METHODS:
        return (
            f"verification trailer names unsanctioned method {normalized!r} — "
            f"sanctioned methods are: {', '.join(sorted(_SANCTIONED_METHODS))}"
        ), None

    return None, normalized


# ---------------------------------------------------------------------------


def _snippet(text: str, limit: int = 48) -> str:
    # Scrub before truncating: a length-anchored credential pattern
    # (`ghp_[A-Za-z0-9]{36}`, `{32,}`, `{40,}`, …) can straddle the
    # truncation boundary, leaving only a short non-matching prefix inside
    # the cut — a scrub that runs afterward, on the already-truncated text,
    # never sees the whole token and cannot redact it.
    flat = _scrub_credential_shaped(" ".join(text.split()))
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _err(msg: str) -> None:
    print(f"criterion-gate: {msg}", file=sys.stderr)


def main(argv: list[str]) -> int:
    del argv  # no flags — the whole interface is stdin

    raw = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
    if len(raw) > _MAX_STDIN_BYTES:
        _err(
            f"reason: spec body on stdin exceeds the {_MAX_STDIN_BYTES}-byte "
            "cap — refusing rather than certifying a truncated document"
        )
        _err(f"reason-code: {_STDIN_TOO_LARGE_REASON_CODE}")
        return 2

    try:
        spec_body = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        _err(f"reason: spec body on stdin is not valid UTF-8: {e}")
        _err(f"reason-code: {_NON_UTF8_STDIN_REASON_CODE}")
        return 2
    if not spec_body.strip():
        _err("reason: spec body on stdin is empty")
        _err(f"reason-code: {_EMPTY_STDIN_REASON_CODE}")
        return 2

    lines = covers_gate._COMMONMARK_LINE_RE.split(spec_body)
    if _ends_inside_masked_region(lines):
        _err(
            "reason: the document ends while still inside an open fenced code "
            "block or an open HTML comment — cannot certify what a masked "
            "criterion might carry"
        )
        _err(f"reason-code: {_UNTERMINATED_MASKED_REGION_REASON_CODE}")
        return 2

    try:
        entries = covers_gate.parse_criteria_with_text(spec_body)
    except covers_gate.DuplicateHeadingError as e:
        _err(f"reason: {e}")
        _err(f"reason-code: {e.reason_code}")
        return 2
    except ValueError as e:
        _err(f"reason: {e}")
        return 2

    identified = [identifier for identifier, _text in entries if identifier is not None]
    if not identified:
        _err(
            f"reason: spec declares no criterion identifiers under {_AC_HEADING!r} "
            "— this is the legacy shape a spec predating the **ACn.** convention "
            "carries"
        )
        _err(f"reason-code: {_ZERO_CRITERIA_REASON_CODE}")
        return 2

    violations: list[str] = []
    certified: list[tuple[str, str]] = []
    for identifier, text in entries:
        if identifier is None:
            violations.append(
                f"bullet with no **ACn.** identifier in a section that declares "
                f"identifiers: {_snippet(text)!r}"
            )
            continue

        offending = _offending_spans(text)
        if offending:
            violations.append(
                f"{identifier} carries implementation-identifier span(s): "
                f"{', '.join(offending)}"
            )

        trailer_problem, method = _check_trailer(text)
        if trailer_problem:
            violations.append(f"{identifier} {trailer_problem}")
        elif not offending:
            certified.append((identifier, method))  # type: ignore[arg-type]

    if violations:
        # Every violation message is built from vault-sourced spec text —
        # an offending span, a raw trailer, a normalized method token, or an
        # unidentified bullet's snippet — so the scrub is applied once, here,
        # at the single point refusal text reaches output. Three separate
        # sites each scrubbing their own fragment is how a fourth message
        # reintroduces the leak; one scrub at the output boundary cannot be
        # bypassed by adding a message. Control-character neutralization
        # runs first, over the raw text, so a vault author cannot use a
        # control byte to split a credential pattern's match and dodge the
        # scrub that follows.
        joined = _neutralize_control_chars("; ".join(violations))
        _err(f"reason: {_scrub_credential_shaped(joined)}")
        return 1

    for identifier, method in certified:
        print(f"{identifier}: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
