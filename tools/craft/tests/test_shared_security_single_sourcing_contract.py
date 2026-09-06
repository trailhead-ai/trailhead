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
