"""Deterministic harness session identity for a (group, slug) workspace.

camp must be able to RESUME the same harness session at the next `camp ai <slug>`
without remembering any per-session state — after launch camp os.execvp's the
harness and never runs again, so there is no point at which it could record the
session id the harness chose. The fix is to choose the id deterministically up
front: a stable UUID derived from (group, slug). The first launch creates the
session under that id; a later launch resumes it by the same id.

This is harness-AGNOSTIC identity (Axiom 1): it lives in camp core and knows
nothing about claude. The claude-specific part — that `--session-id` seeds a new
session id and `--resume` resumes one — lives in the launch profile
(harness_launch.py), which consumes the id this module produces.
"""
from __future__ import annotations

import uuid

# Fixed namespace for camp session ids. Derived (not a magic literal) so it is
# self-documenting and stable across machines/runs.
CAMP_SESSION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "camp.trailhead.session")


def session_id_for(group_name: str, slug: str) -> str:
    """Return the deterministic session UUID (string) for (group_name, slug).

    Stable: the same inputs always yield the same id, so `camp ai <slug>` can
    reconstruct the resumable id with no persisted state. The `<group>/<slug>`
    key keeps ids distinct across groups that happen to share a slug.
    """
    return str(uuid.uuid5(CAMP_SESSION_NAMESPACE, f"{group_name}/{slug}"))
