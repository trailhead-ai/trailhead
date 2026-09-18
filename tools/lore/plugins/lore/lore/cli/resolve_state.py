"""Resolution-session state — the machine-local marker for an in-progress ``lore resolve``.

``lore resolve`` re-runs an aborted vault rebase step by step, and each step is a
separate CLI subprocess. The marker at ``state_dir("lore")/resolve/<vault>-<digest>.json``
is what carries a resolution across those steps: the ownership token ``lore
resolve`` mints when it starts, and the judgment conflicts it parks for
``lore resolve take`` to settle.

**The recorded pid is diagnostics only and is NEVER consulted for liveness.**
One subprocess per verb means no pid is ever alive when the next step runs, so a
pid-liveness check would call every real resolution dead. The sole authority is
git's own rebase state (:func:`vault_is_resolving`): a marker whose vault is
genuinely mid-rebase is live however dead its pid, and a marker whose vault is
not mid-rebase is stale however alive its pid — cleared on the next resolve.

The marker is machine-local operational state, not vault content — the same
posture as ``locking.lock_root_for_vault``'s lock sidecars, and for the same
reason: it has no value on another machine and must never sync. It is therefore
serialized as plain sorted-key JSON rather than through ``record.sidecar.dumps``,
whose byte shape exists to make *git-tracked* sidecars mergeable — a guarantee
this file has no use for.

**The held marker** (:func:`mark_held` / :func:`read_held_marker` /
:func:`clear_held_marker`) records a second, unrelated fact beside the
resolution-session marker: that a held conflict left this vault clean and
diverged, and the instant it entered that state. It has its OWN liveness rule,
and cannot reuse the resolution marker's, because the two markers describe
disjoint moments of the same replay:

  - The resolution marker is live precisely *while the vault is mid-rebase* —
    a resolution is an in-progress replay, one subprocess per step, and the
    marker has to survive across those steps with no live pid to trust.
  - A held vault is, by definition, the opposite: the replay was aborted
    *whole* (``rebase --abort``), so the vault is NOT mid-rebase the moment
    this marker is written, and stays not-mid-rebase for as long as it stays
    held. Asking "is this vault mid-rebase" would always answer ``False`` for
    a held marker — that authority cannot distinguish a held vault from an
    ordinary clean one, so it is the wrong question entirely.

  The held marker's liveness rule is instead plain file presence: the marker
  exists exactly while the vault is held, full stop. It is written once when a
  hold begins (preserving ``entered-at`` on a re-hold), read many times by
  whatever wants to know "held?" without re-running a replay, and cleared once,
  by whatever later sync settles the vault. No process-liveness or git-state
  check sits on top of that presence, because nothing about *being held* is
  time-bounded to one subprocess the way a resolution step is.
"""

from __future__ import annotations

import json
import os
import secrets
import datetime as dt
from pathlib import Path

from ..vault import layers as layers_mod
from .common import _resolve_lore_state_dir, _vault_mid_rebase, machine_state_key

#: Marker directory under ``state_dir("lore")``.
RESOLVE_DIRNAME = "resolve"


def resolve_state_root() -> Path:
    """Return ``state_dir("lore")/resolve`` — the marker directory."""
    return _resolve_lore_state_dir() / RESOLVE_DIRNAME


def marker_path(vault_root: str | Path) -> Path:
    """Return the marker path for *vault_root*, confined to the marker root.

    Keyed on ``common.machine_state_key`` — the vault's basename plus a digest of
    its resolved absolute path — so two configured vaults sharing a final path
    component keep separate markers instead of overwriting each other's parked
    conflicts. The resulting path is confined with ``layers.assert_within_root``,
    the same guard ``vault delete --remove-from-disk`` applies before it touches a
    configured path, so a symlink planted at the marker's name cannot redirect a
    write outside the marker root.

    Raises:
        layers.LayerConfinementError: if the marker path escapes the marker root.
    """
    root = resolve_state_root()
    candidate = root / f"{machine_state_key(vault_root)}.json"
    layers_mod.assert_within_root(candidate, root)
    return candidate


