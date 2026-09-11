"""`camp transfer-probe` and its defensive wire contract.

Two directions, one wire format.

**Answering side** (:func:`build_probe_answer`, run by `camp transfer-probe`
on whatever host it executes on): states this host's own declared name,
whether the named group is configured here, each member's repo root
existence, the group's declared harness account binding, and whether a
workspace of the given slug already exists here and who owns it.

`camp list --json` cannot distinguish "group not configured" from "configured
but empty" — both return an empty array. This module never derives
`group_configured` from a row count; it looks the group up by name in the
loaded config directly, so a misconfigured peer is never reported as an
empty one.

**Sending side** (:func:`parse_probe_response`, :func:`probe_peer`): the
answer above arrives over `camp.host.transport.run_camp` as untrusted input —
size-bounded before parsing, parsed and never evaluated, every field type-
and shape-checked. No field the response carries is ever used to construct a
local path; the schema below has no path-shaped field for that reason. A
response whose self-declared name equals this host's own is a same-name
collision: the declaration source does not verify uniqueness across hosts, so
without this check every ownership comparison silently reads "mine" on both
ends at once.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..group.manifest import ManifestError, manifest_path_for, owner_of, read_central_manifest
from ..host.config import Host
from ..host.transport import (
    Answered,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    Runner,
    TransportOutcome,
    default_runner,
    run_camp,
)

__all__ = [
    "PROBE_CONTRACT_VERSION",
    "MAX_PROBE_RESPONSE_BYTES",
    "MemberRepoStatus",
    "ProbeAnswer",
    "ProbeRefused",
    "SelfNameCollision",
    "InvalidSlugForTransport",
    "build_probe_answer",
    "parse_probe_response",
    "probe_peer",
]

#: Bumped whenever the wire schema changes in a way an old parser cannot
#: safely read. The sending side refuses any other value by name rather than
#: guessing at a shape it was not built to read.
PROBE_CONTRACT_VERSION = 1

#: The response is refused outright above this many UTF-8 bytes, before a
#: single byte of it is handed to a JSON parser. Mirrors camp's existing
#: treatment of untrusted harness transcript content: a malicious or wedged
#: peer cannot make this host buffer or parse an unbounded document.
MAX_PROBE_RESPONSE_BYTES = 65536

# Slugs are re-validated against this strict identifier charset immediately
# before being handed to the transport, regardless of where they came from —
# a stored slug is validated only against path escape elsewhere and may
# legitimately carry other characters. Replicated from spine._VALID_SLUG_RE
# rather than importing the (heavy) spine module into this transfer-only path.
_VALID_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


@dataclass(frozen=True)
class MemberRepoStatus:
    """Whether one declared member's repo root exists on the answering host."""

    name: str
    repo_root_exists: bool


@dataclass(frozen=True)
class ProbeAnswer:
    """A fully parsed, type- and shape-checked probe response."""

    self_name: str | None
    group_configured: bool
    members: tuple[MemberRepoStatus, ...]
    account: str | None
    workspace_exists: bool | None
    workspace_owner: str | None
    contract_version: int


@dataclass(frozen=True)
class ProbeRefused:
    """The response was refused by name — never partially trusted."""

    reason: str


@dataclass(frozen=True)
class SelfNameCollision:
    """The peer's declared self-name equals this host's own.

    Every ownership comparison the transfer preflight makes assumes the two
    ends' declared names are distinct; the declaration source does not check
    this itself, so it is checked here, at the one place both names are ever
    held together.
    """

    peer_self_name: str

    def __str__(self) -> str:
        return (
            f"the peer declares its own name as {self.peer_self_name!r}, the "
            "same name this host declares — camp cannot tell the two hosts "
            "apart, so every ownership check would silently read 'mine' on "
            "both ends. Remedy: give one of the two hosts a distinct "
            "self_name in hosts.toml, then retry."
        )


class InvalidSlugForTransport(Exception):
    """Raised when a slug fails the strict identifier charset immediately
    before it would be handed to the transport."""


