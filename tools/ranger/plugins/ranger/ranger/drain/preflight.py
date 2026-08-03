"""The startup checks a drain must pass before it touches anything.

A drain depends on everything a refine sweep depends on — craft's ritual
procedure (here `execute.md` rather than `refine.md`), a resolvable
committer email, a camp group resolved from cwd, and a camp-group-elected
lore vault above the `default` floor — plus one drain-specific signal:
whether portage is installed at all. Those four shared checks are not
reimplemented here; `run_preflight` calls straight into
`ranger.sweep.preflight` for them; see that module's docstring for why each
one is a startup refusal rather than a per-write failure, why the floor
election is always a refusal, and why camp is imported rather than shelled
out to for group resolution.

**Why portage presence is a flag, not a refusal.** Unlike the other four
checks, an absent portage does not make a drain impossible — it makes the
loop's later PR-push and merge-gate phases unreachable. `ranger` has no
install-time plugin-dependency enforcement (see `ranger.sweep.preflight`),
so a vanilla install without portage must still be able to run the parts of
a drain that do not need it, per the project's vanilla-usage axiom. Rather
than refuse, `run_preflight` sets `degraded=True`, and it is on the loop
(a later slice) to read that flag and skip the phases portage would have
driven.

**Order.** Craft's execute procedure, then provenance, then group, then
vault, then portage — the same four-then-one order the module docstring
above describes, so every refusal in this list still runs before the lock
is taken and before anything is written; a failed precondition here leaves
the filesystem exactly as it found it, same as a refine sweep's `start`.
"""

from __future__ import annotations

from pathlib import Path

from trailhead.paths import state_dir

from ..sweep import preflight as sweep_preflight
from ..sweep.preflight import PreflightError  # re-exported for callers
from ..sweep.queue import Runner

_COMPOSED_SUBDIR = "composed"
_PROCEDURE_GLOB = "*/plugins/craft/skills/_shared/execute.md"
_PROCEDURE_TAIL = "<harness>/plugins/craft/skills/_shared/execute.md"
_TEMPLATES_DIRNAME = "templates"
_PORTAGE_PRESENCE_GLOB = "*/plugins/portage/.claude-plugin/plugin.json"

__all__ = ["PreflightError", "find_execute_procedure", "check_portage_presence", "run_preflight"]


def find_execute_procedure(*, env: dict[str, str] | None = None) -> tuple[Path, Path]:
    """Return ``(procedure_path, templates_root)`` for craft's execute ritual.

    Mirrors `ranger.sweep.preflight.find_refine_procedure` exactly, glob and
    all, except for the file it looks for — craft's execute mode-contract
    procedure rather than refine's. See that function's docstring for why the
    search globs across every composed harness root instead of naming one,
    and why the templates root travels alongside the procedure.
    """
    composed = state_dir("trailhead", env=env) / _COMPOSED_SUBDIR
    matches = sorted(composed.glob(_PROCEDURE_GLOB))
    if not matches:
        raise PreflightError(
            f"craft's execute procedure was not found at {composed}/{_PROCEDURE_TAIL}; "
            "install craft first: trailhead install --plugin craft"
        )
    procedure = matches[0]
    # …/plugins/craft/skills/_shared/execute.md -> …/plugins/craft/templates
    return procedure, procedure.parents[2] / _TEMPLATES_DIRNAME


def check_portage_presence(*, env: dict[str, str] | None = None) -> bool:
    """Return True iff portage is composed under any installed harness.

    Read-only and never raises: an absent portage sets degraded-trust mode
    (see the module docstring) rather than refusing the drain outright. The
    presence marker is the plugin's own `.claude-plugin/plugin.json`, the one
    file `compose.py` always includes for every composed tool (see
    `trailhead/compose.py`), so this check does not depend on which of
    portage's optional skills or agents happened to be selected at install.
    """
    composed = state_dir("trailhead", env=env) / _COMPOSED_SUBDIR
    return any(composed.glob(_PORTAGE_PRESENCE_GLOB))


def run_preflight(
    *, cwd: Path, env: dict[str, str] | None = None, runner: Runner | None = None
) -> dict:
    """Run every drain startup check in order, or raise `PreflightError`.

    Returns a dict carrying everything `drain start` needs to proceed:
    `procedure_path`, `templates_root`, `committer_email`, `group`, `vault`,
    `vault_path`, and `degraded` (True iff portage is absent). Every check
    before `degraded` is a refusal; `degraded` alone never raises.
    """
    procedure_path, templates_root = find_execute_procedure(env=env)
    committer_email = sweep_preflight.check_provenance(env=env, runner=runner)
    group = sweep_preflight.resolve_group(cwd=cwd, env=env)
    resolution = sweep_preflight.resolve_vault(group, runner=runner)
    degraded = not check_portage_presence(env=env)

    return {
        "procedure_path": procedure_path,
        "templates_root": templates_root,
        "committer_email": committer_email,
        "group": group,
        "vault": resolution["vault"],
        "vault_path": resolution["path"],
        "degraded": degraded,
    }
