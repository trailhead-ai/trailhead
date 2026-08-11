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
       - ``depends-on`` sidecar list entries, when the renamed record is a
         ``task``.

**Trust.** ``shared: true`` vaults hold untrusted external content. The sweep
**skips** them by default and reports what it would have rewritten; the caller
opts in with ``include_shared``. The primary rename itself is never skipped —
the operator named that record explicitly.

**Idempotency.** Re-running the same rename after a crash is a no-op that still
completes the sweep: when the old ID is gone but the new one already exists, the
primary rename is reported as already-done and the reference sweep runs again
(records already rewritten match nothing and are left byte-identical).

**Writes go through the standard pipeline.** Rewritten referencing records are
written with :func:`store.validate_and_write`, so they are validated, provenance
re-stamped, and fence-neutralized exactly as any other update. Records the sweep
does not change are never written, so their bytes and provenance are untouched.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple

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
    """One referencing record the sweep touched (or skipped, in a shared vault)."""

    vault: str
    record_id: str
    skipped: bool


class RenameReport(NamedTuple):
    """The outcome of a rename: the IDs, whether the move ran, and the sweep."""

    old_id: str
    new_id: str
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


def rewrite_sidecar(
    sidecar: dict, kind: str, old_stem: str, new_stem: str
) -> tuple[dict, bool]:
    """Rewrite ``related.<kind>`` (and ``depends-on`` for tasks) references.

    Returns ``(sidecar, changed)``; the input is never mutated. ``depends-on``
    is only considered when the renamed record is a ``task`` — it is a task-only
    graph edge, so a non-task rename can never be the target of one.
    """
    updated = dict(sidecar)
    changed = False

    related = updated.get("related")
    if isinstance(related, dict) and isinstance(related.get(kind), list):
        names = [_rewrite_entry(n, kind, old_stem, new_stem) for n in related[kind]]
        if names != related[kind]:
            merged = dict(related)
            merged[kind] = names
            updated["related"] = merged
            changed = True

    if kind == "task" and isinstance(updated.get("depends-on"), list):
        deps = [_rewrite_entry(n, kind, old_stem, new_stem) for n in updated["depends-on"]]
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


def _iter_records(root: Path):
    """Yield ``(record_id, kind, body_path)`` for every record under *root*.

    Only directories named for a real record kind are descended, so stray
    top-level files and non-record directories (``.git``, ``blob`` payload
    trees) never enter the sweep.
    """
    for kind_dir in sorted(Path(root).iterdir()):
        if not kind_dir.is_dir() or kind_dir.name not in record_model.KINDS:
            continue
        for body_path in sorted(kind_dir.rglob("*.md")):
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
) -> tuple[Rewrite, ...]:
    """Rewrite (or, for skipped shared vaults, report) every inbound reference.

    A record is written only when its body or sidecar actually changes, so
    untouched records keep their bytes and their provenance. Shared vaults are
    reported with ``skipped=True`` unless *include_shared* is set; ``dry_run``
    computes the full list without writing anything anywhere.
    """
    rewrites: list[Rewrite] = []
    for vault in vaults:
        skip = vault.shared and not include_shared
        for record_id, _rec_kind, body_path in _iter_records(vault.root):
            sidecar_path = body_path.with_suffix(".json")
            body = body_path.read_text(encoding="utf-8")
            try:
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                sidecar = {}
            if not isinstance(sidecar, dict):
                sidecar = {}

            new_body = rewrite_body(body, kind, old_stem, new_stem)
            new_sidecar, sidecar_changed = rewrite_sidecar(
                sidecar, kind, old_stem, new_stem
            )
            if new_body == body and not sidecar_changed:
                continue

            rewrites.append(Rewrite(vault.name, record_id, skip))
            if skip or dry_run:
                continue
            location = store.locate_record(record_id, vault_root=str(vault.root))
            store.validate_and_write(
                location, new_sidecar, new_body, conn, shared=1 if vault.shared else 0
            )
    return tuple(rewrites)


# ---------------------------------------------------------------------------
# rename_record
# ---------------------------------------------------------------------------


def rename_record(
    record_id: str,
    new_title: str,
    conn,
    *,
    dry_run: bool = False,
    include_shared: bool = False,
) -> RenameReport:
    """Rename *record_id* to *new_title* and rewrite its inbound references.

    Raises :class:`RenameError` — having written nothing — for a malformed ID, a
    ``session`` record (its GUID is its identity, so it has no renameable stem),
    or an ID that resolves to no record in any configured vault.
    """
    if not record_id or "/" not in record_id:
        raise RenameError(f"invalid RECORD_ID {record_id!r}; expected '<kind>/<name>'")
    kind, old_stem = record_id.split("/", 1)
    if kind == "session":
        raise RenameError(
            "session records are identified by their GUID and cannot be renamed"
        )

    vaults = sweep_vaults()
    base = store._kebab(new_title)
    source = _find_vault(vaults, kind, old_stem)

    if source is None:
        # Crash-recovery / idempotent re-run: the primary move already landed.
        # The sweep still runs, so a rename interrupted between the move and the
        # rewrite is repaired by re-issuing the identical command.
        if _find_vault(vaults, kind, base) is None:
            raise RenameError(f"record not found: {record_id}")
        new_id = f"{kind}/{base}"
        return RenameReport(
            record_id,
            new_id,
            False,
            sweep_references(
                vaults, kind, old_stem, base, conn,
                dry_run=dry_run, include_shared=include_shared,
            ),
        )

    # A rename that keeps the current stem must not collide with itself, so that
    # case skips placement entirely (``None`` destination = nothing to move). Any
    # other title is placed exactly the way a create places one — the same
    # ``_kebab`` + ``-2``/``-3`` collision suffix, via :func:`store.place_record`
    # — so a rename and a create of the same title land on the same stem.
    dest = (
        None
        if old_stem == base
        else store.place_record(new_title, kind, None, vault_root=str(source.root))
    )
    new_stem = old_stem if dest is None else dest.name
    new_id = f"{kind}/{new_stem}"

    if dry_run:
        return RenameReport(
            record_id, new_id, False,
            sweep_references(
                vaults, kind, old_stem, new_stem, conn,
                dry_run=True, include_shared=include_shared,
            ),
        )

    location = store.locate_record(record_id, vault_root=str(source.root))
    try:
        sidecar = json.loads(location.sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sidecar = {}
    if not isinstance(sidecar, dict):
        sidecar = {}
    sidecar["title"] = new_title
    body = location.body_path.read_text(encoding="utf-8") if location.body_path.exists() else ""

    shared = 1 if source.shared else 0
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

    return RenameReport(
        record_id, new_id, moved,
        sweep_references(
            vaults, kind, old_stem, new_stem, conn,
            dry_run=False, include_shared=include_shared,
        ),
    )
