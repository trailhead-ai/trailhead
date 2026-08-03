"""Drain queue derivation for an elected vault.

The drain queue is a further narrowing of the refine sweep's own gate: where
`ranger.sweep.queue` drains every open/blocked standalone task, `ranger drain`
drains only the standalone leaves that `lore task list --runnable` already
says are ready to build (status `ready` with every `depends-on` target
`done` — see `lore/record/graph.py`'s `runnable()`), further filtered to the
ones whose payload actually names files this drain can build.

**Runnable is not leaf-only.** `lore task list --runnable` returns every
runnable *task*, including a runnable plan (a task with children) — the
containment shape gate below is what excludes it, mirrored from
`ranger.sweep.queue`'s standalone gate for the same reason: a task with
children is a plan, owned by `execute`'s slice-by-slice loop, never a single
buildable unit.

**Buildable payload.** A refined standalone task's body carries a
`**Files:**` line naming the paths its `**Delivers:**` touches, in
backtick-quoted, comma-separated form (see `craft/refine`'s payload shape).
A task is buildable when at least one of those paths looks like a
member-repo file — anything that is not itself a lore record id (`task/…`,
`spec/…`, …) or a path through a vault's own storage tree. A task refined to
touch only its own record, or another record, produces nothing `ranger
drain` can push as a member-repo change, so it is parked in
`skipped:not-buildable` rather than dispatched to build nothing.

**Slug collision.** Each drained task gets its own ephemeral camp workspace
inside the group drain is running in, named by camp's own slug
normalization (lowercase, non-`[a-z0-9-]` squashed to `-`, trimmed) of the
task's record name — mirrored here rather than imported, since camp's
normalizer (`camp/spine.py`'s `_normalize_slug`) is not exposed as a library
call ranger can import without reaching into camp's CLI-private module.
Two different sources of collision, both reported `skipped:collision`:

- **Intra-queue.** Two tasks in the *same* derive pass whose names normalize
  to the same slug (a case/punctuation variant, e.g. `"Fix Bug"` and
  `"fix-bug"`) would both claim the same camp workspace. Processed in the
  queue's own oldest-first order, the first claims the slug and keeps
  whatever bucket its payload earns; every later task with that slug is
  `skipped:collision`, naming the first task's name (`collision_with`) and
  the shared slug.
- **Against an existing workspace.** A task's derived slug already names a
  workspace in `camp list --json`. A same-branch guess is not proof of
  ownership — camp's `new` re-enters any existing slug on the same default
  `worktree-<slug>` branch regardless of which task asked for it, so a
  workspace with a *different* task's name normalizing to this slug would
  read as "this task's own" under a branch-only check. The actual resume
  marker is the task record's own `craft/branch` label — the plan's
  resume-from-`craft/branch` semantics, written by the execute ritual at
  dispatch (see `craft/skills/_shared/execute.md`). Only a task whose label
  names *exactly* this slug's `worktree-<slug>` branch is treated as this
  workspace's owner; no label, or a label naming anything else, is
  `skipped:collision` naming the existing workspace's slug. The label is
  read off the same `record show` call this module already makes for the
  body (`lore/record/tasks.py`'s documented 2-call pattern — `task list` for
  the candidate set, `record show --vault` per candidate for everything
  `task list`'s summary entries don't carry), never by globbing the vault.

**Read-only**, like `ranger.sweep.queue`: this module only lists and reads,
via the same injectable lore-CLI `Runner` seam, plus its own `run_camp` for
`camp list --json` — the one drain read that has no lore analog.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from ..sweep.queue import QueueDeriveError, Runner, run_lore

__all__ = [
    "QueueDeriveError",
    "DRAIN_BUCKETS",
    "DRAIN_OUTCOME_TOKENS",
    "derive_slug",
    "is_buildable_payload",
    "derive_drain_queue",
    "parse_drain_outcome",
]

DRAIN_BUCKETS = ("buildable", "skipped:not-buildable", "skipped:collision")

_FILES_LINE_RE = re.compile(r"^\*\*Files:\*\*\s*(.*)$")
_BACKTICK_RE = re.compile(r"`([^`]+)`")

#: A backtick-quoted token that names a lore record rather than a
#: member-repo file — either a record-id-shaped path (`task/…`, `spec/…`,
#: …) or a path running through a vault's own on-disk storage tree.
_RECORD_KIND_PREFIXES = (
    "task/", "spec/", "adr/", "area/", "decision/", "lesson/", "follow-up/", "session/",
)
_VAULT_PATH_MARKER = "/vaults/"

_SLUG_INVALID_RE = re.compile(r"[^a-z0-9-]+")

#: The drain outcome grammar. Shared with the sibling
#: `ranger-drain-report-and-outcome-contract` slice, which extends this into
#: full report bucket writing (`PUSHED` splits into merged / in-flight /
#: awaiting-approval / monitor-timeout there); this slice defines the
#: grammar and its validation surface so `drain record` can enforce it now.
#: `PUSHED <branch> <sha> <diffstat>` | `BLOCKED <reason>` | `FAILED
#: <reason>` | `SKIPPED <reason>` — every token takes a mandatory argument.
DRAIN_OUTCOME_TOKENS = frozenset({"PUSHED", "BLOCKED", "FAILED", "SKIPPED"})
_MAX_OUTCOME_ARG_CHARS = 200


def _default_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Production runner: subprocess.run with shell=False, output captured as text.

    Duplicated from `ranger.sweep.queue._default_runner` rather than
    imported — that name is module-private there, and this is the identical
    shape (`trailhead.vcs.runner`'s Runner protocol), just bound to `camp`
    instead of `lore`.
    """
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    return subprocess.run(cmd, **kwargs)


