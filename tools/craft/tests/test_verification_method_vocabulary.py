"""Cross-artifact consistency: the sanctioned verification-method vocabulary
`observation_gate.py` accepts must be exactly the vocabulary the shared
producer contract (`_shared/execute.md`'s Phase 5) documents. A method token
renamed in one artifact and not the other must fail one of these tests.

Both properties are derived by *executing* the gate against constructed
parent bodies — never by importing the gate's own token constant and
comparing it to itself, which would prove nothing about whether the two
artifacts actually agree.
"""

from __future__ import annotations

import re

# The masking-aware span reader over `_shared/execute.md`, the document reader
# it runs on, and the gate runner are the same ones the Phase 6 wiring contract
# drives, so a change to the heading grammar or to the gate invocation reaches
# both suites from one place. Deliberately NOT imported: the gate's own
# `_SANCTIONED_METHODS` — this suite derives the accepted set by executing the
# gate, never by comparing a constant to itself.
from test_close_gate_observation_contract import _doc_text, _phase_spans, _run_gate


# Matches the observation grammar's worked-example lines, e.g.
# `- **AC9** — automated-assertion — <evidence pointer>` — the method token
# is whatever sits between the first and second em-dash. Parsing the actual
# example lines (rather than substring-matching a sentence that names the
# methods in prose) is what makes this break on a semantic rename and
# survive a prose reflow.
_GRAMMAR_METHOD_LINE_RE = re.compile(r"^- \*\*AC\d+\*\* — (\S+) — ", re.MULTILINE)


def _documented_method_tokens() -> set[str]:
    """The method tokens named in Phase 5's observation grammar block."""
    phase5 = _phase_spans(_doc_text()).get("Phase 5: Flow-out")
    assert phase5 is not None, "execute.md must carry a '### Phase 5: Flow-out' heading"
    tokens = set(_GRAMMAR_METHOD_LINE_RE.findall(phase5))
    assert tokens, "Phase 5 must carry the observation grammar's worked-example lines"
    return tokens


def _parent_body(method: str, identifier: str = "AC1", evidence: str = "some evidence") -> str:
    body = (
        f"**Covers:** {identifier}\n\n"
        "## Criterion observations\n\n"
        f"- **{identifier}** — {method} — {evidence}\n"
    )
    if method == "manual-check":
        body += f"\n## Operator attestations\n\n- **{identifier}** — checked by the operator\n"
    return body


# ---------------------------------------------------------------------------
# Item 1 — the gate's accepted token set (derived by execution) equals the
# set the producer contract documents
# ---------------------------------------------------------------------------

# Plausible-looking tokens that are NOT sanctioned — included so the test
# proves the gate refuses them, not merely that it accepts the documented
# ones. Without these, "accepted == documented" could pass vacuously on a
# gate that accepts everything.
_DECOY_TOKENS = {
    "manual-testing",
    "code-review",
    "unit-test",
    "peer-review",
    "smoke-test",
}


def test_gate_accepted_tokens_match_documented_producer_tokens():
    documented = _documented_method_tokens()
    candidates = documented | _DECOY_TOKENS

    accepted: set[str] = set()
    for token in candidates:
        result = _run_gate(_parent_body(token))
        if result.returncode == 0:
            accepted.add(token)
        else:
            assert result.returncode == 1, (
                f"token {token!r} produced exit {result.returncode}, expected 0 (accepted) "
                f"or 1 (refused as unsanctioned): {result.stderr}"
            )
            assert "reason:" in result.stderr and token in result.stderr, (
                f"token {token!r} was refused without naming itself in the reason: "
                f"{result.stderr}"
            )

    assert accepted == documented, (
        f"gate-accepted tokens {accepted} must equal the producer contract's documented "
        f"tokens {documented} — a token present in one artifact and not the other"
    )


# ---------------------------------------------------------------------------
# Item 2 — a fourth, unsanctioned token is refused by the gate and appears
# in neither artifact (pins closure of the set, not just current membership)
# ---------------------------------------------------------------------------


def test_unsanctioned_fourth_token_is_refused_and_undocumented():
    fourth_token = "peer-attestation"

    documented = _documented_method_tokens()
    assert fourth_token not in documented, (
        f"test fixture token {fourth_token!r} must not already be a documented method"
    )

    result = _run_gate(_parent_body(fourth_token))
    assert result.returncode == 1, (
        f"the gate must refuse an unsanctioned fourth token as an integrity violation, "
        f"got exit {result.returncode}: {result.stderr}"
    )
    assert fourth_token in result.stderr, (
        f"the refusal must name the unsanctioned token: {result.stderr}"
    )
