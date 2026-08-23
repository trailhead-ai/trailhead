"""Shown-set state — the machine-local record of what ``lore record show`` has
already returned in full during one session.

``record show`` is a READ path, and the session record it would otherwise be
natural to annotate is a git-backed, syncing vault record: recording a read
there would put a sync-visible write, and a merge surface, on every read. So the
shown-set lives outside the vault entirely, at
``state_dir("lore")/shown/<session-id>.json`` — the same machine-local posture as
``cli.resolve_state``'s resolution marker and ``cli.sync``'s freshness stamp, and
for the same reason: it has no value on another machine and must never sync. It
is serialized as plain sorted-key JSON rather than through ``record.sidecar.dumps``,
whose byte shape exists to make *git-tracked* sidecars mergeable — a guarantee
this file has no use for.

**Keyed on a real session id only.** The caller passes the id resolved from
``--session-id`` / ``$CLAUDE_CODE_SESSION_ID`` / ``$CLAUDE_SESSION_ID``, never
``cli.session._resolve_session_key``'s worktree-name fallback. That fallback key
is stable across *different* sessions in one worktree and persists indefinitely,
so keying on it would report "already shown" to a session that never saw the
body. Every "cannot tell" here fails open: no id, no shown-set, no dedupe.

**The stored digest is what makes staleness detectable.** An entry records a
digest of the exact body that was shown, so a record edited between two shows is
answered with the body rather than an acknowledgement about content the agent
has not seen.

**Bounded growth.** One file per session accumulates forever otherwise, since
nothing signals a session's end. Files untouched for :data:`MAX_AGE_SECONDS` are
pruned on write — age-based staleness, the same handling ``cli.sync``'s fetch
stamp uses, and safe because a pruned session that shows the record again simply
gets the full body.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from ..vault import layers as layers_mod
from .common import _resolve_lore_state_dir

#: Shown-set directory under ``state_dir("lore")``.
SHOWN_DIRNAME = "shown"

#: How long a session's shown-set survives without being written. A week
#: comfortably outlives any single agent session while keeping the directory
#: from growing without bound across months of use.
MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def shown_state_root() -> Path:
    """Return ``state_dir("lore")/shown`` — the shown-set directory."""
    return _resolve_lore_state_dir() / SHOWN_DIRNAME


def shown_path(session_id: str) -> Path:
    """Return the shown-set path for *session_id*, confined to the shown root.

    The session id becomes the filename stem, so it is validated as a layer name
    first — that rejects an empty id and any id carrying a path separator, a
    backslash, ``..``, or a NUL byte — and the resulting path is then confined
    with ``layers.assert_within_root``, the same guard
    ``cli.resolve_state.marker_path`` applies to its own machine-local file, so a
    symlink planted at the file's name cannot redirect a write outside the root.

    Raises:
        layers.LayerConfinementError: if the id is off-shape or the path escapes
            the shown root.
    """
    layers_mod.validate_layer_name(session_id)
    root = shown_state_root()
    candidate = root / f"{session_id}.json"
    layers_mod.assert_within_root(candidate, root)
    return candidate


def body_digest(body: str) -> str:
    """Return the digest stored for *body* — the staleness discriminator."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def read_shown(session_id: str) -> dict:
    """Return *session_id*'s ``{record_id: digest}`` map, empty if unreadable.

    Every failure mode — no file, unreadable file, malformed JSON, a payload that
    is not a dict — degrades to "nothing has been shown", which fails open into a
    full render.
    """
    try:
        data = json.loads(shown_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError, layers_mod.LayerConfinementError):
        return {}
    return data if isinstance(data, dict) else {}


def already_shown(session_id: str, record_id: str, digest: str) -> bool:
    """Return ``True`` iff *record_id* was shown in full to *session_id* at *digest*."""
    return read_shown(session_id).get(record_id) == digest


def mark_shown(session_id: str, record_id: str, digest: str) -> None:
    """Record that *record_id* was returned in full to *session_id* at *digest*.

    Best-effort: a shown-set that cannot be written costs a future dedupe, never
    a failed read, so an unwritable state dir is swallowed rather than turned
    into a ``record show`` error.
    """
    try:
        path = shown_path(session_id)
    except layers_mod.LayerConfinementError:
        return
    shown = read_shown(session_id)
    shown[record_id] = digest
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(shown, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError:
        return
    prune_stale()


def prune_stale(now: float | None = None) -> int:
    """Delete shown-sets untouched for :data:`MAX_AGE_SECONDS`; return the count."""
    root = shown_state_root()
    cutoff = (time.time() if now is None else now) - MAX_AGE_SECONDS
    pruned = 0
    try:
        entries = list(root.glob("*.json"))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                pruned += 1
        except OSError:
            continue
    return pruned