def run_camp(argv: list[str], *, runner: Runner | None) -> Any:
    """Run `camp <argv>` via the injectable runner and return its parsed JSON stdout.

    Mirrors `ranger.sweep.queue.run_lore`'s contract exactly, one CLI name
    swapped for the other: an absent or unrunnable `camp`, a nonzero exit, or
    unparseable stdout all raise `QueueDeriveError` — the same named-refusal
    shape every drain precondition and read already uses, never a raw
    subprocess exception reaching an unattended operator.
    """
    effective = runner if runner is not None else _default_runner
    cmd = ["camp", *argv]
    try:
        result = effective(cmd)
    except FileNotFoundError as exc:
        raise QueueDeriveError(
            "camp CLI not found on PATH — install camp or adjust PATH"
        ) from exc
    except OSError as exc:
        raise QueueDeriveError(
            f"camp CLI could not be run ({exc}) — install camp or adjust PATH"
        ) from exc
    if result.returncode != 0:
        raise QueueDeriveError(
            f"camp {' '.join(argv)} failed: {result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise QueueDeriveError(
            f"camp {' '.join(argv)} emitted unparseable JSON: {exc}"
        ) from exc


def derive_slug(task_name: str) -> str:
    """Return camp's own slug normalization of *task_name*.

    Mirrors `camp/spine.py`'s `_normalize_slug`: lowercase, every run of
    characters outside `[a-z0-9-]` collapsed to a single `-`, leading and
    trailing `-` trimmed. Reimplemented rather than imported — see the
    module docstring's Slug collision section for why.
    """
    lowered = task_name.lower()
    replaced = _SLUG_INVALID_RE.sub("-", lowered)
    return replaced.strip("-")


def _extract_files_paths(body: str) -> list[str]:
    """Return every backtick-quoted token on the body's `**Files:**` line, if any."""
    for line in body.splitlines():
        match = _FILES_LINE_RE.match(line.strip())
        if match:
            return _BACKTICK_RE.findall(match.group(1))
    return []


def _is_member_repo_path(token: str) -> bool:
    if token.startswith(_RECORD_KIND_PREFIXES):
        return False
    if _VAULT_PATH_MARKER in token:
        return False
    return True


def is_buildable_payload(body: str) -> bool:
    """True iff *body*'s `**Files:**` line names at least one member-repo path.

    See the module docstring's Buildable payload section. A body with no
    `**Files:**` line at all, an empty one, or one naming only lore record
    ids is not buildable.
    """
    return any(_is_member_repo_path(p) for p in _extract_files_paths(body))


def _list_runnable_standalone_leaves(vault: str, *, runner: Runner | None) -> list[dict]:
    """Return every runnable standalone-leaf task in *vault*, oldest-first.

    `--runnable` alone is not enough — it returns every runnable task,
    including a runnable plan with children (see the module docstring) — so
    the containment shape gate is applied here, the same as
    `ranger.sweep.queue`'s standalone gate.
    """
    entries = run_lore(
        ["task", "list", "--vault", vault, "--runnable", "--json"],
        runner=runner,
    )
    standalone = [e for e in entries if e.get("parent") is None and not e.get("children")]
    standalone.sort(key=lambda e: (e.get("created-at") or "", e["name"]))
    return standalone


