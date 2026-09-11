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

import enum
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
    ProducerFailed,
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


class Certainty(enum.Enum):
    """Whether a state-changing operation happened, for one transport
    outcome — the mapping every state-changing (non-rows) verb states once
    rather than re-deriving. Closed over :class:`~camp.host.transport.
    TransportOutcome`'s nine-member set; see :func:`classify_certainty`.
    """

    #: The far side never ran camp at all (the five locally-classified
    #: transport failures), or it ran and declined (`RemoteRefusal`). Either
    #: way, the operation certainly did not happen.
    DID_NOT_HAPPEN = "did_not_happen"

    #: The far side ran camp and it answered. The operation certainly
    #: happened.
    HAPPENED = "happened"

    #: Two shapes: the connection completed and the invocation then exceeded
    #: its execution bound without answering (`StoppedResponding`) — camp
    #: cannot tell whether the far side's camp finished before or after the
    #: bound expired; or the local producer feeding a streamed invocation
    #: exited non-zero (`ProducerFailed`) — the remote may have completed
    #: and even reported success, but on a stream that may be truncated or
    #: corrupt, so that report cannot be trusted either way.
    UNKNOWN = "unknown"


def classify_certainty(outcome: _transport.TransportOutcome) -> Certainty:
    """Map one transport outcome to whether the operation it carried
    certainly happened, certainly did not, or is unknown.

    Verb-agnostic and stated once: every state-changing verb that rides
    this transport reuses this mapping rather than re-deriving it per verb.
    A rows-shaped (read-only) verb has no use for it — nothing changed
    either way, so certainty is not a question a listing asks.
    """
    if isinstance(outcome, (StoppedResponding, ProducerFailed)):
        return Certainty.UNKNOWN
    if isinstance(outcome, Answered):
        return Certainty.HAPPENED
    assert isinstance(
        outcome,
        (
            Unreachable,
            IdentityUnknown,
            IdentityChanged,
            CampNotResolvable,
            CredentialsRefused,
            RemoteRefusal,
        ),
    )
    return Certainty.DID_NOT_HAPPEN


@dataclass(frozen=True)
class _TransportFailure:
    """The verb-agnostic rendering of one of the seven transport states that
    never reach a remote camp's own answer — shared by every relay shape.
    ``rows``-specific and single-object-specific wrapping each build their
    own payload around this; this dataclass carries only what both need.
    """

    notices: list[str]
    exit_code: int
    reason: str


