"""Injection-defense XML escaping for the ``<external-memory>`` data channel.

The single source of truth for the shared-content wrapping that guards against
prompt-injection from untrusted (shared-vault) note content. Extracted out of
``recall.py`` (Slice 4, S3) so the consumer — ``search.py`` — does not depend on
``recall.py``, which Slice 5 guts. ``recall.py`` imports these helpers; there is
one implementation of the wrapper logic.

Pure, no I/O.

**Injection defense (A-3, both directions):**
- A literal ``</external-memory>`` in shared content must not terminate the data
  channel early. Encoding the leading ``<`` as ``&lt;`` makes the channel
  un-escapable.
- A literal ``<external-memory`` must not spoof a new framing tag. Same encoding:
  all ``<`` become ``&lt;``, all ``>`` become ``&gt;``.
- ``&`` is encoded first so an entity is never double-encoded.
- The ``source=`` attribute is XML-attribute-escaped so a vault name like
  ``"><script`` cannot break the tag structure.
"""
from __future__ import annotations

# The fenced data-channel framing for shared-layer content.
_FENCE_OPEN = '<external-memory layer="shared" source="{source}">'
_FENCE_CLOSE = "</external-memory>"


def xml_attr_escape(value: str) -> str:
    """XML-attribute-escape a string for use in a double-quoted attribute value.

    Escapes ``& " < >`` so the value is safe inside a double-quoted attribute. A
    vault name like ``"><script`` must not break the tag structure.
    """
    value = value.replace("&", "&amp;")
    value = value.replace('"', "&quot;")
    value = value.replace("<", "&lt;")
    value = value.replace(">", "&gt;")
    return value


def xml_body_escape(text: str) -> str:
    """Encode text so it cannot break out of or spoof the external-memory channel.

    A full XML character-data escape: ``&`` first (so entities are not
    double-encoded), then ``<`` and ``>``. A literal ``</external-memory>`` or
    ``<external-memory`` in the body is rendered inert.
    """
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def wrap_shared(source: str, body_lines: list[str]) -> list[str]:
    """Wrap ``body_lines`` in the shared ``<external-memory>`` data channel.

    The ``source`` attribute is XML-attribute-escaped; each body line is
    XML-body-escaped so literal ``</external-memory>`` / ``<external-memory`` in
    shared content (or its snippet) cannot break out of or spoof the fence.

    Returns the list of lines (open tag, escaped body lines, close tag) so the
    caller can splice them into its output.
    """
    escaped_source = xml_attr_escape(source)
    lines = [_FENCE_OPEN.format(source=escaped_source)]
    for line in body_lines:
        lines.append(xml_body_escape(line))
    lines.append(_FENCE_CLOSE)
    return lines
