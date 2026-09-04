#!/usr/bin/env python3
"""Observation gate — certifies that a slice parent's `**Covers:**` claim is
backed by a recorded observation, BEFORE Phase 6 lets the parent close.

Usage:
    lore record show task/<parent-name> | observation_gate.py

The parent task body arrives on stdin; there is no flag — this gate derives
its answer from the document alone, the same convention `candidate_set.py`
uses rather than `covers_gate.py`'s drafted-value flag.

The `**Covers:**` line, the CommonMark line splitter, and the fenced-block-
and-HTML-comment masker are all imported from the sibling `covers_gate.py` —
never re-derived here, so this gate and its siblings cannot disagree about
what a criterion identifier list looks like or about what counts as document
structure. Lines are split on the CommonMark line grammar only (`\\r\\n`,
`\\r`, `\\n` — never Python's broader `str.splitlines()`); fenced code
blocks and CommonMark HTML comments are invisible to every heading search
and line scan below, exactly as they are for the sibling gates; and a second
unmasked `## Criterion observations` heading is a fail-closed integrity
error rather than a silent substitution of one section for another.

Grammar this gate reads (fixed by the parent plan, not to be re-invented):

    ## Criterion observations

    - **AC9** — automated-assertion — <evidence pointer>
    - **AC3** — manual-check — <what was checked, and by whom>
    - **AC7** — design-doc-review — <evidence pointer>

Sanctioned method tokens are exactly `automated-assertion`,
`design-doc-review`, and `manual-check`. Evidence text is required and never
empty — where "empty" means carrying no character beyond whitespace and
Unicode format/control code points (category Cf/Cc), not merely
`str.strip()` producing an empty string, so an invisible-but-nonempty
payload (e.g. a lone U+200B ZERO WIDTH SPACE) does not satisfy the bar. Each
observation line is `- **ACn** — <method> — <evidence>`: the
identifier and the method are split off the first two `—` (em dash)
separators; everything after the second separator is evidence verbatim,
including any further `—` characters it contains, so a design-doc-review
pointer like `<doc path>#State — <name>` is not truncated at its own
internal dash.

A section's boundary is closed only by the next unmasked `## ` heading, not
by a `### ` sub-heading nested inside it — a documented, deliberate choice
consistent with the sibling gates, not an accident — so an observation bullet
grouped under a sub-heading inside `## Criterion observations` still counts.

A `manual-check` observation additionally requires a matching
`## Operator attestations` line naming the same criterion:

    ## Operator attestations

    - **AC3** — <the operator's own words, recorded at close>

An unattended run must not be able to discharge a manual check by writing a
sentence to itself — the same criterion must have both lines, or the
manual-check observation is refused. The attestation text is held to the
same non-empty bar as evidence text above (whitespace and Cf/Cc code points
alone do not count). The other two methods need no attestation line and are
unaffected by its absence.

The evidence text (and the attestation text) is inert: pattern-matched and
printed only, never opened as a path and never passed to a subprocess. A
vault-authored string is untrusted input; dereferencing it would turn this
gate into a path-traversal and command-injection sink.

Exit codes:
    0  derived — every identifier `**Covers:**` names carries a well-formed
       observation (and every manual-check observation among them has a
       matching attestation), or there is no `**Covers:**` field at all (the
       legacy and enabler shapes close exactly as they do today).
    1  integrity violation — a covered identifier with no observation, a
       duplicated observation, an observation for an identifier `**Covers:**`
       does not declare, an unsanctioned method token, empty evidence text,
       a duplicated identifier within `**Covers:**`, or a manual-check
       observation with no matching attestation (prints a `reason:` line to
       stderr naming the identifier or token at fault).
    2  could not certify — fail-closed: empty stdin
       (`reason-code: empty-stdin`), non-UTF-8 stdin
       (`reason-code: non-utf8-stdin`), a `**Covers:**` value that does not
       match `^AC\\d+(, ?AC\\d+)*$` (`reason-code: malformed-covers-field`), a
       second unmasked `**Covers:**` line (`reason-code:
       duplicate-covers-field`), a second unmasked `## Criterion observations`
       heading (`reason-code: duplicate-observations-section`), a second
       unmasked `## Operator attestations` heading (`reason-code:
       duplicate-attestations-section`), or the document ending while still
       inside an open fenced code block or an open HTML comment
       (`reason-code: unterminated-masked-region`) — every exit-2 case prints
       both a `reason:` and a `reason-code:` line, uniformly, with no
       carve-out. NEVER exits 0 when it could not actually certify the claim.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from covers_gate import (  # noqa: E402
    _COMMONMARK_LINE_RE,
    _COVERS_RE,
    DuplicateHeadingError,
    _find_unique_heading,
    _mask_fenced_lines,
)

_COVERS_FIELD_RE = re.compile(r"^\*\*Covers:\*\*\s*(.*)$", re.IGNORECASE)
_OBSERVATIONS_HEADING = "## Criterion observations"
_OBSERVATIONS_HEADING_RE = re.compile(r"^## Criterion observations$", re.IGNORECASE)
_ATTESTATIONS_HEADING = "## Operator attestations"
_ATTESTATIONS_HEADING_RE = re.compile(r"^## Operator attestations$", re.IGNORECASE)
# Anchored to ASCII digits explicitly (`[0-9]`, not `\d`) — Python's `\d` is
# Unicode-aware by default and matches fullwidth and other non-ASCII decimal
# digits (category Nd), which would let e.g. `AC９` (U+FF19) parse as a
# well-formed identifier. `covers_gate._COVERS_RE` carries the same laxity on
# the `**Covers:**` side and is imported here unmodified rather than
# re-derived — do not "fix" it by loosening these patterns to match: the
# asymmetry is fail-closed, since a `**Covers:**` value with a non-ASCII
# digit then matches no observation line here and the gate refuses.
_OBSERVATION_LINE_RE = re.compile(r"^-\s+\*\*(AC[0-9]+)\*\*\s*—\s*(.*)$")
_ATTESTATION_LINE_RE = re.compile(r"^-\s+\*\*(AC[0-9]+)\*\*\s*—\s*(.*)$")

_SANCTIONED_METHODS = {"automated-assertion", "design-doc-review", "manual-check"}
_MANUAL_CHECK_METHOD = "manual-check"

_MALFORMED_COVERS_REASON_CODE = "malformed-covers-field"
_DUPLICATE_OBSERVATIONS_SECTION_REASON_CODE = "duplicate-observations-section"
_DUPLICATE_ATTESTATIONS_SECTION_REASON_CODE = "duplicate-attestations-section"
_DUPLICATE_COVERS_FIELD_REASON_CODE = "duplicate-covers-field"
_UNTERMINATED_MASKED_REGION_REASON_CODE = "unterminated-masked-region"
_EMPTY_STDIN_REASON_CODE = "empty-stdin"
_NON_UTF8_STDIN_REASON_CODE = "non-utf8-stdin"

# An inert probe line appended after the document's real lines to read back
# the sibling masker's own terminal state — never a marker matched or parsed
# by this gate's own patterns. It must not itself start a fence (```/~~~) or
# an HTML comment (<!--), or it would be mistaken for real structure.
_EOF_PROBE_LINE = "observation-gate: end-of-document probe — matches no gate pattern"

_DUPLICATE_OBSERVATION_MESSAGE = "duplicate observation for identifier {identifier}"
_DUPLICATE_ATTESTATION_MESSAGE = (
    "duplicate '## Operator attestations' line for identifier {identifier}"
)


# Unicode categories that never carry visible content: Cf (format — e.g.
# U+200B ZERO WIDTH SPACE, ZWNJ/ZWJ, U+2060 WORD JOINER, U+FEFF BOM) and Cc
# (control). A string made entirely of these characters renders as nothing in
# any editor or terminal, but `str.isspace()` is False for them, so
# `str.strip()` alone treats such a string as non-empty.
_MEANINGLESS_CATEGORIES = {"Cf", "Cc"}


def _is_meaningfully_nonempty(text: str) -> bool:
    """Return whether `text` carries at least one character that is neither
    whitespace nor Unicode category Cf or Cc.

    Both "evidence is required and never empty" and the operator-attestation
    requirement rest on this bar rather than on `bool(text.strip())` alone —
    a string of invisible format/control code points would satisfy the
    latter while being, by construction, indistinguishable from nothing. A
    zero-width character surviving a copy-paste from a chat client, a doc, or
    a PDF is exactly the accidental-omission case these checks exist to
    catch, not only a hostile one.
    """
    return any(
        not ch.isspace() and unicodedata.category(ch) not in _MEANINGLESS_CATEGORIES
        for ch in text
    )


def _err(msg: str) -> None:
    print(f"observation-gate: {msg}", file=sys.stderr)


class ObservationViolation(ValueError):
    """A covered identifier's observation (or its Covers field) fails an
    integrity check — exit code 1, one `reason:` line naming what is at
    fault."""


class MalformedCoversField(ValueError):
    """The `**Covers:**` value does not parse as an identifier list — exit
    code 2, `reason-code: malformed-covers-field`."""


class DuplicateCoversFieldError(ValueError):
    """A second unmasked `**Covers:**` line was found — the claim is not
    uniquely determined, so certifying against the first occurrence found
    would silently trust whichever claim a first-match-wins scan happened to
    see first, and could never check a later, larger claim. Exit code 2,
    `reason-code: duplicate-covers-field`."""

    reason_code = _DUPLICATE_COVERS_FIELD_REASON_CODE

    def __init__(self) -> None:
        super().__init__(
            "a second unmasked '**Covers:**' line was found — the claim is "
            "not uniquely determined"
        )


class UnterminatedMaskedRegionError(ValueError):
    """The document ends while still inside an open fenced code block or an
    open HTML comment — every line from the opening marker to end of document
    is masked, so any `**Covers:**` line in that span is invisible to the
    field search. Certifying `covers: none` against a document that might
    still carry a real, unseen claim would silently widen the documented
    fail-open (no `**Covers:**` field) to cover a masked one too. Exit code 2,
    `reason-code: unterminated-masked-region`."""

    reason_code = _UNTERMINATED_MASKED_REGION_REASON_CODE

    def __init__(self) -> None:
        super().__init__(
            "the document ends while still inside an open fenced code block "
            "or an open HTML comment — cannot certify what a masked "
            "'**Covers:**' line might claim"
        )


def _ends_inside_masked_region(lines: list[str]) -> bool:
    """Return whether the document ends while `covers_gate._mask_fenced_lines`
    still considers itself inside an open fence or an open HTML comment.

    This never re-derives the sibling's fence/comment state machine: it calls
    the imported masker a second time, on `lines` plus one inert probe line
    that opens nothing and closes nothing, and reads back whether the masker
    marked the probe itself as masked. The probe is masked only if the masker
    carried an open fence or open comment into it from the lines before —
    exactly the terminal state this gate needs and the masker does not
    otherwise expose, since it returns only a per-line boolean list, never
    its own end-of-scan state.
    """
    probed = _mask_fenced_lines(lines + [_EOF_PROBE_LINE])
    return probed[-1]


def find_covers_field(lines: list[str], masked: list[bool]) -> str | None:
    """Return the raw value of the sole unmasked `**Covers:**` line, or None
    if the field is absent — the legacy and enabler shape. Raises
    DuplicateCoversFieldError if a second unmasked `**Covers:**` line exists
    anywhere in the document, even one that comes after the first — a
    first-match-wins scan would silently certify against whichever claim it
    saw first and never check the other."""
    value: str | None = None
    for i, line in enumerate(lines):
        if masked[i]:
            continue
        m = _COVERS_FIELD_RE.match(line)
        if m:
            if value is not None:
                raise DuplicateCoversFieldError()
            value = m.group(1).strip()
    return value


def parse_covers_field(value: str) -> list[str]:
    """Parse the `**Covers:**` grammar into an ordered, duplicate-free
    identifier list. Raises MalformedCoversField on bad grammar and
    ObservationViolation on a repeated identifier."""
    if not value or not _COVERS_RE.match(value):
        raise MalformedCoversField(f"**Covers:** value is not a valid identifier list: {value!r}")
    identifiers = [t.lstrip(" ") for t in value.split(",")]
    seen: set[str] = set()
    for identifier in identifiers:
        if identifier in seen:
            raise ObservationViolation(f"**Covers:** repeats identifier {identifier!r}")
        seen.add(identifier)
    return identifiers


def _scan_section(
    lines: list[str],
    masked: list[bool],
    start: int,
    line_re: re.Pattern[str],
    duplicate_message: str = _DUPLICATE_OBSERVATION_MESSAGE,
) -> dict[str, tuple[str, int]]:
    """Return {identifier: (remainder-after-first-dash, line-index)} for
    every unmasked matching bullet from `start` to the next `## ` heading or
    end of document. A `### ` sub-heading does not end the section — only a
    `## ` heading does, matching the sibling gates' behaviour — so a bullet
    nested under a sub-heading inside the section still counts. Raises
    ObservationViolation on a duplicate identifier, formatting
    `duplicate_message` with `identifier=` so a caller scanning the
    attestations section can name that section in the refusal rather than
    reusing the observations-section wording."""
    found: dict[str, tuple[str, int]] = {}
    for i in range(start, len(lines)):
        if masked[i]:
            continue
        line = lines[i]
        if line.startswith("## "):
            break
        m = line_re.match(line)
        if not m:
            continue
        identifier, remainder = m.group(1), m.group(2)
        if identifier in found:
            raise ObservationViolation(duplicate_message.format(identifier=identifier))
        found[identifier] = (remainder, i)
    return found


def parse_observations(
    lines: list[str], masked: list[bool]
) -> dict[str, tuple[str, str]]:
    """Return {identifier: (method, evidence)} for every well-formed
    observation line under the sole unmasked `## Criterion observations`
    heading. Raises DuplicateHeadingError if a second unmasked heading
    exists, ObservationViolation on a duplicate identifier, an unsanctioned
    method token, or empty evidence text. Returns {} if the heading is
    absent — that is the caller's job to treat as "nothing observed", not
    this function's."""
    start = _find_unique_heading(
        lines,
        masked,
        _OBSERVATIONS_HEADING_RE,
        _OBSERVATIONS_HEADING,
        _DUPLICATE_OBSERVATIONS_SECTION_REASON_CODE,
    )
    if start is None:
        return {}

    raw = _scan_section(lines, masked, start, _OBSERVATION_LINE_RE)
    observations: dict[str, tuple[str, str]] = {}
    for identifier, (remainder, _line_idx) in raw.items():
        method_raw, sep, evidence_raw = remainder.partition("—")
        method = method_raw.strip()
        evidence = evidence_raw.strip() if sep else ""
        if method not in _SANCTIONED_METHODS:
            raise ObservationViolation(
                f"identifier {identifier} carries unsanctioned method token {method!r}"
            )
        if not _is_meaningfully_nonempty(evidence):
            raise ObservationViolation(f"identifier {identifier} carries empty evidence text")
        observations[identifier] = (method, evidence)
    return observations


