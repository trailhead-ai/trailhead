"""Install-manifest loader for the trailhead management tool.

This module parses ``trailhead/install_manifest.toml`` — the suite-level
artifact that pins which repos at which SHAs make up an install.

Separation (D-1)
----------------
``InstallManifest`` / ``load_install_manifest`` are DISTINCT from the
capability ``Manifest`` / ``load_manifest`` in ``capabilities.py``.  They
answer different questions:

* capability manifest — "what can lore do?" (per-tool, path-confined to the
  plugin root, loaded at runtime for composition)
* install manifest — "which repos at which SHAs make up the install?"
  (suite-level, source/rev-pinned, verified at install/update time)

Conflating them would overload a single parser with two security models.

Rev pinning (§1112 / §1115)
----------------------------
Every ``rev`` field MUST be exactly 40 lowercase hexadecimal characters.
Tags (``v1.0``), short SHAs (12 chars), ``HEAD``, ``latest``, and branch
names are rejected at parse time with a named ``InstallManifestError``
naming the offending repo + field.  The rejection is structural — it happens
before any fetch or verification.

${registry} resolution (D29)
-----------------------------
Source paths of the form ``${registry}/path`` are resolved against the
``registry`` argument passed to ``load_install_manifest``.  A ``None`` or
empty-string registry with a ``${registry}``-template source raises a named
``InstallManifestError`` directing the user to ``trailhead config registry``.
A fully-qualified ``https://…`` or ``git@…:…`` source is accepted as-is
(per-repo mirror support, §758-761).

Source validation (S-3)
-----------------------
The resolved source is validated against a true anchored allowlist:
  * ``https://host/path`` — any HTTPS URL
  * ``git@host:path``     — standard SSH git URL
  * A local filesystem path — ONLY when ``local_root`` is provided for
    confinement (D-3 reuse of the existing confinement posture; do NOT invent
    a new confiner).  A local path with no ``local_root`` is refused.

Everything else is rejected — including ``ext::`` (git external-transport
RCE), ``fd::``, ``file://``, ``http://``, and bare paths with no local_root.
The allowlist replaces the previous denylist (shell-metachar check) which
provided false confidence while allowing ``ext::`` through.

Local-self source (L-1)
-----------------------
The reserved source value ``"local"`` marks a *local-self* entry: the install
installs the working tree you are running ``trailhead`` from, tracking ``HEAD``
rather than a pinned commit.  The pin/verify machinery exists for the
supply-chain case (fetching a remote repo at an audited, GPG-signed SHA); a
local checkout you already control and can read does not benefit from a
blessed-SHA gate, and self-pinning is circular (a commit cannot pin its own
hash).  So a local-self entry:
  * is honored ONLY when ``local_root`` is provided (same trust boundary as a
    local-path source — the caller has established where "local" resolves to);
  * pins **no** ``rev`` (``rev`` is None) and MUST NOT carry one — a ``rev`` on
    a ``"local"`` entry is a parse-time error, because it would be ignored and
    re-introduce the staleness foot-gun the local source exists to remove;
  * skips the remote/allowlist source validation (``"local"`` is neither a URL
    nor a filesystem path to confine).
At verify time (``fetch.verify_present_repo``) a local-self entry is checked
for "is this a git checkout?" instead of "HEAD == rev".  Remote entries keep
the full SHA + GPG gate unchanged.

Name validation (S-path)
------------------------
The ``name`` field must not contain path-traversal components (``/``, ``\\``,
or ``..``).  A name like ``../../evil`` would escape ``dest_parent`` at
promote time — rejected at parse time.

Duplicate repo entries
----------------------
``tomllib`` permits duplicate ``[[repo]]`` array entries (arrays of tables
are additive by design).  This module detects duplicate ``name`` values and
raises ``InstallManifestError`` — no last-wins silencing.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from trailhead.capabilities import _confine, ConfineError


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InstallManifestError(Exception):
    """Raised for structural, missing-field, or validation errors in an install manifest.

    Always cites the offending repo name (when known) and the file path.
    Never exposes raw ``TOMLDecodeError``.
    """


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RepoEntry:
    """A single repo entry in the install manifest.

    A remote entry pins ``rev`` to a full 40-char SHA and carries a fetchable
    ``source`` (URL or confined local path).  A *local-self* entry (L-1) has
    ``source == LOCAL_SOURCE``, ``is_local_self == True``, and ``rev is None``:
    it installs the working tree you are running from, tracking HEAD.
    """

    name: str
    rev: str | None
    source: str
    tools: list
    is_local_self: bool = False


@dataclass
class InstallManifest:
    """Parsed and validated install manifest."""

    repos: list  # list of RepoEntry


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Reserved source value marking a local-self entry (L-1): installs the working
# tree being run from, tracking HEAD instead of a pinned SHA.
LOCAL_SOURCE = "local"

_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

# Anchored allowlist for recognized remote source URL schemes.
_HTTPS_RE = re.compile(r"^https://")
_GIT_SSH_RE = re.compile(r"^git@")

# Defense-in-depth: shell metacharacters that must not appear anywhere in a
# recognized remote URL.  Since git is called with an arg list (never shell=True),
# these won't cause shell injection, but they indicate a malformed or hostile URL.
_URL_METACHAR_RE = re.compile(r"[;|`]|\$\(")


def _validate_rev(rev: str, repo_name: str, manifest_path: Path) -> None:
    """Assert rev is exactly 40 lowercase hex chars; raise InstallManifestError otherwise."""
    if not _SHA40_RE.match(rev):
        raise InstallManifestError(
            f"repo {repo_name!r}: 'rev' must be a full 40-character lowercase hex SHA "
            f"(never a tag, short SHA, HEAD, or 'latest'); got {rev!r} — "
            f"file: {manifest_path}"
        )


def _validate_name(name: str, manifest_path: Path) -> None:
    """Assert name contains no path-traversal components; raise InstallManifestError otherwise.

    Rejects names containing '/', '\\', or '..' to prevent path-traversal attacks
    when the name is used as a directory component under dest_parent.
    """
    if "/" in name or "\\" in name or ".." in name:
        raise InstallManifestError(
            f"repo name {name!r} contains path-traversal components ('/', '\\\\', or '..') — "
            f"rejected for security; file: {manifest_path}"
        )


def _validate_source(
    source: str,
    repo_name: str,
    manifest_path: Path,
    *,
    local_root: Path | None,
) -> str:
    """Validate and return the resolved source string.

    Accepted forms (true allowlist — S-3):
      - https://host/path  — HTTPS remote
      - git@host:path      — SSH git remote
      - An absolute local path — ONLY when local_root is provided for confinement.

    Rejected (everything else):
      - ext::, fd::, file://, http:// and any other non-allowlisted scheme
      - Any local path when local_root is None (no confinement root available)
      - Anything beginning with '--' (git option injection)

    The previous denylist (shell metacharacter check) is replaced by this
    true anchored allowlist.  ext::sh -c '...' (git external-transport RCE)
    and other dangerous transports are rejected because they don't match the
    allowlist — not because we enumerate every dangerous form.

    Returns the source string unchanged if valid.
    """
    # HTTPS URL — accepted if no shell metacharacters embedded (anchored allowlist)
    if _HTTPS_RE.match(source):
        if _URL_METACHAR_RE.search(source):
            raise InstallManifestError(
                f"repo {repo_name!r}: source URL contains shell metacharacters — "
                f"rejected for security (S-3); source: {source!r}; file: {manifest_path}"
            )
        return source

    # SSH git URL — accepted if no shell metacharacters embedded (anchored allowlist)
    if _GIT_SSH_RE.match(source):
        if _URL_METACHAR_RE.search(source):
            raise InstallManifestError(
                f"repo {repo_name!r}: source URL contains shell metacharacters — "
                f"rejected for security (S-3); source: {source!r}; file: {manifest_path}"
            )
        return source

    # Local path — only accepted when local_root is provided for confinement.
    # A local path with no local_root is refused: no confinement root means
    # the caller has not established a trust boundary for local sources.
    if local_root is None:
        raise InstallManifestError(
            f"repo {repo_name!r}: source {source!r} is not a recognized remote URL "
            f"(https:// or git@), and no local_root confinement root was provided — "
            f"pass local_root to load_install_manifest to allow local sources; "
            f"file: {manifest_path}"
        )

    try:
        _confine(local_root, source, repo_name, "source")
    except ConfineError as exc:
        raise InstallManifestError(
            f"repo {repo_name!r}: local source escapes the confinement root "
            f"{local_root!r} — {exc}; file: {manifest_path}"
        ) from exc
    return source


def _resolve_registry(
    source: str,
    registry: str | None,
    repo_name: str,
    manifest_path: Path,
) -> str:
    """Expand ``${registry}`` in source; raise if no registry is configured."""
    if "${registry}" not in source:
        return source

    if not registry:
        raise InstallManifestError(
            f"repo {repo_name!r}: source uses '${{registry}}' but no registry is "
            f"configured — run 'trailhead config registry <url>' to set one; "
            f"file: {manifest_path}"
        )

    return source.replace("${registry}", registry.rstrip("/"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_install_manifest(
    path: Path,
    registry: str | None,
    *,
    local_root: Path | None = None,
) -> InstallManifest:
    """Parse, validate, and return an ``InstallManifest`` from *path*.

    Steps:
    1. Parse TOML; wrap ``TOMLDecodeError`` as ``InstallManifestError``.
    2. Assert the manifest contains at least one ``[[repo]]`` entry.
    3. For each entry, validate required fields (``name``, ``rev``, ``source``).
    4. Validate ``name`` contains no path-traversal components (S-path).
    5. Validate ``rev`` is exactly 40 lowercase hex chars.
    6. Resolve ``${registry}`` templating in ``source``.
    7. Validate the resolved source against the S-3 true allowlist.
    8. Detect duplicate ``name`` values (no last-wins).

    Args:
        path:        Absolute (or resolvable) path to ``install_manifest.toml``.
        registry:    Registry base URL used to expand ``${registry}`` templates.
                     Pass ``None`` or ``""`` if no registry is configured; a
                     template source with no registry raises ``InstallManifestError``.
        local_root:  Confinement root for local-path sources (D-3).  When
                     ``None``, local paths are accepted without confinement
                     (post shell-metachar check).

    Returns:
        A :class:`InstallManifest` instance.

    Raises:
        InstallManifestError: Structural, missing-field, duplicate, or
                              validation failure.  Always cites the file path.
    """
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise InstallManifestError(
            f"malformed TOML in {path}: {exc}"
        ) from exc
    except FileNotFoundError as exc:
        raise InstallManifestError(
            f"install manifest not found: {path}"
        ) from exc

    raw_repos = data.get("repo", [])
    if not raw_repos:
        raise InstallManifestError(
            f"install manifest {path} contains no [[repo]] entries"
        )

    seen_names: set[str] = set()
    repos: list[RepoEntry] = []

    for entry in raw_repos:
        if not isinstance(entry, dict):
            raise InstallManifestError(
                f"install manifest {path}: each [[repo]] entry must be a table"
            )

        # Required field: name
        name = entry.get("name")
        if not name:
            raise InstallManifestError(
                f"install manifest {path}: a [[repo]] entry is missing required field 'name'"
            )

        # S-path: reject names with path-traversal components
        _validate_name(str(name), path)

        # Duplicate name check (no last-wins)
        if name in seen_names:
            raise InstallManifestError(
                f"install manifest {path}: duplicate repo entry {name!r} — "
                "each repo must appear exactly once"
            )
        seen_names.add(name)

        # Required field: source (read first so the local-self sentinel can be
        # detected before the remote-oriented rev + allowlist validation).
        raw_source = entry.get("source")
        if raw_source is None:
            raise InstallManifestError(
                f"install manifest {path}: repo {name!r} is missing required field 'source'"
            )
        raw_source = str(raw_source)
        tools = list(entry.get("tools", []))

        # L-1: local-self entry — installs the working tree, tracking HEAD.
        if raw_source == LOCAL_SOURCE:
            if local_root is None:
                raise InstallManifestError(
                    f"repo {name!r}: source = 'local' installs the checkout you are "
                    f"running from and requires a confinement root (local_root) — "
                    f"pass local_root to load_install_manifest; file: {path}"
                )
            if entry.get("rev") is not None:
                raise InstallManifestError(
                    f"repo {name!r}: source = 'local' installs the working tree and "
                    f"does not take a 'rev' (local installs track HEAD); remove the "
                    f"'rev' field; file: {path}"
                )
            repos.append(
                RepoEntry(name=name, rev=None, source=LOCAL_SOURCE, tools=tools, is_local_self=True)
            )
            continue

        # Remote entry: rev is required and strictly pinned (§1112 / §1115).
        rev = entry.get("rev")
        if rev is None:
            raise InstallManifestError(
                f"install manifest {path}: repo {name!r} is missing required field 'rev'"
            )
        _validate_rev(str(rev), name, path)

        # Resolve ${registry} before S-3 allowlist check
        source = _resolve_registry(raw_source, registry, name, path)

        # S-3 source validation
        source = _validate_source(source, name, path, local_root=local_root)

        repos.append(RepoEntry(name=name, rev=str(rev), source=source, tools=tools))

    return InstallManifest(repos=repos)