def _list_group_workspaces(*, runner: Runner | None) -> list[dict]:
    """Return the current camp group's workspaces via `camp list --json`.

    Group-scoped, not global: `camp list` resolves its group from cwd
    exactly like `lore vault resolve` does (see `ranger.sweep.preflight`),
    so this lists only the workspaces of the group the drain is already
    running inside — the collision surface the module docstring describes.
    """
    return run_camp(["list", "--json"], runner=runner)


def _read_task_record(name: str, *, vault: str, runner: Runner | None) -> tuple[str, dict]:
    """Return `(body, labels)` for task *name* in *vault* via one `record show`.

    The second call of the documented 2-call pattern (`lore/record/tasks.py`):
    `task list` supplies the candidate set's summary fields, and this reads
    everything a summary entry doesn't carry — the body (for the `**Files:**`
    buildable check) and the sidecar's `labels` map (for the `craft/branch`
    resume marker) — in the single read this module already needed for the
    body, never a second round trip and never a vault glob.
    """
    payload = run_lore(
        ["record", "show", f"task/{name}", "--vault", vault, "--json"],
        runner=runner,
    )
    body = payload.get("body", "")
    labels = (payload.get("sidecar") or {}).get("labels") or {}
    return body, labels


def parse_drain_outcome(line: str) -> tuple[str | None, str]:
    """Split a drain outcome line into `(token, argument)`.

    Grammar: `PUSHED <branch> <sha> <diffstat>` | `BLOCKED <reason>` |
    `FAILED <reason>` | `SKIPPED <reason>`. Returns `(None, <line>)` when the
    first line is not one of the four tokens, or the token's mandatory
    argument is missing — the caller (`drain record`) treats that as a
    validation failure, mirroring `ranger.sweep.sweep.parse_outcome`'s shape
    for the refine grammar.
    """
    first_line = line.strip().splitlines()[0].strip() if line.strip() else ""
    token, _, argument = first_line.partition(" ")
    argument = argument.strip()
    if token not in DRAIN_OUTCOME_TOKENS or not argument:
        return None, first_line[:_MAX_OUTCOME_ARG_CHARS]
    return token, argument


def derive_drain_queue(vault: str, *, runner: Runner | None = None) -> list[dict]:
    """Derive and classify the drain queue for *vault*.

    Returns a list of dicts — each candidate's `lore task list` entry plus
    `bucket` (one of `DRAIN_BUCKETS`) and `slug` (this task's derived camp
    workspace slug, named in the report whichever bucket it lands in). A
    `skipped:collision` entry additionally carries `collision_with`: the
    colliding task's name for an intra-queue slug clash, or the colliding
    slug again (already in `slug`) for an existing-workspace clash — see the
    module docstring's Slug collision section for both. Ordered oldest-first
    by `created-at`, matching `ranger.sweep.queue`.
    """
    candidates = _list_runnable_standalone_leaves(vault, runner=runner)
    workspaces = _list_group_workspaces(runner=runner)
    by_slug: dict[str, list[dict]] = {}
    for ws in workspaces:
        by_slug.setdefault(ws.get("slug"), []).append(ws)

    queue: list[dict] = []
    slug_owner: dict[str, str] = {}

    for entry in candidates:
        name = entry["name"]
        slug = derive_slug(name)

        # Intra-queue collision: a later task's slug already belongs to an
        # earlier one in this same derive pass. Checked before the record
        # read even runs — two different tasks can never both own this
        # workspace, so there is nothing left to classify.
        if slug in slug_owner:
            queue.append(
                {
                    **entry,
                    "bucket": "skipped:collision",
                    "slug": slug,
                    "collision_with": slug_owner[slug],
                }
            )
            continue
        slug_owner[slug] = name

        body, labels = _read_task_record(name, vault=vault, runner=runner)

        if not is_buildable_payload(body):
            queue.append({**entry, "bucket": "skipped:not-buildable", "slug": slug})
            continue

        existing = by_slug.get(slug, [])
        if existing:
            expected_branch = f"worktree-{slug}"
            resume_label = labels.get("craft/branch")
            if resume_label != expected_branch:
                queue.append(
                    {
                        **entry,
                        "bucket": "skipped:collision",
                        "slug": slug,
                        "collision_with": slug,
                    }
                )
                continue

        queue.append({**entry, "bucket": "buildable", "slug": slug})

    return queue
