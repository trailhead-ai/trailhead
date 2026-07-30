"""Reusable record write library for the lore vault.

This module is the **single importable write API** into the vault — no argv/stdout
coupling, so a bulk migration calls it per-record exactly as the
``lore record`` CLI does. Every write keeps three artifacts consistent:

  - ``<vault>/<kind>/<name>.md``   — the markdown body, verbatim (modulo fence
    neutralization).
  - ``<vault>/<kind>/<name>.json`` — the JSON sidecar, pretty-printed + sorted-key.
  - one derived SQLite index row    — a cached projection (``index_store``).

**Text-wins / index-derived.** The
git-tracked text files are the source of truth; the index is a derived projection
that ``lore reindex`` rebuilds. On any partial failure the text on disk wins — we
never roll back a durable body/sidecar to satisfy the index. So
``validate_and_write`` writes the text *first* (atomically), then updates the
index; if the index update raises, the text is already durable and the exception
propagates with the text intact (the caller's ``reindex`` reconciles).

**Validation lives in ``record_model``, not here.** ``validate_and_write`` calls the pure
``record_model.validate`` (it returns ``(normalized, errors)`` and never raises).
On non-empty ``errors`` we raise :class:`RecordValidationError` carrying those
messages and write nothing. We do not re-implement validation.

**Provenance posture.** ``created-by``/``updated-by`` are
written from :func:`resolve_committer_email` — ``$LORE_EMAIL`` then
``git config --global user.email`` — which is **deterministic, not cwd-dependent**
(a repo-local ``user.email`` override cannot change the stamped value). An empty
email is a **hard error** (:class:`ProvenanceError`): we write nothing,
because a placeholder would silently pollute provenance. ``*-by`` is
**self-asserted, spoofable provenance PII** — it is written
for humans and **nothing keys trust on it**; it is never an authz/authn signal.

**Atomic write.** Bodies and sidecars land via
:func:`write_temp_then_rename` — write a sibling temp file, ``fsync``, then
``os.replace`` it onto the target. A crash before the rename leaves only the temp
file (or nothing), never a half-written target. ``os.replace`` always succeeds by
clobbering, which is exactly what an update/move needs but never what a
NEW record should tolerate. ``validate_and_write(require_new=True)`` instead
uses :func:`write_temp_then_create_exclusive` — the same temp-write-then-fsync,
but claims the target via ``os.link`` (atomically create-only, raising
``FileExistsError`` rather than clobbering) — the exclusivity guard behind
adr sequence-numbered creates (``cli/record.py``'s ``--kind adr`` branch): a
losing concurrent writer gets a named :class:`RecordAlreadyExistsError`
instead of silently overwriting the winner. For adr the contended resource is
the sequence NUMBER rather than the stem, so that per-artifact claim sits inside
a number-scoped one (:func:`adr_number_claim`, an ``fcntl.flock`` on a canonical
per-number sidecar) — without it, two creates that computed the same number from
different titles claim different stems and both succeed.

**Fence neutralization.** The injection fence token is the literal
``<external-memory …>`` / ``</external-memory>`` pair (the output-wrapping
contract). Write-time neutralization is the structural backstop: a body can never
land a *live* fence on disk, even via a future ``--diff`` path.

**Move safety.** :func:`move_record` copies the new artifacts →
repoints the index → deletes the old copy **in that order**. A crash after the
index repoint but before the old delete leaves a stranded old artifact whose ID
the index no longer resolves — the *safe* direction (no data loss; the new copy is
durable and indexed), self-healing via ``lore reindex``. Callers holding the old
ID must re-read.

Pure stdlib (``json``, ``sqlite3`` via ``index_store``, ``os``/``pathlib``,
``subprocess`` for git identity, ``datetime``, ``fcntl`` for the adr
sequence-number lock).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, NamedTuple, Optional

from ..search import index as index_store
from ..vault import vault as vault_mod
from . import model as record_model

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# The record ID is the vault-relative ``<kind>/<name>`` path. Vault
# qualification (a vault token prefix) is a future multi-vault concern; today the
# active vault is implicit, so a plain str suffices.
RecordId = str


class RecordLocation(NamedTuple):
    """A resolved write target: the vault root, kind, slugged name, and ID.

    ``record_id`` is the vault-relative ``<kind>/<name>``. ``body_path`` and
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
    """Empty committer email — provenance is required and cannot be defaulted."""


