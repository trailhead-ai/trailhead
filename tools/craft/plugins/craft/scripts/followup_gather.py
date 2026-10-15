#!/usr/bin/env python3
"""Follow-up gathering, entry half: resolve a spec across the configured lore
vaults and decide whether its slice loop has ended.

Usage:
    followup_gather.py <spec-name>

`<spec-name>` is the bare name of a spec record (the part after `spec/`) and
must match `^[A-Za-z0-9._/-]+$`, must not start with `-`, and must not
contain a `..` path segment; anything else is refused before `lore` is ever
invoked. A name starting with `-` is only reachable after a `--` separator,
since otherwise it parses as an option.

The spec is looked up with `lore vault ls` followed by `lore record show
--json --vault <vault> spec/<name>` in each listed vault. Exactly one vault
must hold it. Its slice loop has ended when it carries the label
`craft/slice-loop=complete` (entry `complete-marker`), carries
`craft/slice-loop=stopped` (entry `stopped-marker`), or has status `complete`
(entry `complete-status`), checked in that order.

Stdout on exit 0 is one JSON object: `spec` (the name given), `vault` (the
vault holding it) and `entry` (how the loop ended).

Exit codes:
    0  the slice loop has ended; the JSON object is on stdout.
    1  refused: the spec exists but its slice loop has not ended. stderr
       carries `reason-code: slice-loop-not-ended`.
    2  could not certify. stderr carries one `reason-code:` line:
       invalid-spec-name, invalid-arguments, no-vaults, spec-not-found, spec-ambiguous,
       not-a-spec, malformed-sidecar, empty-sidecar, lore-missing,
       lore-timeout, lore-failed, lore-output-not-json, internal-error.

Invariants and security posture:
- Every `lore` call is an argv list run without a shell, with a timeout and
  no stdin.
- `lore`'s stdout and stderr are vault-derived and are never printed: a
  failure prints its `reason-code:` and nothing parsed from `lore`.
- Any unexpected exception exits 2 with `reason-code: internal-error`. Python's
  default uncaught-exception exit is 1, which would read as the refusal.
- A sidecar that is valid JSON but lacks `kind` or `status` is a failed
  lookup, never a verdict.

`--help` / `-h` prints usage and exits 0.

`lore_runner` is the single seam through which every `lore` call is made, and
`resolve_spec` takes the runner as an argument, so callers and tests can
substitute it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

NAME_RE = re.compile(r"[A-Za-z0-9._/-]+")
LORE_TIMEOUT_SECONDS = 30
LOOP_LABEL = "craft/slice-loop"
MARKER_ENTRIES = {"complete": "complete-marker", "stopped": "stopped-marker"}


class GatherError(Exception):
    """A failure that maps to a stable `reason-code:`; carries no vault text."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def lore_runner(argv: list[str]) -> subprocess.CompletedProcess:
    """Run `lore <argv>` with no shell and a timeout. May raise
    FileNotFoundError (no `lore` on PATH) or subprocess.TimeoutExpired."""
    return subprocess.run(
        ["lore", *argv],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=LORE_TIMEOUT_SECONDS,
        shell=False,
    )


def _call(runner, argv: list[str]) -> subprocess.CompletedProcess:
    try:
        return runner(argv)
    except FileNotFoundError:
        raise GatherError("lore-missing") from None
    except subprocess.TimeoutExpired:
        raise GatherError("lore-timeout") from None


def list_vaults(runner) -> list[str]:
    done = _call(runner, ["vault", "ls"])
    if done.returncode != 0:
        raise GatherError("lore-failed")
    names = [line.split("\t", 1)[0].strip() for line in done.stdout.splitlines()]
    names = [n for n in names if n]
    if not names:
        raise GatherError("no-vaults")
    return names


def _parse_sidecar(stdout: str) -> dict:
    try:
        doc = json.loads(stdout)
    except ValueError:
        raise GatherError("lore-output-not-json") from None
    if not isinstance(doc, dict):
        raise GatherError("lore-output-not-json")
    sidecar = doc.get("sidecar")
    if not sidecar:
        raise GatherError("empty-sidecar")
    if not isinstance(sidecar, dict) or "kind" not in sidecar or "status" not in sidecar:
        raise GatherError("malformed-sidecar")
    return sidecar


def resolve_spec(runner, name: str) -> tuple[str, dict]:
    """Return (vault, sidecar) for `spec/<name>`, found in exactly one vault."""
    found: list[tuple[str, dict]] = []
    for vault in list_vaults(runner):
        done = _call(
            runner, ["record", "show", "--json", "--vault", vault, f"spec/{name}"]
        )
        if done.returncode != 0:
            if "record not found" in done.stderr:
                continue
            raise GatherError("lore-failed")
        found.append((vault, _parse_sidecar(done.stdout)))
    if not found:
        raise GatherError("spec-not-found")
    if len(found) > 1:
        raise GatherError("spec-ambiguous")
    vault, sidecar = found[0]
    if sidecar["kind"] != "spec":
        raise GatherError("not-a-spec")
    return vault, sidecar


def entry_verdict(sidecar: dict) -> str | None:
    """How the spec's slice loop ended, or None when it has not."""
    labels = sidecar.get("labels")
    marker = labels.get(LOOP_LABEL) if isinstance(labels, dict) else None
    if marker in MARKER_ENTRIES:
        return MARKER_ENTRIES[marker]
    if sidecar["status"] == "complete":
        return "complete-status"
    return None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise GatherError("invalid-arguments")


def _name_is_safe(name: str) -> bool:
    return (
        NAME_RE.fullmatch(name) is not None
        and not name.startswith("-")
        and ".." not in name.split("/")
    )


def _refuse(code: int, reason: str) -> int:
    print(f"reason-code: {reason}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None, runner=None) -> int:
    try:
        parser = _Parser(
            prog="followup_gather.py", description="Decide whether a spec's slice loop has ended."
        )
        parser.add_argument("spec_name", help="bare spec record name (the part after spec/)")
        try:
            name = parser.parse_args(argv).spec_name
        except GatherError as err:
            return _refuse(2, err.reason)
        if not _name_is_safe(name):
            return _refuse(2, "invalid-spec-name")
        try:
            vault, sidecar = resolve_spec(runner or lore_runner, name)
        except GatherError as err:
            return _refuse(2, err.reason)
        entry = entry_verdict(sidecar)
        if entry is None:
            return _refuse(1, "slice-loop-not-ended")
        print(json.dumps({"spec": name, "vault": vault, "entry": entry}))
        return 0
    except Exception:
        return _refuse(2, "internal-error")


if __name__ == "__main__":
    sys.exit(main())
