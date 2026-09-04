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
       (`reason-code: empty-stdin`), non-UTF-8 stdin
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


def _scrub_credential_shaped(text: str) -> str:
    for pattern in _CREDENTIAL_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


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
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _err(msg: str) -> None:
    print(f"criterion-gate: {msg}", file=sys.stderr)


def main(argv: list[str]) -> int:
    del argv  # no flags — the whole interface is stdin

    try:
        spec_body = sys.stdin.buffer.read().decode("utf-8")
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
        # bypassed by adding a message.
        _err(f"reason: {_scrub_credential_shaped('; '.join(violations))}")
        return 1

    for identifier, method in certified:
        print(f"{identifier}: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
