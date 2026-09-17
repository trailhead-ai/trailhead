"""The group-qualified tmux session name, and the retired-name recognizer.

A workspace's tmux session is named ``camp-<group>-<slug>``. The property
this derivation owes: when two workspaces share a slug, their groups must
still resolve to different session names — a listing or a stopping slice
that only ever compares within one slug must never see two workspaces
collapse onto one session. This holds for *any* two distinct groups sharing
a slug, not just ones that happen to look alike, because it is established
component-by-component before the two are joined:

1. :func:`_escape_component` maps *raw* to a string drawn only from
   ``[A-Za-z0-9_-]`` — every character outside ``[A-Za-z0-9-]``, including
   ``_`` itself, is replaced by ``_<hex>_`` (the escape character is escaped
   first, so a literal ``_`` in the input and an escape-introducer can never
   be confused). ``-`` is left alone: it is common in real group and slug
   names, and escaping it would make every ordinary name harder to read and
   type for no gain the join needs. This is a uniquely-decodable code:
   scanning left to right, a bare ``_`` can only be the start of an escape
   sequence, since every literal ``_`` in the input was itself escaped. Two
   different raw strings therefore always escape to two different strings.
2. The escaped output is folded through
   :func:`~camp.launch.recovery.sanitize_name_component` — the same rule the
   launch engine already applies to every other tmux name component — which
   is the identity on ``[A-Za-z0-9_-]`` (nothing left to fold), so this step
   changes nothing except mapping an empty component to its fallback word.

Two components that are equal as *strings* only reach that equality by
having equal raw input (step 1 is injective), so for a fixed slug, two
distinct groups always escape to distinct group components, and therefore
always join to distinct names — the property this module owes. What leaving
``-`` literal gives up is join-level injectivity over the *pair*: the
assembled name does not let a reader tell where the group component ends
and the slug component begins, so two different (group, slug) pairs whose
concatenation coincides — e.g. ``("trailhead", "camp-cli")`` and
``("trailhead-camp", "cli")`` — derive the same name. This is a known,
accepted limitation, not a defect this module attempts to close: it only
becomes observable when both pairs are listed side by side, and it never
causes two workspaces sharing a slug to collide.

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
#: outside this set — including ``_`` itself, so it can serve as the escape
#: introducer — is replaced by an escape sequence. ``-`` is included: it
#: stays literal in the escaped output, which is what leaves the join between
#: components ambiguous at the pair level (see the module docstring's known
#: limitation).
_UNESCAPED = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
)


def _escape_component(raw: str) -> str:
    return "".join(
        ch if ch in _UNESCAPED else f"_{ord(ch):x}_" for ch in raw
    )


def workspace_session_name(group_name: str, slug: str) -> str:
    """The tmux session name for the workspace at *slug* in group *group_name*.

    Both components are escaped with :func:`_escape_component` and then
    folded through :func:`sanitize_name_component` before joining — see the
    module docstring for why that keeps two distinct groups sharing one slug
    from ever colliding, and for the known limitation this leaves open.
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
