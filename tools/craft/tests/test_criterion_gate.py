"""Tests for the criterion content gate.

The gate reads a spec body on stdin and enforces two bars over every
criterion under `## Acceptance Criteria`: no implementation identifier
(AC3), and exactly one sanctioned verification method (AC4). It reuses
`covers_gate.parse_criteria_with_text` (the sibling accessor to
`parse_criteria`) for the walk, and `observation_gate._SANCTIONED_METHODS`
for the accepted method vocabulary — never re-deriving either.

Fixtures are synthetic spec bodies under `tests/fixtures/` — except
`crit_live_spec_ac1_to_ac9.md` (captured verbatim from
`spec/acceptance-criteria-are-atomic-assertions-a-slice-carries`) and
`crit_live_legacy_draft.md` (captured verbatim from a real draft spec that
takes the legacy carve-out).

Exit-code contract (matches the sibling gates):
  0 → certified — every criterion is clean, stdout names each identifier
      with its declared method
  1 → integrity violation — a refused code-location span, a missing/bad
      verification trailer, or an unidentified bullet in a section that
      also declares identifiers (prints a `reason:` line)
  2 → could not certify — fail-closed: empty/non-UTF-8 stdin, no
      `## Acceptance Criteria` heading, a spec declaring zero criterion
      identifiers (`reason-code: zero-criterion-identifiers`), a second
      unmasked heading (`reason-code: duplicate-acceptance-criteria-heading`),
      or the document ending inside an open fence/comment
      (`reason-code: unterminated-masked-region`)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "criterion_gate.py"
FIXTURES = Path(__file__).parent / "fixtures"
SCRIPTS = REPO_ROOT / "plugins" / "craft" / "scripts"

sys.path.insert(0, str(SCRIPTS))
import criterion_gate  # noqa: E402


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _run(spec_body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE)], input=spec_body, capture_output=True, text=True
    )


ALL_CLEAN = _fixture("crit_all_clean.md")
ZERO_IDENTIFIERS = _fixture("crit_zero_identifiers.md")
REFUSED_CODE_LOCATION = _fixture("crit_refused_code_location.md")
ALLOW_FORMS = _fixture("crit_allow_forms.md")
REFUSE_FORMS = _fixture("crit_refuse_forms.md")
NO_TRAILER = _fixture("crit_no_trailer.md")
UNSANCTIONED_TOKEN = _fixture("crit_unsanctioned_token.md")
TWO_METHODS = _fixture("crit_two_methods.md")
AC_HEADING_IN_FENCE = _fixture("crit_ac_heading_in_fence.md")
CRITERION_IN_FENCE = _fixture("crit_criterion_in_fence.md")
CRITERION_IN_HTML_COMMENT = _fixture("crit_criterion_in_html_comment.md")
DUPLICATE_HEADING = _fixture("crit_duplicate_heading.md")
UNTERMINATED_FENCE = _fixture("crit_unterminated_fence.md")
UNTERMINATED_HTML_COMMENT = _fixture("crit_unterminated_html_comment.md")
INVISIBLE_TRAILER = _fixture("crit_invisible_trailer.md")
ACCENTED_PROSE = _fixture("crit_accented_prose.md")
LIVE_SPEC_AC1_TO_AC9 = _fixture("crit_live_spec_ac1_to_ac9.md")
LIVE_LEGACY_DRAFT = _fixture("crit_live_legacy_draft.md")
MULTIPLE_VIOLATIONS = _fixture("crit_multiple_violations.md")
MIXED_IDENTIFIERS = _fixture("crit_mixed_identifiers.md")
CREDENTIAL_SPAN = _fixture("crit_credential_span.md")
CAMELCASE_BOUNDARY = _fixture("crit_camelcase_boundary.md")
CREDENTIAL_TRAILER = _fixture("crit_credential_trailer.md")
CREDENTIAL_UNSANCTIONED_METHOD = _fixture("crit_credential_unsanctioned_method.md")
CREDENTIAL_UNIDENTIFIED_BULLET = _fixture("crit_credential_unidentified_bullet.md")
SUBBULLET_IDENTIFIER_LEAK = _fixture("crit_subbullet_identifier_leak.md")
TWO_TRAILERS = _fixture("crit_two_trailers.md")
LOOSE_LIST_SUBBULLET_LEAK = _fixture("crit_loose_list_subbullet_leak.md")
LOOSE_LIST_TRAILER_CONTINUATION = _fixture("crit_loose_list_trailer_continuation.md")
CONTROL_BYTE_SPAN = _fixture("crit_control_byte_span.md")
CREDENTIAL_BOUNDARY_TRUNCATION = _fixture("crit_credential_boundary_truncation.md")

_ZERO_CRITERIA_REASON_CODE = "reason-code: zero-criterion-identifiers"
_DUPLICATE_HEADING_REASON_CODE = "reason-code: duplicate-acceptance-criteria-heading"
_UNTERMINATED_REGION_REASON_CODE = "reason-code: unterminated-masked-region"

_SANCTIONED_METHODS = {"automated-assertion", "design-doc-review", "manual-check"}


# ---- item 1: clean spec certifies, stdout names identifier + method ------


def test_all_clean_spec_exits_0_and_names_each_identifier_with_its_method():
    r = _run(ALL_CLEAN)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "AC1" in r.stdout and "automated-assertion" in r.stdout
    assert "AC2" in r.stdout and "design-doc-review" in r.stdout
    assert "AC3" in r.stdout and "manual-check" in r.stdout


# ---- item 2: zero identifiers takes the legacy carve-out ------------------


def test_zero_criterion_identifiers_exits_2_with_carveout_reason_code():
    r = _run(ZERO_IDENTIFIERS)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE in r.stderr


# ---- item 3: refused span names both criterion and span -------------------


def test_refused_code_location_span_exits_1_naming_criterion_and_span():
    r = _run(REFUSED_CODE_LOCATION)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr
    assert "event_id" in r.stderr


# ---- item 4: each allowed product-surface form certifies ------------------


def test_each_allowed_product_surface_form_certifies():
    r = _run(ALLOW_FORMS)
    assert r.returncode == 0, r.stderr + r.stdout
    for identifier in ("AC1", "AC2", "AC3", "AC4", "AC5"):
        assert identifier in r.stdout


# ---- item 5: each refused code-location form refuses ----------------------


def test_each_refused_code_location_form_refuses():
    r = _run(REFUSE_FORMS)
    assert r.returncode == 1, r.stderr + r.stdout
    for identifier in ("AC1", "AC2", "AC3", "AC4", "AC5"):
        assert identifier in r.stderr, f"{identifier} missing from: {r.stderr}"


# ---- item 6: no verification trailer ---------------------------------------


def test_no_verification_trailer_exits_1_naming_identifier():
    r = _run(NO_TRAILER)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr


# ---- item 7: unsanctioned method token -------------------------------------


def test_unsanctioned_method_token_exits_1_naming_token_and_identifier():
    r = _run(UNSANCTIONED_TOKEN)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr
    assert "manual-testing" in r.stderr


def test_unsanctioned_method_refusal_lists_all_three_sanctioned_tokens():
    """Item 16: the refusal names the remedy, not just the fault."""
    r = _run(UNSANCTIONED_TOKEN)
    assert r.returncode == 1, r.stderr + r.stdout
    for method in _SANCTIONED_METHODS:
        assert method in r.stderr, f"{method} missing from remedy text: {r.stderr}"


# ---- item 8: exactly one method ---------------------------------------------


def test_trailer_naming_two_methods_exits_1():
    r = _run(TWO_METHODS)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr


# ---- item 9: spaced and hyphenated spellings both certify (executed) -------


def _spec_with_trailer(method_text: str) -> str:
    return (
        "## Acceptance Criteria\n\n"
        f"- **AC1.** A clean criterion. *Verified by: {method_text}.*\n\n"
        "## Non-Goals\n\nn/a — fixture only.\n"
    )


def test_spaced_and_hyphenated_spellings_of_sanctioned_methods_certify():
    for spaced, hyphenated in (
        ("automated assertion", "automated-assertion"),
        ("design doc review", "design-doc-review"),
        ("manual check", "manual-check"),
    ):
        r_spaced = _run(_spec_with_trailer(spaced))
        assert r_spaced.returncode == 0, f"{spaced!r}: {r_spaced.stderr}"
        assert hyphenated in r_spaced.stdout

        r_hyphenated = _run(_spec_with_trailer(hyphenated))
        assert r_hyphenated.returncode == 0, f"{hyphenated!r}: {r_hyphenated.stderr}"
        assert hyphenated in r_hyphenated.stdout


def test_accepted_method_set_is_derived_by_executing_the_gate():
    """Never import the gate's own constant and compare it to itself — run
    every candidate token through the gate and observe the exit code, the
    discipline `test_verification_method_vocabulary.py` establishes."""
    candidates = _SANCTIONED_METHODS | {"manual-testing", "peer-review", "code-review"}
    accepted = set()
    for token in candidates:
        r = _run(_spec_with_trailer(token))
        if r.returncode == 0:
            accepted.add(token)
        else:
            assert r.returncode == 1, f"{token!r} produced {r.returncode}: {r.stderr}"
    assert accepted == _SANCTIONED_METHODS


# ---- item 10: empty / non-UTF-8 stdin never exit 0 -------------------------


def test_empty_stdin_exits_2():
    r = _run("")
    assert r.returncode == 2, r.stderr + r.stdout
    assert "reason-code: empty-stdin" in r.stderr


def test_non_utf8_stdin_exits_2():
    r = subprocess.run(
        [sys.executable, str(GATE)], input=b"\xff\xfe\x00garbage", capture_output=True
    )
    assert r.returncode == 2, r.stderr + r.stdout
    assert b"reason-code: non-utf8-stdin" in r.stderr


# ---- item 11: forged-structure class ---------------------------------------


def test_ac_heading_inside_fence_is_invisible_to_heading_search():
    r = _run(AC_HEADING_IN_FENCE)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE not in r.stderr


def test_criterion_bullet_inside_fence_is_invisible_to_scan():
    r = _run(CRITERION_IN_FENCE)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "AC99" not in r.stdout


def test_criterion_bullet_inside_html_comment_is_invisible_to_scan():
    r = _run(CRITERION_IN_HTML_COMMENT)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "AC99" not in r.stdout


def test_duplicate_ac_heading_exits_2_with_inherited_reason_code():
    r = _run(DUPLICATE_HEADING)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _DUPLICATE_HEADING_REASON_CODE in r.stderr


# ---- item 12: unterminated-region class ------------------------------------


def test_unterminated_fence_exits_2_never_0():
    r = _run(UNTERMINATED_FENCE)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _UNTERMINATED_REGION_REASON_CODE in r.stderr


def test_unterminated_html_comment_exits_2_never_0():
    r = _run(UNTERMINATED_HTML_COMMENT)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _UNTERMINATED_REGION_REASON_CODE in r.stderr


# ---- item 13: invisible-content class --------------------------------------


def test_trailer_of_only_invisible_unicode_does_not_satisfy_names_a_method():
    r = _run(INVISIBLE_TRAILER)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr


def test_accented_non_ascii_prose_still_certifies():
    r = _run(ACCENTED_PROSE)
    assert r.returncode == 0, r.stderr + r.stdout


# ---- item 14: live-shape fixtures -------------------------------------------


def test_live_spec_nine_criteria_certify():
    r = _run(LIVE_SPEC_AC1_TO_AC9)
    assert r.returncode == 0, r.stderr + r.stdout
    for n in range(1, 10):
        assert f"AC{n}" in r.stdout


def test_live_legacy_draft_spec_takes_the_carveout():
    r = _run(LIVE_LEGACY_DRAFT)
    assert r.returncode == 2, r.stderr + r.stdout
    assert _ZERO_CRITERIA_REASON_CODE in r.stderr


# ---- item 15: every failing criterion named in one pass ---------------------


def test_three_distinct_violations_named_in_a_single_refusal():
    r = _run(MULTIPLE_VIOLATIONS)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC2" in r.stderr
    assert "AC3" in r.stderr
    assert "AC4" in r.stderr


# ---- item 17: mixed-identifier sections -------------------------------------


def test_mixed_identifier_section_gates_identified_and_refuses_unidentified():
    r = _run(MIXED_IDENTIFIERS)
    assert r.returncode == 1, r.stderr + r.stdout
    # the identified bullets are clean; only the unidentified bullet refuses
    assert "AC1" not in r.stderr
    assert "AC2" not in r.stderr


# ---- item 18: credential-scrubbed refusal output -----------------------------


def test_credential_shaped_span_refuses_without_reproducing_the_secret():
    r = _run(CREDENTIAL_SPAN)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "sk_live_Zq7Kd2" not in r.stderr
    assert "AC1" in r.stderr


# ---- CamelCase boundary (both directions), per U1's binding correction ------


def test_camelcase_genuine_alternation_refuses_and_allcaps_certifies():
    r = _run(CAMELCASE_BOUNDARY)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC1" in r.stderr
    assert "AC2" not in r.stderr


# ---- credential scrub is a class, applied at the single output point --------
# Fix for the correctness-review finding: the scrub was applied to the
# offending-span path only, leaving the two-method trailer message, the
# unsanctioned-method message, and the unidentified-bullet snippet unscrubbed.
# Each of these three sites is exercised independently below.


def test_two_method_trailer_message_does_not_reproduce_a_credential_token():
    r = _run(CREDENTIAL_TRAILER)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "Kq7Zd2Mx9Vb4Nr6Tj8Wc3Hy5Ps1Lg0Fa7Ue2Sd4R" not in r.stderr
    assert "AC1" in r.stderr


def test_unsanctioned_method_message_does_not_reproduce_a_credential_token():
    r = _run(CREDENTIAL_UNSANCTIONED_METHOD)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "sk_live_zq7kd2" not in r.stderr
    assert "sk_live_Zq7Kd2" not in r.stderr
    assert "AC1" in r.stderr


def test_unidentified_bullet_snippet_does_not_reproduce_a_credential_token():
    r = _run(CREDENTIAL_UNIDENTIFIED_BULLET)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "password=hunter2supersecretvalue" not in r.stderr


# ---- AC3 sub-bullet text is not an escape hatch ------------------------------


def test_implementation_identifier_hidden_in_a_sub_bullet_is_refused():
    """A criterion whose sub-bullet names an implementation identifier must
    refuse — the spec defines a sub-bullet as qualifying its parent
    criterion, so its text is part of what AC3 scans."""
    r = _run(SUBBULLET_IDENTIFIER_LEAK)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC1" in r.stderr
    assert "covers_gate.py:191" in r.stderr


def test_implementation_identifier_hidden_in_a_loose_list_sub_bullet_is_refused():
    """A blank line between a criterion's own line and its sub-bullet does not
    end the criterion — per CommonMark's loose-list rule, the item continues
    until the next top-level bullet, a heading, or a dedent to column zero. A
    sub-bullet separated from its parent by a blank line still qualifies that
    parent, so its text is still part of what AC3 scans."""
    r = _run(LOOSE_LIST_SUBBULLET_LEAK)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC1" in r.stderr
    assert "covers_gate.py:191" in r.stderr


def test_trailer_in_a_loose_list_continuation_paragraph_certifies():
    """A verification trailer that lives in its own paragraph, separated from
    the criterion's own line by a blank line, is still part of the criterion
    block — the same loose-list continuation that keeps a sub-bullet attached
    to its parent keeps a continuation paragraph attached too."""
    r = _run(LOOSE_LIST_TRAILER_CONTINUATION)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "AC1: automated-assertion" in r.stdout


# ---- AC4 "exactly one" holds across separate trailers, not just within one --


def test_two_separate_verification_trailers_on_one_criterion_refuses():
    r = _run(TWO_TRAILERS)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "AC1" in r.stderr


# ---- AC3 does not false-positive on a purely numeric dotted literal ---------


def _spec_with_span(span_text: str) -> str:
    return (
        "## Acceptance Criteria\n\n"
        f"- **AC1.** A criterion naming `{span_text}`. *Verified by: automated assertion.*\n\n"
        "## Non-Goals\n\nn/a — fixture only.\n"
    )


def test_purely_numeric_dotted_literals_do_not_false_positive_as_paths():
    for literal in ("2.0", "3.11", "v2.1", "99.9", "1.2.3"):
        r = _run(_spec_with_span(literal))
        assert r.returncode == 0, f"{literal!r} incorrectly refused: {r.stderr}"


def test_real_paths_with_extensions_still_refuse():
    for path in ("main.py", "CLAUDE.md", "migrations/008_streams.sql", "workspace-join.ts"):
        r = _run(_spec_with_span(path))
        assert r.returncode == 1, f"{path!r} incorrectly certified: {r.stdout}"


# ---- the allow-list wins over a widened refuse pattern (ordering pin) -------


def test_widened_refuse_pattern_still_loses_to_the_allow_list():
    """The allow-list is checked first, by construction. Widen a refuse
    pattern until it also matches an allow-listed shape (a CLI flag) and
    confirm the span still classifies as "allow" — the condition under which
    the inert allow-list is kept, per the plan's End Phases note."""
    original = list(criterion_gate._REFUSE_SPAN_PATTERNS)
    criterion_gate._REFUSE_SPAN_PATTERNS.append(re.compile(r".*"))
    try:
        assert criterion_gate._classify_span("--related") == "allow"
    finally:
        criterion_gate._REFUSE_SPAN_PATTERNS[:] = original


