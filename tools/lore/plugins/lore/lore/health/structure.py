"""Structural check: one finding per defect of a malformed record. Pure; writes nothing.

Findings come from three places: the scan's own unreadable records, the
``model.validate`` errors of each readable sidecar (plus a kind that differs
from its directory), and a confined walk for orphan artifacts the scan skips
silently. The walk uses ``scandir`` and realpath only and never opens or reads a
file. A symlinked orphan whose realpath leaves the vault is an escaping skip,
returned as a number and never as a path; one resolving inside the vault is the
orphan defect it is.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..record import model as record_model
from ..record import rename, store
from . import references, scan as scan_mod
from .model import VaultScan

CODE_SIDECAR_MISSING = "sidecar-missing"
CODE_BODY_MISSING = "body-missing"
CODE_KIND_MISMATCH = "kind-mismatch"
CODE_INVALID_SIDECAR = "invalid-sidecar"


@dataclass(frozen=True)
class StructuralFinding:
    vault: str
    record_id: str
    code: str
    detail: str


def _orphans(vault: str, root: Path, reported: frozenset[tuple[str, str]] = frozenset()):
    """Return ``(findings, escaping_skips)`` for flat artifacts missing their partner.

    A symlinked artifact of a complete pair that resolves to no existing file
    (dangling or a loop) is unreadable, which the scan skips silently; it is a
    finding here unless the scan already reported that record (*reported* holds
    its ``(kind, stem)`` pairs). The link is resolved, never opened.

    Escaping artifacts are counted once across scan and walk. The scan counts a
    symlinked kind directory and any escaping ``.md``, so the walk skips those;
    it counts only an escaping ``.json`` orphan, whatever its name, which the
    scan never sees; an in-vault entry with a non-record name is ignored. An
    existing non-symlink entry of any type counts as present, so an unreadable
    sidecar is the scan's one finding and not also a missing one.
    """
    findings: list[StructuralFinding] = []
    skips = 0
    for kind_dir, root_real in rename._confined_kind_dirs(root):
        kind = kind_dir.name
        try:
            with os.scandir(kind_dir) as it:
                entries = list(it)
        except OSError:
            continue
        present: dict[str, dict[str, bool]] = {}
        dead: dict[str, list[str]] = {}
        for entry in entries:
            stem, ext = os.path.splitext(entry.name)
            if ext not in (".md", ".json"):
                continue
            escapes = entry.is_symlink() and not store._realpath_is_descendant(entry.path, root_real)
            if references.resolve(stem, kind) is None:
                if escapes and ext == ".json":
                    skips += 1
                continue
            present.setdefault(stem, {})[ext] = escapes
            if entry.is_symlink() and not escapes and not os.path.exists(entry.path):
                dead.setdefault(stem, []).append(ext)
        for stem, exts in present.items():
            if len(exts) == 2:
                if not any(exts.values()) and (kind, stem) not in reported:
                    for ext in dead.get(stem, ()):
                        code = scan_mod.REASON_SIDECAR_UNREADABLE if ext == ".json" else scan_mod.REASON_BODY_UNREADABLE
                        findings.append(StructuralFinding(vault, f"{kind}/{stem}", code, "record could not be read"))
                continue
            if any(exts.values()):
                if ".json" in exts:
                    skips += 1
                continue
            code = CODE_SIDECAR_MISSING if ".md" in exts else CODE_BODY_MISSING
            detail = "has a body but no sidecar" if ".md" in exts else "has a sidecar but no body"
            findings.append(StructuralFinding(vault, f"{kind}/{stem}", code, detail))
    return findings, skips


def check_structure(scan: VaultScan, root) -> tuple[list[StructuralFinding], int]:
    """Return ``(findings, escaping_skips)`` for one scanned vault rooted at *root*.

    ``escaping_skips`` counts only escaping symlinked ``.json`` orphans, which
    the scan never sees, so it adds to the scan's ``skipped_escaping`` without
    overlap.
    """
    findings = [
        StructuralFinding(u.vault, f"{u.kind}/{u.name}", u.reason, "record could not be read")
        for u in scan.unreadable
    ]
    for rec in scan.records:
        record_id = f"{rec.kind}/{rec.name}"
        declared = rec.sidecar.get("kind")
        if declared in record_model.KINDS and declared != rec.kind:
            findings.append(
                StructuralFinding(
                    scan.vault, record_id, CODE_KIND_MISMATCH,
                    f"sidecar kind {declared!r} differs from directory {rec.kind!r}",
                )
            )
        for error in record_model.validate(rec.sidecar).errors:
            findings.append(StructuralFinding(scan.vault, record_id, CODE_INVALID_SIDECAR, error))
    orphan_findings, skips = _orphans(scan.vault, Path(root), frozenset((u.kind, u.name) for u in scan.unreadable))
    findings.extend(orphan_findings)
    findings.sort(key=lambda f: (f.vault, f.record_id, f.code, f.detail))
    return findings, skips
