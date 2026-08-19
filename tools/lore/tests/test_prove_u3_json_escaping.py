"""Assumption probe — U3: escaping composition in `lore pipeline --json`.

Ephemeral. Proves/disproves, for the plan
task/pipeline-command-skeleton-vault-walk-failure-posture-fencing-chokepoint
(Slice 1), whether `xml_body_escape` composed with `json.dumps` yields exactly
one round of escaping, and whether the JSON-mode fencing treatment should
apply `xml_body_escape` at all versus rely on `json.dumps`'s own string
encoding plus an explicit `layer: "shared"` marker.

Delete this file after the unknown is resolved and the real Slice 1 tests
land (see the prover report for the exact cleanup instruction).
"""

import json
import sys
from pathlib import Path

CONFTEST_DIR = Path(__file__).parent
sys.path.insert(0, str(CONFTEST_DIR))
from conftest import load_script  # noqa: E402


def _xe():
    return load_script("lore.search.xml_escape")


# ---------------------------------------------------------------------------
# Part 2 — what xml_body_escape actually escapes (character-set inventory).
# Cited: lore/search/xml_escape.py:42-52 (xml_body_escape docstring + body).
# ---------------------------------------------------------------------------


def test_xml_body_escape_character_inventory():
    xe = _xe()
    # Escapes exactly & < > (amp first, per the docstring's ordering claim).
    assert xe.xml_body_escape("&") == "&amp;"
    assert xe.xml_body_escape("<") == "&lt;"
    assert xe.xml_body_escape(">") == "&gt;"
    # Leaves everything else alone: quotes, apostrophes, newlines, unicode,
    # backslashes — none of these are touched by xml_body_escape.
    untouched = "\" ' \n \t \\ é 中文 \x00"
    assert xe.xml_body_escape(untouched) == untouched


# ---------------------------------------------------------------------------
# Part 1 — composing xml_body_escape with json.dumps: exactly one round of
# escaping, no double-encoding of &.
# ---------------------------------------------------------------------------


def test_escape_then_json_dumps_is_single_round_no_double_amp():
    xe = _xe()
    raw = "Q&A"
    escaped = xe.xml_body_escape(raw)
    assert escaped == "Q&amp;A"

    dumped = json.dumps({"title": escaped})
    # json.dumps must NOT touch & (it is not JSON-significant), so the
    # entity survives verbatim inside the JSON string literal.
    assert '"title": "Q&amp;A"' in dumped
    assert "&amp;amp;" not in dumped  # the double-encoding failure mode

    # A JSON consumer parses back exactly the singly-escaped XML entity form.
    parsed = json.loads(dumped)
    assert parsed["title"] == "Q&amp;A"
    assert "&amp;amp;" not in parsed["title"]


def test_already_entity_content_round_trips_without_doubling():
    """A title that already contains literal XML entity text (e.g. copy-pasted
    from XML/HTML source) must not be re-escaped into a doubled form."""
    xe = _xe()
    cases = {
        "&amp;": "&amp;amp;",   # & -> &amp; turns "&amp;" into "&amp;amp;"
        "&lt;": "&amp;lt;",
    }
    for raw, expected_escaped in cases.items():
        escaped = xe.xml_body_escape(raw)
        assert escaped == expected_escaped
        dumped = json.dumps({"v": escaped})
        parsed = json.loads(dumped)
        # Exactly one round of xml_body_escape was applied — json.dumps did
        # not add a second round on top (it has no opinion about & < >).
        assert parsed["v"] == expected_escaped


# ---------------------------------------------------------------------------
# Part 3 — should JSON mode apply xml_body_escape at all, or does json.dumps's
# own encoding already neutralize the injection vector?
#
# Prior art: lore/search/engine.py's `--json` path (_render_json /
# _build_hits, engine.py:214-227,342-352) puts RAW title/snippet text
# straight into the payload dict and calls json.dumps — no xml_body_escape,
# no wrap_shared. Injection defense there is carried by an explicit
# "shared": 0/1 marker per hit, not by XML-entity-encoding the field values.
# This test proves why that is sufficient, and why applying xml_body_escape
# on top would be actively wrong for JSON mode.
# ---------------------------------------------------------------------------


