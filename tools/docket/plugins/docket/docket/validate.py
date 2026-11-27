"""Validation of the values a change command takes, before anything is read or written.

Every value is checked here, in order: its shape first (no lore call at all), then the vault it names
against the configured vaults (one `lore vault ls` call, which also locates the default vault), then,
for an add, the record's kind through `lore record show`. A failure raises `Refused`, which the
change commands report as the outcome `refused` with exit 2 and nothing written.

A REF is `<vault>/<kind>/<slug>`: exactly three non-empty parts, no value begins with a dash, and
kind and slug use the store's own validators (lowercase letters, digits, `.`, `_`, `-`). A workspace
is `GROUP/SLUG` under the same rule. A vault named on its own (set-order, vault-order) is one part with
no `/`, under the store's vault-name rule. The store validates again on every path it opens.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import NamedTuple, Sequence

from docket.errors import DocketError
from docket.store import (
    Runner, StoreError, UnsafeNameError, Workspace, locate_default_vault, validate_kind, validate_slug,
    validate_vault_name,
)

NOTE_KEYWORD = "note"
LISTABLE_KINDS = ("spec", "task")


class Refused(DocketError):
    """A value or request the change commands refuse; nothing was written."""

    def __init__(self, message: str, entries: Sequence[dict] = ()) -> None:
        super().__init__(message)
        self.entries = list(entries)


class Ref(NamedTuple):
    vault: str
    kind: str
    slug: str

    @property
    def text(self) -> str:
        return f"{self.vault}/{self.kind}/{self.slug}"

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.slug)


def _no_dash(value: str) -> None:
    if value.startswith("-"):
        raise Refused(f"{value!r} begins with a dash; pass values after --")


def parse_ref(value: str) -> Ref:
    _no_dash(value)
    parts = value.split("/")
    if len(parts) != 3 or not all(parts):
        raise Refused(f"{value!r} is not a REF; expected <vault>/<kind>/<slug> with no empty part")
    try:
        return Ref(validate_vault_name(parts[0]), validate_kind(parts[1]), validate_slug(parts[2]))
    except UnsafeNameError as exc:
        raise Refused(str(exc)) from exc


def parse_vault(value: str) -> str:
    if value.startswith("-") or "/" in value or not value:
        raise Refused(f"{value!r} is not a vault name; expected <vault> was <REF...> now <REF...>")
    try:
        return validate_vault_name(value)
    except UnsafeNameError as exc:
        raise Refused(str(exc)) from exc


def parse_workspace(value: str) -> Workspace:
    _no_dash(value)
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        raise Refused(f"{value!r} is not a workspace; expected GROUP/SLUG")
    try:
        return Workspace(validate_slug(parts[0]), validate_slug(parts[1]))
    except UnsafeNameError as exc:
        raise Refused(str(exc)) from exc


class Vaults(NamedTuple):
    names: tuple[str, ...]
    default_name: str
    default_path: Path


def configured_vaults(runner: Runner) -> Vaults:
    proc = runner(["lore", "vault", "ls", "--json"])
    path = locate_default_vault(lambda _argv: proc)
    rows = json.loads(proc.stdout)
    if not all(isinstance(r, dict) and isinstance(r.get("name"), str) for r in rows):
        raise DocketError("cannot list vaults: lore printed a vault without a name")
    default = next(r["name"] for r in rows if r.get("scope") == "default")
    return Vaults(tuple(r["name"] for r in rows), default, path)


def require_configured(vaults: Vaults, names: Sequence[str]) -> None:
    for name in names:
        if name not in vaults.names:
            raise Refused(f"vault {name!r} is not one of the configured vaults ({', '.join(vaults.names)})")


def show_record(runner: Runner, vault: str, kind: str, slug: str) -> "subprocess.CompletedProcess[str]":
    """`lore record show --json` for one record; the caller reads the exit code and the `sidecar` it prints."""
    return runner(["lore", "record", "show", "--json", "--vault", vault, f"{kind}/{slug}"])


def check_kind(runner: Runner, ref: Ref) -> None:
    """Refuse a record that does not resolve, or is not a spec, a task or a blob keyworded `note`."""
    refusal = None
    try:
        proc = show_record(runner, *ref)
    except (OSError, subprocess.SubprocessError) as exc:
        raise StoreError(f"cannot look up {ref.text}: {exc}") from exc
    if proc.returncode != 0:
        refusal = f"{ref.text} does not resolve to a record"
    else:
        try:
            sidecar = json.loads(proc.stdout)["sidecar"]
            keywords = sidecar.get("keywords") or []
        except (ValueError, KeyError, TypeError, AttributeError):
            raise Refused(f"{ref.text}: lore printed a record that cannot be read") from None
        if ref.kind in LISTABLE_KINDS or (ref.kind == "blob" and NOTE_KEYWORD in keywords):
            return
        refusal = f"{ref.text} is not a spec, a task or a note; the docket holds only those"
    raise Refused(refusal, [{"ref": ref.text, "status": "refused"}])