class RecordValidationError(RecordStoreError):
    """Sidecar failed validation; carries the ordered validation error messages."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors) if errors else "validation failed")


class RecordNotFoundError(RecordStoreError):
    """The record ID does not resolve to an on-disk record."""


class RecordAlreadyExistsError(RecordStoreError):
    """A ``require_new=True`` write found its target already occupied.

    Raised by :func:`validate_and_write`'s exclusive-create path (today: adr
    sequence-numbered creates) when either claim loses — a concurrent writer,
    or a pre-existing stray file, already holds the resource:

      - the **sequence number** (:func:`adr_number_claim`): another writer holds
        the number's claim, or an ``adr-<n>-*`` record already carries it under
        some other title.
      - the **stem** (:func:`_write_new_artifacts`): ``location.body_path`` or
        ``location.sidecar_path`` already exists.

    Nothing from THIS call is left written in either case: an exclusive claim
    never touches an occupied target, and a body claimed just before a losing
    sidecar claim is rolled back before this raises.
    """


class InvalidRecordIdError(RecordStoreError):
    """The RECORD_ID is malformed or would escape the vault root.

    A RECORD_ID is a vault-relative ``<kind>/<name>`` path. Any ``..`` segment,
    absolute component, NUL byte, or empty part is rejected here — the same
    confinement enforced for ``blob`` paths, applied symmetrically to every
    RECORD_ID-bearing op (``update``/``delete``) so a crafted ID cannot read,
    overwrite, or unlink ``.md``/``.json`` files outside the active vault.
    """


class DiffRejectError(RecordStoreError):
    """A unified diff is valid format but its context doesn't match the body.

    A *stale* diff: the structure parses, but one or more hunks' context/deletion
    lines do not match the current body verbatim (so the diff was generated from a
    different version of the body). On reject the on-disk body is byte-for-byte
    unchanged and NO index update happens.

    Attributes:
        original_body: the body exactly as received — byte-for-byte unmodified.
        rejected: list of ``(header, reason)`` pairs for each failing hunk, in a
            stable order, for the CLI's parseable one-line-per-hunk stderr output.
    """

    def __init__(self, original_body: str, rejected: list[tuple[str, str]]) -> None:
        self.original_body = original_body
        self.rejected = rejected
        super().__init__(
            f"{len(rejected)} hunk(s) rejected: " + "; ".join(f"{h}: {r}" for h, r in rejected)
        )


class DiffFormatError(RecordStoreError):
    """A unified diff string is structurally unparseable.

    Distinct from :class:`DiffRejectError` (valid format, stale context). Two known
    triggers:

      - ``difflib.unified_diff``'s **concatenated-no-newline** edge case: when BOTH
        the deleted and the inserted line lack a trailing newline it emits
        ``-old+new`` with no separator, and the embedded ``+`` is indistinguishable
        from content. The applier detects this via a hunk-count deficit and raises
        rather than guessing. Unreachable for well-formed lore bodies —
        :func:`write_temp_then_rename` always trailing-newlines — but handled
        safely.
      - A **bare hunk header** — a line starting with ``@@`` that does not carry
        the ``-old_start,old_count +new_start,new_count`` line ranges (e.g. a
        hand-authored or LLM-authored diff that omits them). Without a range the
        header fails :data:`_HUNK_HEADER_RE` and would otherwise be silently
        dropped by :func:`_parse_hunks` — producing a diff that parses to ZERO
        hunks and "applies" as a **silent no-op**: the caller sees the normal
        success exit and record ID with nothing actually written. Rejecting here
        turns that data-loss footgun into an explicit, named error instead.
    """


# ---------------------------------------------------------------------------
# Unified-diff applier (pure-stdlib decision rule)
# ---------------------------------------------------------------------------

# The two-phase applier: Phase 1 verifies EVERY hunk's context+deletion lines
# verbatim against ``body.splitlines(keepends=True)`` (verbatim comparison
# auto-detects CRLF-vs-LF and trailing-newline mismatches — the core safety
# invariant); if ANY hunk fails it raises :class:`DiffRejectError` with the body
# unmodified and runs NO Phase 2. Phase 2 (only if all hunks verified) applies in
# order tracking ``offset = Σ(new_count − old_count)`` of prior hunks so each hunk
# indexes the evolving result correctly. Verified against three adversarial cases
# (CRLF body, trailing-newline mismatch, adjacent hunks).

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class _Hunk(NamedTuple):
    """A single parsed unified-diff hunk (header + body lines with endings kept)."""

    header: str  # raw ``@@ -L,N +L,M @@`` line (for error reporting)
    old_start: int  # 1-based line number in the original body
    old_count: int
    new_count: int
    lines: list[str]  # hunk lines WITH leading marker AND original line ending


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
    edge case (via :func:`_validate_hunk_counts`) and on a bare hunk header — see
    :class:`DiffFormatError` for both triggers.
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
        elif raw_stripped.startswith("@@"):
            raise DiffFormatError(
                f"hunk header missing line ranges: {raw_stripped!r} — expected "
                f"'@@ -old_start,old_count +new_start,new_count @@'. A bare '@@' "
                f"cannot be located in the body and would be silently dropped "
                f"rather than applied; regenerate the diff with explicit line "
                f"ranges."
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
    """Apply a unified *diff* to *body*. Returns ``(new_body, [])``.

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
            rejected.append(
                (
                    hunk.header,
                    f"context overruns body (body has {len(body_lines)} lines, "
                    f"hunk starts at line {hunk.old_start} and expects "
                    f"{len(ctx_lines)} context/deletion lines)",
                )
            )
            continue

        mismatch_line: Optional[int] = None
        for i, expected in enumerate(ctx_lines):
            if body_lines[start_0 + i] != expected:
                mismatch_line = hunk.old_start + i
                break
        if mismatch_line is not None:
            rejected.append(
                (
                    hunk.header,
                    f"context mismatch at body line {mismatch_line}",
                )
            )

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
        result_lines[start_0 : start_0 + len(old_slice)] = new_slice
        offset += len(new_slice) - len(old_slice)

    return "".join(result_lines), []


