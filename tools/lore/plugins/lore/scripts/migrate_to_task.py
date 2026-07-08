"""One-shot, **throwaway** migration: fold ``backlog`` + ``plan`` into ``task``.

The record model now ships a single ``task`` kind unifying the retired
``backlog`` and ``plan`` kinds. This script is the corpus-wide cutover that
brings a live vault's data in line with the model. It is **dry-run by default**;
``--apply`` is required to mutate anything. It is a one-shot tool, removed once
the cutover is verified.

What it does (one unified per-record pass — the kind/status remap and the
related-key sweep are applied together in memory, so no half-migrated,
schema-invalid record is ever written to disk):

  * every ``backlog/<name>`` record becomes ``task/<name>`` with its status
    mapped (``open→open``, ``tracking→blocked``, ``dropped→dropped``);
  * every ``plan/<name>`` record becomes ``task/<name>`` with its status mapped
    (``draft→open``, ``ready→ready``, ``in-progress→in-progress``,
    ``complete→done``, ``superseded→superseded``, ``dropped→dropped``);
  * **related sweep (vault-wide)** — on records of *every* kind, any
    ``related.backlog`` / ``related.plan`` target names are MERGED into
    ``related.task`` (dedup, order-preserving) and the retired keys removed.
    Target names are bare record names, so the sweep only renames the map key —
    a backlog/plan record keeps its stem, so inbound links stay valid;
  * **reindex** — an in-process ``index_store.rebuild`` over the migrated tree.

Safety model (git is the only safety net):

  * ``--apply`` preconditions, in order: (a) the vault git working tree is clean
    — uncommitted edits are refused, since ``git reset --hard`` cannot recover
    them; (b) a named restore point is created by the script (the
    ``pre-task-migration`` tag at the clean HEAD — since the tree is clean, that
    commit's tree *is* the pre-apply state); (c) the **composed** live skill tree
    (not the repo) greps clean of retired-kind command/reference forms, so the
    vault never flips while the installed skills still emit ``--kind backlog`` /
    ``--kind plan``.
  * Planning (walk + transcode + validate + link-integrity gate) writes NOTHING;
    an ``--apply`` run that finds any problem aborts before the first write.
  * **Post-checks** after the writes: zero legacy kinds/keys remain; every sidecar
    validates clean (checked AFTER both the remap and the sweep — a mid-sequence
    check would transiently reject records still carrying a swept key); and a
    **per-record subset gate** — the pre-migration union of
    ``related.{backlog,plan,task}`` targets must be a subset of the post-migration
    ``related.task`` targets. This is a real set-subset check, not a count check:
    a count check passes while a link silently drops.
  * **On any error during or after the writes: restore to the pre-apply tag**
    (``git reset --hard`` + ``git clean -fd``) and exit non-zero.

Idempotent by design: a run over an already-migrated vault finds nothing to
change and is a no-op — no restore point, no writes. Intended procedure: run
once without ``--apply`` to review the plan, then ``--apply`` and commit the
vault; remove the script afterwards.
"""
# NOTE: no ``from __future__ import annotations`` — this throwaway is loaded via
# an isolated importlib exec in the test harness; runtime-evaluated annotations
# keep it importable without registering in ``sys.modules``.

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Put the plugin root (this file's ``../../``) on sys.path so the sibling ``lore``
# package is importable when this script is run standalone from the repo — the
# test harness has already done so, so the insert is a harmless duplicate there.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The in-process write/validate/index APIs, imported at module level so tests
# patch them on this module's collaborators (the project's DI convention).
from lore.record import model as record_model  # noqa: E402
from lore.record import store as record_store  # noqa: E402
from lore.search import index as index_store  # noqa: E402

# ---------------------------------------------------------------------------
# Mapping tables
# ---------------------------------------------------------------------------

#: The retired kinds folded into ``task``.
LEGACY_KINDS = ("backlog", "plan")
TARGET_KIND = "task"

#: Total legacy-status → task-status maps (from the plan's axioms). The domains
#: cover the full legacy vocab; an out-of-domain value is a hard error, never a
#: silent pass-through.
_STATUS_MAP = {
    "backlog": {"open": "open", "tracking": "blocked", "dropped": "dropped"},
    "plan": {
        "draft": "open",
        "ready": "ready",
        "in-progress": "in-progress",
        "complete": "done",
        "superseded": "superseded",
        "dropped": "dropped",
    },
}

#: The named git restore point the script creates before any ``--apply`` write.
RESTORE_TAG = "pre-task-migration"

#: Retired-kind reference forms a composed skill could still EMIT. Deliberately
#: the machine/command shapes (``--kind backlog``, ``related.plan``, YAML
#: ``kind: plan``) rather than the bare word "plan" — the composed craft tree
#: legitimately contains the ``/plan`` skill and prose "plan" everywhere, so a
#: bare-word grep would be all false positives. Case-sensitive: kinds are
#: written lowercase.
_LEGACY_COMPOSED_RE = re.compile(
    r"--kind\s+(?:backlog|plan)\b"
    r"|--related\s+(?:backlog|plan)="
    r"|\brelated\.(?:backlog|plan)\b"
    r"|\b(?:kind|type):\s*(?:backlog|plan)\b"
)


# ---------------------------------------------------------------------------
# Pure transforms
# ---------------------------------------------------------------------------


def map_status(legacy_kind, status):
    """Map a legacy ``backlog``/``plan`` status to its ``task`` equivalent.

    Raises ``ValueError`` on any value outside the legacy vocab — the migration
    refuses to guess a status it was not told how to map.
    """
    table = _STATUS_MAP[legacy_kind]
    if status not in table:
        raise ValueError(f"no status mapping for {legacy_kind} status {status!r}")
    return table[status]


def sweep_related(related):
    """Merge ``related.backlog``/``related.plan`` into ``related.task``.

    Order-preserving and deduplicated: the resulting ``task`` list is the
    existing ``task`` targets followed by any new backlog/plan targets not
    already present. Every other kind key passes through untouched. Returns a new
    dict; the retired keys never survive.
    """
    if not isinstance(related, dict):
        return related
    result = {k: list(v) for k, v in related.items() if k not in LEGACY_KINDS}
    merged = list(result.get(TARGET_KIND, []))
    for legacy_kind in LEGACY_KINDS:
        for name in related.get(legacy_kind, []):
            if name not in merged:
                merged.append(name)
    if merged:
        result[TARGET_KIND] = merged
    return result


def related_union(related):
    """The set of target names under the ``backlog``/``plan``/``task`` keys."""
    names = set()
    if isinstance(related, dict):
        for key in (*LEGACY_KINDS, TARGET_KIND):
            names.update(related.get(key, []) or [])
    return names


def check_subset(pre_union, post_targets):
    """Return the target names present pre-migration but missing post-migration.

    A non-empty result means the merge dropped a link — the run must fail. This
    is a set-subset check, NOT a count check.
    """
    return set(pre_union) - set(post_targets)


def check_composed_tree(composed_root):
    """Grep the composed skill tree for retired-kind reference forms.

    Returns a list of ``"<path>:<lineno>: <line>"`` violation strings (empty ==
    clean). The path is injectable so the precondition is provable against a
    fixture tree without a real ``bin/trailhead install`` run.
    """
    root = Path(composed_root)
    violations = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if _LEGACY_COMPOSED_RE.search(line):
                violations.append(f"{path}:{lineno}: {line.strip()}")
    return violations


# ---------------------------------------------------------------------------
# Planning (read-only)
# ---------------------------------------------------------------------------


@dataclass
class _Change:
    """One record's planned migration: where it goes and what it becomes."""

    source_json: Path
    source_body: Path
    location: object  # record_store.RecordLocation
    sidecar: dict
    body: str
    pre_union: set
    retire_source: bool


@dataclass
class _Plan:
    changes: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def blocked(self):
        return bool(self.errors)


def plan_migration(vault_root):
    """Walk the vault, compute each record's fully-migrated form, and gate it.

    Read-only. For every ``<kind>/<name>.json`` at the flat top level: applies the
    kind/status remap (backlog/plan only) and the related sweep (all kinds) in
    memory, validates the result, and runs the per-record subset gate. Records
    that come out identical are skipped (idempotency). Validation failures and
    subset violations accumulate in ``_Plan.errors``.
    """
    root = Path(vault_root)
    plan = _Plan()
    for source_json in sorted(root.rglob("*.json")):
        if ".git" in source_json.parts:
            continue
        if source_json.parent.parent != root:
            continue  # not a flat <kind>/<name>.json record
        legacy_kind = source_json.parent.name
        name = source_json.stem
        sidecar = json.loads(source_json.read_text(encoding="utf-8"))
        source_body = source_json.with_suffix(".md")
        body = source_body.read_text(encoding="utf-8") if source_body.exists() else ""

        original_related = sidecar.get("related", {})
        pre_union = related_union(original_related)

        migrated = dict(sidecar)
        is_legacy = legacy_kind in LEGACY_KINDS
        target_kind = TARGET_KIND if is_legacy else sidecar.get("kind", legacy_kind)

        if is_legacy:
            try:
                migrated["status"] = map_status(legacy_kind, sidecar.get("status"))
            except ValueError as exc:
                plan.errors.append(f"{legacy_kind}/{name}: {exc}")
                continue
            migrated["kind"] = target_kind

        swept = sweep_related(original_related)
        if swept:
            migrated["related"] = swept
        elif "related" in migrated:
            del migrated["related"]

        kind_changed = target_kind != legacy_kind
        status_changed = migrated.get("status") != sidecar.get("status")
        related_changed = swept != original_related
        if not (kind_changed or status_changed or related_changed):
            continue

        location = record_store.RecordLocation(
            vault_root=str(root),
            kind=target_kind,
            name=name,
            record_id=f"{target_kind}/{name}",
            body_path=root / target_kind / f"{name}.md",
            sidecar_path=root / target_kind / f"{name}.json",
        )

        # Validate only after BOTH the remap and the sweep are applied — checking
        # in between would transiently reject a record still carrying a swept key.
        result = record_model.validate(migrated, kind=target_kind)
        if result.errors:
            plan.errors.append(f"{location.record_id}: {result.errors}")

        # Per-record link-integrity subset gate.
        missing = check_subset(pre_union, related_union(migrated.get("related", {})))
        if missing:
            plan.errors.append(
                f"{location.record_id}: related links dropped: {sorted(missing)}"
            )

        plan.changes.append(
            _Change(
                source_json=source_json,
                source_body=source_body,
                location=location,
                sidecar=migrated,
                body=body,
                pre_union=pre_union,
                retire_source=kind_changed,
            )
        )
    return plan


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
    )


