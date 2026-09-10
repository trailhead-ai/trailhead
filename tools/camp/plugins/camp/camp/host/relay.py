"""Relay a `--host <name>` verb through the SSH transport to a rendered answer.

This is where the three layers meet: `camp.host.config` has already resolved
the declared host by the time `relay_all_groups` is reached, `camp.host.transport`
runs the far side and classifies the outcome, and this module turns that
classification into exactly the rendering
`docs/design/named-remote-host-answers.md` fixes for every state a named-host
verb can produce.

`relay_all_groups` is the ONE shared entry point every `--host` verb
dispatches through. A verb plugs in by:

  - assembling its own "answer for every group, as JSON" remote argv (the
    same form `--all-groups --json` already answers locally with), and
  - supplying a `render_human_rows` callback that prints its own answered
    rows the way that verb already prints them locally (`list` prints
    `slug workspace_path`; `sessions` prints its own per-session line).

Everything else — host-unreachable through camp-not-resolvable, the JSON
shape of a relayed row, and the remote-refusal passthrough — is rendered
identically for every verb, because none of those states carry verb-specific
data. This module is deliberately UNAWARE of any verb's own JSON key set: it
only adds one key, `host`, to whatever the remote already emitted, verbatim
and in the order the remote emitted it. Deciding the rest of a row's shape is
each verb's own renderer's job (for `list`, that's
`camp.provision.lifecycle.render_workspace_list`'s `_LIST_JSON_KEYS`).
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable, Sequence

from . import transport as _transport
from .config import Host
from .transport import (
    Answered,
    CampNotResolvable,
    CredentialsRefused,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    IdentityChanged,
    IdentityUnknown,
    RemoteRefusal,
    Runner,
    StoppedResponding,
    Unreachable,
    default_runner,
)

#: Prints the answered, host-stamped rows the way this verb prints them
#: locally (human path only — the JSON path is identical for every verb and
#: is printed by `relay_all_groups` itself).
RenderHumanRows = Callable[[list[dict[str, Any]]], None]


def relay_all_groups(
    verb: str,
    host: Host,
    host_name: str,
    remote_argv: Sequence[str],
    *,
    as_json: bool,
    render_human_rows: RenderHumanRows,
    runner: Runner = default_runner,
) -> None:
    """Run *remote_argv* on *host* over the transport and render the answer.

    Exits the process in every branch — there is no return path a caller
    needs to handle further, mirroring the fixed-message refusals `main()`
    already prints before this is ever reached.
    """
    outcome = _transport.run_camp(host, remote_argv, runner=runner)

    if isinstance(outcome, Unreachable):
        _fail(
            verb, host_name, as_json,
            human=f"is unreachable — no response within {DEFAULT_CONNECT_TIMEOUT_SECONDS:g}s",
            reason=f"unreachable — no response within {DEFAULT_CONNECT_TIMEOUT_SECONDS:g}s",
        )

    if isinstance(outcome, StoppedResponding):
        t = outcome.execution_timeout
        _fail(
            verb, host_name, as_json,
            human=f"connected but did not finish within {t:g}s — no answer was received",
            reason=f"connected but did not finish within {t:g}s",
        )

    if isinstance(outcome, IdentityUnknown):
        _fail(
            verb, host_name, as_json,
            human="has no pinned key — camp will not accept one on first contact",
            reason="no pinned host key",
            extra_human_line=(
                f"pin it yourself, then re-run: ssh-keyscan {host.ssh} "
                ">> ~/.ssh/known_hosts"
            ),
        )

    if isinstance(outcome, IdentityChanged):
        _fail(
            verb, host_name, as_json,
            human="presented a different key than the pinned one — refusing to connect",
            reason="host key differs from the pinned key",
            extra_human_line=(
                "if this machine was not rebuilt, the connection may be "
                "intercepted; verify before removing the pinned key"
            ),
        )

    if isinstance(outcome, CampNotResolvable):
        _fail(
            verb, host_name, as_json,
            human="answered, but camp could not be run there — declare camp_bin "
            "for this host in hosts.toml",
            reason="camp could not be run on the host — declare camp_bin for "
            "this host in hosts.toml",
        )

    if isinstance(outcome, CredentialsRefused):
        _fail(
            verb, host_name, as_json,
            human="refused every credential offered — camp never ran there",
            reason="host refused our credentials",
            extra_human_line=(
                "load the identity authorized on that host (e.g. ssh-add) "
                "and confirm it is in the host's authorized_keys, then re-run"
            ),
        )

    # Answered / RemoteRefusal both carry stdout/stderr/exit_code. The
    # remote camp's own stdout is the discriminator, not the outcome type:
    # camp always invokes the far side with --json, so a relayable answer
    # (zero/one/many rows, or its own "collection failure" ok:false rows) is
    # whatever the remote's stdout decodes as a JSON array — the remote
    # camp decided everything else already. Anything that does not decode
    # is a refusal with nothing structured to relay.
    assert isinstance(outcome, (Answered, RemoteRefusal))
    rows = _try_parse_rows(outcome.stdout)
    if rows is None:
        _print_verbatim(outcome.stderr, file=sys.stderr)
        sys.exit(outcome.exit_code)

    # Stamp `host` on each row in place — never re-sort, never drop a row,
    # never touch any other field. This is the entire local contribution to
    # a relayed row.
    for row in rows:
        row["host"] = host_name

    if as_json:
        print(json.dumps(rows))
    else:
        render_human_rows(rows)
    _print_verbatim(outcome.stderr, file=sys.stderr)
    sys.exit(outcome.exit_code)


def _try_parse_rows(stdout: str) -> list[dict[str, Any]] | None:
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    return data


def _print_verbatim(text: str, *, file) -> None:
    if not text:
        return
    print(text, end="" if text.endswith("\n") else "\n", file=file)


def _fail(
    verb: str,
    host_name: str,
    as_json: bool,
    *,
    human: str,
    reason: str,
    extra_human_line: str | None = None,
) -> None:
    """Print the fixed rendering for one of the five non-relayable failure
    states and exit 1 — never a raw transport error, always the operator's
    terms."""
    print(f"camp {verb}: host {host_name!r} {human}", file=sys.stderr)
    if extra_human_line is not None:
        print(f"camp {verb}: {extra_human_line}", file=sys.stderr)
    if as_json:
        print(json.dumps([{"ok": False, "host": host_name, "reason": reason}]))
    sys.exit(1)
