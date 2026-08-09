"""The four startup checks a sweep must pass before it touches anything.

A sweep depends on four things it cannot install for itself: craft's refine
procedure (the per-task worker reads it as a document), a camp group (the
sweep's unit of work and the report's owner), a camp-group-elected lore
vault for kind ``task`` (the queue's source), and a resolvable committer
email (every promotion this sweep writes is provenance-stamped with it).
There is no install-time plugin-dependency enforcement anywhere in the suite,
so these runtime checks are the only guard — and each one fails with its own
one-line message naming the remediation, because "sweep didn't start" without
a next action is a dead end for an unattended operator.

**Order and atomicity.** All four run *before* the lock is acquired and
before the report is created, so a failed precondition leaves the filesystem
exactly as it found it. A half-started sweep — a lock with no report, or a
report no one will ever finish — is worse than no sweep at all.

**Why provenance is a startup check and not a per-write failure.** Lore
refuses to write a record without a committer email, and that refusal lands
mid-ritual inside a dispatched agent — where there is no human to ask and the
agent's only remaining moves are to fail the task or to invent an identity.
Agents have been observed doing the latter: improvising a plausible
``LORE_EMAIL`` and completing the write, so a sweep's promoted records carry
an author nobody chose. Checking once at ``start`` converts that into a
refusal before any task is dispatched, which is the only point where the
operator is still present to fix it.

**Refusal on a floor election.** ``lore vault resolve`` is total: it always
names a destination, falling back to the unconditional default vault when
nothing else matches. That floor is silent by design on lore's write path,
but for a sweep it is a trap: draining the default vault when the operator
meant to drain their group's vault would rewrite a pile of unrelated records
unattended, and nothing downstream would notice. So a
``scope`` of ``default`` is always a refusal here, and the message
distinguishes the three shapes it can take — no binding at all, a binding
whose vault was passed over by an allowlist, and a binding naming a vault
that isn't in lore's config — because the fix differs for each. The decision
reads ``scope``/``vault``/``skipped``/``skipped_reason``/``unmatched_scopes``;
``source`` is used only to name the failing binding back to the operator.

**Cross-plugin import.** Camp's group resolver is reached the same way lore
reaches it (``lore/vault/layers.py``): walk up from this file for the
trailhead repo-root marker, put camp's plugin root on ``sys.path``, then
import. Camp absent is a named error, never an ImportError traceback.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from trailhead.paths import config_dir, state_dir

from .queue import Runner, run_lore

#: Lore's committer-email sources, in the order lore itself resolves them
#: (``lore/record/store.py``'s ``resolve_committer_email``). Mirrored rather
#: than imported: ranger shells out to the ``lore`` CLI and never imports
#: lore's internals (see ``ranger.sweep.queue``), and lore exposes no verb
#: that reports the resolved email. The coupling is to an env var and a git
#: config key — a documented, stable contract — not to lore's vault layout,
#: which is what that decoupling rule exists to protect. If lore ever grows a
#: verb that prints the resolved identity, this check should call it instead.
_PROVENANCE_ENV = "LORE_EMAIL"
_PROVENANCE_GIT_KEY = "user.email"

_COMPOSED_SUBDIR = "composed"
_PROCEDURE_GLOB = "*/plugins/craft/skills/_shared/refine.md"
_PROCEDURE_TAIL = "<harness>/plugins/craft/skills/_shared/refine.md"
_TEMPLATES_DIRNAME = "templates"
_TASK_KIND = "task"

# Walk upward from this file for the trailhead repo root (the directory that
# contains trailhead/paths.py), then derive camp's plugin root from it.
_TRAILHEAD_ROOT: Path | None = None
for _p in (Path(__file__).resolve(), *Path(__file__).resolve().parents):
    if (_p / "trailhead" / "paths.py").exists():
        _TRAILHEAD_ROOT = _p
        break

_CAMP_PLUGIN_ROOT: Path | None = (
    _TRAILHEAD_ROOT / "tools" / "camp" / "plugins" / "camp" if _TRAILHEAD_ROOT else None
)


class PreflightError(Exception):
    """Raised when a startup check fails. The message is the operator-facing
    one-liner, remediation included — callers print it and exit nonzero."""


def find_refine_procedure(*, env: dict[str, str] | None = None) -> tuple[Path, Path]:
    """Return ``(procedure_path, templates_root)`` for craft's refine ritual.

    Globs across every composed harness root rather than naming one, so the
    sweep works under whichever harness (or harnesses) trailhead installed
    into. The templates root travels alongside the procedure because the
    procedure dereferences ``${CLAUDE_PLUGIN_ROOT}/templates/…``, which does
    not resolve inside a dispatched agent — the agent needs both absolute
    paths or it cannot follow the document it is handed.
    """
    composed = state_dir("trailhead", env=env) / _COMPOSED_SUBDIR
    matches = sorted(composed.glob(_PROCEDURE_GLOB))
    if not matches:
        raise PreflightError(
            f"craft's refine procedure was not found at {composed}/{_PROCEDURE_TAIL}; "
            "install craft first: trailhead install --plugin craft"
        )
    procedure = matches[0]
    # …/plugins/craft/skills/_shared/refine.md -> …/plugins/craft/templates
    return procedure, procedure.parents[2] / _TEMPLATES_DIRNAME


def check_provenance(
    *, env: dict[str, str] | None = None, runner: Runner | None = None
) -> str:
    """Return the committer email this sweep's writes will be stamped with, or refuse.

    Resolves in lore's own order — ``$LORE_EMAIL`` first, then
    ``git config --global user.email`` — because that is the identity every
    ``lore record update`` a dispatched agent makes will be stamped with. The
    ``--global`` scope is lore's, not an accident: a bare ``git config
    user.email`` would inherit whatever repo-local override the sweep happens
    to be running inside, so checking it here would pass on an identity lore
    will not use.

    Absent or unrunnable ``git`` is treated as "unresolved" rather than
    surfaced as its own error: the operator's next action is identical either
    way, and the remediation message already names both sources.
    """
    environ = os.environ if env is None else env
    email = (environ.get(_PROVENANCE_ENV) or "").strip()
    if not email:
        cmd = ["git", "config", "--global", "--includes", _PROVENANCE_GIT_KEY]
        effective = runner if runner is not None else _default_git_runner
        try:
            result = effective(cmd)
        except OSError:
            result = None
        if result is not None and result.returncode == 0:
            email = (result.stdout or "").strip()

    if not email:
        raise PreflightError(
            "no committer email resolves, so every record this sweep promotes would fail to "
            f"write (lore requires it for provenance); set it with 'git config --global "
            f"{_PROVENANCE_GIT_KEY} <you@example.com>', or export {_PROVENANCE_ENV} for this "
            "run — do not leave it to the dispatched agents, which cannot ask and have been "
            "observed inventing one"
        )
    return email


def _default_git_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5)


def _import_camp() -> tuple[Any, Any]:
    """Put camp's plugin root on ``sys.path`` and return ``(config, resolve)``."""
    if _CAMP_PLUGIN_ROOT is not None and str(_CAMP_PLUGIN_ROOT) not in sys.path:
        sys.path.append(str(_CAMP_PLUGIN_ROOT))
    try:
        import camp.group.config as camp_config
        import camp.group.resolve as camp_resolve
    except ImportError as exc:
        raise PreflightError(
            f"camp is not importable, so no group can be resolved ({exc}); "
            "install camp first: trailhead install --plugin camp"
        ) from exc
    return camp_config, camp_resolve


