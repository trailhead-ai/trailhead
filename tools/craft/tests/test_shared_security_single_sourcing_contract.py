"""The credential-pattern scrub's prose pattern list is single-sourced in a
document of its own, not merely present somewhere in the corpus.

`plugins/craft/skills/_shared/execute.md` used to be the only place the
scrub's five-category pattern list was written out, but it sat 680 lines into
an 1,100-line build-loop controller. Moving it to a `_shared` document of its
own is only a real relocation if the document holding it is small and
standalone — "exactly one file has the list" is trivially true before the
move too, since execute.md was already the sole holder. So this suite pins
the stronger, meaningful property: exactly one *small* `_shared` document
(one that is not itself the build-loop controller) states the full list,
derived by scanning the corpus, never from a hardcoded filename.

It asserts no removal: it never checks that `execute.md` no longer carries
the list (that would pass vacuously on a tree where it never did, and is
false besides — execute.md still applies the scrub by name). It asserts,
positively, that a small standalone document carries the full definition.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SHARED = REPO_ROOT / "plugins" / "craft" / "skills" / "_shared"

# A document holding only the security posture, not a build-loop controller,
# is well under this — execute.md alone is 1,100+ lines. Comfortably above
# where the extracted document is expected to land.
_SMALL_DOCUMENT_LINES = 200

# The scrub's five pattern-category bullet labels, verbatim from the prose.
# A document carrying all five states the pattern list; a document merely
# naming "the credential-pattern scrub" in passing (an application site, not
# the definition) will not match every label.
_SCRUB_CATEGORY_MARKERS = [
    "**Key-like tokens**",
    "**Vendor fixed-prefix tokens**",
    "**Bearer / api-key shapes**",
    "**High-entropy literals**",
    "**PEM private-key blocks**",
]

# The general safe-value-shape rule's canonical regex, verbatim from the
# prose. A document stating this owns the untrusted-value rule's general
# statement, as opposed to one of the several loop-specific applications of
# it that stay inlined at their own call sites.
_SAFE_VALUE_SHAPE_MARKER = "^[A-Za-z0-9._/-]+$"


def _shared_docs() -> list[Path]:
    return sorted(SHARED.glob("*.md"))


def _small_docs_stating_full_scrub_list() -> list[Path]:
    hits = []
    for path in _shared_docs():
        text = path.read_text(encoding="utf-8")
        if len(text.splitlines()) > _SMALL_DOCUMENT_LINES:
            continue
        if all(marker in text for marker in _SCRUB_CATEGORY_MARKERS):
            hits.append(path)
    return hits


def test_there_are_shared_documents_to_scan():
    """Guards the scans below against silently covering nothing."""
    assert _shared_docs(), f"no document found in {SHARED}"


def test_exactly_one_small_shared_document_states_the_full_scrub_pattern_list():
    hits = _small_docs_stating_full_scrub_list()
    assert len(hits) == 1, (
        "expected exactly one small (<= "
        f"{_SMALL_DOCUMENT_LINES}-line) _shared document to state the full "
        f"credential-pattern scrub pattern list; found {len(hits)}: "
        f"{[p.name for p in hits]}"
    )


def test_the_document_stating_the_scrub_also_states_the_safe_value_shape():
    """The scrub and the untrusted-value rule ship together in the same
    `_shared` document, per the composition the citation eval measured."""
    hits = _small_docs_stating_full_scrub_list()
    assert hits, "no small document states the scrub pattern list"
    text = hits[0].read_text(encoding="utf-8")
    assert _SAFE_VALUE_SHAPE_MARKER in text


def test_security_document_content_floor():
    """Guards against a move that quietly drops material: the document
    holding the scrub list still carries the framing prose around it, not
    just the bare bullets."""
    hits = _small_docs_stating_full_scrub_list()
    assert hits, "no small document states the scrub pattern list"
    text = hits[0].read_text(encoding="utf-8")
    assert "Prefer over-matching to under-matching" in text
    assert "Known blind spot" in text
    assert "binary" in text.lower()


# --- Partial restatements of the category list -----------------------------
#
# A document may summarise the scrub's categories for a reader instead of
# dispatching to the canonical list. That convenience is only safe while the
# summary is complete: a four-of-five summary reads as exhaustive and silently
# drops a whole credential family, which is how a vendor fixed-prefix token
# ("ghp_...", "AKIA...") slips past a surface whose reader took the summary as
# the set.
#
# So the invariant is stated positively and derived from the corpus: any
# `_shared` document that enumerates the scrub's categories at all must
# enumerate every one of them. It pins no phrasing and no count -- both sides
# come from the canonical document -- and it never asserts that any document
# stopped saying something. A document is free to name none of them, and free
# to name all of them; naming only some is the defect.

_ENUMERATION_THRESHOLD = 2


def _category_keywords() -> list[str]:
    """The canonical categories' distinguishing keywords, read off the
    document that states the full list rather than hardcoded here."""
    canonical = _small_docs_stating_full_scrub_list()
    assert canonical, "no canonical scrub document found to derive categories from"
    text = canonical[0].read_text(encoding="utf-8")
    keywords = []
    for marker in _SCRUB_CATEGORY_MARKERS:
        assert marker in text, f"{canonical[0].name} lost the {marker} bullet"
        label = marker.strip("*")
        keywords.append(label.split()[0].strip("*").lower())
    return keywords


def test_category_keyword_set_is_non_empty():
    """Non-vacuity guard: the scan below proves nothing on an empty set."""
    keywords = _category_keywords()
    assert len(keywords) == len(_SCRUB_CATEGORY_MARKERS), keywords


def test_every_partial_category_enumeration_is_complete():
    keywords = _category_keywords()
    canonical = {p.name for p in _small_docs_stating_full_scrub_list()}
    offenders = {}
    for path in _shared_docs():
        if path.name in canonical:
            continue
        lowered = path.read_text(encoding="utf-8").lower()
        present = [k for k in keywords if k in lowered]
        if len(present) >= _ENUMERATION_THRESHOLD:
            missing = [k for k in keywords if k not in present]
            if missing:
                offenders[path.name] = missing
    assert not offenders, (
        "these _shared documents summarise the credential-scrub categories but "
        f"omit some, so the summary reads as exhaustive while it is not: {offenders} "
        "-- name every category or name none and point at the canonical list"
    )
