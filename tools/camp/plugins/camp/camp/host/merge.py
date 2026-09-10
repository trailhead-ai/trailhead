"""Merge a local answer with per-host answers into one ordered result.

This is the pure merge `docs/design/the-all-hosts-answer-merges-every-declared-machine.md`
fixes: given the local answer (already computed by the caller — `camp
list`'s or `camp sessions`' own value-returning local path) and the remote
answers already collected per declared host (`camp.host.relay.HostAnswer`,
one per host, in `hosts.toml` declaration order), it returns one merged
`(rows, notices, exit_code)` with:

  - the local block first, then each declared host's rows in the order the
    caller supplied them — nothing sorted across machines, nothing
    de-duplicated;
  - every row stamped with the machine it came from (`self_host_name()` or
    `None` for the local block; remote rows already carry `host` from
    `answer_for_host`);
  - remote rows filtered to the resolved group when one was given, dropping
    a row whose `group` is null along with rows of other groups — a row
    with no `group` key at all (a failure row) is never filtered, because
    the group axis has nothing to say about it;
  - the exit code taken from the local answer alone, whatever the declared
    machines did.

This module does no I/O, no printing, no exiting, no network, and holds no
state across calls — it is a pure function over values its caller already
collected. Contacting the declared hosts (concurrently, through a thread
pool) is the next task's job; this module trusts whatever order and content
its caller hands it for `host_answers`.
"""
from __future__ import annotations

from typing import Any, Sequence

from .relay import HostAnswer


def merge_all_hosts_answer(
    local_rows: list[dict[str, Any]],
    local_notices: list[str],
    local_exit_code: int,
    *,
    self_name: str | None,
    host_answers: Sequence[tuple[str, HostAnswer]] = (),
    hosts_error: str | None = None,
    group: str | None = None,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Merge the local answer with every declared host's answer.

    Args:
        local_rows: The local answer's own rows (from `cmd_ls_group`'s
            entries projected onto the list schema, or from
            `_sessions_live_answer`'s rows) — untouched beyond the `host`
            stamp this function adds.
        local_notices: The local answer's own stderr notices, in order.
        local_exit_code: The local answer's own exit code — becomes the
            merged answer's exit code unconditionally.
        self_name: This machine's own declared name
            (`camp.host.config.self_host_name()`), or `None` when
            undeclared. Stamped onto every local row as `host`.
        host_answers: One `(host_name, HostAnswer)` pair per declared host,
            already in `hosts.toml` declaration order. Empty when no hosts
            are declared. Ignored when `hosts_error` is given — an
            unparsable `hosts.toml` means the declared hosts could never be
            enumerated, so there is nothing here to merge.
        hosts_error: Set only when `hosts.toml` itself failed to parse —
            the collection failure that leaves no hosts to contact at all.
            Rendered as one `ok: false` row naming the file (the caller
            supplies the already-formatted detail, e.g. from
            `HostConfigError`) rather than silently returning local rows
            alone, which would read as "no machines are declared" — a
            different and false statement.
        group: The resolved group to narrow the merged rows to, or `None`
            to apply no filter. Never crosses the wire — every remote
            invocation is already the all-groups form; this narrows the
            rows already in hand.

    Returns:
        `(rows, notices, exit_code)` — the same shape a value-returning
        local answer already returns.
    """
    rows: list[dict[str, Any]] = [
        {**row, "host": self_name} for row in local_rows
    ]
    notices: list[str] = list(local_notices)

    if hosts_error is not None:
        rows.append({"ok": False, "host": None, "reason": hosts_error})
        notices.append(f"camp: {hosts_error}")
    else:
        for _host_name, answer in host_answers:
            rows.extend(answer.rows)
            notices.extend(answer.notices)

    if group is not None:
        rows = [r for r in rows if "group" not in r or r.get("group") == group]

    return rows, notices, local_exit_code
