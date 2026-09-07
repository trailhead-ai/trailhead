"""The record-URL contract: the single documented reader link for a record.

Owns the path form (``/records/<vault>/<kind>/<slug>``), the base-URL
precedence, base validation, and URL construction. Pure and offline — this
module formats a string from configuration and performs no network I/O, so a
stopped reader daemon costs the printed link its target and nothing else.

The single-record read path this URL points at is deliberately cookie-less;
widening where these URLs travel (into agent terminal output) does not change
that classification.

**The vault segment is always the resolved, configured vault name — never a
directory basename.** ``Vault.name`` (see ``lore.vault.config``) is normalized
and ``config.json`` enforces unique vault *names*, not unique vault *paths*, so
two entries may point at the same directory. Deriving the segment from a
basename would print a wrong-vault link for any such vault. Every caller here
takes the vault name as an explicit parameter for this reason.
"""

from __future__ import annotations

import os
import warnings
from urllib.parse import quote, urlsplit

from .vault import config as vault_config

#: The built-in base URL when neither the environment nor config.json name one.
#: Matches the reader daemon's own default UI port.
DEFAULT_BASE = "http://127.0.0.1:7313"

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def resolve_base(env: dict | None = None) -> str:
    """Resolve the record-URL base, in precedence order.

    1. ``LORE_RECORD_URL_BASE``, if set to a non-empty value.
    2. The ``record_url_base`` key in ``config.json``, if present.
    3. :data:`DEFAULT_BASE`.

    A resolved base with no scheme, or a scheme other than ``http``/``https``,
    is rejected: a :class:`RuntimeWarning` names the rejected value and
    :data:`DEFAULT_BASE` is used in its place, so a garbled or hostile base
    never silently becomes a trusted-looking printed link.

    Args:
        env: Optional ``{str: str}`` environment override, used both for
             reading ``LORE_RECORD_URL_BASE`` and forwarded to
             :func:`lore.vault.config.read_record_url_base` for XDG
             resolution. ``None`` reads the real process environment.
    """
    source = os.environ if env is None else env
    env_base = source.get("LORE_RECORD_URL_BASE", "")
    if env_base:
        return _validate_base(env_base)

    config_base = vault_config.read_record_url_base(env=env)
    if config_base:
        return _validate_base(config_base)

    return DEFAULT_BASE


def _validate_base(base: str) -> str:
    """Return *base* if it carries an http(s) scheme AND a netloc, else the default.

    A scheme alone is not enough: ``urlsplit("http:nonsense")`` parses to
    scheme ``"http"`` with an empty netloc, and joining that with the record
    path would print ``http:nonsense/records/...`` — a broken link that looks
    superficially valid. Rejection is surfaced as a :class:`RuntimeWarning`
    rather than swallowed — a caller that wants to know why its configured
    base was ignored can capture it.
    """
    parsed = urlsplit(base)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        reason = "scheme must be http or https"
    elif not parsed.netloc:
        reason = "no host in the URL"
    elif "@" in parsed.netloc:
        # Userinfo makes the authority the text AFTER the "@", so a base like
        # http://127.0.0.1:7313@evil.test reads as local and resolves
        # elsewhere. A reader base never needs credentials.
        reason = "credentials in the URL"
    else:
        return base
    warnings.warn(
        f"lore: ignoring invalid record_url_base {base!r} "
        f"({reason}); falling back to {DEFAULT_BASE!r}",
        RuntimeWarning,
        stacklevel=3,
    )
    return DEFAULT_BASE


def _quote_segment(part: str) -> str:
    """Percent-encode one path segment, dot-segments included.

    ``quote`` leaves ``.`` unescaped — it is unreserved in RFC 3986 — so a
    segment that is exactly ``.`` or ``..`` would survive into the path and be
    collapsed by any client that normalizes dot segments, resolving the URL to
    a different record. Escaping the dots keeps the segment opaque.
    """
    if part in (".", ".."):
        return part.replace(".", "%2E")
    return quote(part, safe="")


def build_record_url(vault: str, kind: str, slug: str, *, env: dict | None = None) -> str:
    """Return the full reader URL for a record.

    ``vault`` must be the resolved, configured vault name (e.g. ``Vault.name``
    from :func:`lore.vault.config.load_config`), never a directory basename —
    see the module docstring. The path form is
    ``/records/<vault>/<kind>/<slug>``, the reader's only kind-general
    record-detail route. Every segment is percent-encoded, so a vault, kind,
    or slug containing a space, ``/``, ``..``, or ``?`` cannot alter the
    path's structure.

    Args:
        vault: The resolved, configured vault name.
        kind:  The record kind.
        slug:  The record slug.
        env:   Optional ``{str: str}`` environment override, forwarded to
               :func:`resolve_base`.
    """
    base = resolve_base(env=env).rstrip("/")
    segments = "/".join(_quote_segment(part) for part in (vault, kind, slug))
    return f"{base}/records/{segments}"