# ---------------------------------------------------------------------------
# Provenance + helpers
# ---------------------------------------------------------------------------

# The injection fence pair (output-wrapping contract). Matched
# case-insensitively across the open/close tokens, attributes tolerated; the
# captured ``external`` group is rewritten in place so a mixed-case
# ``<External-Memory>`` is neutralized too (the literal spelling is lowercase, but
# the structural backstop tolerates no case variant).
_FENCE_RE = re.compile(r"</?\s*(external)(-memory)\b[^>]*>", re.IGNORECASE)
# Zero-width word joiner inserted between ``external`` and ``-memory`` so the token
# is not a parseable fence but remains human-legible.
_JOINER = "⁠"


def resolve_committer_email() -> str:
    """Return the committer email for ``*-by`` provenance (delegates to vault_mod).

    Deterministic source (``$LORE_EMAIL`` → ``git config --global user.email``),
    cwd-independent; empty when unset. See
    :func:`vault.resolve_committer_email`. Exposed here so ``validate_and_write``
    calls a patchable module-level seam.
    """
    return vault_mod.resolve_committer_email()


def _active_vault_root() -> str:
    """Resolve the active vault root from config, as a ``str``.

    Function-local import of ``vault_config`` (which imports ``layers`` +
    ``record_model``) keeps this module free of the ``vault ↔ vault_config``
    module-load cycle — ``vault.py`` stays pure-stdlib (Axiom 6).
    """
    from ..vault import config as vault_config
    return str(vault_config.resolve_active_vault())


def neutralize_fences(text: str) -> str:
    """Neutralize ``<external-memory>`` / ``</external-memory>`` fence tokens.

    Replaces any open/close ``external-memory`` tag with a non-parseable but
    legible form, so a stored body can never reconstruct a *live* fence. Idempotent
    enough for the contract: the output contains no fence token.
    """
    return _FENCE_RE.sub(
        lambda m: m.group(0).replace(m.group(1) + m.group(2), m.group(1) + _JOINER + m.group(2), 1),
        text,
    )


def write_temp_then_rename(path: Path, text: str) -> None:
    """Atomically write *text* to *path* via a sibling temp file + ``os.replace``.

    Writes ``<path>.<pid>.tmp``, ``fsync``s it, then renames it onto the target.
    A crash before the rename leaves only the temp file (or nothing) — never a
    half-written target. Cleans up the temp file on any failure.
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


def write_temp_then_create_exclusive(path: Path, text: str) -> None:
    """Atomically write *text* to *path* IFF *path* does not already exist.

    The create-only sibling of :func:`write_temp_then_rename`. Writes a
    sibling temp file (name-uniqued with a UUID, not just the PID, so two
    threads in the same process never collide on the temp name), ``fsync``s
    it, then claims *path* via ``os.link`` — a single atomic syscall that
    creates the hard link only if the target name is free, raising
    ``FileExistsError`` when it is not. Unlike ``os.replace`` (used by
    :func:`write_temp_then_rename`, which always succeeds by silently
    clobbering an existing target), a losing concurrent writer gets a clean,
    detectable failure instead of overwriting the winner. The temp file is
    always removed; on a collision *path* is left completely untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


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
        k: existing[k] for k in ("created-at", "created-by") if isinstance(existing.get(k), str)
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