def _git_porcelain(root):
    """``git status --porcelain`` for *root* (``None`` when not a git repo)."""
    result = _git(root, "status", "--porcelain")
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _create_restore_point(root):
    """Capture the clean HEAD as the pre-apply restore point.

    Returns the HEAD commit SHA — the authoritative rollback ref (the tree is
    clean, so that commit's tree *is* the pre-apply state). Also stamps a
    human-discoverable ``pre-task-migration`` tag, forcing the signing config off
    so a global ``tag.gpgSign`` / ``tag.forceSignAnnotated`` cannot block the
    lightweight marker; the tag is a convenience, never the rollback dependency.
    """
    sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    _git(
        root,
        "-c",
        "tag.gpgSign=false",
        "-c",
        "tag.forceSignAnnotated=false",
        "tag",
        "-f",
        RESTORE_TAG,
    )
    return sha


def _rollback(root, restore_sha):
    """Restore the vault to the pre-apply commit (git-as-rollback)."""
    _git(root, "reset", "--hard", restore_sha)
    _git(root, "clean", "-fd")


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _write_changes(plan, root):
    """Write every planned record, retire moved legacy sources, then reindex."""
    conn = index_store.open_index()
    try:
        for change in plan.changes:
            record_store.validate_and_write(
                change.location, change.sidecar, change.body, conn
            )
            if change.retire_source:
                _unlink_quietly(change.source_json)
                _unlink_quietly(change.source_body)
        _prune_empty_dirs(root)
        index_store.rebuild([str(root)], conn)
        conn.commit()
    finally:
        conn.close()


