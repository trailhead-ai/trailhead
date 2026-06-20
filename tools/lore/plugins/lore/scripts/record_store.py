"""Reusable record write library for the lore vault (Slice 2, S2).

This module is the **single importable write API** into the vault — no argv/stdout
coupling (AC-LIB1/LIB2), so the S7 migration calls it per-record exactly as the
``lore record`` CLI does. Every write keeps three artifacts consistent:

  - ``<vault>/<kind>/<name>.md``   — the markdown body, verbatim (modulo fence
    neutralization).
  - ``<vault>/<kind>/<name>.json`` — the JSON sidecar, pretty-printed + sorted-key.
  - one derived SQLite index row    — a cached projection (``index_store``).

**Text-wins / index-derived (locked, umbrella decision 7; spec AC-TX2).** The
git-tracked text files are the source of truth; the index is a derived projection
that ``lore reindex`` rebuilds. On any partial failure the text on disk wins — we
never roll back a durable body/sidecar to satisfy the index. So
``validate_and_write`` writes the text *first* (atomically), then updates the
index; if the index update raises, the text is already durable and the exception
propagates with the text intact (the caller's ``reindex`` reconciles).

**Validation is S1's, not ours.** ``validate_and_write`` calls the pure
``record_model.validate`` (it returns ``(normalized, errors)`` and never raises).
On non-empty ``errors`` we raise :class:`RecordValidationError` carrying those
messages and write nothing. We do not re-implement validation.

**Provenance posture (AC, council/Security).** ``created-by``/``updated-by`` are
written from :func:`resolve_committer_email` — ``$LORE_EMAIL`` then
``git config --global user.email`` — which is **deterministic, not cwd-dependent**
(a repo-local ``user.email`` override cannot change the stamped value). An empty
email is the KU4 **hard error** (:class:`ProvenanceError`): we write nothing,
because a placeholder would pollute provenance the way AC17 forbids silent list
garbage. ``*-by`` is **self-asserted, spoofable provenance PII** — it is written
for humans and **nothing keys trust on it**; it is never an authz/authn signal.

**Atomic write (AC-TX1/LIB3).** Bodies and sidecars land via
:func:`write_temp_then_rename` — write a sibling temp file, ``fsync``, then
``os.replace`` it onto the target. A crash before the rename leaves only the temp
file (or nothing), never a half-written target.

**Fence neutralization (AC-FENCE1).** The injection fence token is the literal
``<external-memory …>`` / ``</external-memory>`` pair (the S3 output-wrapping
contract). Write-time neutralization is the structural backstop: a body can never
land a *live* fence on disk, even via a future ``--diff`` path.

**Move safety (AC-LIB3 / AC12).** :func:`move_record` copies the new artifacts →
repoints the index → deletes the old copy **in that order**. A crash after the
index repoint but before the old delete leaves a stranded old artifact whose ID
the index no longer resolves — the *safe* direction (no data loss; the new copy is
durable and indexed), self-healing via ``lore reindex``. Callers holding the old
ID must re-read.

Pure stdlib (``json``, ``sqlite3`` via ``index_store``, ``os``/``pathlib``,
``subprocess`` for git identity, ``datetime``). References: Slice 2, AC-TX1/TX2,
AC-LIB1/2/3, AC-FENCE1, AC12/AC13, KU4/KU5.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Optional

import index_store
import record_model
import vault as vault_mod

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# The record ID is the vault-relative ``<kind>/<name>`` path (KU5). Vault
# qualification (a vault token prefix) is S4's job; in S2 the active vault is
# implicit, so a plain str suffices.
RecordId = str


class RecordLocation(NamedTuple):
    """A resolved write target: the vault root, kind, slugged name, and ID.

    ``record_id`` is the vault-relative ``<kind>/<name>`` (KU5). ``body_path`` and
    ``sidecar_path`` are the absolute on-disk paths for the ``.md`` / ``.json``
    pair.
    """

    vault_root: str
    kind: str
    name: str
    record_id: RecordId
    body_path: Path
    sidecar_path: Path


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class RecordStoreError(Exception):
    """Base class for record_store write errors."""


class ProvenanceError(RecordStoreError):
    """Empty committer email (KU4) — provenance is required and cannot be defaulted."""


class RecordValidationError(RecordStoreError):
    """Sidecar failed S1 validation; carries the ordered S1 error messages."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors) if errors else "validation failed")


