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
from typing import Any, NamedTuple

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


# ---------------------------------------------------------------------------
# update_index
# ---------------------------------------------------------------------------


def update_index(
    conn,
    record_id: RecordId,
    sidecar: dict[str, Any],
    body: str,
    vault_root: str,
) -> None:
    """Upsert the index row for *record_id* (thin pass-through to ``index_store``).

    The seam S3 enriches (FTS5/BM25). *record_id* is ``<kind>/<name>``; the index
    is keyed ``(vault, kind, name)``.
    """
    kind, name = record_id.split("/", 1)
    index_store.upsert_row(conn, vault_root, kind, name, sidecar, body)


# ---------------------------------------------------------------------------
# validate_and_write — the transactional write primitive
# ---------------------------------------------------------------------------


def validate_and_write(
    location: RecordLocation,
    sidecar: dict[str, Any],
    body: str,
    conn,
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
      6. Update the index. **If this raises, the text is already durable and
         wins** (AC-TX2) — we do not roll back; the exception propagates.

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
    sidecar_text = json.dumps(stamped, indent=2, sort_keys=True)
    write_temp_then_rename(location.body_path, safe_body)
    write_temp_then_rename(location.sidecar_path, sidecar_text)

    # 6 — index last; on failure the text already won (AC-TX2 — no rollback).
    update_index(conn, location.record_id, stamped, safe_body, location.vault_root)

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
    """
    old_kind, old_name = old_id.split("/", 1)
    old_root = old_vault_root if old_vault_root is not None else vault_mod.resolve_vault()
    old_body_path = Path(old_root) / old_kind / f"{old_name}.md"
    old_sidecar_path = Path(old_root) / old_kind / f"{old_name}.json"

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
    kind, name = record_id.split("/", 1)
    root = vault_root if vault_root is not None else vault_mod.resolve_vault()
    body_path = Path(root) / kind / f"{name}.md"
    sidecar_path = Path(root) / kind / f"{name}.json"

    if not body_path.exists() and not sidecar_path.exists():
        raise RecordNotFoundError(f"record not found: {record_id}")

    if body_path.exists():
        body_path.unlink()
    if sidecar_path.exists():
        sidecar_path.unlink()
    index_store.delete_row(conn, root, kind, name)
