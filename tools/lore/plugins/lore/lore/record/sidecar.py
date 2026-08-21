"""The canonical sidecar serializer — one byte shape, every write site.

``adr/record-storage-text-is-truth-the-index-is-derived`` makes sidecar
formatting load-bearing for git: pretty-printed, keys sorted, trailing newline.
The reason is mergeability — two devices editing *different* fields of one
record must merge without a conflict, which a single compact line can never do
(any two edits collide on the one line).

**Volatile tail.** ``updated-at``/``updated-by`` are re-stamped by every write,
so they change on edits that touch nothing else semantic. Serialized in sorted
position they would sit in the diff context of whichever semantic keys happen to
neighbor them alphabetically, dragging those lines into every conflict hunk.
They are therefore emitted AFTER all semantic keys, in that fixed order — the
churn is confined to the tail of the file.

**What this format does NOT buy.** Git merges lines, not keys: two edits to keys
that serialize as *neighboring* lines leave no unchanged context between them and
still conflict. Pretty-printing removes the whole-file collision, not every
collision — field-wise conflict resolution is still needed for the rest.

**Nested maps sort; lists do not.** ``labels``/``annotations``/``related`` are
maps whose key order carries no meaning, so sorting them buys stable bytes for
free. List values (``keywords``, ``related-*`` targets, task edges) are ordered
by the author and are round-tripped verbatim.

Pure stdlib (``json``). No I/O — callers pair this with
``record.store.write_temp_then_rename``.
"""

from __future__ import annotations

import json
from typing import Any

#: Provenance keys re-stamped on every write, serialized after all semantic keys.
VOLATILE_KEYS = ("updated-at", "updated-by")


def _stable(value: Any) -> Any:
    """Recursively sort map keys; leave list order verbatim."""
    if isinstance(value, dict):
        return {k: _stable(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def dumps(sidecar: dict) -> str:
    """Return *sidecar* as canonical sidecar text (newline-terminated).

    Idempotent: ``dumps(json.loads(dumps(x))) == dumps(x)``.
    """
    ordered = {k: _stable(sidecar[k]) for k in sorted(sidecar) if k not in VOLATILE_KEYS}
    for key in VOLATILE_KEYS:
        if key in sidecar:
            ordered[key] = _stable(sidecar[key])
    return json.dumps(ordered, indent=2) + "\n"