class RecordNotFoundError(RecordStoreError):
    """The record ID does not resolve to an on-disk record."""


class InvalidRecordIdError(RecordStoreError):
    """The RECORD_ID is malformed or would escape the vault root.

    A RECORD_ID is a vault-relative ``<kind>/<name>`` path. Any ``..`` segment,
    absolute component, NUL byte, or empty part is rejected here — the same
    confinement AC14a mandates for ``blob`` paths, applied symmetrically to every
    RECORD_ID-bearing op (``update``/``delete``) so a crafted ID cannot read,
    overwrite, or unlink ``.md``/``.json`` files outside the active vault.
    """


class InvalidBlobPathError(RecordStoreError):
    """A ``lore record blob`` path is malformed or would escape the blob root (AC14a).

    Same confinement posture as :class:`InvalidRecordIdError` but for free-form
    blob paths under ``<vault>/blob/`` — rejects NUL bytes, absolute paths, and any
    empty / ``.`` / ``..`` segment, then asserts the realpath of the target is a
    descendant of ``realpath(blob_root)`` (catching symlink escapes).
    """


class DiffRejectError(RecordStoreError):
    """A unified diff is valid format but its context doesn't match the body (Slice 4, KU2).

    A *stale* diff: the structure parses, but one or more hunks' context/deletion
    lines do not match the current body verbatim (so the diff was generated from a
    different version of the body). On reject the on-disk body is byte-for-byte
    unchanged and NO index update happens (AC-DIFF1).

    Attributes:
        original_body: the body exactly as received — byte-for-byte unmodified.
        rejected: list of ``(header, reason)`` pairs for each failing hunk, in a
            stable order, for the CLI's parseable one-line-per-hunk stderr output.
    """

    def __init__(self, original_body: str, rejected: list[tuple[str, str]]) -> None:
        self.original_body = original_body
        self.rejected = rejected
        super().__init__(
            f"{len(rejected)} hunk(s) rejected: "
            + "; ".join(f"{h}: {r}" for h, r in rejected)
        )


class DiffFormatError(RecordStoreError):
    """A unified diff string is structurally unparseable (Slice 4, KU2).

    Distinct from :class:`DiffRejectError` (valid format, stale context). The known
    trigger is ``difflib.unified_diff``'s **concatenated-no-newline** edge case:
    when BOTH the deleted and the inserted line lack a trailing newline it emits
    ``-old+new`` with no separator, and the embedded ``+`` is indistinguishable
    from content. The applier detects this via a hunk-count deficit and raises
    rather than guessing. Unreachable for well-formed lore bodies —
    :func:`write_temp_then_rename` always trailing-newlines — but handled safely.
    """


# ---------------------------------------------------------------------------
# Unified-diff applier (Slice 4, KU2 — proven pure-stdlib decision rule)
# ---------------------------------------------------------------------------

# The two-phase applier: Phase 1 verifies EVERY hunk's context+deletion lines
# verbatim against ``body.splitlines(keepends=True)`` (verbatim comparison
# auto-detects CRLF-vs-LF and trailing-newline mismatches — the core safety
# invariant); if ANY hunk fails it raises :class:`DiffRejectError` with the body
# unmodified and runs NO Phase 2. Phase 2 (only if all hunks verified) applies in
# order tracking ``offset = Σ(new_count − old_count)`` of prior hunks so each hunk
# indexes the evolving result correctly. Proven on the three KU2 adversarial cases
# (CRLF body, trailing-newline mismatch, adjacent hunks).

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class _Hunk(NamedTuple):
    """A single parsed unified-diff hunk (header + body lines with endings kept)."""

    header: str          # raw ``@@ -L,N +L,M @@`` line (for error reporting)
    old_start: int       # 1-based line number in the original body
    old_count: int
    new_count: int
    lines: list[str]     # hunk lines WITH leading marker AND original line ending