def parse_attestations(lines: list[str], masked: list[bool]) -> dict[str, str]:
    """Return {identifier: attestation text} for every unmasked attestation
    line under the sole unmasked `## Operator attestations` heading. Returns
    {} if the heading is absent."""
    start = _find_unique_heading(
        lines,
        masked,
        _ATTESTATIONS_HEADING_RE,
        _ATTESTATIONS_HEADING,
        _DUPLICATE_ATTESTATIONS_SECTION_REASON_CODE,
    )
    if start is None:
        return {}
    raw = _scan_section(
        lines, masked, start, _ATTESTATION_LINE_RE, _DUPLICATE_ATTESTATION_MESSAGE
    )
    return {identifier: remainder.strip() for identifier, (remainder, _i) in raw.items()}


def certify(body: str) -> tuple[list[str], dict[str, tuple[str, str]]]:
    """Return (covers, observations) on success. Raises MalformedCoversField,
    ObservationViolation, DuplicateHeadingError, or DuplicateCoversFieldError
    on failure. `covers` is empty exactly when the `**Covers:**` field is
    absent — the caller distinguishes that from a present-but-empty field,
    which parse_covers_field already refuses as malformed."""
    lines = _COMMONMARK_LINE_RE.split(body)
    masked = _mask_fenced_lines(lines)

    if _ends_inside_masked_region(lines):
        raise UnterminatedMaskedRegionError()

    covers_value = find_covers_field(lines, masked)
    if covers_value is None:
        return [], {}

    covers = parse_covers_field(covers_value)
    covers_set = set(covers)

    observations = parse_observations(lines, masked)

    undeclared = [i for i in observations if i not in covers_set]
    if undeclared:
        raise ObservationViolation(
            f"observation recorded for identifier(s) not in **Covers:**: {', '.join(undeclared)}"
        )

    missing = [i for i in covers if i not in observations]
    if missing:
        raise ObservationViolation(
            f"covered identifier(s) with no observation: {', '.join(missing)}"
        )

    attestations = parse_attestations(lines, masked)
    unattested = [
        i
        for i in covers
        if observations[i][0] == _MANUAL_CHECK_METHOD
        and not _is_meaningfully_nonempty(attestations.get(i, ""))
    ]
    if unattested:
        raise ObservationViolation(
            "manual-check observation(s) with no matching operator attestation: "
            f"{', '.join(unattested)}"
        )

    return covers, observations