def build_probe_answer(
    *,
    group_name: str,
    slug: str,
    groups: list[dict[str, Any]],
    self_name: str | None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Construct this host's JSON-serializable answer about itself.

    `groups` is the already-loaded list `load_all_groups` returns. The group
    is looked up by name directly — never inferred from whether it holds any
    workspaces — so "not configured" and "configured but empty" are never
    the same answer.
    """
    group = next(
        (g for g in groups if g["group"]["name"] == group_name), None
    )
    if group is None:
        return {
            "contract_version": PROBE_CONTRACT_VERSION,
            "self_name": self_name,
            "group_configured": False,
            "members": None,
            "account": None,
            "workspace_exists": None,
            "workspace_owner": None,
        }

    members = [
        {"name": m["name"], "repo_root_exists": Path(m["repo_root"]).exists()}
        for m in group["members"]
    ]
    account = (group.get("launch") or {}).get("account")

    mpath = manifest_path_for(group_name, slug, env=env)
    workspace_exists = mpath.is_file()
    workspace_owner: str | None = None
    if workspace_exists:
        try:
            workspace_owner = owner_of(read_central_manifest(mpath))
        except ManifestError:
            workspace_owner = None

    return {
        "contract_version": PROBE_CONTRACT_VERSION,
        "self_name": self_name,
        "group_configured": True,
        "members": members,
        "account": account,
        "workspace_exists": workspace_exists,
        "workspace_owner": workspace_owner,
    }


def parse_probe_response(raw: str) -> ProbeAnswer | ProbeRefused:
    """Parse *raw* stdout from the peer as untrusted input.

    Refused before parsing when oversized; refused by name (never partially
    trusted) when it is not JSON, not a JSON object, carries an unrecognized
    contract version, or any field's type or shape does not match the schema.
    """
    encoded = raw.encode("utf-8", errors="surrogateescape")
    if len(encoded) > MAX_PROBE_RESPONSE_BYTES:
        return ProbeRefused(
            reason=(
                f"response is {len(encoded)} bytes, exceeding the "
                f"{MAX_PROBE_RESPONSE_BYTES}-byte size bound — refused before parsing"
            )
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return ProbeRefused(reason=f"response is not valid JSON — {e}")

    if not isinstance(data, dict):
        return ProbeRefused(
            reason=f"response must be a JSON object, got {type(data).__name__}"
        )

    version = data.get("contract_version")
    if version != PROBE_CONTRACT_VERSION:
        return ProbeRefused(
            reason=(
                f"unrecognized contract_version {version!r} — this side "
                f"speaks {PROBE_CONTRACT_VERSION}"
            )
        )

    self_name = data.get("self_name")
    if self_name is not None and not isinstance(self_name, str):
        return ProbeRefused(reason="field 'self_name' must be a string or null")

    group_configured = data.get("group_configured")
    if not isinstance(group_configured, bool):
        return ProbeRefused(reason="field 'group_configured' must be a boolean")

    members_raw = data.get("members")
    members: tuple[MemberRepoStatus, ...] = ()
    if group_configured:
        if not isinstance(members_raw, list):
            return ProbeRefused(
                reason="field 'members' must be a list when the group is configured"
            )
        parsed_members: list[MemberRepoStatus] = []
        for i, entry in enumerate(members_raw):
            if not isinstance(entry, dict):
                return ProbeRefused(reason=f"members[{i}] must be an object")
            name = entry.get("name")
            exists = entry.get("repo_root_exists")
            if not isinstance(name, str):
                return ProbeRefused(reason=f"members[{i}].name must be a string")
            if not isinstance(exists, bool):
                return ProbeRefused(
                    reason=f"members[{i}].repo_root_exists must be a boolean"
                )
            parsed_members.append(MemberRepoStatus(name=name, repo_root_exists=exists))
        members = tuple(parsed_members)
    elif members_raw is not None:
        return ProbeRefused(
            reason="field 'members' must be null when the group is not configured"
        )

    account = data.get("account")
    if account is not None and not isinstance(account, str):
        return ProbeRefused(reason="field 'account' must be a string or null")

    workspace_exists_raw = data.get("workspace_exists")
    if group_configured:
        if not isinstance(workspace_exists_raw, bool):
            return ProbeRefused(
                reason="field 'workspace_exists' must be a boolean when the group is configured"
            )
    elif workspace_exists_raw is not None:
        return ProbeRefused(
            reason="field 'workspace_exists' must be null when the group is not configured"
        )

    workspace_owner = data.get("workspace_owner")
    if workspace_owner is not None and not isinstance(workspace_owner, str):
        return ProbeRefused(reason="field 'workspace_owner' must be a string or null")

    return ProbeAnswer(
        self_name=self_name,
        group_configured=group_configured,
        members=members,
        account=account,
        workspace_exists=workspace_exists_raw if group_configured else None,
        workspace_owner=workspace_owner,
        contract_version=version,
    )


def _revalidate_slug_for_transport(slug: str) -> None:
    if not slug or not _VALID_SLUG_RE.match(slug):
        raise InvalidSlugForTransport(
            f"slug {slug!r} fails the strict identifier charset [a-z0-9-]+ "
            "and will not be sent to the peer"
        )


def probe_peer(
    host: Host,
    *,
    group: str,
    slug: str,
    self_name: str | None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    runner: Runner = default_runner,
) -> TransportOutcome | ProbeAnswer | ProbeRefused | SelfNameCollision:
    """Probe *host* for *group*/*slug* over the shipped transport, classified.

    Consumes `camp.host.transport.run_camp` verbatim: every non-`Answered`
    outcome (unreachable, stopped responding, the two identity states,
    credentials refused, camp not resolvable, remote refusal) is returned
    unchanged. An `Answered` response is parsed as untrusted input and
    further classified into a validated :class:`ProbeAnswer`, a
    :class:`ProbeRefused` (malformed wire payload), or a
    :class:`SelfNameCollision` (the peer declares this host's own name) — so
    none of these ever collapses into another or into a raw transport
    outcome.

    *slug* is re-validated against the strict identifier charset immediately
    before it is handed to the transport, regardless of where it came from;
    an invalid slug raises :class:`InvalidSlugForTransport` before the
    transport is ever reached.
    """
    _revalidate_slug_for_transport(slug)

    remote_argv = ["transfer-probe", "--group", group, "--slug", slug]
    outcome = run_camp(
        host,
        remote_argv,
        connect_timeout=connect_timeout,
        execution_timeout=execution_timeout,
        runner=runner,
    )
    if not isinstance(outcome, Answered):
        return outcome

    parsed = parse_probe_response(outcome.stdout)
    if isinstance(parsed, ProbeRefused):
        return parsed

    if (
        self_name is not None
        and parsed.self_name is not None
        and parsed.self_name == self_name
    ):
        return SelfNameCollision(peer_self_name=parsed.self_name)

    return parsed