def _validate_hunk_counts(hunk: _Hunk) -> None:
    """Raise :class:`DiffFormatError` on a parsed-vs-header line-count deficit.

    A deficit indicates the concatenated-no-newline format ``difflib`` emits when
    both sides of a change lack a trailing newline (see :class:`DiffFormatError`).

    Note: the ``@@`` header counts are *advisory* — application is driven entirely
    by the parsed marker lines (Phase 1 verifies against the original body; Phase 2
    replaces using ``len(old_slice)``), so a lying or surplus header count cannot
    misdrive the apply. This check uses the counts only to detect the one ambiguous
    difflib format above; a surplus is harmless and intentionally not rejected.
    """
    old_seen = sum(1 for ln in hunk.lines if ln and ln[0] in (" ", "-"))
    new_seen = sum(1 for ln in hunk.lines if ln and ln[0] in (" ", "+"))
    if old_seen < hunk.old_count or new_seen < hunk.new_count:
        raise DiffFormatError(
            f"Hunk {hunk.header!r}: parsed {old_seen}/{hunk.old_count} old lines "
            f"and {new_seen}/{hunk.new_count} new lines — the diff appears to use "
            f"the concatenated-no-newline format difflib emits when both the "
            f"deleted and inserted lines lack a trailing newline. This format is "
            f"ambiguous and cannot be applied safely. Regenerate the diff from "
            f"content with a trailing newline."
        )


def _parse_hunks(diff: str) -> list[_Hunk]:
    """Parse a unified diff into ``_Hunk`` objects, line endings kept verbatim.

    Uses ``diff.splitlines(keepends=True)`` so each hunk line retains its original
    line ending (``\\n`` / ``\\r\\n`` / none). File-header (``--- ``/``+++ ``) lines
    are skipped. Raises :class:`DiffFormatError` on the concatenated-no-newline
    edge case (via :func:`_validate_hunk_counts`).
    """
    hunks: list[_Hunk] = []
    current: Optional[_Hunk] = None

    for raw in diff.splitlines(keepends=True):
        raw_stripped = raw.rstrip("\r\n")
        m = _HUNK_HEADER_RE.match(raw_stripped)
        if m:
            if current is not None:
                _validate_hunk_counts(current)
                hunks.append(current)
            current = _Hunk(
                header=raw_stripped,
                old_start=int(m.group(1)),
                old_count=int(m.group(2)) if m.group(2) is not None else 1,
                new_count=int(m.group(4)) if m.group(4) is not None else 1,
                lines=[],
            )
        elif current is not None:
            if raw_stripped.startswith(("--- ", "+++ ")):
                continue
            if raw and raw[0] in (" ", "-", "+"):
                current.lines.append(raw)

    if current is not None:
        _validate_hunk_counts(current)
        hunks.append(current)

    return hunks