def main(argv: list[str]) -> int:
    del argv  # no flags — the whole interface is stdin

    try:
        body = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError as e:
        _err(f"reason: parent body on stdin is not valid UTF-8: {e}")
        _err(f"reason-code: {_NON_UTF8_STDIN_REASON_CODE}")
        return 2
    if not body.strip():
        _err("reason: parent body on stdin is empty")
        _err(f"reason-code: {_EMPTY_STDIN_REASON_CODE}")
        return 2

    try:
        covers, observations = certify(body)
    except DuplicateHeadingError as e:
        _err(f"reason: {e}")
        _err(f"reason-code: {e.reason_code}")
        return 2
    except DuplicateCoversFieldError as e:
        _err(f"reason: {e}")
        _err(f"reason-code: {e.reason_code}")
        return 2
    except UnterminatedMaskedRegionError as e:
        _err(f"reason: {e}")
        _err(f"reason-code: {e.reason_code}")
        return 2
    except MalformedCoversField as e:
        _err(f"reason: {e}")
        _err(f"reason-code: {_MALFORMED_COVERS_REASON_CODE}")
        return 2
    except ObservationViolation as e:
        _err(f"reason: {e}")
        return 1

    if not covers:
        print("covers: none")
        return 0

    print(f"covers: {', '.join(covers)}")
    for identifier in covers:
        method, _evidence = observations[identifier]
        print(f"{identifier}: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