# ---- the identifier:method pairing is asserted as a unit --------------------


def test_all_clean_spec_pairs_each_identifier_with_its_own_method():
    """A run pairing AC1 with AC3's method must not satisfy this — assert the
    exact `<identifier>: <method>` line, not identifier-presence and
    method-presence independently."""
    r = _run(ALL_CLEAN)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "AC1: automated-assertion" in r.stdout
    assert "AC2: design-doc-review" in r.stdout
    assert "AC3: manual-check" in r.stdout


# ---- Critical: control bytes in a refused span must not reach stderr raw ----
# A vault record is team-writable, and a refused span is echoed verbatim into
# the violation message. Three refuse patterns (call syntax, HTTP verb, and
# subscript) place no character restriction on part of their match, so a raw
# ESC byte is legal input. The threat is two-fold: terminal-escape injection
# into a human's terminal, and prompt injection into the operator agent the
# gauntlet skill directs to re-quote the offending span verbatim.


def test_control_bytes_in_a_refused_call_syntax_span_are_neutralized():
    r = _run(CONTROL_BYTE_SPAN)
    assert r.returncode == 1, r.stderr + r.stdout
    assert b"\x1b" not in r.stderr.encode("utf-8", "surrogateescape")
    assert "AC1" in r.stderr
    # the neutralized escape must still be legible as evidence, not dropped
    assert "\\x1b" in r.stderr


