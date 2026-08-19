"""Sidecar-only walk of the configured vaults — the pipeline board's whole read path.

**Sidecars only.** For each vault this reads ``<vault>/<kind>/*.json`` for the
kinds in :data:`PIPELINE_KINDS` and never opens the ``.md`` body beside them.
That is a correctness requirement, not an optimization: a vault is a git
working tree that a background sync can update mid-walk, and a record read as
a body/sidecar *pair* can be torn across that update. One sidecar is one
atomically-written file, so every record this yields is a coherent snapshot of
some moment.

**Nothing raises.** A read failure is data, not control flow. A file that is
unreadable, unparseable, non-object, or gone by the time it is opened becomes
one :class:`SidecarWarning` and costs only itself. A vault directory that
cannot be listed at all becomes that vault's ``error`` marker, and every other
vault still walks. The caller decides what a given failure means for the exit
code; this module only reports.

**Per-vault, never merged.** Each vault comes back as its own
``{"kind/name": sidecar}`` mapping — the shape
:func:`record.graph.evaluate_dependencies` consumes directly, with no adapter.
Confinement is the caller's discipline: that evaluator is a pure function over
whatever mapping it is handed, so merging two vaults' mappings would let a
record in one vault satisfy a dependency declared in another. This module
never builds such a merged mapping, and no caller may.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple, Sequence

from ..record import guards as guards_mod

#: The record kinds the pipeline board is derived from — design lineage roots,
#: their derived specs, and routed tasks. Every other kind in a vault is left
#: unread.
PIPELINE_KINDS: tuple[str, ...] = ("adr", "spec", "task")


class SidecarWarning(NamedTuple):
    """One sidecar that could not be read, named by its vault-relative path.

    ``file`` and ``message`` both originate in the vault that produced them —
    ``file`` carries a filename stem that only the CLI's own slugifier
    validates, and a record synced in by git never passed through it — so both
    are vault-authored free text for fencing purposes.
    """

    file: str
    message: str


class VaultWalk(NamedTuple):
    """What one vault contributed to the board.

    ``error`` is ``None`` when the vault was walked, and a message naming the
    problem when its directory could not be listed at all. The two are
    deliberately distinguishable from "walked, held nothing": an empty
    ``records`` with ``error is None`` means consulted and empty, which is not
    the same fact as not consulted.

    ``error`` is composed here from the vault's configured path and the OS
    error — never from vault content — so it is not shared-authored free text.
    """

    name: str
    path: str
    shared: bool
    error: str | None
    records: dict[str, dict]
    warnings: tuple[SidecarWarning, ...]


def walk_vault(name: str, path: str, *, shared: bool) -> VaultWalk:
    """Walk one vault's :data:`PIPELINE_KINDS` sidecars into a :class:`VaultWalk`.

    The vault root is listed once up front purely to separate "this vault is
    not readable" from "this vault holds no records of these kinds" — a
    distinction the per-kind reads below cannot make on their own, since an
    absent kind directory is a normal, unremarkable state.
    """
    root = Path(path)
    try:
        with os.scandir(root):
            pass
    except OSError as exc:
        return VaultWalk(name, str(path), shared, f"cannot read vault directory: {exc}", {}, ())

    records: dict[str, dict] = {}
    warnings: list[SidecarWarning] = []
    for kind in PIPELINE_KINDS:
        sidecars, kind_warnings = guards_mod.load_kind_sidecars_with_warnings(str(root), kind)
        for stem, sidecar in sidecars.items():
            records[f"{kind}/{stem}"] = sidecar
        warnings.extend(SidecarWarning(file, message) for file, message in kind_warnings)
    return VaultWalk(name, str(path), shared, None, records, tuple(warnings))


def walk_vaults(
    vaults: Sequence[tuple[str, Path]], shared_names: set[str]
) -> list[VaultWalk]:
    """Walk each ``(name, path)`` in order, marking shared vaults from *shared_names*.

    *shared_names* must come from the same ``config.json`` read that produced
    *vaults*; deriving it from a second read lets the two views disagree and
    the shared filter fail open.
    """
    return [
        walk_vault(name, str(path), shared=name in shared_names)
        for name, path in vaults
    ]
