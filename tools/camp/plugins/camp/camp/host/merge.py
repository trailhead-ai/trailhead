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
collected.

`answer_all_hosts_concurrently` is the companion that produces the
`host_answers` this merge consumes: it fans the declared hosts out through a
bounded thread pool, one worker per host, and runs the caller's local answer
on its own pool slot so the local work overlaps the fan-out instead of
preceding it. `run_camp` is stateless across calls, so the same injected
`runner` is safe to use from every worker at once. A worker whose call
raises something outside `camp.host.transport`'s closed outcome set is
camp's own bug, not a machine that failed to answer — it is never allowed to
be misread as one, so it is caught and turned into a row whose reason names
it as an internal camp fault rather than reusing any transport-failure
reason.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Sequence

from .config import Host
from .relay import HostAnswer, answer_for_host
from .transport import DEFAULT_CONNECT_TIMEOUT_SECONDS, Runner, default_runner

#: The reason stamped on a host's row when its worker raised something
#: outside the transport's closed outcome set — a bug in camp's own
#: fan-out code, never a real machine failure. Deliberately worded so it
#: cannot collide with any reason `camp.host.relay.answer_for_host` itself
#: produces for an actual host-failure state.
INTERNAL_FAULT_REASON = "internal camp fault — not a host failure"


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
    # The local answer arrives already narrowed to the resolved group by its
    # own caller (`local_list_answer`/`local_sessions_answer`) — it is never
    # re-filtered here. Only remote rows carry groups this side never asked
    # for (every remote invocation is the all-groups form), so only remote
    # rows are narrowed.
    rows: list[dict[str, Any]] = [
        {**row, "host": self_name} for row in local_rows
    ]
    notices: list[str] = list(local_notices)

    if hosts_error is not None:
        rows.append({"ok": False, "host": None, "reason": hosts_error})
        notices.append(f"camp: {hosts_error}")
    else:
        for _host_name, answer in host_answers:
            host_rows = answer.rows
            if group is not None:
                # A failure row (`ok: false`) is never narrowed by group —
                # discriminated on camp's own `ok` field, never on whether a
                # `group` key happens to be present, since a collection-
                # failure row carries `"group": null` and a version-skewed
                # ok row can omit the key entirely.
                host_rows = [
                    r for r in host_rows
                    if not r.get("ok", True) or r.get("group") == group
                ]
            rows.extend(host_rows)
            notices.extend(answer.notices)

    return rows, notices, local_exit_code


LocalAnswer = Callable[[], tuple[list[dict[str, Any]], list[str], int]]

#: A per-host worker: given a declared host's name and its declaration,
#: returns that host's contribution as a `HostAnswer`. Called exactly once
#: per declared host, on its own pool slot — completion order never leaks
#: into the result.
HostWorker = Callable[[str, Host], HostAnswer]


def answer_all_hosts_concurrently(
    local_answer: LocalAnswer,
    hosts: Sequence[tuple[str, Host]],
    *,
    verb: str,
    remote_argv: Sequence[str],
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    runner: Runner = default_runner,
    worker: HostWorker | None = None,
) -> tuple[tuple[list[dict[str, Any]], list[str], int], list[tuple[str, HostAnswer]]]:
    """Compute the local answer and contact every declared host, concurrently.

    Runs *local_answer* and one worker per entry in *hosts* on a single
    bounded thread pool (`len(hosts) + 1` workers — one slot per declared
    host plus one for the local answer), so the local work overlaps the
    fan-out instead of running before or after it. `run_camp` holds no state
    across calls, so the same *runner* is safe to share across every worker.

    Args:
        local_answer: Zero-argument callable returning the local
            `(rows, notices, exit_code)` — the caller's own value-returning
            local path (`cmd_ls_group`'s projection, or
            `_sessions_live_answer`).
        hosts: One `(host_name, Host)` pair per declared host, in
            `hosts.toml` declaration order. The pool starts a worker for
            each entry, but the returned `host_answers` is always in this
            same declared order regardless of which worker finished first —
            completion order never leaks into the result.
        verb: Passed through to the default worker's `answer_for_host` call
            (`"list"` or `"sessions"`) and used to word the internal-fault
            notice when any worker raises.
        remote_argv: Passed through to the default worker's
            `answer_for_host` call — the same all-groups, JSON remote
            command every `--host` verb already builds. Unused when
            *worker* is injected.
        connect_timeout: The operator's resolved connect timeout
            (`camp.host.config.connect_timeout_seconds()`), passed through
            to the default worker's `answer_for_host` call so it composes
            with an injected *worker* rather than duplicating it — an
            injected *worker* decides its own timeout handling and this
            value is unused for it. Defaults to the transport's own
            documented default.
        runner: Injected transport runner, used by the default worker.
            Unused when *worker* is injected.
        worker: Injected per-host worker, called as `worker(host_name,
            host)` once per entry in *hosts*, returning that host's
            `HostAnswer`. Defaults to today's relay worker — one call to
            `answer_for_host` per host, using *verb*, *remote_argv*, and
            *runner* — so a caller fanning out a plain listing sees no
            change. A raising worker (injected or default) is never allowed
            to propagate: it is caught here and turned into a row whose
            reason names it as an internal camp fault rather than a real
            machine failure, and every other host's result still comes
            back.

    Returns:
        `(local_result, host_answers)` — `local_result` is whatever
        *local_answer* returned; `host_answers` is one `(host_name,
        HostAnswer)` pair per entry in *hosts*, in declared order.
    """
    active_worker: HostWorker = worker if worker is not None else (
        lambda host_name, host: answer_for_host(
            verb, host, host_name, remote_argv, connect_timeout=connect_timeout, runner=runner
        )
    )

    with ThreadPoolExecutor(max_workers=len(hosts) + 1) as pool:
        local_future = pool.submit(local_answer)
        host_futures = [
            (host_name, pool.submit(_run_host_worker, active_worker, host_name, host, verb))
            for host_name, host in hosts
        ]

        local_result = local_future.result()
        host_answers = [(host_name, future.result()) for host_name, future in host_futures]

    return local_result, host_answers


def _run_host_worker(
    worker: HostWorker,
    host_name: str,
    host: Host,
    verb: str,
) -> HostAnswer:
    """Run *worker* for one host, with any exception outside its own closed
    outcome set caught and turned into an internal-fault row instead of
    propagating out of the pool — a bug in worker code must never render as
    a host that failed to answer."""
    try:
        return worker(host_name, host)
    except Exception as exc:
        return HostAnswer(
            rows=[{"ok": False, "host": host_name, "reason": INTERNAL_FAULT_REASON}],
            notices=[
                f"camp {verb}: host {host_name!r} hit an internal camp error — {exc}"
            ],
            exit_code=1,
            answered=False,
        )