def _run_bytes(spec_body: bytes) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GATE)], input=spec_body, capture_output=True)


def test_control_bytes_anywhere_in_the_refusal_output_are_neutralized():
    """Pin the property as a class at the output boundary, not just on the one
    fixture above: no C0/C1 control byte reaches stdout or stderr on any
    refusal path, using raw bytes on the wire rather than a decoded string so
    a raw ESC byte cannot hide from the assertion."""
    r = _run_bytes(CONTROL_BYTE_SPAN.encode("utf-8"))
    assert r.returncode == 1
    control_bytes = bytes(b for b in range(0x00, 0x20) if b not in (0x09, 0x0A, 0x0D))
    for b in control_bytes:
        assert bytes([b]) not in r.stderr, f"raw control byte {b:#04x} leaked into stderr"


# ---- High: the credential scrub must not be defeated by truncate-then-scrub -
# `_snippet` truncates to 48 chars before the credential scrub ever runs, so a
# secret positioned to straddle that boundary survives with only its
# non-matching prefix intact. Every existing credential fixture places its
# secret at the start of the bullet, inside the 48-char window — this fixture
# deliberately straddles it.


def test_credential_straddling_the_snippet_truncation_boundary_is_still_scrubbed():
    r = _run(CREDENTIAL_BOUNDARY_TRUNCATION)
    assert r.returncode == 1, r.stderr + r.stdout
    assert "Rw5Xn3Qp8Bt1Cv6Km2Zy9Ld4Hg7Jf0Ns5Ar3Ei8T" not in r.stderr
    # the truncated-but-unscrubbed partial token must not leak either
    assert "Rw5Xn3Qp8" not in r.stderr