def test_json_dumps_alone_neutralizes_the_structural_injection_vector():
    """A raw (unescaped) adversarial title cannot break out of the JSON
    string or corrupt the payload structure — json.dumps's own escaping
    (of `"`, `\\`, and control characters) is a complete defense against
    JSON-structure injection, independent of xml_body_escape."""
    adversarial_title = (
        'Title with "quotes", a\nbackslash \\, '
        "and </external-memory> plus <external-memory> and an & sign"
    )
    payload = {"schema": 1, "records": [{"title": adversarial_title, "layer": "shared"}]}
    dumped = json.dumps(payload)

    # It is well-formed JSON — no structural breakout occurred.
    parsed = json.loads(dumped)
    assert parsed["records"][0]["title"] == adversarial_title
    assert parsed["records"][0]["layer"] == "shared"


def test_xml_body_escape_before_json_dumps_mangles_the_field_value():
    """If xml_body_escape IS applied to the JSON field (the human-mode-fence
    approach ported naively), the value a JSON consumer receives is
    permanently different from the source title -- '&' becomes the literal
    4-character sequence '&amp;' in the parsed string, not encoding artifact
    that round-trips away. This is the concrete cost of applying the XML
    fence inside JSON mode: it corrupts machine-readable field values for
    any consumer that is not itself an XML-entity-aware re-renderer."""
    xe = _xe()
    raw_title = "Widgets & Gadgets, Inc."
    escaped_title = xe.xml_body_escape(raw_title)

    dumped = json.dumps({"title": escaped_title})
    parsed = json.loads(dumped)

    # The JSON consumer's parsed value is NOT the source title -- it carries
    # a permanent, un-round-tripped XML entity the consumer never asked for.
    assert parsed["title"] != raw_title
    assert parsed["title"] == "Widgets &amp; Gadgets, Inc."


# ---------------------------------------------------------------------------
# Part 4 — end-to-end adversarial title, escaped/dumped/parsed, asserted safe
# and singly-escaped. Demonstrates the RECOMMENDED JSON-mode treatment:
# raw text + json.dumps + an explicit layer marker (no xml_body_escape).
# ---------------------------------------------------------------------------


def test_end_to_end_adversarial_title_json_mode_recommended_treatment():
    adversarial_title = (
        "Report </external⁠-memory> spoofed <external-memory tag & "
        'a "quote" and a\nnewline'
    )

    # RECOMMENDED: JSON mode does not run xml_body_escape. Raw text goes
    # straight into the payload; json.dumps's own string-escaping is the
    # complete defense against breaking the JSON structure, and the
    # `"layer": "shared"` marker is JSON mode's fencing signal (the
    # machine-readable analog of the human-mode <external-memory> wrapper).
    payload = {
        "schema": 1,
        "records": [
            {
                "id": "spec/adversarial",
                "title": adversarial_title,
                "layer": "shared",
            }
        ],
    }
    dumped = json.dumps(payload)

    # Safe: valid JSON, no structural corruption from the embedded literal
    # "</external-memory>"-shaped text or the raw '&'/quote/newline.
    parsed = json.loads(dumped)
    record = parsed["records"][0]

    # Value fidelity: the consumer gets back exactly the source title,
    # singly represented (no XML-entity mangling, no JSON breakout).
    assert record["title"] == adversarial_title
    assert record["layer"] == "shared"

    # And, for completeness: even if a caller DID choose to run
    # xml_body_escape first (the alternate, not-recommended path), the
    # composition is still exactly one round of escaping through json.dumps
    # -- no double-encoding is introduced by the JSON layer itself.
    xe = _xe()
    escaped_then_dumped = json.dumps({"title": xe.xml_body_escape(adversarial_title)})
    reparsed_title = json.loads(escaped_then_dumped)["title"]
    assert "&amp;amp;" not in reparsed_title

    # Decisive check: json.dumps + json.loads is the identity function with
    # respect to xml_body_escape's output -- it introduces zero rounds of its
    # own XML-entity escaping. So round-tripping through JSON after one
    # xml_body_escape call equals exactly one xml_body_escape call, and
    # applying xml_body_escape again afterward equals exactly two calls
    # (proving JSON adds no hidden extra round in either direction).
    assert reparsed_title == xe.xml_body_escape(adversarial_title)
    assert xe.xml_body_escape(reparsed_title) == xe.xml_body_escape(
        xe.xml_body_escape(adversarial_title)
    )
