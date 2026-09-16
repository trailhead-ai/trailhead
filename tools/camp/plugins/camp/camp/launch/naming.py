"""The group-qualified tmux session name, and the retired-name recognizer.

A workspace's tmux session is named ``camp-<group>-<slug>``. The derivation's
one binding property: it is injective on the ``(group, slug)`` pair — two
different pairs never produce the same name. That is what the group
qualification is *for* — the derived name is the contract the door, stopping,
and reconciliation slices resolve their tmux session through, and it is used
to decide whether a tmux session belongs to a given workspace. Two workspaces
colliding onto one name means one workspace's session gets attributed to
another.

Injectivity is established in two steps, applied to each component
separately before the two are joined:

1. :func:`_escape_component` maps *raw* to a string drawn only from
   ``[A-Za-z0-9_]`` — every character outside ``[A-Za-z0-9]``, including
   ``-`` and ``_`` themselves, is replaced by ``_<hex>_`` (the escape
   character is escaped first, so a literal ``_`` in the input and an
   escape-introducer can never be confused). This is a uniquely-decodable
   code: scanning left to right, a bare ``_`` can only be the start of an
   escape sequence, since every literal ``_`` in the input was itself
   escaped. Two different raw strings therefore always escape to two
   different strings.
2. The escaped output is folded through
   :func:`~camp.launch.recovery.sanitize_name_component` — the same rule the
   launch engine already applies to every other tmux name component — which
   is the identity on ``[A-Za-z0-9_]`` (nothing left to fold), so this step
   changes nothing except mapping an empty component to its fallback word.

Because neither escaped, sanitized component can contain a literal ``-``,
joining them with a single literal ``-`` is unambiguous: the interior ``-``
in the assembled name is never mistaken for one internal to either
component, so the pair cannot be reconstructed two different ways. This is
what makes the join injective, not just the folding — joining two components
that could themselves contain ``-`` (as ``sanitize_name_component`` alone
allows) is exactly the defect this scheme avoids.

This is deliberately pure — no I/O, no tmux, no filesystem — so it can be
established, and its edge cases pinned, before any of those consumers exist.

``is_retired_session_name`` recognizes the OTHER naming scheme this module
does not mint: the one-session-per-conversation form camp used before the
group-qualified name existed, ``camp-<component>-<8 hex>``, degrading to
``camp-<8 hex>`` when the launch root was unknown at creation time
(``camp/launch/session.py`` / ``camp/launch/recovery.py``). A name can satisfy
both this module's own naming rule and the retired pattern at once — a slug
that happens to end in what reads as an 8-hex tail — and this module makes no
attempt to disambiguate that case. It is a recognizer, not an arbiter; the
caller resolving a tmux enumeration against a group's known workspaces decides
precedence.
"""

from __future__ import annotations

import re

from .recovery import sanitize_name_component

#: ``camp-<component>-<8 hex>`` or the degraded ``camp-<8 hex>``, where
#: ``<component>`` is anything the launch engine could have folded a name
#: component to (see ``sanitize_name_component``): letters, digits,
#: underscores, and internal hyphens, non-empty when present. The hex tail is
#: exactly 8 characters, anchored at both ends so a longer or shorter run
#: never matches.
_RETIRED_SESSION_NAME_RE = re.compile(
    r"^camp-(?:[A-Za-z0-9_-]+-)?[0-9a-fA-F]{8}$"
)


#: Characters that pass through :func:`_escape_component` unescaped. Anything
#: outside this set — including ``-`` and ``_`` — is replaced by an escape
#: sequence, so the escaped output never contains a literal ``-`` and the
#: later ``-`` join between components is unambiguous.
_UNESCAPED = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)


def _escape_component(raw: str) -> str:
    return "".join(
        ch if ch in _UNESCAPED else f"_{ord(ch):x}_" for ch in raw
    )


def workspace_session_name(group_name: str, slug: str) -> str:
    """The tmux session name for the workspace at *slug* in group *group_name*.

    Both components are escaped with :func:`_escape_component` and then
    folded through :func:`sanitize_name_component` before joining — see the
    module docstring for why that combination is injective on the
    ``(group_name, slug)`` pair.
    """
    group_component = sanitize_name_component(_escape_component(group_name))
    slug_component = sanitize_name_component(_escape_component(slug))
    return f"camp-{group_component}-{slug_component}"


def is_retired_session_name(name: str) -> bool:
    """Whether *name* matches the retired one-session-per-conversation form.

    Matches both the live form, ``camp-<component>-<8 hex>``, and the
    degraded root-unknown form, ``camp-<8 hex>``. Does not attempt to tell a
    retired name apart from a group-qualified name that happens to look like
    one — see the module docstring.
    """
    return _RETIRED_SESSION_NAME_RE.match(name) is not None