#: Matches the sequence number at the front of an adr stem (e.g.
#: ``adr-007-some-decision`` → ``"007"``). Zero-padding is cosmetic here —
#: ``int()`` tolerates the leading zeros. The number may be followed by a
#: hyphen (the common case) or by the end of the stem — a kebab-empty title
#: (all-punctuation / non-Latin) falls back through ``_kebab`` to a bare
#: ``adr-<n>`` with no trailing hyphen, and that stem must still be visible to
#: the number scan and claim, not silently invisible to both.
_ADR_STEM_NUMBER_RE = re.compile(r"^adr-(\d+)(?:-|$)")

#: Strips a user-supplied numbered prefix (``"ADR-9: "``, any case, any
#: digit count) from a raw title before the CLI's own ``ADR-NNN:`` prefix is
#: applied — the override is deliberate, not a merge (see
#: :func:`format_adr_title`).
_ADR_TITLE_PREFIX_RE = re.compile(r"^adr-\d+:\s*", re.IGNORECASE)


def _numbered_adr_artifacts(kind_dir: Path) -> Iterator[tuple[int, Path]]:
    """Yield ``(number, path)`` for every numbered adr artifact in *kind_dir*.

    **Both stems count.** A crash between the body claim and the sidecar claim
    can leave an orphaned ``.json`` (or, after a manual body deletion, an
    orphaned ``.md``); either one still occupies its sequence number, so a scan
    that looked only at ``.md`` would hand that number out again and the write
    would then refuse on a collision the scan could have seen. Same pair-aware
    rule as :func:`_stem_occupied`, applied per NUMBER instead of per stem.

    A missing ``kind_dir`` yields nothing. Zero-padding is normalized away by
    ``int()``, so ``adr-1-x`` and ``adr-001-x`` both report number 1.
    """
    kind_dir = Path(kind_dir)
    if not kind_dir.is_dir():
        return
    for pattern in ("*.md", "*.json"):
        for path in kind_dir.glob(pattern):
            match = _ADR_STEM_NUMBER_RE.match(path.stem)
            if match:
                yield int(match.group(1)), path


def next_adr_number(kind_dir: Path) -> int:
    """Return the next per-vault adr sequence number: highest existing + 1.

    Scans ``kind_dir`` (the vault's ``adr/`` directory) for stems matching
    ``^adr-(\\d+)-`` (:func:`_numbered_adr_artifacts` — both the ``.md`` and the
    ``.json`` half) and returns ``max(numbers) + 1``, or ``1`` when the
    directory is missing or holds no numbered adr. A dropped/superseded number
    is never reused — the scan only ever looks at what currently exists on
    disk, so a gap (e.g. ``adr-003`` deleted) stays a gap.

    This picks a *candidate* only; it is not the collision guard. Two writers
    scanning concurrently both see the same highest number, so the atomic
    refusal lives at write time (:func:`adr_number_claim`).
    """
    return max((number for number, _ in _numbered_adr_artifacts(kind_dir)), default=0) + 1


def _adr_number_occupied(kind_dir: Path, number: int) -> bool:
    """Whether any ``adr-<number>-*`` artifact already exists in *kind_dir*.

    Number-scoped, not stem-scoped: ``adr-001-decision-a.md`` occupies number 1
    against a write of ``adr-001-decision-b`` — the sequence number is the
    identity being reserved, and a title cannot make a second ADR-001 legal.
    """
    return any(found == number for found, _ in _numbered_adr_artifacts(kind_dir))


def _adr_lock_path(kind_dir: Path, number: int) -> Path:
    """The canonical per-number lock path two racing adr writers contend on.

    Derived from the number alone (no zero-padding, no title), so every writer
    that computed the same number contends on the same single path whatever its
    title. ``.lock``-suffixed to match the sidecar-lock convention the session
    capture path already uses (``session/<key>.lock``) — and, more concretely,
    because a freshly-initialized vault's ``.gitignore`` ships ``*.lock``, so
    ``lore sync``'s catch-all ``git add -A`` never commits it. Dotted so it also
    stays out of a plain directory listing.
    """
    return Path(kind_dir) / f".adr-{number}.lock"


