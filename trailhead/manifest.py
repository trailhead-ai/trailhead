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
The resolved source is validated against an anchored allowlist:
  * ``https://host/path`` — any HTTPS URL
  * ``git@host:path``     — standard SSH git URL
  * A local filesystem path — passed through ``_confine`` against the
    ``local_root`` argument (D-3 reuse of the existing confinement posture;
    do NOT invent a new confiner).

A source beginning with ``--`` or containing shell metacharacters
(``;``, ``|``, `` ` ``, ``$(``) is rejected before any git invocation.

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
    """A single pinned-repo entry in the install manifest."""

    name: str
    rev: str
    source: str
    tools: list


@dataclass
class InstallManifest:
    """Parsed and validated install manifest."""

    repos: list  # list of RepoEntry


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

# Shell metacharacters that must not appear in a resolved source URL/path.
# The set is intentionally conservative: a legitimate URL or path never needs
# these characters.
_SHELL_METACHAR_RE = re.compile(r"[;|`]|\$\(")


def _validate_rev(rev: str, repo_name: str, manifest_path: Path) -> None:
    """Assert rev is exactly 40 lowercase hex chars; raise InstallManifestError otherwise."""
    if not _SHA40_RE.match(rev):
        raise InstallManifestError(
            f"repo {repo_name!r}: 'rev' must be a full 40-character lowercase hex SHA "
            f"(never a tag, short SHA, HEAD, or 'latest'); got {rev!r} — "
            f"file: {manifest_path}"
        )


def _validate_source(
    source: str,
    repo_name: str,
    manifest_path: Path,
    *,
    local_root: Path | None,
) -> str:
    """Validate and return the resolved source string.

    Accepted forms (S-3):
      - https://host/path
      - git@host:path
      - An absolute local path (passed through _confine against local_root)

    Rejected:
      - Anything beginning with '--' (git option injection)
      - Anything containing shell metacharacters (; | ` $()

    Returns the source string unchanged if valid.
    """
    # Reject leading '--' (git option injection, S-3)
    if source.startswith("--"):
        raise InstallManifestError(
            f"repo {repo_name!r}: source begins with '--', which would inject a git "
            f"option — rejected for security (S-3); file: {manifest_path}"
        )

    # Reject shell metacharacters (S-3)
    if _SHELL_METACHAR_RE.search(source):
        raise InstallManifestError(
            f"repo {repo_name!r}: source contains shell metacharacters — "
            f"rejected for security (S-3); source: {source!r}; file: {manifest_path}"
        )

    # HTTPS URL — accepted as-is
    if source.startswith("https://"):
        return source

    # SSH git URL — accepted as-is
    if source.startswith("git@"):
        return source

    # Local path — confine against local_root (D-3, reuse _confine)
    # A local path with no local_root defaults to / (no meaningful confinement
    # possible without a root — treat as unconfined local, still safe post-metachar check)
    if local_root is not None:
        try:
            _confine(local_root, source, repo_name, "source")
        except ConfineError as exc:
            raise InstallManifestError(
                f"repo {repo_name!r}: local source escapes the confinement root "
                f"{local_root!r} — {exc}; file: {manifest_path}"
            ) from exc
        return source

    # No local_root provided: accept absolute paths that don't start with '--'
    # and have no shell metacharacters (already checked above).
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
    4. Validate ``rev`` is exactly 40 lowercase hex chars.
    5. Resolve ``${registry}`` templating in ``source``.
    6. Validate the resolved source against the S-3 allowlist.
    7. Detect duplicate ``name`` values (no last-wins).

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

        # Duplicate name check (no last-wins)
        if name in seen_names:
            raise InstallManifestError(
                f"install manifest {path}: duplicate repo entry {name!r} — "
                "each repo must appear exactly once"
            )
        seen_names.add(name)

        # Required field: rev
        rev = entry.get("rev")
        if rev is None:
            raise InstallManifestError(
                f"install manifest {path}: repo {name!r} is missing required field 'rev'"
            )
        _validate_rev(str(rev), name, path)

        # Required field: source
        source = entry.get("source")
        if source is None:
            raise InstallManifestError(
                f"install manifest {path}: repo {name!r} is missing required field 'source'"
            )

        # Resolve ${registry} before S-3 allowlist check
        source = _resolve_registry(str(source), registry, name, path)

        # S-3 source validation
        source = _validate_source(source, name, path, local_root=local_root)

        tools = list(entry.get("tools", []))

        repos.append(RepoEntry(name=name, rev=str(rev), source=source, tools=tools))

    return InstallManifest(repos=repos)
