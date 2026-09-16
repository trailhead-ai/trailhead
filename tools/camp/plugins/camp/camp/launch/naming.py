"""The group-qualified tmux session name, and the retired-name recognizer.

A workspace's tmux session is named ``camp-<group>-<slug>``: both halves are
folded through :func:`~camp.launch.recovery.sanitize_name_component`, the same
rule the launch engine already applies to every other tmux name component, so
a group or slug carrying a tmux target separator (``.`` or ``:``) still yields
a name tmux can address and later find again.

This is the contract the door, stopping, and reconciliation slices resolve
their tmux session through. It is deliberately pure — no I/O, no tmux, no
filesystem — so it can be established, and its edge cases pinned, before any
of those consumers exist.

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


#: ``sanitize_name_component`` folds every tmux target separator to the same
#: ``-``, so ``"a.b"`` and ``"a:b"`` would fold identically and two distinct
#: workspaces would mint the same session name. Each named separator is
#: spelled out to a distinct, already-safe word before folding, so the
#: distinction survives the fold instead of being erased by it. The raw
#: string still goes through :func:`sanitize_name_component` unmodified —
#: this only changes what reaches it.
_SEPARATOR_SPELLINGS = {
    ".": "-dot-",
    ":": "-colon-",
}


def _spell_out_separators(raw: str) -> str:
    for separator, spelling in _SEPARATOR_SPELLINGS.items():
        raw = raw.replace(separator, spelling)
    return raw


def workspace_session_name(group_name: str, slug: str) -> str:
    """The tmux session name for the workspace at *slug* in group *group_name*.

    Both components are folded through :func:`sanitize_name_component` before
    joining, so a separator either one carries never reaches the tmux name.
    A tmux target separator is spelled out to a distinct word first (see
    :data:`_SEPARATOR_SPELLINGS`), so two components differing only in which
    separator character they carry still fold to different names — a plain
    character-for-character fold would collapse them, since every unsafe
    character (including every separator) folds to the same ``-``.
    """
    group_component = sanitize_name_component(_spell_out_separators(group_name))
    slug_component = sanitize_name_component(_spell_out_separators(slug))
    return f"camp-{group_component}-{slug_component}"


def is_retired_session_name(name: str) -> bool:
    """Whether *name* matches the retired one-session-per-conversation form.

    Matches both the live form, ``camp-<component>-<8 hex>``, and the
    degraded root-unknown form, ``camp-<8 hex>``. Does not attempt to tell a
    retired name apart from a group-qualified name that happens to look like
    one — see the module docstring.
    """
    return _RETIRED_SESSION_NAME_RE.match(name) is not None
