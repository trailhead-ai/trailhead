"""Record rename + inbound-reference rewrite.

The library behind ``lore record rename``. A rename is two things, in this
order:

  1. **The primary rename.** The record's stem is re-derived from the new title
     (``_kebab`` + the standard ``-2``/``-3`` collision suffix), its sidecar
     ``title`` is set, and the record is relocated within its own vault via
     :func:`store.move_record` — copy-new → index-repoint → delete-old, so a
     crash mid-move never loses data.
  2. **The inbound-reference sweep.** Every other record in every configured
     vault is scanned for references to the *old* stem and rewritten to the new
     one. Three reference shapes are rewritten, and only these — informal
     shorthand (``[[adr-002]]`` pointing at ``adr-002-something``) is
     deliberately out of scope because it cannot be resolved mechanically:

       - bare wikilinks ``[[old-stem]]`` and kind-qualified
         ``[[<kind>/old-stem]]`` in record bodies (an ``|alias`` suffix is
         preserved),
       - ``related.<kind>`` sidecar list entries,
       - ``depends-on`` sidecar list entries and the ``parent`` sidecar string,
         when the renamed record is a ``task`` (leaving ``parent`` alone would
         dangle the renamed task's children),
       - ``depends-on`` sidecar list entries of the ``<kind>/<stem>[@stage]``
         design grammar, when the renamed record is a ``spec`` or ``adr``. The
         ``@stage`` tail is carried through byte-for-byte and an unqualified
         entry stays unqualified — a rename never introduces a stage.

**Trust.** ``shared: true`` vaults hold untrusted external content. The sweep
**skips** them by default and reports what it would have rewritten; the caller
opts in with ``include_shared``. The primary rename itself is never skipped —
the operator named that record explicitly.

**Vault scope, and why it differs per reference shape.** Wikilinks and
``related`` entries are rewritten in every configured vault: they are plain
cross-references, and a reference is only ever to the record it names. Task
edges likewise stay vault-wide, unchanged. Design ``depends-on`` edges do NOT:
they resolve vault-locally, so ``spec/foo`` in another vault names *that*
vault's ``spec/foo`` — a different record this rename says nothing about. The
design branch therefore fires only within the renamed record's own vault, which
is a security boundary and not merely a scoping nicety: other configured vaults
may be shared, and rewriting their graph edges would let content the operator
does not control steer writes by choosing a colliding stem. The source vault is
threaded from each :func:`rename_record` path into :func:`sweep_references` and
compared per record, never applied by narrowing the vault list — the same sweep
owes the all-vault shapes their full reach.

**Confinement.** Independently of that trust split, the sweep never reads
outside the vault it is walking: every kind directory, body, and sidecar is
realpath-descendant checked against the vault root before it is opened, and
anything escaping is skipped silently. This holds for ALL vaults, not just
shared ones — a symlink planted in any vault would otherwise turn the sweep
into a reader of arbitrary files, and its error reporting into an
existence oracle for the paths it was pointed at.

**Idempotency and crash-resume.** Re-running the same rename after a crash is a
no-op that still completes the sweep: when the old ID is gone but a record with
the new title already sits at the new stem, the primary rename is reported as
already-done, its index row is repointed, and the reference sweep runs again
(records already rewritten match nothing and are left byte-identical). Resume is
gated on that title match — occupancy of the new stem alone is NOT enough, since
an interrupted run that collided and suffixed leaves the unsuffixed stem held by
an unrelated record.

**Ordering and failure.** The primary move's index repoint is committed before
the sweep begins, so a sweep problem can never roll back a relocation that is
already durable on disk. The sweep itself is fault-isolated: a record it cannot
read or write is reported and stepped over, never allowed to abort a rename
whose primary move has landed.

**Writes go through the standard pipeline.** Rewritten referencing records are
written with :func:`store.validate_and_write`, so they are validated, provenance
re-stamped, and fence-neutralized exactly as any other update. Records the sweep
does not change are never written, so their bytes and provenance are untouched.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, NamedTuple

from .. import locking
from . import graph
from . import model as record_model
from . import store


class RenameError(Exception):
    """A rename that cannot proceed; nothing has been written."""


class SweepVault(NamedTuple):
    """A vault the sweep may visit: display name, root path, trust flag."""

    name: str
    root: Path
    shared: bool


class Rewrite(NamedTuple):
    """One referencing record the sweep touched, skipped, could not check, or failed on.

    Exactly one of four outcomes holds: ``skipped`` (a shared vault, not opted
    into), ``unchecked`` (the record could not be read at all, so whether it
    references the old stem is unknown), ``error`` set without ``unchecked``
    (the record DOES reference the old stem and the rewrite failed), or none of
    them (rewritten). ``unchecked`` entries carry their reason in ``error`` too,
    but they are not evidence of a dangling reference and must not be reported
    as one.
    """

    vault: str
    record_id: str
    skipped: bool
    error: str | None = None
    unchecked: bool = False


class RenameReport(NamedTuple):
    """The outcome of a rename: the IDs, the source vault, the move, the sweep."""

    old_id: str
    new_id: str
    vault: str
    moved: bool
    rewrites: tuple[Rewrite, ...]


# ---------------------------------------------------------------------------
# Vault enumeration
# ---------------------------------------------------------------------------


def sweep_vaults() -> list[SweepVault]:
    """Enumerate the vaults the sweep covers, in config order.

    Reads ``config.json`` through the vault package's own resolution helpers
    (:func:`vault.config._resolve_config_path` + :func:`vault.config.load_config`)
    rather than a hardcoded location. Vanilla usage — no config, or a
    malformed one — degrades to the single config-resolved active vault, so a
    plain install still renames and sweeps its own vault.
    """
    from ..vault import config as vault_config

    try:
        config_path = vault_config._resolve_config_path()
        if config_path.exists():
            return [
                SweepVault(v.name, Path(v.path), vault_config.is_shared(v))
                for v in vault_config.load_config(str(config_path))
            ]
    except (vault_config.VaultConfigError, OSError, ValueError):
        pass
    return [SweepVault("default", Path(store._active_vault_root()), False)]


# ---------------------------------------------------------------------------
# Reference rewriting (pure)
# ---------------------------------------------------------------------------


def _wikilink_re(kind: str, old_stem: str) -> re.Pattern:
    """Match ``[[old-stem]]`` / ``[[kind/old-stem]]``, with an optional alias."""
    return re.compile(
        r"\[\[(" + re.escape(kind) + r"/)?" + re.escape(old_stem) + r"(\|[^\]\n]*)?\]\]"
    )


def rewrite_body(body: str, kind: str, old_stem: str, new_stem: str) -> str:
    """Rewrite every exact-stem wikilink to *old_stem*, preserving its shape.

    The qualifier (``kind/``) and any ``|alias`` suffix are carried through
    verbatim, and nothing else in the body is touched — so a body without a
    trailing newline round-trips byte-identically apart from the substitution.
    """
    return _wikilink_re(kind, old_stem).sub(
        lambda m: f"[[{m.group(1) or ''}{new_stem}{m.group(2) or ''}]]", body
    )


def _rewrite_entry(entry: Any, kind: str, old_stem: str, new_stem: str) -> Any:
    """Rewrite one list entry, accepting both bare and ``kind/``-qualified forms."""
    if entry == old_stem:
        return new_stem
    if entry == f"{kind}/{old_stem}":
        return f"{kind}/{new_stem}"
    return entry


def _rewrite_design_entry(entry: Any, kind: str, old_stem: str, new_stem: str) -> Any:
    """Rewrite one design ``depends-on`` entry, carrying its ``@stage`` tail through.

    The design grammar is ``<kind>/<name>[@stage]``, so only the qualified form
    matches — a bare name is not a design edge at all (:func:`graph.parse_dependency`
    rejects it), and rewriting one would invent a reference the operator never
    wrote. The tail is sliced off and re-appended byte-for-byte rather than
    re-rendered: an unqualified entry stays unqualified (a rename never
    introduces a stage), and a stage the current vocabulary would reject is
    still carried rather than silently dropped.
    """
    if not isinstance(entry, str):
        return entry
    qualified = f"{kind}/{old_stem}"
    if entry == qualified:
        return f"{kind}/{new_stem}"
    if entry.startswith(f"{qualified}@"):
        return f"{kind}/{new_stem}{entry[len(qualified):]}"
    return entry


def rewrite_sidecar(
    sidecar: dict,
    kind: str,
    old_stem: str,
    new_stem: str,
    *,
    vault: SweepVault,
    source_vault: SweepVault,
) -> tuple[dict, bool]:
    """Rewrite ``related.<kind>``, plus the graph edges the renamed kind carries.

    Returns ``(sidecar, changed)``; the input is never mutated. *vault* is the
    vault the record being examined lives in and *source_vault* the vault
    holding the record being renamed; the two are equal for every record in the
    renamed record's own vault.

    ``related`` is rewritten in **every** vault — it is a plain cross-reference,
    and a reference is only ever to the record it names.

    The graph-edge branches are gated on the renamed record's kind, since a kind
    that cannot be the target of an edge can never be referenced by one (see
    :data:`model.KIND_GATED_FIELDS`):

    - ``task`` rewrites ``depends-on`` and ``parent`` in every vault. ``parent``
      is a single string, not a list, so it is rewritten in place rather than
      mapped.
    - ``spec``/``adr`` rewrite ``depends-on`` **only within the renamed record's
      own vault**.

    **Security posture of the own-vault gate.** A design ``depends-on`` target is
    resolved vault-locally, so ``spec/foo`` in another vault names that vault's
    ``spec/foo`` — a different record that this rename says nothing about.
    Rewriting it would reach across a trust boundary: other configured vaults
    may be ``shared: true``, holding content this operator does not control, and
    a sweep that edited their graph edges would let an attacker-chosen stem
    steer writes into records the rename never named. The gate is therefore a
    per-record decision made here, against the source vault threaded in from the
    call site — never a narrowing of the vault list the sweep walks, which would
    also strip ``related`` and wikilink rewriting of their required all-vault
    reach.

    Vault identity is decided on ``os.path.realpath`` — the same primitive every
    other confinement and identity check in this module resolves through — so
    two configured entries naming one physical directory (a symlinked root, a
    relative path beside an absolute one) are correctly one vault, and a
    directory reached by a symlink out of the vault is not silently a second.
    """
    updated = dict(sidecar)
    changed = False
    own_vault = os.path.realpath(vault.root) == os.path.realpath(source_vault.root)

    related = updated.get("related")
    if isinstance(related, dict) and isinstance(related.get(kind), list):
        names = [_rewrite_entry(n, kind, old_stem, new_stem) for n in related[kind]]
        if names != related[kind]:
            merged = dict(related)
            merged[kind] = names
            updated["related"] = merged
            changed = True

    if kind == "task":
        if isinstance(updated.get("depends-on"), list):
            deps = [
                _rewrite_entry(n, kind, old_stem, new_stem) for n in updated["depends-on"]
            ]
            if deps != updated["depends-on"]:
                updated["depends-on"] = deps
                changed = True

        if isinstance(updated.get("parent"), str):
            parent = _rewrite_entry(updated["parent"], kind, old_stem, new_stem)
            if parent != updated["parent"]:
                updated["parent"] = parent
                changed = True

    elif kind in graph.DESIGN_KINDS and own_vault:
        if isinstance(updated.get("depends-on"), list):
            deps = [
                _rewrite_design_entry(n, kind, old_stem, new_stem)
                for n in updated["depends-on"]
            ]
            if deps != updated["depends-on"]:
                updated["depends-on"] = deps
                changed = True

    return updated, changed


# ---------------------------------------------------------------------------
# Location helpers
# ---------------------------------------------------------------------------


def _find_vault(vaults, kind: str, stem: str) -> SweepVault | None:
    """First configured vault (config order) holding ``<kind>/<stem>``, or None.

    Occupancy is :func:`store._stem_occupied` — the pair-aware rule, so a record
    left holding only one of its two artifacts by an interrupted write is still
    found rather than reported missing.
    """
    for vault in vaults:
        if store._stem_occupied(Path(vault.root) / kind, stem):
            return vault
    return None


def _confined_kind_dirs(root: Path):
    """Yield each kind directory directly under *root* that is safe to descend.

    A *root* that does not exist yields nothing rather than raising: a vault can
    be configured before it is created, and that must not abort a rename whose
    primary move has already landed.

    Only directories named for one of :data:`model.KINDS` are yielded, so stray
    top-level files and non-record directories (``.git``, editor state) never
    enter the sweep. Every real kind IS yielded — ``blob`` included: a blob is an
    ordinary record whose body and sidecar can carry references like any other.

    **Confinement.** A kind directory whose realpath is not a descendant of the
    vault root is skipped, silently. A vault is attacker-influenced content —
    emphatically so when it is ``shared: true`` — and descending a symlinked
    kind directory would walk an arbitrary external tree, reading whatever it
    holds. The check is the same realpath-descendant rule
    (:func:`store._realpath_is_descendant`) that confines every write, applied
    here at discovery so nothing outside the vault is ever opened.
    """
    root = Path(root)
    if not root.is_dir():
        return
    root_real = os.path.realpath(root)
    for kind_dir in sorted(root.iterdir()):
        if not kind_dir.is_dir() or kind_dir.name not in record_model.KINDS:
            continue
        if not store._realpath_is_descendant(kind_dir, root_real):
            continue
        yield kind_dir, root_real


def _iter_records(root: Path):
    """Yield ``(record_id, kind, body_path)`` for every confined record under *root*.

    Discovery walks only the kind directories :func:`_confined_kind_dirs`
    approves, and a record is yielded only when **both** of its artifacts —
    ``<stem>.md`` and ``<stem>.json`` — resolve inside the vault root. Either one
    escaping skips the whole record, silently: reading the escaping artifact
    would leak an external file into the sweep, and reporting the skip by name
    would echo the attacker's chosen path (and the OS error text for it) back to
    the operator as an existence oracle. A record that is skipped is simply not
    swept, so nothing about it is read, written, or named.
    """
    for kind_dir, root_real in _confined_kind_dirs(root):
        for body_path in sorted(kind_dir.rglob("*.md")):
            if not store._realpath_is_descendant(body_path, root_real):
                continue
            if not store._realpath_is_descendant(
                body_path.with_suffix(".json"), root_real
            ):
                continue
            name = body_path.relative_to(kind_dir).with_suffix("").as_posix()
            yield f"{kind_dir.name}/{name}", kind_dir.name, body_path


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def sweep_references(
    vaults,
    kind: str,
    old_stem: str,
    new_stem: str,
    conn,
    *,
    dry_run: bool,
    include_shared: bool,
    source_vault: SweepVault,
) -> tuple[Rewrite, ...]:
    """Rewrite (or, for skipped shared vaults, report) every inbound reference.

    A record is written only when its body or sidecar actually changes, so
    untouched records keep their bytes and their provenance. Shared vaults are
    reported with ``skipped=True`` unless *include_shared* is set; ``dry_run``
    computes the full list without writing anything anywhere.

    *source_vault* is the vault holding the record being renamed. The sweep
    still walks every vault in *vaults* — wikilinks and ``related`` entries
    point wherever they point — but :func:`rewrite_sidecar` needs it to tell an
    own-vault record from a foreign one, since design ``depends-on`` targets
    resolve vault-locally and must not be rewritten across that boundary. It is
    threaded rather than inferred: a sweep that guessed its own source would
    guess wrong on the crash-resume path, where the record sits at a stem the
    caller resolved by title.

    **Fault isolation.** The sweep runs AFTER the primary move has landed, so it
    must never abort partway and leave the operator with a moved record and no
    account of the references. Each record is handled independently: an
    unreadable body (missing, or not UTF-8), an invalid sidecar, or a failed
    ``validate_and_write`` is recorded as a :class:`Rewrite` carrying ``error``
    and the sweep moves to the next record. The caller reports every entry —
    rewritten, skipped, unchecked, and failed alike — so a partial failure names
    exactly which records still point at the old stem. A record whose body could
    not be READ is reported ``unchecked`` rather than failed: nothing is known
    about its references, so it is not a dangling one.

    **Commit boundary.** Each vault's rewrites are committed before the next
    vault is entered. The rewrites are durable on disk the moment they are
    written, so holding every vault's index upserts in one transaction would let
    an abort in a later vault discard index rows for files that already changed,
    leaving the index disagreeing with the vault it describes.

    **Session bodies** are appended to concurrently by live agent sessions, so a
    session record is rewritten while holding its
    :func:`locking.session_write_lock` — otherwise the sweep's
    read-modify-write can drop a candidate appended between the two.
    """
    rewrites: list[Rewrite] = []
    for vault in vaults:
        skip = vault.shared and not include_shared
        for record_id, rec_kind, body_path in _iter_records(vault.root):
            try:
                body = body_path.read_text(encoding="utf-8")
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                rewrites.append(
                    Rewrite(
                        vault.name,
                        record_id,
                        False,
                        f"unreadable body: {exc}",
                        unchecked=True,
                    )
                )
                continue

            try:
                sidecar_text = body_path.with_suffix(".json").read_text(encoding="utf-8")
                sidecar = json.loads(sidecar_text)
            except (OSError, ValueError):
                sidecar = {}
            if not isinstance(sidecar, dict):
                sidecar = {}

            new_body = rewrite_body(body, kind, old_stem, new_stem)
            new_sidecar, sidecar_changed = rewrite_sidecar(
                sidecar, kind, old_stem, new_stem,
                vault=vault, source_vault=source_vault,
            )
            if new_body == body and not sidecar_changed:
                continue

            if skip or dry_run:
                rewrites.append(Rewrite(vault.name, record_id, skip))
                continue

            try:
                _write_rewrite(vault, record_id, rec_kind, new_sidecar, new_body, conn)
            except Exception as exc:  # one bad record must not abort the sweep
                rewrites.append(Rewrite(vault.name, record_id, False, str(exc)))
                continue
            rewrites.append(Rewrite(vault.name, record_id, False))
        if not dry_run:
            conn.commit()
    return tuple(rewrites)


def _write_rewrite(vault, record_id, rec_kind, new_sidecar, new_body, conn) -> None:
    """Write one rewritten record, holding the session lock for session bodies."""
    location = store.locate_record(record_id, vault_root=str(vault.root))
    shared = 1 if vault.shared else 0
    if rec_kind == "session":
        with locking.session_write_lock(vault.root, location.name):
            store.validate_and_write(location, new_sidecar, new_body, conn, shared=shared)
        return
    store.validate_and_write(location, new_sidecar, new_body, conn, shared=shared)


# ---------------------------------------------------------------------------
# rename_record
# ---------------------------------------------------------------------------


def _read_sidecar(sidecar_path: Path) -> dict:
    """Read a sidecar, degrading a missing/invalid/non-object one to ``{}``."""
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return sidecar if isinstance(sidecar, dict) else {}


def _title_matches(vaults, kind: str, new_title: str) -> list[tuple[SweepVault, str]]:
    """Every ``(vault, stem)`` of *kind* whose sidecar ``title`` is *new_title*.

    Confined exactly like the sweep's own discovery: a symlinked kind directory
    is not descended, and a sidecar resolving outside the vault root is skipped
    rather than read (see :func:`_iter_records`).
    """
    matches = []
    for vault in vaults:
        root_real = os.path.realpath(vault.root)
        kind_dir = Path(vault.root) / kind
        if not kind_dir.is_dir():
            continue
        if not store._realpath_is_descendant(kind_dir, root_real):
            continue
        for sidecar_path in sorted(kind_dir.rglob("*.json")):
            if not store._realpath_is_descendant(sidecar_path, root_real):
                continue
            if _read_sidecar(sidecar_path).get("title") == new_title:
                stem = sidecar_path.relative_to(kind_dir).with_suffix("").as_posix()
                matches.append((vault, stem))
    return matches


def _resume(
    vaults, search, kind, old_stem, new_title, conn, *, dry_run, include_shared
):
    """Complete a rename whose primary move already landed, or refuse to guess.

    *search* is where the landed record is looked for (narrowed by ``--vault``);
    *vaults* is the full set the inbound-reference sweep covers.

    Reached when the old ID resolves to nothing on disk. That means either the
    move already landed (a crash between the move and the sweep) or the ID was
    never real — and the two are told apart by **identity**, never by occupancy
    of the base stem. Occupancy is the wrong test twice over: the base stem can
    be held by a total stranger (a first run that collided and suffixed to
    ``base-2`` before failing leaves ``base`` held by the record it collided
    WITH, and adopting it would repoint every inbound reference at that
    stranger), and the record that actually landed may not be at ``base`` at
    all, for exactly the same reason.

    So the landed record is identified by its **new title**, wherever it sits:
    the rename stamps ``title`` before the move, so the resumed record is the
    one carrying *new_title*. Exactly one match is adopted — including one at a
    suffixed stem, which is what repairs a collision against a record that
    merely slugs to the same stem under a different title. Zero matches means
    the ID was never real. More than one match cannot be resolved from the vault
    alone (two records legitimately share the title, which is the very reason
    the interrupted run had to suffix), so it is refused rather than guessed,
    naming the candidates so the operator can act on the right one.
    """
    matches = _title_matches(search, kind, new_title)
    if not matches:
        raise RenameError(f"record not found: {kind}/{old_stem}")
    if len(matches) > 1:
        candidates = ", ".join(f"{v.name}:{kind}/{s}" for v, s in matches)
        raise RenameError(
            f"cannot resume the rename of {kind}/{old_stem}: it is gone, and more "
            f"than one record is titled {new_title!r} ({candidates}). Re-issue the "
            f"rename naming the ID the interrupted run actually landed on."
        )

    landed, landed_stem = matches[0]
    new_id = f"{kind}/{landed_stem}"
    location = store.locate_record(new_id, vault_root=str(landed.root))
    sidecar = _read_sidecar(location.sidecar_path)

    if not dry_run:
        # The interrupted run's index writes were never committed, so the index
        # still carries the old row and no new one. Repoint it before the sweep,
        # and commit — the sweep must never be able to roll back a move that is
        # already durable on disk.
        body = (
            location.body_path.read_text(encoding="utf-8")
            if location.body_path.exists()
            else ""
        )
        with locking.vault_write_lock(landed.root):
            store.index_store.delete_row(conn, str(landed.root), kind, old_stem)
            store.update_index(
                conn, new_id, sidecar, body, str(landed.root),
                shared=1 if landed.shared else 0,
            )
        conn.commit()

    return RenameReport(
        f"{kind}/{old_stem}",
        new_id,
        landed.name,
        False,
        sweep_references(
            vaults, kind, old_stem, landed_stem, conn,
            dry_run=dry_run, include_shared=include_shared, source_vault=landed,
        ),
    )


def rename_record(
    record_id: str,
    new_title: str,
    conn,
    *,
    dry_run: bool = False,
    include_shared: bool = False,
    vault_name: str | None = None,
) -> RenameReport:
    """Rename *record_id* to *new_title* and rewrite its inbound references.

    Raises :class:`RenameError` — having written nothing — for a malformed ID, a
    ``session`` record (its GUID is its identity, so it has no renameable stem),
    an ID that resolves to no record in the searched vaults, or a *vault_name*
    absent from the config.

    *vault_name* restricts the search for the record being renamed to exactly
    that configured vault, mirroring ``record show``/``delete --vault``: with
    the same stem in two vaults, config order alone picks the wrong one. It
    narrows only the SOURCE lookup — the inbound-reference sweep still covers
    every configured vault, since references live wherever they live. A record
    absent from the named vault but present in another one is refused, naming
    the vault that holds it: it is a mis-aimed rename, not a crash to resume.

    Whichever path resolves the source vault — the narrowed lookup, the
    collision-suffixed placement, or the resume-by-title match — that vault is
    threaded into :func:`sweep_references`, which needs it to keep design
    ``depends-on`` rewriting inside the renamed record's own vault. Every other
    reference shape the sweep rewrites stays vault-wide.

    **Commit boundary.** Once the primary move is durable on disk, its index
    repoint is committed before the sweep starts. The sweep is fault-isolated
    and reports per-record failures rather than raising, but a commit after it
    would still put the move's index row at the mercy of the sweep — leaving
    files at the new path and the index pointing at the old one.
    """
    if not record_id or "/" not in record_id:
        raise RenameError(f"invalid RECORD_ID {record_id!r}; expected '<kind>/<name>'")
    kind, old_stem = record_id.split("/", 1)
    if kind == "session":
        raise RenameError(
            "session records are identified by their GUID and cannot be renamed"
        )

    vaults = sweep_vaults()
    search = vaults
    if vault_name is not None:
        search = [v for v in vaults if v.name == vault_name]
        if not search:
            raise RenameError(f"unknown vault: {vault_name}")

    # A slash alone does not make an ID well-formed: ``adr/`` and ``adr/../x``
    # both carry one, and both would otherwise reach the resume path — where a
    # unique title match would be adopted and every inbound reference repointed
    # at a record the operator never named. Confinement is checked against every
    # searched vault root, the same guard every other RECORD_ID-bearing caller
    # uses, so a traversal is refused before anything is read or written.
    for vault in search:
        try:
            store.confine_record_id(record_id, str(vault.root))
        except store.InvalidRecordIdError as exc:
            raise RenameError(str(exc)) from exc

    base = store._kebab(new_title)
    source = _find_vault(search, kind, old_stem)

    if source is None:
        # Resume answers "the move already landed" — it is only ever the right
        # reading when the record is gone from EVERY configured vault. With
        # ``--vault`` narrowing the search, a record alive and well elsewhere
        # would otherwise be resolved by title inside the named vault, adopting
        # an unrelated record and repointing every inbound reference at it.
        elsewhere = (
            None if search is vaults else _find_vault(vaults, kind, old_stem)
        )
        if elsewhere is not None:
            raise RenameError(
                f"{kind}/{old_stem} is not in vault {vault_name!r}; it lives in "
                f"vault {elsewhere.name!r}. Re-issue the rename with "
                f"--vault {elsewhere.name}."
            )
        return _resume(
            vaults, search, kind, old_stem, new_title, conn,
            dry_run=dry_run, include_shared=include_shared,
        )

    if dry_run:
        # Predict the destination stem without claiming it: no lock, no write.
        dest_name = (
            old_stem
            if old_stem == base
            else store.place_record(
                new_title, kind, None, vault_root=str(source.root)
            ).name
        )
        return RenameReport(
            record_id, f"{kind}/{dest_name}", source.name, False,
            sweep_references(
                vaults, kind, old_stem, dest_name, conn,
                dry_run=True, include_shared=include_shared, source_vault=source,
            ),
        )

    shared = 1 if source.shared else 0

    # Placement and the move it feeds are ONE critical section: place_record
    # picks the first free stem, and a concurrent create/rename that claims it
    # before move_record writes there would be clobbered. The vault lock is
    # reentrant per thread, so move_record's own acquisition is a depth bump.
    with locking.vault_write_lock(source.root):
        # A rename that keeps the current stem must not collide with itself, so
        # that case skips placement entirely (``None`` destination = nothing to
        # move). Any other title is placed exactly the way a create places one —
        # the same ``_kebab`` + ``-2``/``-3`` collision suffix — so a rename and
        # a create of the same title land on the same stem.
        dest = (
            None
            if old_stem == base
            else store.place_record(new_title, kind, None, vault_root=str(source.root))
        )
        new_stem = old_stem if dest is None else dest.name
        new_id = f"{kind}/{new_stem}"

        location = store.locate_record(record_id, vault_root=str(source.root))
        sidecar = _read_sidecar(location.sidecar_path)
        sidecar["title"] = new_title
        body = (
            location.body_path.read_text(encoding="utf-8")
            if location.body_path.exists()
            else ""
        )

        moved = dest is not None
        if moved:
            # Stamp + neutralize BEFORE the move so the mutated record is written
            # once, at its destination — move_record writes overrides verbatim.
            stamped, safe_body = store.validate_stamp_neutralize(location, sidecar, body)
            store.move_record(
                record_id,
                dest,
                conn,
                old_vault_root=str(source.root),
                new_sidecar=stamped,
                new_body=safe_body,
                shared=shared,
            )
        else:
            store.validate_and_write(location, sidecar, body, conn, shared=shared)

    # The move is durable; publish its index row before the sweep can fail.
    conn.commit()

    return RenameReport(
        record_id, new_id, source.name, moved,
        sweep_references(
            vaults, kind, old_stem, new_stem, conn,
            dry_run=False, include_shared=include_shared, source_vault=source,
        ),
    )
