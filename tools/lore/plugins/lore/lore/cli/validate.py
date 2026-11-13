"""``lore validate`` — a read-only check that a vault's records are well formed and its links resolve.

Usage: ``lore validate [--vault NAME | --path DIR]``.

**Scoping.** With no flag every configured vault is checked and its links are
resolved against every configured vault. ``--vault NAME`` checks only that vault
and still resolves against every configured vault. ``--path DIR`` reads no lore
configuration: DIR is scanned as one vault labelled with the fixed word ``path``
and its links resolve only within DIR, even when DIR is a configured vault's
folder. A ``--path`` directory holding no records is a ``no-records`` finding, so
an empty or wrongly laid-out folder never passes silently.

**Checks.** The structural check runs on every checked vault, and the link check
(sessions included) on every checked record against the resolution set.

**Output.** One stdout line per finding, sorted by vault, record id, code and
detail so two runs over one tree are byte-identical:

* ``<vault> <kind>/<name>: <code>: <detail>`` for a record finding;
* ``<vault>: <code>: <detail>`` for a vault-level finding.

Record codes: ``sidecar-malformed``, ``sidecar-unreadable``, ``body-unreadable``,
``oversize``, ``sidecar-missing``, ``body-missing``, ``kind-mismatch``,
``invalid-sidecar`` (structure); ``missing-target``, ``wrong-kind`` (names the
kinds the target exists under), ``not-a-record-id`` (a body line or entry
position, never the text) (links). Vault codes: ``vault-missing``,
``vault-unreadable``, ``records-outside-vault`` (a count, never a path),
``no-records`` (``--path`` only) and ``links-not-checked`` (names the vault that
was not fully read, so no link could be confirmed missing). Vault-level findings
are reported for every vault in the checked or resolution set. Every line is
escaped through the shared control-character escaper, since record names, field
names and validation detail are untrusted text.

A stderr summary always follows a run: ``lore: validated <N> records in <M>
vaults: <K> findings``, with per-code counts when K > 0.

**Exit codes.** 0 with empty stdout when there are no findings; 1 when there are
any findings, vault-level ones included (also when a closed stdout pipe ends the
run); 2 with one ``lore:`` stderr line when it cannot run (unreadable or invalid
config, unknown ``--vault``, ``--path`` not a directory, both flags given, or any
unexpected error, never a traceback). A gate must fail on any non-zero exit.

Read-only: nothing is written, and no lock, index or scaffolding file is created.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..health import dangling as dangling_mod
from ..health import scan as scan_mod
from ..health import structure as structure_mod
from ..health.model import VaultScan
from .health import _malformed_evidence
from .record import _printable

PATH_LABEL = "path"

CODE_VAULT_MISSING = "vault-missing"
CODE_VAULT_UNREADABLE = "vault-unreadable"
CODE_OUTSIDE = "records-outside-vault"
CODE_NO_RECORDS = "no-records"
CODE_LINKS_NOT_CHECKED = "links-not-checked"
CODE_MISSING_TARGET = "missing-target"
CODE_WRONG_KIND = "wrong-kind"
CODE_NOT_A_RECORD_ID = "not-a-record-id"

# Seam: the run reads vaults only through this.
_scan_vault = scan_mod.scan_vault
_check_structure = structure_mod.check_structure


class _CannotRun(Exception):
    pass


@dataclass(frozen=True)
class Finding:
    vault: str
    record_id: str
    code: str
    detail: str

    def line(self) -> str:
        who = f"{self.vault} {self.record_id}" if self.record_id else self.vault
        return _printable(f"{who}: {self.code}: {self.detail}")

    def key(self) -> tuple[str, str, str, str]:
        return (self.vault, self.record_id, self.code, self.detail)


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _structure(scan: VaultScan, root: Path) -> tuple[list[Finding], int]:
    """Structural findings, and the escaping skips the orphan walk adds beyond the scan's own.

    A walk that cannot read the vault is a ``vault-unreadable`` finding, unless the
    scan already reported one, so a failure never reads as a clean pass.
    """
    try:
        found, skips = _check_structure(scan, root)
    except OSError:
        if scan.unreadable_reason is not None:
            return [], 0
        return [Finding(scan.vault, "", CODE_VAULT_UNREADABLE, "the vault directory could not be read")], 0
    return [Finding(f.vault, f.record_id, f.code, f.detail) for f in found], skips


def _vault_level(scan: VaultScan, extra_skips: int) -> list[Finding]:
    out = []
    if scan.unreadable_reason == scan_mod.REASON_VAULT_MISSING:
        out.append(Finding(scan.vault, "", CODE_VAULT_MISSING, "the vault directory does not exist"))
    elif scan.unreadable_reason is not None:
        out.append(Finding(scan.vault, "", CODE_VAULT_UNREADABLE, "the vault directory could not be read"))
    outside = scan.skipped_escaping + extra_skips
    if outside:
        noun = _plural(outside, "record lies", "records lie")
        verb = "was" if outside == 1 else "were"
        out.append(Finding(scan.vault, "", CODE_OUTSIDE, f"{noun} outside the vault root and {verb} not read"))
    return out


def _link_finding(f: dangling_mod.Finding) -> Finding:
    record_id = f"{f.kind}/{f.name}"
    if f.malformed:
        detail = f"{f.field} → {_malformed_evidence(f.field, f.locations)}"
        return Finding(f.vault, record_id, CODE_NOT_A_RECORD_ID, detail)
    if f.exists_as:
        detail = f"{f.field} → {f.target}: exists only as {', '.join(f.exists_as)}"
        return Finding(f.vault, record_id, CODE_WRONG_KIND, detail)
    return Finding(f.vault, record_id, CODE_MISSING_TARGET, f"{f.field} → {f.target}: no such record")


def validate(only: str | None, path: str | None) -> tuple[list[Finding], int, int]:
    """Run the checks; return ``(sorted findings, records checked, vaults checked)``."""
    if only is not None and path is not None:
        raise _CannotRun("give --vault or --path, not both")
    if path is not None:
        root = Path(path)
        if not root.is_dir():
            raise _CannotRun(f"{path} is not a directory")
        roots = [(PATH_LABEL, root)]
        checked = [PATH_LABEL]
    else:
        try:
            roots = scan_mod.resolve_vaults()
            if only is not None:
                scan_mod.resolve_vaults(only)
        except scan_mod.HealthResolutionError as exc:
            if only is not None and exc.configured:
                raise _CannotRun(
                    f"no vault named '{only}' is configured (configured: {', '.join(exc.configured)})"
                ) from exc
            raise _CannotRun(str(exc)) from exc
        checked = [n for n, _ in roots if only is None or n == only]

    scans = [_scan_vault(name, root) for name, root in roots]
    findings: list[Finding] = []
    records = 0
    for scan, (_, root) in zip(scans, roots):
        extra = 0
        if scan.vault in checked:
            structural, extra = _structure(scan, Path(root))
            findings += structural
            records += len(scan.records) + len(scan.unreadable)
            if path is not None and not scan.records and not scan.unreadable:
                findings.append(Finding(scan.vault, "", CODE_NO_RECORDS, "the directory holds no records"))
        findings += _vault_level(scan, extra)

    result = dangling_mod.check_dangling(scans, checked, include_sessions=True)
    if isinstance(result, dangling_mod.DanglingSkipped):
        for name in result.vaults:
            findings.append(Finding(name, "", CODE_LINKS_NOT_CHECKED,
                                    "the vault could not be fully read, so links were not checked"))
    else:
        findings += [_link_finding(f) for f in result.findings]

    findings.sort(key=Finding.key)
    return findings, records, len(checked)


def _summary(findings: list[Finding], records: int, vaults: int) -> str:
    text = f"lore: validated {records} records in {vaults} vaults: {len(findings)} findings"
    if findings:
        counts = Counter(f.code for f in findings)
        text += " (" + ", ".join(f"{c} {n}" for c, n in sorted(counts.items())) + ")"
    return _printable(text)


def _stdout_closed() -> None:
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def cmd_validate(args) -> int:
    """Run ``lore validate``; see the module docstring for the exit contract."""
    try:
        findings, records, vaults = validate(args.vault, args.path)
    except _CannotRun as exc:
        print(_printable(f"lore: cannot validate: {exc}"), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the contract is one line, never a traceback
        print(_printable(f"lore: validate failed: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return 2
    try:
        for f in findings:
            print(f.line())
        sys.stdout.flush()
    except BrokenPipeError:
        _stdout_closed()
        print(_summary(findings, records, vaults), file=sys.stderr)
        return 1
    print(_summary(findings, records, vaults), file=sys.stderr)
    return 1 if findings else 0


def add_validate_subparser(sub) -> None:
    """Register the ``validate`` command parser."""
    p = sub.add_parser(
        "validate",
        help="Check records are well formed and their links resolve; exit non-zero on any finding (read-only)",
        description=(
            "Report each structurally invalid record and each link that points at no record, "
            "or at a record of a different kind than it names, one line per finding. With no "
            "flag every configured vault is checked; --vault NAME checks one configured vault "
            "(links still resolve against every configured vault); --path DIR checks a "
            "directory as one vault and resolves links only within it. Read-only. Exit status: "
            "0 no findings; 1 findings; 2 cannot run. A gate must fail on any non-zero exit."
        ),
    )
    p.add_argument("--vault", default=None, metavar="NAME", help="Check only this configured vault")
    p.add_argument("--path", default=None, metavar="DIR",
                   help="Check this directory as a vault, reading no lore configuration")
    p.set_defaults(func=cmd_validate)
