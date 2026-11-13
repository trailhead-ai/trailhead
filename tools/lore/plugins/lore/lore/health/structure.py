"""Structural check: one finding per defect of a malformed record. Pure; writes nothing.

Findings come from three places: the scan's own unreadable records, the
``model.validate`` errors of each readable sidecar (plus a kind that differs
from its directory), and a confined walk for orphan artifacts the scan skips
silently. The walk uses ``lstat`` only and never opens or follows a file, and a
symlinked orphan counts as an escaping skip, returned as a number and never as a path.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from ..record import model as record_model
from ..record import rename
from . import references
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


def _orphans(vault: str, root: Path):
    """Return ``(findings, escaping_skips)`` for flat artifacts missing their partner."""
    findings: list[StructuralFinding] = []
    skips = 0

    def kind_dir_skipped():
        nonlocal skips
        skips += 1

    for kind_dir, _ in rename._confined_kind_dirs(root, kind_dir_skipped):
        kind = kind_dir.name
        try:
            with os.scandir(kind_dir) as it:
                entries = list(it)
        except OSError:
            continue
        present: dict[str, dict[str, bool]] = {}
        for entry in entries:
            stem, ext = os.path.splitext(entry.name)
            if ext not in (".md", ".json") or references.resolve(stem, kind) is None:
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            is_link = entry.is_symlink()
            if is_link or stat.S_ISREG(st.st_mode):
                present.setdefault(stem, {})[ext] = is_link
        for stem, exts in present.items():
            if len(exts) == 2:
                continue
            if any(exts.values()):
                skips += 1
                continue
            code = CODE_SIDECAR_MISSING if ".md" in exts else CODE_BODY_MISSING
            detail = "has a body but no sidecar" if ".md" in exts else "has a sidecar but no body"
            findings.append(StructuralFinding(vault, f"{kind}/{stem}", code, detail))
    return findings, skips


def check_structure(scan: VaultScan, root) -> tuple[list[StructuralFinding], int]:
    """Return ``(findings, escaping_skips)`` for one scanned vault rooted at *root*."""
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
    orphan_findings, skips = _orphans(scan.vault, Path(root))
    findings.extend(orphan_findings)
    findings.sort(key=lambda f: (f.vault, f.record_id, f.code, f.detail))
    return findings, skips