def _verify_post(plan, root):
    """Post-write checks reading from disk: zero legacy, all valid, links intact."""
    errors = []
    for legacy_kind in LEGACY_KINDS:
        kind_dir = root / legacy_kind
        if kind_dir.exists() and any(kind_dir.glob("*.json")):
            errors.append(f"legacy {legacy_kind}/ records remain")

    for source_json in root.rglob("*.json"):
        if ".git" in source_json.parts:
            continue
        sidecar = json.loads(source_json.read_text(encoding="utf-8"))
        related = sidecar.get("related", {})
        for legacy_kind in LEGACY_KINDS:
            if legacy_kind in related:
                errors.append(f"{source_json}: related.{legacy_kind} remains")
        kind = source_json.parent.name
        result = record_model.validate(sidecar, kind=kind)
        if result.errors:
            errors.append(f"{source_json}: {result.errors}")

    for change in plan.changes:
        sidecar = json.loads(change.location.sidecar_path.read_text(encoding="utf-8"))
        post = related_union(sidecar.get("related", {}))
        missing = check_subset(change.pre_union, post)
        if missing:
            errors.append(
                f"{change.location.record_id}: related links dropped: {sorted(missing)}"
            )
    return errors


def _unlink_quietly(path):
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def _prune_empty_dirs(root):
    """Remove now-empty legacy kind dirs (bottom-up); never touch root or .git."""
    root = Path(root)
    for path in sorted(
        (p for p in root.rglob("*") if p.is_dir() and ".git" not in p.parts),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _default_composed_root():
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "trailhead" / "composed"


def run_migration(vault_root, *, apply=False, composed_root=None):
    """Migrate a vault's backlog+plan records to ``task``. Returns an exit code.

    Dry-run (``apply=False``) plans + reports and writes nothing. ``apply=True``
    enforces the preconditions, makes the restore point, writes, verifies, and
    rolls back on any failure.
    """
    root = Path(vault_root)

    # Dry-run: plan, report, write nothing (no git/composed preconditions — the
    # composed tree is expected to still be legacy pre-recompose; that is exactly
    # what dry-run + review happens before).
    if not apply:
        plan = plan_migration(root)
        _report(plan, root, applying=False)
        return 1 if plan.blocked else 0

    # --- Precondition (a): clean git working tree. -------------------------
    dirty = _git_porcelain(root)
    if dirty is None:
        print(f"abort: {root} is not a git repository (git is the only safety net)")
        return 1
    if dirty:
        print(
            f"abort: vault working tree at {root} is dirty — commit or stash "
            "first (git reset --hard cannot recover uncommitted edits)"
        )
        return 1

    # --- Precondition (c): composed live skill tree is clean of legacy kinds. -
    composed = Path(composed_root) if composed_root is not None else _default_composed_root()
    if not composed.exists():
        print(
            f"abort: composed skill tree not found at {composed} — run "
            "`bin/trailhead install` so the live skills are migrated before the vault"
        )
        return 1
    composed_violations = check_composed_tree(composed)
    if composed_violations:
        print(
            f"abort: composed skill tree at {composed} still emits retired kinds "
            "(recompose before migrating the vault):"
        )
        for violation in composed_violations:
            print(f"  {violation}")
        return 1

    # --- Plan + gate (writes nothing). -------------------------------------
    plan = plan_migration(root)
    _report(plan, root, applying=True)
    if plan.blocked:
        return 1
    if not plan.changes:
        print("nothing to migrate — vault already on the task kind")
        return 0

    # --- Precondition (b): named restore point at the clean HEAD. ----------
    restore_sha = _create_restore_point(root)

    # --- Write, then verify; roll back to the restore point on any failure. -
    try:
        _write_changes(plan, root)
    except Exception as exc:  # noqa: BLE001 — any raise means a partial write.
        _rollback(root, restore_sha)
        print(
            f"error mid-apply: {exc}\nrolled back to {RESTORE_TAG}; vault restored",
            file=sys.stderr,
        )
        return 1

    post_errors = _verify_post(plan, root)
    if post_errors:
        _rollback(root, restore_sha)
        print(
            f"post-check failed; rolled back to {RESTORE_TAG}:\n  "
            + "\n  ".join(post_errors),
            file=sys.stderr,
        )
        return 1

    print(f"migrated {len(plan.changes)} record(s) to the task kind")
    return 0


def _report(plan, root, *, applying):
    mode = "apply" if applying else "dry-run"
    print(f"[{mode}] {len(plan.changes)} record(s) to migrate under {root}")
    if plan.errors:
        print(f"[{mode}] {len(plan.errors)} blocking problem(s):")
        for error in plan.errors:
            print(f"  {error}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Migrate backlog+plan records to the task kind.")
    parser.add_argument("vault_root", help="path to the vault git repository")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the migration (default: dry-run, writes nothing)",
    )
    parser.add_argument(
        "--composed-root",
        default=None,
        help="composed skill tree to grep for retired kinds (default: the live tree)",
    )
    args = parser.parse_args(argv)
    return run_migration(
        args.vault_root, apply=args.apply, composed_root=args.composed_root
    )


if __name__ == "__main__":
    raise SystemExit(main())
