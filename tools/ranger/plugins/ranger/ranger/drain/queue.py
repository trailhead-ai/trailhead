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
call ranger can import without reaching into camp's CLI-private module. A
task's derived slug already exists as a workspace read from `camp list
--json` in one of two shapes: the drain's own prior attempt at this exact
task (a fresh `camp new <slug>` workspace's branch is always
`worktree-<slug>`, so a workspace at this task's slug carrying that exact
branch is presumed to be this task's own — a resumable in-flight or
crashed run, not a collision) or something else entirely (a differently-named
task whose name normalizes to the same slug, or a workspace a human made by
hand) — reported `skipped:collision` with the colliding slug named, since
drain cannot safely reuse a workspace it did not create.

**Read-only**, like `ranger.sweep.queue`: this module only lists and reads,
via the same injectable lore-CLI `Runner` seam, plus its own `run_camp` for
`camp list --json` — the one drain read that has no lore analog.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from ..sweep.queue import QueueDeriveError, Runner, read_body, run_lore

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
    workspace slug, named in the report whichever bucket it lands in).
    Ordered oldest-first by `created-at`, matching `ranger.sweep.queue`.
    """
    candidates = _list_runnable_standalone_leaves(vault, runner=runner)
    workspaces = _list_group_workspaces(runner=runner)
    by_slug: dict[str, list[dict]] = {}
    for ws in workspaces:
        by_slug.setdefault(ws.get("slug"), []).append(ws)

    queue: list[dict] = []
    for entry in candidates:
        name = entry["name"]
        slug = derive_slug(name)
        body = read_body(name, vault=vault, runner=runner)

        if not is_buildable_payload(body):
            bucket = "skipped:not-buildable"
        else:
            expected_branch = f"worktree-{slug}"
            existing = by_slug.get(slug, [])
            collides = any(ws.get("branch") != expected_branch for ws in existing)
            bucket = "skipped:collision" if collides else "buildable"

        queue.append({**entry, "bucket": bucket, "slug": slug})
    return queue