# ---- Medium: unbounded stdin ahead of an eight-agent dispatch ---------------


def test_stdin_over_the_size_cap_refuses_fail_closed_rather_than_certifying_a_partial_document():
    oversized = (
        "## Acceptance Criteria\n\n"
        "- **AC1.** " + ("x" * (criterion_gate._MAX_STDIN_BYTES + 1)) + " *Verified by: automated assertion.*\n"
    )
    r = _run(oversized)
    assert r.returncode == 2, r.stderr + r.stdout
    assert "reason-code: stdin-too-large" in r.stderr
    assert "AC1" not in r.stdout


def test_stdin_at_or_under_the_size_cap_is_unaffected():
    body = _spec_with_span("/craft:slice")
    assert len(body.encode("utf-8")) <= criterion_gate._MAX_STDIN_BYTES
    r = _run(body)
    assert r.returncode == 0, r.stderr + r.stdout


def test_stdin_exactly_at_the_size_cap_still_certifies():
    """The cap is a threshold — pin the boundary itself, not just comfortably
    inside or outside it: a body whose byte length equals the cap exactly
    must not refuse."""
    cap = criterion_gate._MAX_STDIN_BYTES
    prefix = "## Acceptance Criteria\n\n- **AC1.** "
    suffix = " *Verified by: automated assertion.*\n"
    pad = cap - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
    body = prefix + ("x" * pad) + suffix
    assert len(body.encode("utf-8")) == cap
    r = _run(body)
    assert r.returncode == 0, r.stderr + r.stdout