def read_marker(vault_root: str | Path) -> "dict | None":
    """Return the marker for *vault_root*, or ``None`` if absent or unreadable.

    A raw read with no staleness judgment — see :func:`live_marker` for the
    liveness-aware reader.
    """
    path = marker_path(vault_root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_marker(vault_root: str | Path, marker: dict) -> dict:
    """Write *marker* for *vault_root* and return it."""
    path = marker_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return marker


def begin_session(vault_root: str | Path) -> dict:
    """Mint and write a resolution-session marker for *vault_root*.

    Called by ``lore resolve`` alone — the token is its ownership claim on the
    resolution. ``pid`` is recorded for a human reading the marker after the
    fact; nothing branches on it.
    """
    return write_marker(vault_root, {
        "token": secrets.token_hex(16),
        "pid": os.getpid(),
        "vault": Path(vault_root).name,
        "started-at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "conflicts": [],
    })


def vault_is_resolving(vault_root: str | Path) -> bool:
    """Return ``True`` iff *vault_root* is mid-rebase — the one liveness authority."""
    return _vault_mid_rebase(Path(vault_root))


def live_marker(vault_root: str | Path) -> "dict | None":
    """Return the marker only while the vault is genuinely mid-rebase, else ``None``."""
    if not vault_is_resolving(vault_root):
        return None
    return read_marker(vault_root)


def clear_marker(vault_root: str | Path) -> bool:
    """Delete the marker. Returns ``True`` iff one was there to delete."""
    path = marker_path(vault_root)
    try:
        path.unlink()
        return True
    except OSError:
        return False


def clear_if_stale(vault_root: str | Path) -> bool:
    """Clear a marker whose vault is no longer mid-rebase. Returns ``True`` if cleared."""
    if vault_is_resolving(vault_root):
        return False
    return clear_marker(vault_root)


def resolve_remedy(vault_root: str | Path) -> str:
    """Return the ``lore resolve <vault>`` remedy naming *vault_root*'s vault."""
    return f"run `lore resolve {Path(vault_root).name}`"


#: Filename suffix distinguishing the held marker from the resolution-session
#: marker, which is keyed on the bare ``<machine_state_key>.json`` name. Both
#: markers live under the same :func:`resolve_state_root`.
HELD_SUFFIX = ".held"


def held_marker_path(vault_root: str | Path) -> Path:
    """Return the held-marker path for *vault_root*, confined to the marker root.

    Sits beside :func:`marker_path` in the same directory, keyed on the same
    ``machine_state_key`` but with :data:`HELD_SUFFIX` appended so the two
    markers never collide on one filename — a vault can be mid-resolution and
    held (briefly, at the moment a hold is recorded) without either write
    clobbering the other.

    Raises:
        layers.LayerConfinementError: if the marker path escapes the marker root.
    """
    root = resolve_state_root()
    candidate = root / f"{machine_state_key(vault_root)}{HELD_SUFFIX}.json"
    layers_mod.assert_within_root(candidate, root)
    return candidate


def read_held_marker(vault_root: str | Path) -> "dict | None":
    """Return the held marker for *vault_root*, or ``None`` if absent or unreadable.

    Unlike :func:`read_marker`'s resolution-session counterpart, presence here
    needs no liveness check layered on top: the resolution marker's liveness
    rule (:func:`vault_is_resolving`) asks whether the vault is genuinely
    mid-rebase, because a resolution marker can outlive its own subprocess and
    a live pid says nothing. A held marker has the opposite shape — it is
    written only *after* the replay has been aborted whole, so the vault is by
    definition NOT mid-rebase while held, and it is written once and read many
    times across process boundaries with no rebase state to consult. Its
    liveness rule is simply file presence: the marker exists exactly while the
    vault is held, and stops existing the moment a later sync settles it
    (:func:`clear_held_marker`). There is nothing mid-rebase to distinguish a
    live write from a stale one, so no separate ``live_held_marker`` exists.
    """
    path = held_marker_path(vault_root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def mark_held(vault_root: str | Path) -> dict:
    """Record that *vault_root* is held, and return the marker.

    ``entered-at`` is preserved across a re-hold: calling this again on a vault
    already held (the sweep retries and lands on the same conflict) must not
    restamp the instant, because the duration a person eventually reads off
    this marker is how long the vault has been *waiting*, not how long since
    the last sweep looked. Holding a vault that was previously cleared (a new
    conflict, or the same one recurring after a person resolved and released
    it) has no prior instant to preserve and stamps a fresh one.
    """
    existing = read_held_marker(vault_root)
    entered_at = (
        existing["entered-at"] if existing
        else dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    marker = {"vault": Path(vault_root).name, "entered-at": entered_at}
    path = held_marker_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return marker


def vault_is_held(vault_root: str | Path) -> bool:
    """Return ``True`` iff *vault_root* has a held marker — the ``awaiting-person`` test."""
    return read_held_marker(vault_root) is not None


def clear_held_marker(vault_root: str | Path) -> bool:
    """Delete the held marker. Returns ``True`` iff one was there to delete."""
    path = held_marker_path(vault_root)
    try:
        path.unlink()
        return True
    except OSError:
        return False


def refusal_notice(vault_root: str | Path, op: str) -> str:
    """Return the stderr line for a write path refused by an in-progress resolution."""
    return (
        f"lore: vault {Path(vault_root).name!r} is mid-resolution — {op} wrote "
        f"nothing. To finish the resolution, {resolve_remedy(vault_root)}."
    )


def warning_notice(vault_root: str | Path, op: str) -> str:
    """Return the stderr line for a write path allowed *despite* a resolution.

    ``session candidate`` takes this path: losing a finding is worse than
    capturing it into a vault that is mid-resolution.
    """
    return (
        f"notice: vault {Path(vault_root).name!r} is mid-resolution — {op} still "
        f"captured, but the vault stays unsynced until you {resolve_remedy(vault_root)}."
    )
