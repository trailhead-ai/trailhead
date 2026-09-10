"""Monitor's machine-readable completion channel — the outcome-file grammar.

`monitor` (see `agents/monitor.md`) may be handed an `outcome_file` path by
its dispatcher. On reaching a terminal state it writes exactly one line to
that path, naming the state: `MERGED` | `READY <reason>` | `BLOCKED
<reason>` | `STOPPED <reason>`, so a caller that cannot wait on monitor's
prose reply can poll for its result instead.

This module also owns the strategy-disclosure grammar: `portage merge`
prints one line to stderr per pull request naming the merge strategy it
resolved and why (`trailhead.vcs.github.resolve_merge_strategy`'s reason,
prefixed from `RESOLUTION_REASON_PREFIXES`). `parse_strategy_disclosure`
reads one such line back; `summarize_strategy_disclosures` aggregates a
run's lines into monitor's report `Strategy:` field (see "Strategy
disclosure" in `agents/monitor.md`), attributing each pull request's
strategy to its repository whenever a run resolved more than one.

This module owns only the read/parse side. Monitor is a prose-driven
subagent — there is no monitor-side Python that writes the file or the
report — so nothing here writes an outcome file or a report; those
contracts live in `agents/monitor.md` itself.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

#: The four terminal-state tokens monitor's outcome file may carry.
MONITOR_OUTCOME_TOKENS = frozenset({"MERGED", "READY", "BLOCKED", "STOPPED"})

#: `MERGED` needs no argument (there is nothing further to say); the other
#: three are always followed by a reason.
_TOKENS_REQUIRING_ARGUMENT = frozenset({"READY", "BLOCKED", "STOPPED"})

_MAX_ARGUMENT_CHARS = 200


def parse_monitor_outcome(line: str) -> tuple[str | None, str]:
    """Split a monitor outcome line into `(token, argument)`.

    Only the first physical line is parsed — a well-formed outcome file has
    exactly one, but a caller reading a file some other process appended to
    should not have later lines corrupt the result. Returns `(None,
    <line>)` when the first line is not one of `MONITOR_OUTCOME_TOKENS`, or
    when a token that requires an argument (`READY`/`BLOCKED`/`STOPPED`) was
    given none — the caller treats that as a validation failure.
    """
    first_line = line.strip().splitlines()[0].strip() if line.strip() else ""
    token, _, argument = first_line.partition(" ")
    argument = argument.strip()
    if token not in MONITOR_OUTCOME_TOKENS:
        return None, first_line[:_MAX_ARGUMENT_CHARS]
    if token in _TOKENS_REQUIRING_ARGUMENT and not argument:
        return None, first_line[:_MAX_ARGUMENT_CHARS]
    return token, argument


def read_monitor_outcome(path: Path) -> str:
    """Return the outcome line monitor wrote, or a synthesized `BLOCKED` line.

    A missing or empty file means monitor died, timed out, or never ran —
    the crashed signal callers rely on (see the module docstring and
    `agents/monitor.md`). `BLOCKED` is the closest of the four terminal
    tokens to that meaning: an unattended caller cannot tell "crashed" from
    "stuck awaiting an operator" from the file alone, and both need the same
    response — hold, don't treat as merged or ready, and surface the file's
    absence for a human to investigate.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return f"BLOCKED no outcome written to {path.name} — monitor died, timed out, or never ran"
    if not text.strip():
        return f"BLOCKED empty outcome file {path.name} — monitor wrote no return token"
    return text


class StrategyDisclosure(NamedTuple):
    """One pull request's resolved merge strategy, parsed off `portage merge`'s
    per-pull-request stderr disclosure line."""

    pr_number: str
    member_name: str
    strategy: str
    reason: str


#: `_merge_prs` (`trailhead/vcs/github.py`) prints exactly this shape once per
#: pull request, immediately before attempting its merge:
#: `portage merge: PR #<pr_number> (<member_name>): strategy '<strategy>' — <reason>`
_STRATEGY_DISCLOSURE_LINE = re.compile(
    r"^portage merge: PR #(?P<pr_number>\d+) \((?P<member_name>[^)]+)\): "
    r"strategy '(?P<strategy>[a-z]+)' — (?P<reason>.+)$"
)


def parse_strategy_disclosure(line: str) -> StrategyDisclosure | None:
    """Parse one of `portage merge`'s per-pull-request strategy disclosure
    lines, or return `None` when `line` isn't one.

    `reason` is carried verbatim — it already starts with one of
    `trailhead.vcs.github.RESOLUTION_REASON_PREFIXES`'s pairwise-distinct
    prefixes, which is what lets `summarize_strategy_disclosures` tell a
    capability-lookup failure apart from an ordinary automatic selection or
    an explicitly configured strategy without this module inspecting the
    reason text itself.
    """
    match = _STRATEGY_DISCLOSURE_LINE.match(line.strip())
    if not match:
        return None
    return StrategyDisclosure(**match.groupdict())


def summarize_strategy_disclosures(disclosures: list[StrategyDisclosure]) -> str:
    """Render monitor's report `Strategy:` field from a run's disclosures.

    Empty input (nothing merged this run) renders `n/a`. When every
    disclosure names the same strategy and the same reason, it is reported
    once: `<strategy> — <reason>`. Otherwise each pull request's strategy is
    attributed to its own repository, in the order given:
    `<member_name>=<strategy> (<reason>)`, comma-separated — never collapsed
    to a single strategy name, since the per-disclosure reason is what
    distinguishes a capability-lookup failure or an explicitly configured
    strategy from an ordinary automatic selection.
    """
    if not disclosures:
        return "n/a"
    first = disclosures[0]
    if all(d.strategy == first.strategy and d.reason == first.reason for d in disclosures):
        return f"{first.strategy} — {first.reason}"
    return ", ".join(f"{d.member_name}={d.strategy} ({d.reason})" for d in disclosures)