def apply_unified_diff(body: str, diff: str) -> tuple[str, list[str]]:
    """Apply a unified *diff* to *body* (Slice 4, KU2). Returns ``(new_body, [])``.

    Two-phase, atomic:
      - **Phase 1** — parse all hunks, then verify EVERY hunk's context (`` ``) +
        deletion (``-``) lines **verbatim** against ``body.splitlines(keepends=True)``.
        Verbatim comparison auto-detects CRLF-vs-LF and trailing-newline
        mismatches — the core safety invariant. Collect ALL failures; if any →
        raise :class:`DiffRejectError(original_body, rejected)` and run NO Phase 2.
      - **Phase 2** (only if all hunks verified) — apply in order tracking
        ``offset = Σ(new_count − old_count)`` of prior hunks so each hunk indexes
        the evolving result correctly.

    Raises :class:`DiffRejectError` on stale context (body unmodified) and
    :class:`DiffFormatError` on the concatenated-no-newline format. The returned
    list is always empty on success (rejections raise — never partial).
    """
    original_body = body
    body_lines = body.splitlines(keepends=True)
    hunks = _parse_hunks(diff)

    # --- Phase 1: verify every hunk against the original body -------------------
    rejected: list[tuple[str, str]] = []
    for hunk in hunks:
        ctx_lines: list[str] = []
        for hl in hunk.lines:
            if hl[0] in (" ", "-"):
                ctx_lines.append(hl[1:])  # strip marker; keep line ending

        start_0 = hunk.old_start - 1
        end_0 = start_0 + len(ctx_lines)
        if end_0 > len(body_lines):
            rejected.append((
                hunk.header,
                f"context overruns body (body has {len(body_lines)} lines, "
                f"hunk starts at line {hunk.old_start} and expects "
                f"{len(ctx_lines)} context/deletion lines)",
            ))
            continue

        mismatch_line: Optional[int] = None
        for i, expected in enumerate(ctx_lines):
            if body_lines[start_0 + i] != expected:
                mismatch_line = hunk.old_start + i
                break
        if mismatch_line is not None:
            rejected.append((
                hunk.header,
                f"context mismatch at body line {mismatch_line}",
            ))

    if rejected:
        raise DiffRejectError(original_body=original_body, rejected=rejected)

    # --- Phase 2: apply all hunks (all verified) -------------------------------
    result_lines = list(body_lines)
    offset = 0
    for hunk in hunks:
        old_slice: list[str] = []
        new_slice: list[str] = []
        for hl in hunk.lines:
            marker, content = hl[0], hl[1:]
            if marker == " ":
                old_slice.append(content)
                new_slice.append(content)
            elif marker == "-":
                old_slice.append(content)
            elif marker == "+":
                new_slice.append(content)
        start_0 = hunk.old_start - 1 + offset
        result_lines[start_0:start_0 + len(old_slice)] = new_slice
        offset += len(new_slice) - len(old_slice)

    return "".join(result_lines), []


# ---------------------------------------------------------------------------
# Provenance + helpers
# ---------------------------------------------------------------------------

# The injection fence pair (S3 output-wrapping contract). Matched
# case-insensitively across the open/close tokens, attributes tolerated; the
# captured ``external`` group is rewritten in place so a mixed-case
# ``<External-Memory>`` is neutralized too (the literal spelling is lowercase, but
# the structural backstop tolerates no case variant).
_FENCE_RE = re.compile(r"</?\s*(external)(-memory)\b[^>]*>", re.IGNORECASE)
# Zero-width word joiner inserted between ``external`` and ``-memory`` so the token
# is no longer a parseable fence but remains human-legible.
_JOINER = "⁠"


def resolve_committer_email() -> str:
    """Return the committer email for ``*-by`` provenance (delegates to vault_mod).

    Deterministic source (``$LORE_EMAIL`` → ``git config --global user.email``),
    cwd-independent; empty when unset. See
    :func:`vault.resolve_committer_email`. Exposed here so ``validate_and_write``
    calls a patchable module-level seam.
    """
    return vault_mod.resolve_committer_email()


def neutralize_fences(text: str) -> str:
    """Neutralize ``<external-memory>`` / ``</external-memory>`` fence tokens (AC-FENCE1).

    Replaces any open/close ``external-memory`` tag with a non-parseable but
    legible form, so a stored body can never reconstruct a *live* fence. Idempotent
    enough for the contract: the output contains no fence token.
    """
    return _FENCE_RE.sub(
        lambda m: m.group(0).replace(
            m.group(1) + m.group(2), m.group(1) + _JOINER + m.group(2), 1
        ),
        text,
    )


