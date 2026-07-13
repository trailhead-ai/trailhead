"""Boundary marker for untrusted external content ingested at the VCS seam.

Everything a hostile CI run, review bot, or PR author can influence — CI-annotation
messages, bot-review bodies, PR titles/bodies, diffs, and inline review comments —
is free text that must never be mistaken for instructions once an agent retells it.
This module is the single source of truth for wrapping that text in an escaped
structural marker at the moment it crosses the VCS boundary
(``trailhead/vcs/github.py``), so downstream agents read it as DATA, not commands.

The design mirrors lore's ``<external-memory>`` data channel
(``tools/lore/plugins/lore/lore/search/xml_escape.py``): a fenced marker whose body
is XML-character-escaped so it cannot be broken out of or spoofed.

**Injection defense (both directions):**
- A literal ``</untrusted-content>`` in the content must not terminate the marker
  early. Encoding the leading ``<`` as ``&lt;`` makes the fence un-escapable.
- A literal ``<untrusted-content …>`` must not spoof a fresh, "trusted" frame. Same
  encoding renders it inert.
- ``&`` is encoded first so an existing entity is never double-encoded.

Pure, no I/O. Only *free-text* fields are wrapped; structural fields the PR
action-logic keys on (``path`` / ``state`` / ``submittedAt`` / …) are left
untouched — wrapping them would change their meaning (e.g. an empty ``path`` becomes
a truthy wrapped string, flipping CI classification).
"""

from __future__ import annotations

_MARKER_OPEN = '<untrusted-content source="{source}">'
_MARKER_CLOSE = "</untrusted-content>"


def _xml_escape(text: str) -> str:
    """Encode ``& < >`` so the text cannot break out of or spoof the marker.

    ``&`` first (so entities are not double-encoded), then ``<`` and ``>``.
    """
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def wrap_untrusted(text: str, *, source: str) -> str:
    """Wrap free-text ``text`` in the escaped ``<untrusted-content>`` marker.

    ``source`` is a short, caller-supplied provenance label (e.g. ``ci-annotation``,
    ``bot-review``, ``pr-diff``) — it is escaped too, so even a mis-sourced literal
    can never break the tag structure. The body is XML-character-escaped so a literal
    ``</untrusted-content>`` or ``<untrusted-content …>`` inside it is rendered inert.
    """
    return f"{_MARKER_OPEN.format(source=_xml_escape(source))}{_xml_escape(text)}{_MARKER_CLOSE}"
