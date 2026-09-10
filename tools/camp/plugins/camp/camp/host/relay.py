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
import re
import sys
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class HostAnswer:
    """One machine's contribution to a per-host answer, produced as a value.

    ``rows`` are the host-stamped rows to relay — either the remote's own
    answered rows (untouched beyond the `host` stamp), or the single
    ``ok: false`` row synthesized locally for a state the remote never
    answered at all. ``notices`` are the stderr lines this machine owes,
    already formatted and in the emission order the named-host surface
    prints them in — a caller reprints each with its own trailing newline.
    ``exit_code`` is the code this machine's own invocation produced (or the
    fixed ``1`` for a state classified before any remote exit code exists).

    ``answered`` is true only when ``rows`` came from the remote's own
    parseable JSON answer. Every other state synthesizes its failure row
    locally instead of relaying one, and a caller choosing how to render
    text output (hand ``rows`` to the verb's own renderer, or print the
    failure line under the machine's header) needs to tell the two apart —
    a bare ``ok: false`` row is not a row a verb's renderer understands.
    """

    rows: list[dict[str, Any]]
    notices: list[str] = field(default_factory=list)
    exit_code: int = 0
    answered: bool = False


def answer_for_host(
    verb: str,
    host: Host,
    host_name: str,
    remote_argv: Sequence[str],
    *,
    runner: Runner = default_runner,
) -> HostAnswer:
    """Run *remote_argv* on *host* over the transport and return its answer.

    Never calls ``sys.exit`` and never prints — every transport outcome
    becomes a :class:`HostAnswer` instead. Holds no state across calls, so
    it is safe to call once per declared host, including from several
    threads at once.
    """
    outcome = _transport.run_camp(host, remote_argv, runner=runner)

    if isinstance(outcome, Unreachable):
        return _fail_answer(
            verb, host_name,
            human=f"is unreachable — no response within {DEFAULT_CONNECT_TIMEOUT_SECONDS:g}s",
            reason=f"unreachable — no response within {DEFAULT_CONNECT_TIMEOUT_SECONDS:g}s",
        )

    if isinstance(outcome, StoppedResponding):
        t = outcome.execution_timeout
        return _fail_answer(
            verb, host_name,
            human=f"connected but did not finish within {t:g}s — no answer was received",
            reason=f"connected but did not finish within {t:g}s",
        )

    if isinstance(outcome, IdentityUnknown):
        return _fail_answer(
            verb, host_name,
            human="has no pinned key — camp will not accept one on first contact",
            reason="no pinned host key",
            extra_human_line=(
                f"pin it yourself, then re-run: ssh-keyscan {host.ssh} "
                ">> ~/.ssh/known_hosts"
            ),
        )

    if isinstance(outcome, IdentityChanged):
        return _fail_answer(
            verb, host_name,
            human="presented a different key than the pinned one — refusing to connect",
            reason="host key differs from the pinned key",
            extra_human_line=(
                "if this machine was not rebuilt, the connection may be "
                "intercepted; verify before removing the pinned key"
            ),
        )

    if isinstance(outcome, CampNotResolvable):
        return _fail_answer(
            verb, host_name,
            human="answered, but camp could not be run there — declare camp_bin "
            "for this host in hosts.toml",
            reason="camp could not be run on the host — declare camp_bin for "
            "this host in hosts.toml",
        )

    if isinstance(outcome, CredentialsRefused):
        return _fail_answer(
            verb, host_name,
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
        # Nothing structured to relay — whether a genuine remote refusal (its
        # own stderr, printed as it wrote it, per the module docstring) or a
        # transport-level failure that slipped past classification. Either
        # way a --json caller must still get a row: printing nothing here
        # would be the one thing this whole design exists to prevent — an
        # empty answer read as "the host has nothing to report" rather than
        # "the host could not be understood".
        return HostAnswer(
            rows=[{"ok": False, "host": host_name, "reason": "remote answer could not be parsed"}],
            notices=_verbatim_notice(outcome.stderr),
            exit_code=outcome.exit_code,
            answered=False,
        )

    # Stamp `host` on each row in place — never re-sort, never drop a row,
    # never touch any other field. This is the entire local contribution to
    # a relayed row.
    for row in rows:
        row["host"] = host_name

    return HostAnswer(
        rows=rows,
        notices=_verbatim_notice(outcome.stderr),
        exit_code=outcome.exit_code,
        answered=True,
    )


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
    already prints before this is ever reached. Built on
    :func:`answer_for_host`: this is that value, printed and exited on
    immediately.
    """
    answer = answer_for_host(verb, host, host_name, remote_argv, runner=runner)

    if answer.answered:
        if as_json:
            print(json.dumps(answer.rows))
        else:
            render_human_rows(answer.rows)
        for notice in answer.notices:
            print(notice, file=sys.stderr)
    else:
        for notice in answer.notices:
            print(notice, file=sys.stderr)
        if as_json:
            print(json.dumps(answer.rows))

    sys.exit(answer.exit_code)


def _try_parse_rows(stdout: str) -> list[dict[str, Any]] | None:
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    # Every element must be a row (a dict) — a remote camp of a different
    # version, or one that is simply broken, could emit a well-formed JSON
    # array of something else entirely. Stamping `host` onto a non-dict
    # element is not a row to relay, so treat the whole answer as unparsable
    # rather than raising partway through the stamping loop below.
    if not all(isinstance(item, dict) for item in data):
        return None
    return data


#: C0 (U+0000-U+001F) and C1 (U+0080-U+009F) controls, plus DEL (U+007F) —
#: everything a remote camp's stderr could use to manipulate this side's
#: terminal (cursor moves, colour, OSC/CSI introducers). Newline (U+000A)
#: and tab (U+0009) are excluded so multi-line, tab-formatted stderr keeps
#: its line structure. Matched by Unicode code point over the decoded
#: `str`, never by byte — a byte-oriented filter would corrupt multi-byte
#: UTF-8 sequences, since a C1 byte value can appear as a continuation byte
#: inside one.
_CONTROL_SEQUENCE_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]")


def _strip_control_sequences(text: str) -> str:
    """Remove C0/C1 control code points (and DEL) from *text*, preserving
    newline and tab and every other code point untouched — including a lone
    surrogate `errors="surrogateescape"` may have put in the string, which
    is not a control code point and is left exactly as decoded."""
    return _CONTROL_SEQUENCE_RE.sub("", text)


def _verbatim_notice(text: str) -> list[str]:
    """One notice entry for *text* — the remote's own stderr, normalized to
    a single trailing newline the caller re-adds and with control sequences
    stripped so relayed remote stderr cannot drive this side's terminal — or
    no entry at all when there is nothing to say."""
    if not text:
        return []
    stripped = text[:-1] if text.endswith("\n") else text
    return [_strip_control_sequences(stripped)]


def _fail_answer(
    verb: str,
    host_name: str,
    *,
    human: str,
    reason: str,
    extra_human_line: str | None = None,
) -> HostAnswer:
    """The fixed answer for one of the six non-relayable failure states:
    its own stderr notice line(s), a single `ok: false` row, and exit 1 —
    never a raw transport error, always the operator's terms."""
    notices = [f"camp {verb}: host {host_name!r} {human}"]
    if extra_human_line is not None:
        notices.append(f"camp {verb}: {extra_human_line}")
    return HostAnswer(
        rows=[{"ok": False, "host": host_name, "reason": reason}],
        notices=notices,
        exit_code=1,
        answered=False,
    )
