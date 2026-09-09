#!/usr/bin/env python3
"""Migration-suppression directive renderer — resolves a plan's target
repository's maturity level and reports whether migration and backfill
tasks are suppressed for a plan built against it.

Usage:
    lore record show spec/<name> \\
      | migration_bar.py [--target-repo <name>] [--agent-instruction-file <path>]

The spec body arrives on stdin, mirroring the sibling renderers
(`maturity_bars.py`, `edge_confirmations.py`, `maturity_stamp.py`).
`--agent-instruction-file` names the repository's agent-instruction file
(e.g. `CLAUDE.md`), consulted only when the spec carries no `## Maturity`
section at all — the same contract `maturity_bars.py` already uses.

Every primitive here is imported from a sibling script rather than
re-derived: the stamp grammar comes from `maturity_stamp.parse_entries`,
and the absent-stamp fallback chain (agent-instruction-file, then
`production`) comes from `maturity_bars.resolve_level` — never
re-implemented here. The canonical "migration and backfill" concern phrase
this renderer names in its suppression block is imported from
`maturity_bars._CONCERNS` rather than retyped.

`--target-repo` names the plan's target repository. It is optional when
the spec's `## Maturity` section names exactly one repository — that
repository is the implicit target — and required once the section names
more than one, since there is then no way to tell which stamped level
applies without being told. A `--target-repo` naming a repository absent
from a stamp that names other repositories refuses (reason-code
`target-repo-absent`) rather than falling back to any level: refusal and
the safe direction coincide here (neither suppresses), so refusal is the
legible outcome. Omitting `--target-repo` against a multi-repository
stamp likewise refuses (reason-code `target-repo-required`).

When the spec carries no `## Maturity` section at all — including empty
stdin, a legitimate no-spec case rather than a refusal, mirroring
`maturity_bars.py`'s own treatment of it — `--target-repo` is irrelevant:
there is no per-repository stamp to select from, and resolution falls
straight through to `maturity_bars.resolve_level`'s own
agent-instruction-file / default fallback chain exactly as it does for
that renderer.

On a resolved level of `prototype`, this renderer prints a block naming
the resolved level, its basis, and — when a stamp was consulted — the
target repository the level was resolved for, stating that migration and
backfill tasks are suppressed for this plan, and stating the one
condition that reopens them: acceptance criteria that require preserving
existing state. At every other level it prints a block stating nothing is
suppressed. The target repository is omitted on the absent-stamp fallback
chain, where there is no per-repository stamp to name it from.

No refusal ever writes the offending value a `StampError` may carry to
stdout or stderr — the stamp is vault-writable content read by an agent
deciding what to do next. This script's own `RenderError` carries only a
`reason_code`; it is structurally unable to hold the offending text at
all, mirroring `edge_confirmations.py`'s and `maturity_bars.py`'s own
`RenderError`.

Stdout on success (exit 0), the directive block, exactly once.

Exit codes:
    0  resolved — the block is printed, exactly once.
    2  fail-closed — nothing is printed on stdout, and stderr names a
       stable `reason-code:` — this script's own `invalid-utf8-stdin` /
       `target-repo-required` / `target-repo-absent`, any reason-code
       `maturity_stamp.py`'s `parse_entries` raises (`section-absent` is
       not fail-closed here — it is the trigger for the fallback chain), or
       `maturity_bars.py`'s own `agent-instruction-file-unreadable`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import maturity_bars  # noqa: E402
from maturity_stamp import StampError, parse_entries  # noqa: E402

_PROTOTYPE_LEVEL = "prototype"

_INVALID_UTF8_STDIN_REASON_CODE = "invalid-utf8-stdin"
_TARGET_REPO_REQUIRED_REASON_CODE = "target-repo-required"
_TARGET_REPO_ABSENT_REASON_CODE = "target-repo-absent"

_SECTION_ABSENT_REASON_CODE = "section-absent"

_MIGRATION_CONCERN = maturity_bars._CONCERNS[1]


class RenderError(Exception):
    """A fail-closed outcome: `reason_code` is always set. Never carries
    the offending text a `StampError` may have named — this script never
    forwards that text to its own caller."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _err(msg: str) -> None:
    print(f"migration-bar: {msg}", file=sys.stderr)


def resolve_target_level(
    spec_text: str, target_repo: str | None, agent_instruction_file: str | None
) -> tuple[str, str, str | None]:
    """Return `(level, basis, resolved_repo)` for the plan's target
    repository, or raise `RenderError` on any fail-closed outcome.
    `resolved_repo` is the stamp key the level was read from — set only
    when a stamp was consulted, since only then has the name passed the
    stamp grammar's own character class. It is `None` on the absent-stamp
    fallback chain, where there is no per-repository stamp to select from."""
    try:
        entries = parse_entries(spec_text)
    except StampError as e:
        if e.reason_code != _SECTION_ABSENT_REASON_CODE:
            raise RenderError(e.reason_code) from e
        # No stamp at all — target_repo has nothing to select from, so
        # resolution falls straight through to the shared absent-stamp
        # fallback chain, imported rather than re-derived.
        try:
            level, basis, _entries = maturity_bars.resolve_level(
                spec_text, agent_instruction_file
            )
        except maturity_bars.RenderError as e:
            raise RenderError(e.reason_code) from e
        return level, basis, None

    if target_repo is None:
        if len(entries) != 1:
            raise RenderError(_TARGET_REPO_REQUIRED_REASON_CODE)
        resolved_repo = next(iter(entries))
        return entries[resolved_repo], "stamp", resolved_repo

    if target_repo not in entries:
        raise RenderError(_TARGET_REPO_ABSENT_REASON_CODE)

    return entries[target_repo], "stamp", target_repo


def render(level: str, basis: str, target_repo: str | None) -> str:
    repo_field = f" (target-repo: {target_repo})" if target_repo is not None else ""
    header = f"migration-bar: (level: {level}) (basis: {basis}){repo_field}"

    if level == _PROTOTYPE_LEVEL:
        return (
            f"{header} — suppressed: {_MIGRATION_CONCERN}\n"
            f"Migration and backfill tasks are suppressed for this plan.\n"
            "Reopens only if the plan's acceptance criteria require "
            "preserving existing state — decompose the migration/backfill "
            "work normally in that case despite this suppression.\n"
        )

    return (
        f"{header} — not-suppressed: {_MIGRATION_CONCERN}\n"
        "Nothing is suppressed; decompose migration and backfill tasks "
        "normally for this plan.\n"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target-repo")
    parser.add_argument("--agent-instruction-file")
    args = parser.parse_args(argv)

    raw = sys.stdin.buffer.read()

    try:
        spec_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _err(f"reason-code: {_INVALID_UTF8_STDIN_REASON_CODE}")
        return 2

    try:
        level, basis, target_repo = resolve_target_level(
            spec_text, args.target_repo, args.agent_instruction_file
        )
    except RenderError as e:
        _err(f"reason-code: {e.reason_code}")
        return 2

    sys.stdout.write(render(level, basis, target_repo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