@contextmanager
def adr_number_claim(kind_dir: Path, number: int) -> Iterator[Path]:
    """Hold the exclusive claim on adr sequence *number* for a write's duration.

    The **number-scoped** half of the adr create guard, and the only part of it
    that serializes anything. The stem-scoped claims in
    :func:`_write_new_artifacts` cannot separate two concurrent creates that
    computed the same number from DIFFERENT titles: their stems differ, so both
    claims succeed and two records ship carrying the same number. A post-write
    re-scan cannot repair that either — each writer would see the other and
    both could refuse, or neither. So the contended resource is made the number
    itself:

      1. Take ``fcntl.flock`` LOCK_EX on :func:`_adr_lock_path` — the same
         sidecar-lock primitive the session capture path uses. Exactly one
         writer holds a given number at a time; the other blocks here.
      2. **Inside** the lock, check number occupancy
         (:func:`_adr_number_occupied`): an existing ``adr-<number>-*`` record
         refuses whatever title it carries, via
         :class:`RecordAlreadyExistsError`, with nothing written.

    Together those make the two-writer outcome deterministic rather than
    interleaving-dependent: the winner writes its artifacts inside the lock, so
    the loser's occupancy check — which cannot run until the winner releases —
    always sees them and always refuses.

    The lock is an empty sidecar, never a record: it is opened ``"a"`` (never
    truncated, so it is safe to reuse), released via LOCK_UN + close on every
    exit path, and left on disk deliberately. The kernel drops a held flock when
    the fd closes — including on a crash — so unlike an unlink-on-exit claim
    artifact, an interrupted write can never strand a lock that wedges its
    number. It is also, deliberately, not counted by :func:`next_adr_number`: a
    lock records contention, not a record's existence.
    """
    kind_dir = Path(kind_dir)
    kind_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _adr_lock_path(kind_dir, number)
    lock_fd = open(lock_path, "a")  # create-or-open, no truncate; held until unlock
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)  # blocks until exclusive
        if _adr_number_occupied(kind_dir, number):
            raise RecordAlreadyExistsError(
                f"an adr already carries number {number} in {str(kind_dir)!r} — "
                f"refusing to issue that number twice"
            )
        yield lock_path
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def format_adr_title(number: int, title: str) -> str:
    """Return *title* rewritten as ``"ADR-NNN: <title>"`` (zero-padded to 3).

    Any existing ``"ADR-<digits>: "`` prefix on *title* is stripped first —
    a user-supplied already-numbered title (e.g. ``"ADR-9: foo"``) has its
    number overridden by *number*, deliberately: the CLI's per-vault sequence
    always wins over an operator-typed number, which cannot itself be
    verified collision-free.
    """
    stripped = _ADR_TITLE_PREFIX_RE.sub("", title, count=1)
    return f"ADR-{number:03d}: {stripped}"


def _stem_occupied(kind_dir: Path, stem: str) -> bool:
    """A stem is occupied if EITHER ``<stem>.md`` or ``<stem>.json`` exists.

    The pair-aware occupancy check: a crash can leave an orphaned ``<stem>.json``
    with no ``.md``; treating the slot as free on ``.md``-absence alone would
    silently overwrite that orphan.
    """
    return (kind_dir / f"{stem}.md").exists() or (kind_dir / f"{stem}.json").exists()


def _unique_stem(kind_dir: Path, base: str) -> str:
    """Return ``base`` (or ``base-2``/``base-3``/…) for the first free stem.

    Pair-aware: checks both artifacts (:func:`_stem_occupied`), not just ``.md``.
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
    """Validate a ``<kind>/<name>`` RECORD_ID and resolve its confined paths.

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
            raise InvalidRecordIdError(f"RECORD_ID resolves outside the vault root: {record_id!r}")
    return kind, name, body_path, sidecar_path


def confine_record_id(record_id: RecordId, root: str) -> tuple[str, str, Path, Path]:
    """Public alias for :func:`_confine_record_id` — the module's confinement seam.

    Exposed so callers outside this module (e.g. the CLI's ``--parent``/
    ``--depends-on`` edge-reference guard) validate a candidate RECORD_ID against
    the SAME confinement rule as every in-module caller, without reaching into a
    private, underscore-prefixed name.
    """
    return _confine_record_id(record_id, root)


# ---------------------------------------------------------------------------
# place_record
# ---------------------------------------------------------------------------