def write_temp_then_rename(path: Path, text: str) -> None:
    """Atomically write *text* to *path* via a sibling temp file + ``os.replace``.

    Writes ``<path>.<pid>.tmp``, ``fsync``s it, then renames it onto the target.
    A crash before the rename leaves only the temp file (or nothing) — never a
    half-written target (AC-TX1). Cleans up the temp file on any failure.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now_utc_z() -> str:
    """Return the current time as an ISO-8601 UTC ``…Z`` string (second precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_existing_provenance(sidecar_path: Path) -> dict[str, Any]:
    """Return ``{created-at, created-by}`` from an on-disk sidecar, if any.

    Used to preserve the *original* ``created-*`` provenance across a rewrite when
    the caller's in-memory sidecar does not carry it. Tolerant: a missing or
    malformed sidecar yields ``{}`` (the write then stamps a fresh ``created-*``).
    """
    try:
        existing = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(existing, dict):
        return {}
    return {
        k: existing[k]
        for k in ("created-at", "created-by")
        if isinstance(existing.get(k), str)
    }


def _kebab(title: str) -> str:
    """Lowercase, collapse non-alphanumerics to single hyphens, trim.

    Importable equivalent of the CLI's ``_kebab`` (cli/lore:127). Falls back to
    ``note-<sha1[:6]>`` when the slug would be empty (all-punctuation / non-Latin).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        slug = f"note-{hashlib.sha1(title.encode()).hexdigest()[:6]}"
    return slug


def _stem_occupied(kind_dir: Path, stem: str) -> bool:
    """A stem is occupied if EITHER ``<stem>.md`` or ``<stem>.json`` exists (KU5).

    The pair-aware occupancy check: a crash can leave an orphaned ``<stem>.json``
    with no ``.md``; treating the slot as free on ``.md``-absence alone would
    silently overwrite that orphan (council/Reliability).
    """
    return (kind_dir / f"{stem}.md").exists() or (kind_dir / f"{stem}.json").exists()


def _unique_stem(kind_dir: Path, base: str) -> str:
    """Return ``base`` (or ``base-2``/``base-3``/…) for the first free stem.

    Importable, pair-aware equivalent of the CLI's ``_unique_path`` (cli/lore:143)
    — checks both artifacts (:func:`_stem_occupied`), not just ``.md``.
    """
    if not _stem_occupied(kind_dir, base):
        return base
    counter = 2
    while _stem_occupied(kind_dir, f"{base}-{counter}"):
        counter += 1
    return f"{base}-{counter}"


def _realpath_is_descendant(target: str | Path, root_real: str) -> bool:
    """True iff ``realpath(target)`` is ``root_real`` itself or strictly within it.

    The single-source realpath-descendant guard shared by every path-confinement
    check (record IDs and blob paths). The explicit ``+ os.sep`` guard stops a
    sibling dir sharing a name prefix (root ``/a/blob`` vs target ``/a/blob2/x``)
    from satisfying the check; resolving via realpath catches symlink escapes.
    ``root_real`` must already be a realpath.
    """
    target_real = os.path.realpath(target)
    return target_real == root_real or target_real.startswith(root_real + os.sep)


def _confine_record_id(record_id: RecordId, root: str) -> tuple[str, str, Path, Path]:
    """Validate a ``<kind>/<name>`` RECORD_ID and resolve its confined paths (AC14a).

    Returns ``(kind, name, body_path, sidecar_path)``. Raises
    :class:`InvalidRecordIdError` if the ID is malformed or would escape the vault:
    a missing slash, a NUL byte, any empty / ``.`` / ``..`` path segment, an
    absolute component, or a resolved ``.md``/``.json`` path whose realpath is not a
    descendant of the vault root (the same realpath-descendant check ``blob`` uses,
    catching symlinked ``kind`` dirs). This is the library-boundary guard so every
    RECORD_ID-bearing caller (``update`` via :func:`locate_record`, ``delete`` via
    :func:`delete_record`) is confined symmetrically.
    """
    if not record_id or "\x00" in record_id or "/" not in record_id:
        raise InvalidRecordIdError(f"malformed RECORD_ID: {record_id!r}")
    kind, name = record_id.split("/", 1)
    # Reject absolute components and any degenerate / traversal segment in EITHER
    # half. Path(...).parts surfaces every segment; an absolute path yields a
    # leading "/" part, and "."/".." are caught explicitly (Path(".").parts == ()).
    if os.path.isabs(kind) or os.path.isabs(name):
        raise InvalidRecordIdError(f"RECORD_ID must be vault-relative: {record_id!r}")
    # Every path segment of both halves must be a real name — no empty, ".", or
    # ".." segments (Path(...).parts splits on "/", so segments never contain it).
    segments = [kind, *Path(name).parts]
    if not segments or any(seg in ("", ".", "..") for seg in segments):
        raise InvalidRecordIdError(f"illegal RECORD_ID segment in {record_id!r}")

    kind_dir = Path(root) / kind
    body_path = kind_dir / f"{name}.md"
    sidecar_path = kind_dir / f"{name}.json"

    # Realpath-descendant containment (catches symlink escapes).
    root_real = os.path.realpath(root)
    for p in (body_path, sidecar_path):
        if not _realpath_is_descendant(p, root_real):
            raise InvalidRecordIdError(
                f"RECORD_ID resolves outside the vault root: {record_id!r}"
            )
    return kind, name, body_path, sidecar_path


def confine_blob_path(blob_path: str, blob_root: str | Path) -> Path:
    """Validate a relative blob path and return its confined absolute target (AC14a).

    The library-boundary guard for ``lore record blob`` paths, the blob-root
    counterpart of :func:`_confine_record_id`. Rejects NUL bytes, absolute paths,
    and any empty / ``.`` / ``..`` segment, then asserts ``realpath(target)`` is a
    descendant of ``realpath(blob_root)`` (catching symlink escapes). Raises
    :class:`InvalidBlobPathError` on any violation; returns the confined ``Path``.

    ``blob_root`` is created if absent so ``realpath`` resolves through real
    directories (a directory-level symlink under an not-yet-created tree would not
    otherwise be caught — this mkdir is load-bearing for the confinement check).
    """
    if not blob_path:
        raise InvalidBlobPathError("blob path is required")
    if "\x00" in blob_path:
        raise InvalidBlobPathError("blob path must not contain NUL bytes")
    if os.path.isabs(blob_path):
        raise InvalidBlobPathError(
            f"blob path must be relative, got absolute path: {blob_path!r}"
        )
    parts = Path(blob_path).parts
    if not parts or any(seg in ("", ".", "..") for seg in parts):
        raise InvalidBlobPathError(
            f"blob path must be a relative path with no '..'/'.' segments: {blob_path!r}"
        )

    blob_root = Path(blob_root)
    blob_root.mkdir(parents=True, exist_ok=True)
    blob_root_real = os.path.realpath(blob_root)
    target = blob_root / blob_path
    if not _realpath_is_descendant(target, blob_root_real):
        raise InvalidBlobPathError(
            f"blob path {blob_path!r} escapes the blob root "
            f"(resolved to {os.path.realpath(target)!r}, outside {blob_root_real!r})"
        )
    return target


# ---------------------------------------------------------------------------
# place_record
# ---------------------------------------------------------------------------


def place_record(
    name: str,
    kind: str,
    scope: str | None,
    vault_root: str | None = None,
) -> RecordLocation:
    """Resolve the on-disk target for a new record (KU5 naming + collision).

    Resolves the target vault (``vault_root`` when given, else the active vault
    via ``vault_mod.resolve_vault()`` — the multi-vault eligibility hook is where
    S4 plugs in). The vault-relative name is ``_kebab(name)`` with a ``-2``/``-3``
    collision suffix, **except** ``session`` kind, whose name is the
    ``session_id`` GUID **verbatim** (no slug, no suffix). Collision occupancy
    checks both the ``.md`` and ``.json`` stem.

    ``scope`` is accepted for the S4 multi-vault routing hook; it is unused in S2.
    Returns a :class:`RecordLocation` whose ``record_id`` is ``<kind>/<name>``.
    """
    root = vault_root if vault_root is not None else vault_mod.resolve_vault()
    kind_dir = Path(root) / kind

    if kind == "session":
        # The GUID is the identity; never slugged, never suffixed.
        stem = name
    else:
        stem = _unique_stem(kind_dir, _kebab(name))

    record_id = f"{kind}/{stem}"
    return RecordLocation(
        vault_root=root,
        kind=kind,
        name=stem,
        record_id=record_id,
        body_path=kind_dir / f"{stem}.md",
        sidecar_path=kind_dir / f"{stem}.json",
    )


def locate_record(
    record_id: RecordId,
    vault_root: str | None = None,
) -> RecordLocation:
    """Resolve an **existing** ``<kind>/<name>`` record to its on-disk location (Slice 4).

    Unlike :func:`place_record`, this does NOT slug or apply a collision suffix —
    it points at the record's existing ``.md``/``.json`` pair so an update writes
    in place (preserving the ID). Raises :class:`RecordNotFoundError` when neither
    artifact exists (AC8).
    """
    root = vault_root if vault_root is not None else vault_mod.resolve_vault()
    kind, name, body_path, sidecar_path = _confine_record_id(record_id, root)
    if not body_path.exists() and not sidecar_path.exists():
        raise RecordNotFoundError(f"record not found: {record_id}")
    return RecordLocation(
        vault_root=root,
        kind=kind,
        name=name,
        record_id=record_id,
        body_path=body_path,
        sidecar_path=sidecar_path,
    )


# ---------------------------------------------------------------------------
# update_index
# ---------------------------------------------------------------------------


def update_index(
    conn,
    record_id: RecordId,
    sidecar: dict[str, Any],
    body: str,
    vault_root: str,
    shared: int = 0,
) -> None:
    """Upsert the index row for *record_id* (thin pass-through to ``index_store``).

    The seam S3 enriches (FTS5/BM25). *record_id* is ``<kind>/<name>``; the index
    is keyed ``(vault, kind, name)``.

    ``shared`` is the trust flag stamped on the row (0 = own/trusted, unfenced by
    ``search``; 1 = untrusted/shared, fenced). It **defaults to 0** so the vanilla
    no-config write path (and every pre-S4 caller) keeps stamping trusted rows. S4's
    config-driven create path passes ``shared=1`` when the resolved destination is a
    ``shared: true`` vault (``vault_config.is_shared``), so a record routed into a
    shared vault is fenced correctly — the trust source now matches the vault, not a
    blanket "CLI writes are always own" assumption.
    """
    kind, name = record_id.split("/", 1)
    index_store.upsert_row(
        conn, vault_root, kind, name, sidecar, body, shared=shared
    )


# ---------------------------------------------------------------------------
# validate_and_write — the transactional write primitive
# ---------------------------------------------------------------------------


def validate_and_write(
    location: RecordLocation,
    sidecar: dict[str, Any],
    body: str,
    conn,
    shared: int = 0,
) -> RecordId:
    """Validate, stamp provenance, and durably write a record (AC-TX1/LIB3).

    Pipeline (text-wins / index-derived):
      1. Validate via S1 ``record_model.validate``; non-empty errors →
         :class:`RecordValidationError`, **nothing written**.
      2. Resolve the committer email; empty → :class:`ProvenanceError` (KU4),
         **nothing written**.
      3. Stamp provenance on the (normalized) sidecar: ``created-at``/``-by`` set
         once (preserved if already present on rewrite), ``updated-at``/``-by``
         re-stamped every write.
      4. Neutralize ``<external-memory>`` fences in the body (AC-FENCE1).
      5. Atomically write body then sidecar (write-temp-then-rename).
      6. Update the index with the resolved vault's ``shared`` trust flag (S4 —
         default 0/own preserves vanilla). **If this raises, the text is already
         durable and wins** (AC-TX2) — we do not roll back; the exception propagates.

    ``shared`` is the trust flag for the destination vault (0 = own/trusted, 1 =
    untrusted/shared). The caller (the CLI) computes it from
    ``vault_config.is_shared(resolved_vault)`` when a config is present, else 0.

    Returns the vault-relative ``RecordId``.
    """
    # 1 — validation (pure; never raises).
    result = record_model.validate(sidecar, kind=location.kind)
    if result.errors:
        raise RecordValidationError(list(result.errors))
    normalized = result.sidecar

    # 2 — provenance is required and cannot be defaulted (KU4).
    email = resolve_committer_email()
    if not email:
        raise ProvenanceError(
            "set git config user.email; *-by provenance is required and "
            "cannot be defaulted"
        )

    # 3 — stamp provenance. created-* set once; updated-* re-stamped each write.
    # "Set once" means: preserve the *original* created-* across rewrites. The
    # caller's in-memory sidecar may not carry it (a fresh edit), so the prior
    # value is recovered from the existing on-disk sidecar when present.
    now = _now_utc_z()
    stamped = dict(normalized)
    prior = _read_existing_provenance(location.sidecar_path)
    stamped["created-at"] = stamped.get("created-at") or prior.get("created-at") or now
    stamped["created-by"] = stamped.get("created-by") or prior.get("created-by") or email
    stamped["updated-at"] = now
    stamped["updated-by"] = email

    # 4 — fence neutralization (structural backstop).
    safe_body = neutralize_fences(body)

    # 5 — durable text first (atomic). Body before sidecar; both atomic.
    # Compact format: single-line, sorted keys, no trailing newline — stable bytes for
    # diff/grep and later slice round-trip asserts.
    sidecar_text = json.dumps(stamped, sort_keys=True, separators=(",", ":"))
    write_temp_then_rename(location.body_path, safe_body)
    write_temp_then_rename(location.sidecar_path, sidecar_text)

    # 6 — index last; on failure the text already won (AC-TX2 — no rollback).
    # ``shared`` is the resolved vault's trust flag (S4): default 0 (own/trusted)
    # preserves vanilla; the config-driven create path passes 1 for a shared vault.
    update_index(
        conn, location.record_id, stamped, safe_body, location.vault_root,
        shared=shared,
    )

    return location.record_id


# ---------------------------------------------------------------------------
# move_record — safe relocation
# ---------------------------------------------------------------------------


def move_record(
    old_id: RecordId,
    new_location: RecordLocation,
    conn,
    old_vault_root: str | None = None,
) -> RecordId:
    """Relocate a record to a new vault/path (AC-LIB3 / AC12).

    Order (the *safe* direction — council/Reliability): **copy-new →
    index-repoint → delete-old**. A crash after the repoint but before the delete
    leaves a stranded old artifact whose ID the index no longer resolves — no data
    loss (the new copy is durable + indexed), self-healing via ``lore reindex``.

    Reads the old body+sidecar, writes them atomically under *new_location*,
    repoints the index (delete old row → upsert new row), then deletes the old
    artifacts. Returns the new ``RecordId``.

    ``old_id`` is confined via :func:`_confine_record_id` so a direct library
    caller (e.g. S7 migration) cannot read/unlink ``.md``/``.json`` files outside
    the source vault — every RECORD_ID-bearing op is guarded at the boundary.
    """
    old_root = old_vault_root if old_vault_root is not None else vault_mod.resolve_vault()
    old_kind, old_name, old_body_path, old_sidecar_path = _confine_record_id(
        old_id, old_root
    )

    if not old_body_path.exists() and not old_sidecar_path.exists():
        raise RecordNotFoundError(f"record not found: {old_id}")

    body = old_body_path.read_text(encoding="utf-8") if old_body_path.exists() else ""
    sidecar_text = (
        old_sidecar_path.read_text(encoding="utf-8")
        if old_sidecar_path.exists()
        else "{}"
    )
    sidecar = json.loads(sidecar_text)

    # copy-new (atomic).
    write_temp_then_rename(new_location.body_path, body)
    write_temp_then_rename(new_location.sidecar_path, sidecar_text)

    # index-repoint: drop the old keyed row, upsert the new one.
    index_store.delete_row(conn, old_root, old_kind, old_name)
    update_index(conn, new_location.record_id, sidecar, body, new_location.vault_root)

    # delete-old (last — a crash here is the safe, self-healing direction).
    if old_body_path.exists():
        old_body_path.unlink()
    if old_sidecar_path.exists():
        old_sidecar_path.unlink()

    return new_location.record_id


# ---------------------------------------------------------------------------
# delete_record
# ---------------------------------------------------------------------------


def delete_record(
    record_id: RecordId,
    conn,
    vault_root: str | None = None,
) -> None:
    """Remove a record's body+sidecar+index row in one op (AC13).

    A missing ID (neither artifact on disk) → :class:`RecordNotFoundError`.
    """
    root = vault_root if vault_root is not None else vault_mod.resolve_vault()
    kind, name, body_path, sidecar_path = _confine_record_id(record_id, root)

    if not body_path.exists() and not sidecar_path.exists():
        raise RecordNotFoundError(f"record not found: {record_id}")

    if body_path.exists():
        body_path.unlink()
    if sidecar_path.exists():
        sidecar_path.unlink()
    index_store.delete_row(conn, root, kind, name)