# ---- Low: the credential pattern list must not silently drift from the -----
# ---- shared skill document it was hand-copied from --------------------------

_SHARED_EXECUTE_MD = REPO_ROOT / "plugins" / "craft" / "skills" / "_shared" / "execute.md"

# A pattern span in the doc's bulleted list always begins with one of these
# regex-only prefixes; every prose example backtick-span in the same lines
# (e.g. `SECRET_KEY=`, `[=:]`) begins with a literal character instead, so
# this discriminates the pattern spans unambiguously without hand-picking
# line offsets into the bullet text.
_PATTERN_PREFIXES = ("(?i)", r"\b", "-----BEGIN")


def _parse_credential_patterns_from_shared_doc() -> list[str]:
    text = _SHARED_EXECUTE_MD.read_text(encoding="utf-8")
    start = text.index("Key-like tokens")
    end = text.index("Prefer over-matching")
    section = text[start:end]
    spans = re.findall(r"`([^`]+)`", section)
    return [s for s in spans if s.startswith(_PATTERN_PREFIXES)]


def _normalize_python_string_literal_escaping(pattern: str) -> str:
    """`\\"` and `"` are equivalent inside a regex (the escape exists only
    because the code's source lives in a double-quoted Python string literal
    and must escape an embedded `"`; the doc's markdown backtick span needs
    no such escaping). Normalize this one syntactic artifact away so the
    comparison is over regex semantics, not which quote character the
    source file happens to use."""
    return pattern.replace('\\"', '"')


def test_credential_pattern_list_matches_the_shared_document_it_was_copied_from():
    doc_patterns = _parse_credential_patterns_from_shared_doc()
    assert doc_patterns, "could not parse any credential patterns out of the shared document"
    code_patterns = [
        _normalize_python_string_literal_escaping(p.pattern)
        for p in criterion_gate._CREDENTIAL_PATTERNS
    ]
    assert doc_patterns == code_patterns