def place_record(
    name: str,
    kind: str,
    scope: str | None,
    vault_root: str | None = None,
) -> RecordLocation:
    """Resolve the on-disk target for a new record (naming + collision).

    Resolves the target vault (``vault_root`` when given, else the config-resolved
    active vault via :func:`_active_vault_root` — the multi-vault eligibility
    hook). The vault-relative name is ``_kebab(name)`` with a ``-2``/``-3``
    collision suffix, **except**: ``session`` kind, whose name is the
    ``session_id`` GUID **verbatim** (no slug, no suffix); and ``adr`` kind,
    whose stem is ``_kebab(name)`` **without** a suffix — the caller (``lore
    record create``) has already rewritten ``name`` into its numbered
    ``"ADR-NNN: <title>"`` form, and a colliding sequence NUMBER must surface as
    a refusal at write time (:func:`validate_and_write`'s ``require_new`` path),
    never be silently papered over by a suffix — a suffixed ``adr-001-x-2``
    would be a second record carrying number 1. Collision occupancy for every
    other kind checks both the ``.md`` and ``.json`` stem.

    ``scope`` is accepted for the multi-vault routing hook; it is currently unused.
    Returns a :class:`RecordLocation` whose ``record_id`` is ``<kind>/<name>``.
    """
    root = vault_root if vault_root is not None else _active_vault_root()
    kind_dir = Path(root) / kind

    if kind == "session":
        # The GUID is the identity; never slugged, never suffixed.
        stem = name
    elif kind == "adr":
        # Already-numbered by the caller; a collision refuses, it never suffixes.
        stem = _kebab(name)
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
    """Resolve an **existing** ``<kind>/<name>`` record to its on-disk location.

    Unlike :func:`place_record`, this does NOT slug or apply a collision suffix —
    it points at the record's existing ``.md``/``.json`` pair so an update writes
    in place (preserving the ID). Raises :class:`RecordNotFoundError` when neither
    artifact exists.
    """
    root = vault_root if vault_root is not None else _active_vault_root()
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

    The write seam the FTS5/BM25 search builds on. *record_id* is ``<kind>/<name>``;
    the index is keyed ``(vault, kind, name)``.

    ``shared`` is the trust flag stamped on the row (0 = own/trusted, unfenced by
    ``search``; 1 = untrusted/shared, fenced). It **defaults to 0** so the vanilla
    no-config write path keeps stamping trusted rows. The
    config-driven create path passes ``shared=1`` when the resolved destination is a
    ``shared: true`` vault (``vault_config.is_shared``), so a record routed into a
    shared vault is fenced correctly — the trust source now matches the vault, not a
    blanket "CLI writes are always own" assumption.
    """
    kind, name = record_id.split("/", 1)
    index_store.upsert_row(conn, vault_root, kind, name, sidecar, body, shared=shared)


@contextmanager
def index_transaction():
    """Open the search index, yield the connection, always close it on exit.

    The shared open/close wrapper for the ``record create``/``update``/``delete``
    handlers: it owns only the resource lifetime (open on enter, ``close()`` in
    ``finally``). Commit stays the caller's responsibility — the handler calls
    ``conn.commit()`` on its success path, so an early return on a guard rejection
    or a raised store error skips the commit and leaves the index unchanged, while
    the connection is still closed unconditionally.
    """
    conn = index_store.open_index()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# validate_and_write — the transactional write primitive
# ---------------------------------------------------------------------------


def validate_stamp_neutralize(
    location: RecordLocation,
    sidecar: dict[str, Any],
    body: str,
) -> tuple[dict[str, Any], str]:
    """Validate + provenance-stamp the sidecar and neutralize the body's fences.

    The shared pre-write step factored out of :func:`validate_and_write`
    so the in-place write path and the override-move write path stamp + neutralize
    **identically**: :func:`move_record` writes its overrides
    VERBATIM (it does NOT stamp or neutralize), so the auto-move update path runs
    this first and hands the result to ``move_record``. Steps:

      1. Validate via ``record_model.validate``; non-empty errors →
         :class:`RecordValidationError`, returns nothing.
      2. Resolve the committer email; empty → :class:`ProvenanceError`.
      3. Stamp provenance on the (normalized) sidecar: ``created-at``/``-by`` set
         once (preserved if already present on rewrite, recovered from the on-disk
         sidecar at ``location`` otherwise), ``updated-at``/``-by`` re-stamped.
      4. Neutralize ``<external-memory>`` fences in the body.

    Returns ``(stamped_sidecar, safe_body)``.
    """
    # 1 — validation (pure; never raises).
    result = record_model.validate(sidecar, kind=location.kind)
    if result.errors:
        raise RecordValidationError(list(result.errors))
    normalized = result.sidecar

    # 2 — provenance is required and cannot be defaulted.
    email = resolve_committer_email()
    if not email:
        raise ProvenanceError(
            "set git config user.email; *-by provenance is required and cannot be defaulted"
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
    return stamped, safe_body


def _number_claim_for(location: RecordLocation):
    """Return the number-scoped claim context for *location*, else a no-op.

    The bridge between the generic ``require_new`` write path and the adr
    sequence-number guard. The number is derived from the resolved stem rather
    than passed in as an argument, deliberately: any caller writing an
    ``adr-<n>-*`` stem exclusively is claiming that number and cannot forget to
    say so. Every other kind (and any unnumbered adr stem) gets
    :func:`contextlib.nullcontext` — number scoping means nothing there, and the
    stem-scoped claims alone remain the guard.
    """
    match = _ADR_STEM_NUMBER_RE.match(location.name)
    if location.kind != "adr" or match is None:
        return nullcontext()
    return adr_number_claim(location.body_path.parent, int(match.group(1)))


def _write_new_artifacts(location: RecordLocation, safe_body: str, sidecar_text: str) -> None:
    """Claim both stem artifacts exclusively, or leave nothing behind.

    The stem-scoped half of the create guard: each artifact lands via
    :func:`write_temp_then_create_exclusive`, so an occupied target raises
    rather than clobbering. The body is claimed first; if the sidecar claim then
    loses (an orphan ``.json`` with no matching ``.md`` — the same edge
    :func:`_stem_occupied` guards elsewhere), the just-claimed body is unlinked
    before the error propagates, so "nothing written on refusal" holds even for
    that partial-claim case.
    """
    try:
        write_temp_then_create_exclusive(location.body_path, safe_body)
    except FileExistsError as exc:
        raise RecordAlreadyExistsError(
            f"a record already exists at {location.record_id!r} — refusing to overwrite it"
        ) from exc
    try:
        write_temp_then_create_exclusive(location.sidecar_path, sidecar_text)
    except FileExistsError as exc:
        location.body_path.unlink(missing_ok=True)
        raise RecordAlreadyExistsError(
            f"a record already exists at {location.record_id!r} — refusing to overwrite it"
        ) from exc


def validate_and_write(
    location: RecordLocation,
    sidecar: dict[str, Any],
    body: str,
    conn,
    shared: int = 0,
    require_new: bool = False,
) -> RecordId:
    """Validate, stamp provenance, and durably write a record.

    Pipeline (text-wins / index-derived):
      1-4. Validate + stamp provenance + neutralize fences via
         :func:`validate_stamp_neutralize` (the shared pre-write step). Non-empty
         validation errors → :class:`RecordValidationError`, an empty committer
         email → :class:`ProvenanceError` — **nothing written** in either case.
      5. Atomically write body then sidecar — via ``write_temp_then_rename``
         (default: always succeeds, clobbering any existing target — the
         update/move semantics every other kind relies on) or, when
         ``require_new`` is set, via :func:`_write_new_artifacts` under
         :func:`_number_claim_for` (never clobbers; a losing race raises
         :class:`RecordAlreadyExistsError` instead).
      6. Update the index with the resolved vault's ``shared`` trust flag
         (default 0/own preserves vanilla). **If this raises, the text is already
         durable and wins** — we do not roll back; the exception propagates.

    ``shared`` is the trust flag for the destination vault (0 = own/trusted, 1 =
    untrusted/shared). The caller (the CLI) computes it from
    ``vault_config.is_shared(resolved_vault)`` when a config is present, else 0.

    ``require_new`` is the create-time exclusivity guard (today: adr
    sequence-numbered creates — see ``cli/record.py``'s ``--kind adr`` branch).
    It must NEVER be set on an update: an in-place update's whole point is to
    write over the record's own existing artifacts, which ``require_new``
    would refuse as a collision against itself. It layers two claims, both
    raising :class:`RecordAlreadyExistsError` with nothing written:

      - **number-scoped** (:func:`adr_number_claim`, adr stems only) — the
        atomic one. Exactly one concurrent writer can hold a given ADR number,
        so two creates that computed the same number from different titles
        cannot both ship.
      - **stem-scoped** (:func:`_write_new_artifacts`) — the per-artifact
        create-only claim, which also covers every non-adr ``require_new``
        caller, where number scoping does not apply.

    Returns the vault-relative ``RecordId``.
    """
    stamped, safe_body = validate_stamp_neutralize(location, sidecar, body)

    # 5 — durable text first (atomic). Body before sidecar; both atomic.
    # Compact format: single-line, sorted keys, no trailing newline — stable bytes for
    # diff/grep and round-trip asserts.
    sidecar_text = json.dumps(stamped, sort_keys=True, separators=(",", ":"))
    if require_new:
        with _number_claim_for(location):
            _write_new_artifacts(location, safe_body, sidecar_text)
    else:
        write_temp_then_rename(location.body_path, safe_body)
        write_temp_then_rename(location.sidecar_path, sidecar_text)

    # 6 — index last; on failure the text already won (no rollback).
    # ``shared`` is the resolved vault's trust flag: default 0 (own/trusted)
    # preserves vanilla; the config-driven create path passes 1 for a shared vault.
    update_index(
        conn,
        location.record_id,
        stamped,
        safe_body,
        location.vault_root,
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
    new_sidecar: dict | None = None,
    new_body: str | None = None,
    shared: int = 0,
) -> RecordId:
    """Relocate a record to a new vault/path.

    Order (the *safe* direction): **copy-new →
    index-repoint → delete-old**. A crash after the repoint but before the delete
    leaves a stranded old artifact whose ID the index no longer resolves — no data
    loss (the new copy is durable + indexed), self-healing via ``lore reindex``.

    By default the old body+sidecar are read verbatim and written atomically under
    *new_location*. **In-memory overrides** — ``new_sidecar`` /
    ``new_body`` — write the *already-mutated, validated, provenance-stamped* record
    AT the destination instead of re-reading the old disk. This is the
    single-durable-write-at-destination requirement: a
    scope-changing ``record update`` stamps + validates the mutated sidecar in
    memory, then moves it here, so the mutated sidecar (e.g. ``team: beta``) is
    NEVER written at the old location and then relocated. The overrides are written
    VERBATIM — provenance stamping and fence neutralization live in
    :func:`validate_and_write` (and its shared helper), so the caller MUST have
    already stamped/neutralized before passing them here.

    Both endpoints are confined at the library boundary: ``old_id`` via
    :func:`_confine_record_id` (a direct caller cannot read/unlink ``.md``/``.json``
    outside the source vault), and *new_location*'s paths via
    :func:`_realpath_is_descendant` against the declared dest vault root, so a
    destination that escapes its vault root is rejected before any write.

    ``shared`` is the destination vault's trust flag (0 = own/trusted, 1 =
    ``shared: true``), stamped on the repointed index row so a relocation into a
    shared vault fences the moved record correctly — the caller computes it from
    ``vault_config.shared_flag(dest_vault)`` (symmetric with the create path).

    Returns the new ``RecordId``.
    """
    old_root = old_vault_root if old_vault_root is not None else _active_vault_root()
    old_kind, old_name, old_body_path, old_sidecar_path = _confine_record_id(old_id, old_root)

    # Dest-confinement: the destination paths must be descendants of the
    # declared dest vault root (mirrors the source-side _confine_record_id guard).
    dest_root_real = os.path.realpath(new_location.vault_root)
    for p in (new_location.body_path, new_location.sidecar_path):
        if not _realpath_is_descendant(p, dest_root_real):
            raise InvalidRecordIdError(
                f"destination resolves outside the dest vault root: {new_location.record_id!r}"
            )

    if not old_body_path.exists() and not old_sidecar_path.exists():
        raise RecordNotFoundError(f"record not found: {old_id}")

    # In-memory overrides write the already-mutated record at the destination;
    # otherwise the old disk is re-read verbatim.
    if new_body is not None:
        body = new_body
    else:
        body = old_body_path.read_text(encoding="utf-8") if old_body_path.exists() else ""
    if new_sidecar is not None:
        sidecar = new_sidecar
        sidecar_text = json.dumps(sidecar, sort_keys=True, separators=(",", ":"))
    else:
        sidecar_text = (
            old_sidecar_path.read_text(encoding="utf-8") if old_sidecar_path.exists() else "{}"
        )
        sidecar = json.loads(sidecar_text)

    # copy-new (atomic).
    write_temp_then_rename(new_location.body_path, body)
    write_temp_then_rename(new_location.sidecar_path, sidecar_text)

    # index-repoint: drop the old keyed row, upsert the new one. ``shared`` is the
    # destination vault's trust flag: a relocation INTO a ``shared: true``
    # vault must stamp the new row shared=1, not the default 0 — otherwise the moved
    # record leaks into ``search`` as own-vault until the next ``lore reindex``.
    index_store.delete_row(conn, old_root, old_kind, old_name)
    update_index(
        conn, new_location.record_id, sidecar, body, new_location.vault_root, shared=shared
    )

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
    """Remove a record's body+sidecar+index row in one op.

    A missing ID (neither artifact on disk) → :class:`RecordNotFoundError`.
    """
    root = vault_root if vault_root is not None else _active_vault_root()
    kind, name, body_path, sidecar_path = _confine_record_id(record_id, root)

    if not body_path.exists() and not sidecar_path.exists():
        raise RecordNotFoundError(f"record not found: {record_id}")

    if body_path.exists():
        body_path.unlink()
    if sidecar_path.exists():
        sidecar_path.unlink()
    index_store.delete_row(conn, root, kind, name)