def resolve_group(*, cwd: Path, env: dict[str, str] | None = None) -> str:
    """Return the camp group this sweep belongs to, resolved from ``cwd``.

    Cwd is the only input on purpose. The group determines where the report is
    filed and which vault the sweep drains, and the vault half of that comes
    from ``lore vault resolve``, which reads the current directory's binding
    and takes no group argument. A flag naming a different group could
    therefore only relabel the report while the sweep still drained cwd's
    vault — a mismatch with no honest use, so the remediation for "no group
    here" is to move, not to override.
    """
    camp_config, camp_resolve = _import_camp()
    groups_dir = config_dir("camp", env=env) / "groups"

    try:
        configs = camp_config.load_all_groups(groups_dir)
    except camp_config.GroupConfigError as exc:
        raise PreflightError(f"camp group config is unreadable: {exc}") from exc

    try:
        name, _slug = camp_resolve.resolve_from_cwd(
            Path(cwd), configs, camp_state_dir=state_dir("camp", env=env), env=env
        )
    except camp_resolve.GroupResolutionError as exc:
        # Camp's own message is kept (it diagnoses overlap cases this layer
        # can't), minus its CLI prefix, which would read as a second tool
        # reporting the error.
        detail = str(exc).removeprefix("camp: ")
        raise PreflightError(
            f"no camp group resolves from {cwd} ({detail}); run the sweep from inside the "
            "camp group's workspace or one of its member repos — the group and its vault "
            "are both read from the current directory"
        ) from exc
    return name


def _binding_for_vault(source: dict[str, str], vault_name: str) -> str:
    """Render the ``<scope>:<name>`` binding that points at *vault_name*."""
    for scope, name in source.items():
        if name == vault_name:
            return f"{scope}:{name}"
    return vault_name


def resolve_vault(group: str, *, runner: Runner | None = None) -> dict:
    """Return ``lore vault resolve --kind task --json``'s payload, or refuse.

    Refuses whenever the election lands on the default floor — see the module
    docstring for why a silent floor is a trap for a sweep, and the three
    shapes the refusal message distinguishes.
    """
    result = run_lore(["vault", "resolve", "--kind", _TASK_KIND, "--json"], runner=runner)

    if result.get("scope") != "default" and result.get("vault"):
        return result

    prefix = f"camp group {group!r} elects no vault for kind {_TASK_KIND}"
    unmatched = result.get("unmatched_scopes") or []
    if unmatched:
        raise PreflightError(
            f"{prefix}: lore_scopes binding {unmatched[0]!r} names a vault that is not in "
            "lore's config.json; add that vault with 'lore vault add', or correct the binding"
        )

    skipped = result.get("skipped")
    if skipped:
        binding = _binding_for_vault(result.get("source") or {}, skipped)
        reason = result.get("skipped_reason") or "ineligible"
        raise PreflightError(
            f"{prefix}: lore_scopes binding {binding!r} was passed over ({reason}); "
            f"add {_TASK_KIND!r} to that vault's records allowlist, or bind a vault that "
            "accepts tasks"
        )

    raise PreflightError(
        f"{prefix} (resolution fell through to the default vault); add a lore_scopes "
        f"binding naming a configured vault to the group's camp TOML, e.g. "
        f'[[lore_scopes]] scope = "team", name = "<vault>"'
    )