def _classify_transport_failure(
    verb: str, host: Host, host_name: str, outcome: _transport.TransportOutcome
) -> _TransportFailure | None:
    """The seven transport-failure renderings, shared by every relay shape.

    Returns ``None`` for :class:`Answered` and :class:`RemoteRefusal` — the
    two outcomes that carry the remote's own stdout/stderr for a caller to
    parse itself, rows or object, rather than a fixed local rendering.
    """
    if isinstance(outcome, Unreachable):
        return _TransportFailure(
            notices=[
                f"camp {verb}: host {host_name!r} is unreachable — no response "
                f"within {DEFAULT_CONNECT_TIMEOUT_SECONDS:g}s"
            ],
            exit_code=1,
            reason=f"unreachable — no response within {DEFAULT_CONNECT_TIMEOUT_SECONDS:g}s",
        )

    if isinstance(outcome, StoppedResponding):
        t = outcome.execution_timeout
        return _TransportFailure(
            notices=[
                f"camp {verb}: host {host_name!r} connected but did not finish "
                f"within {t:g}s — no answer was received"
            ],
            exit_code=1,
            reason=f"connected but did not finish within {t:g}s",
        )

    if isinstance(outcome, IdentityUnknown):
        return _TransportFailure(
            notices=[
                f"camp {verb}: host {host_name!r} has no pinned key — camp will "
                "not accept one on first contact",
                f"camp {verb}: pin it yourself, then re-run: ssh-keyscan "
                f"{host.ssh} >> ~/.ssh/known_hosts",
            ],
            exit_code=1,
            reason="no pinned host key",
        )

    if isinstance(outcome, IdentityChanged):
        return _TransportFailure(
            notices=[
                f"camp {verb}: host {host_name!r} presented a different key "
                "than the pinned one — refusing to connect",
                f"camp {verb}: if this machine was not rebuilt, the connection "
                "may be intercepted; verify before removing the pinned key",
            ],
            exit_code=1,
            reason="host key differs from the pinned key",
        )

    if isinstance(outcome, CampNotResolvable):
        return _TransportFailure(
            notices=[
                f"camp {verb}: host {host_name!r} answered, but camp could not "
                "be run there — declare camp_bin for this host in hosts.toml"
            ],
            exit_code=1,
            reason="camp could not be run on the host — declare camp_bin for "
            "this host in hosts.toml",
        )

    if isinstance(outcome, CredentialsRefused):
        return _TransportFailure(
            notices=[
                f"camp {verb}: host {host_name!r} refused every credential "
                "offered — camp never ran there",
                f"camp {verb}: load the identity authorized on that host (e.g. "
                "ssh-add) and confirm it is in the host's authorized_keys, "
                "then re-run",
            ],
            exit_code=1,
            reason="host refused our credentials",
        )

    if isinstance(outcome, ProducerFailed):
        # `run_camp` never returns this today — only `stream_camp` does, for
        # a streamed invocation no named-host verb currently relays through
        # here. Handled anyway so this function and `classify_certainty`
        # stay exhaustive over `TransportOutcome` together, for whenever a
        # stream-fed relay caller lands.
        return _TransportFailure(
            notices=[
                f"camp {verb}: host {host_name!r}'s local producer exited "
                f"{outcome.exit_code} — its stream may be truncated or "
                "corrupt, so the remote's own answer is discarded rather "
                "than trusted"
            ],
            exit_code=1,
            reason=f"local producer failed (exit {outcome.exit_code})",
        )

    assert isinstance(outcome, (Answered, RemoteRefusal))
    return None


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

    failure = _classify_transport_failure(verb, host, host_name, outcome)
    if failure is not None:
        return HostAnswer(
            rows=[{"ok": False, "host": host_name, "reason": failure.reason}],
            notices=failure.notices,
            exit_code=failure.exit_code,
            answered=False,
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
        # The transport does not carry the remote command's own exit status
        # (see the module docstring's "honesty gap"), so a remote status of
        # 0 here means "unknown", not "succeeded" — relaying it unchanged
        # would tell every caller that does not parse rows that this host
        # had nothing to report. A remote status that is already non-zero is
        # real signal the far side produced and is relayed as-is rather than
        # flattened to a fixed code.
        exit_code = outcome.exit_code if outcome.exit_code != 0 else 1
        return HostAnswer(
            rows=[{"ok": False, "host": host_name, "reason": "remote answer could not be parsed"}],
            notices=_verbatim_notice(outcome.stderr),
            exit_code=exit_code,
            answered=False,
        )

    # Stamp `host` on each row in place — never re-sort, never drop a row,
    # never touch any other field beyond the strip below. This is the
    # entire local contribution to a relayed row.
    for row in rows:
        row["host"] = host_name

    return HostAnswer(
        rows=_strip_control_sequences_deep(rows),
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
    no entry at all when there is nothing to say. Stderr consisting only of
    control sequences (or a bare newline) strips down to an empty string,
    which is checked for here rather than before stripping — an empty-string
    notice would print as a blank line under the machine's header."""
    if not text:
        return []
    stripped = text[:-1] if text.endswith("\n") else text
    stripped = _strip_control_sequences(stripped)
    if not stripped:
        return []
    return [stripped]


@dataclass(frozen=True)
class HostObjectAnswer:
    """One machine's contribution to a per-host answer, for a verb whose
    remote answer is one object rather than a list of rows.

    ``answer`` is the remote's own parsed JSON object, with every string
    value (including nested ones, e.g. inside ``account_binding``) run
    through the same control-sequence strip a rows answer's stderr already
    gets — the success path is the one an operator trusts most, so it is
    the one worth spoofing. ``answer`` is ``None`` for every outcome that
    is not a relayable object: the seven locally-classified transport
    failures, and a remote answer whose stdout does not decode as a JSON
    object.

    ``certainty`` states whether the operation the verb asked for happened,
    from :func:`classify_certainty` — the one piece of information a rows
    answer has no use for and this shape exists to carry.
    """

    answer: dict[str, Any] | None
    certainty: Certainty
    notices: list[str] = field(default_factory=list)
    exit_code: int = 0


@dataclass(frozen=True)
class HostPayloadAnswer:
    """One machine's contribution to a per-host answer, for a verb whose
    remote answer may come back as *either* shape — a stop answers with one
    object until the reference is ambiguous, at which point it answers with
    an array of candidate rows.

    Exactly one of ``obj`` / ``rows`` is set for a relayable answer; both are
    ``None`` for every state that carries no payload — the six
    locally-classified transport failures, and a remote answer whose stdout
    decodes as neither a JSON object nor a JSON array of row objects.

    ``obj`` is control-stripped the same way :attr:`HostObjectAnswer.answer`
    is. ``rows`` are host-stamped the same way :func:`answer_for_host`
    stamps them, and are control-stripped too — the far side's stderr and a
    relayed object's fields already get that treatment, and a row's own
    values (a session id, a multiplexer name) are the operator's next
    decision just as much as either of those.

    ``certainty`` states whether the operation the verb asked for happened,
    from :func:`classify_certainty` — the same mapping
    :class:`HostObjectAnswer` carries.
    """

    obj: dict[str, Any] | None
    rows: list[dict[str, Any]] | None
    certainty: Certainty
    notices: list[str] = field(default_factory=list)
    exit_code: int = 0


def answer_payload_for_host(
    verb: str,
    host: Host,
    host_name: str,
    remote_argv: Sequence[str],
    *,
    runner: Runner = default_runner,
) -> HostPayloadAnswer:
    """Run *remote_argv* on *host* over the transport and return whichever
    payload shape it answered with — one object, an array of rows, or
    nothing camp could parse.

    Same transport, same six locally-classified failure states (shared via
    :func:`_classify_transport_failure`), and the same :class:`Certainty`
    :func:`answer_object_for_host` carries — this is the general reader
    :func:`answer_object_for_host` is re-expressed on top of. Never calls
    ``sys.exit`` and never prints. Holds no state across calls.
    """
    outcome = _transport.run_camp(host, remote_argv, runner=runner)
    certainty = classify_certainty(outcome)

    failure = _classify_transport_failure(verb, host, host_name, outcome)
    if failure is not None:
        return HostPayloadAnswer(
            obj=None,
            rows=None,
            certainty=certainty,
            notices=failure.notices,
            exit_code=failure.exit_code,
        )

    assert isinstance(outcome, (Answered, RemoteRefusal))
    notices = _verbatim_notice(outcome.stderr)

    parsed_obj = _try_parse_object(outcome.stdout)
    if parsed_obj is not None:
        return HostPayloadAnswer(
            obj=_strip_control_sequences_deep(parsed_obj),
            rows=None,
            certainty=certainty,
            notices=notices,
            exit_code=outcome.exit_code,
        )

    parsed_rows = _try_parse_rows(outcome.stdout)
    if parsed_rows is not None:
        for row in parsed_rows:
            row["host"] = host_name
        return HostPayloadAnswer(
            obj=None,
            rows=_strip_control_sequences_deep(parsed_rows),
            certainty=certainty,
            notices=notices,
            exit_code=outcome.exit_code,
        )

    return HostPayloadAnswer(
        obj=None,
        rows=None,
        certainty=certainty,
        notices=notices,
        exit_code=outcome.exit_code,
    )


def answer_object_for_host(
    verb: str,
    host: Host,
    host_name: str,
    remote_argv: Sequence[str],
    *,
    runner: Runner = default_runner,
) -> HostObjectAnswer:
    """Run *remote_argv* on *host* over the transport and return its
    single-object answer.

    Re-expressed on top of :func:`answer_payload_for_host`: same transport,
    same six locally-classified failure states, same :class:`Certainty` —
    only the payload reader's ``obj`` field is exposed, so a rows-shaped
    answer (including an array of row objects) still relays as
    ``answer=None`` here. Never calls ``sys.exit`` and never prints. Holds
    no state across calls.
    """
    payload = answer_payload_for_host(verb, host, host_name, remote_argv, runner=runner)
    return HostObjectAnswer(
        answer=payload.obj,
        certainty=payload.certainty,
        notices=payload.notices,
        exit_code=payload.exit_code,
    )


def _try_parse_object(stdout: str) -> dict[str, Any] | None:
    """The single-object counterpart to :func:`_try_parse_rows`: the far
    side's stdout must decode as a JSON *object*, not an array — a launch
    answers with one session, never a list of them. An array (even of rows)
    is not a relayable object here, mirroring how a bare object is not a
    relayable row through :func:`_try_parse_rows`."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _strip_control_sequences_deep(value: Any) -> Any:
    """Recursively apply :func:`_strip_control_sequences` to every string
    value reachable from *value* — a dict's values (including nested
    dicts, e.g. ``account_binding``), a list's elements, and bare strings.
    Non-string, non-container values (bool, int, float, None) pass through
    unchanged. This is what lets a single-object answer's success path get
    the same protection a rows answer's stderr notice already gets, without
    this module knowing any verb's own key set."""
    if isinstance(value, str):
        return _strip_control_sequences(value)
    if isinstance(value, dict):
        return {key: _strip_control_sequences_deep(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_control_sequences_deep(item) for item in value]
    return value
