"""``lore record rename`` — stem/title rename with inbound-reference rewrite.

Covers the rename subcommand end-to-end through the CLI: the primary rename
(stem + sidecar title + index row), the cross-vault inbound-reference sweep
(bare/kind-qualified wikilinks, ``related.<kind>`` sidecar lists, task
``depends-on`` lists and ``parent`` edges), the shared-vault skip/opt-in split,
``--dry-run``, ``--vault`` source targeting, collision suffixing, refusals, and
provenance.

Crash-resume gets its own section: the identity gate that decides whether a
record sitting at the new stem IS the one that moved, the index repoint a
resumed run owes the interrupted one, and the refusal when two records share
the new title and the landed one cannot be told apart.

Sweep fault isolation is covered too — one unreadable record, or a configured
vault whose directory does not exist, must not abort a rename whose primary
move has already landed.

Lock scope and the sweep's write-order safety (copy → repoint → delete) are
exercised in-process at the bottom of the file, since the behaviors they guard
(a lock held across placement, an index write that raises mid-move) are not
observable from a subprocess.
"""

import json
import os
import sqlite3

import pytest

from conftest import load_script, run_cli


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class Install:
    """A configured multi-vault lore install rooted under ``tmp_path``."""

    def __init__(self, tmp_path, specs):
        self.tmp_path = tmp_path
        self.state = tmp_path / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.config_home = tmp_path / "cfg"
        self.roots = {}
        entries = []
        for name, scope, shared in specs:
            root = tmp_path / "vaults" / name
            root.mkdir(parents=True, exist_ok=True)
            self.roots[name] = root
            entry = {"name": name, "scope": scope, "path": str(root)}
            if shared:
                entry["shared"] = True
            entries.append(entry)
        lore_cfg = self.config_home / "lore"
        lore_cfg.mkdir(parents=True, exist_ok=True)
        (lore_cfg / "config.json").write_text(
            json.dumps({"vaults": entries}, indent=2), encoding="utf-8"
        )

    def cli(self, args, stdin_text=None):
        return run_cli(
            args,
            vault=self.roots["default"],
            state_dir=self.state,
            stdin_text=stdin_text,
            env_extra={"XDG_CONFIG_HOME": str(self.config_home)},
        )

    def create(self, vault, kind, title, body=None, extra=()):
        proc = self.cli(
            ["record", "create", "--kind", kind, "--title", title, "--vault", vault, *extra],
            stdin_text=body,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    def body(self, vault, record_id):
        kind, name = record_id.split("/", 1)
        return (self.roots[vault] / kind / f"{name}.md").read_text(encoding="utf-8")

    def sidecar(self, vault, record_id):
        kind, name = record_id.split("/", 1)
        return json.loads(
            (self.roots[vault] / kind / f"{name}.json").read_text(encoding="utf-8")
        )

    def index_names(self, vault, kind):
        db = self.state / "lore" / "index.sqlite"
        conn = sqlite3.connect(str(db))
        try:
            rows = conn.execute(
                "SELECT name FROM records WHERE vault=? AND kind=?",
                (str(self.roots[vault]), kind),
            ).fetchall()
        finally:
            conn.close()
        return sorted(r[0] for r in rows)

    def snapshot(self):
        """Byte-level snapshot of every vault tree (for write-nothing asserts)."""
        out = {}
        for name, root in self.roots.items():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    out[f"{name}:{path.relative_to(root)}"] = path.read_bytes()
        return out


@pytest.fixture()
def install(tmp_path):
    return Install(
        tmp_path,
        [
            ("default", "default", False),
            ("other", "repo", False),
            ("upstream", "product", True),
        ],
    )


# ---------------------------------------------------------------------------
# Primary rename
# ---------------------------------------------------------------------------


def test_rename_updates_stem_title_and_index(install):
    body = "First line.\n\nNo trailing newline here."
    old_id = install.create("default", "adr", "ADR-001: Old Name", body=body)
    assert old_id == "adr/adr-001-old-name"

    proc = install.cli(["record", "rename", old_id, "--title", "Old Name"])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "adr/old-name"

    # Old id gone, new id resolves.
    assert install.cli(["record", "show", old_id]).returncode != 0
    show = install.cli(["record", "show", "adr/old-name"])
    assert show.returncode == 0

    # Body byte-identical, including the missing trailing newline.
    assert install.body("default", "adr/old-name") == body

    # Sidecar title updated.
    assert install.sidecar("default", "adr/old-name")["title"] == "Old Name"

    # Index repointed — exactly one row, at the new name.
    assert install.index_names("default", "adr") == ["old-name"]


def test_rename_rewrites_wikilinks_across_vaults(install):
    old_id = install.create("default", "adr", "ADR-001: Old Name", body="x")
    install.create("default", "spec", "Same Vault Ref", body="see [[adr-001-old-name]] ok")
    install.create(
        "other", "spec", "Other Vault Ref", body="see [[adr/adr-001-old-name]] ok"
    )

    proc = install.cli(["record", "rename", old_id, "--title", "Old Name"])
    assert proc.returncode == 0, proc.stderr

    # stdout carries ONLY the new RECORD_ID.
    assert proc.stdout.strip().splitlines() == ["adr/old-name"]

    assert install.body("default", "spec/same-vault-ref") == "see [[old-name]] ok"
    assert install.body("other", "spec/other-vault-ref") == "see [[adr/old-name]] ok"

    # stderr names each rewritten record individually, with its vault.
    assert "spec/same-vault-ref" in proc.stderr
    assert "spec/other-vault-ref" in proc.stderr
    assert "default" in proc.stderr and "other" in proc.stderr


def test_rename_rewrites_related_and_depends_on(install):
    adr_id = install.create("default", "adr", "ADR-001: Old Name", body="x")
    install.create(
        "other", "spec", "Related Ref", body="x",
        extra=["--related", "adr=adr-001-old-name"],
    )
    proc = install.cli(["record", "rename", adr_id, "--title", "Old Name"])
    assert proc.returncode == 0, proc.stderr
    assert install.sidecar("other", "spec/related-ref")["related"]["adr"] == ["old-name"]

    # depends-on: rewritten when the renamed record is a task.
    dep_id = install.create("default", "task", "Dep Task", body="x")
    install.create(
        "default", "task", "Downstream", body="x",
        extra=["--depends-on", "dep-task"],
    )
    proc = install.cli(["record", "rename", dep_id, "--title", "Renamed Dep"])
    assert proc.returncode == 0, proc.stderr
    assert install.sidecar("default", "task/downstream")["depends-on"] == ["renamed-dep"]


# ---------------------------------------------------------------------------
# Shared vaults
# ---------------------------------------------------------------------------


def test_shared_vault_skipped_by_default_and_included_on_opt_in(install):
    old_id = install.create("default", "adr", "ADR-001: Old Name", body="x")
    install.create("upstream", "spec", "Shared Ref", body="see [[adr-001-old-name]]")

    proc = install.cli(["record", "rename", old_id, "--title", "Old Name"])
    assert proc.returncode == 0, proc.stderr
    # Not rewritten...
    assert install.body("upstream", "spec/shared-ref") == "see [[adr-001-old-name]]"
    # ...but reported.
    assert "spec/shared-ref" in proc.stderr
    assert "shared" in proc.stderr


def test_include_shared_rewrites_shared_vault_references(install):
    old_id = install.create("default", "adr", "ADR-001: Old Name", body="x")
    install.create("upstream", "spec", "Shared Ref", body="see [[adr-001-old-name]]")

    proc = install.cli(
        ["record", "rename", old_id, "--title", "Old Name", "--include-shared"]
    )
    assert proc.returncode == 0, proc.stderr
    assert install.body("upstream", "spec/shared-ref") == "see [[old-name]]"


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing_and_predicts_the_real_stem(install):
    old_id = install.create("default", "adr", "ADR-001: Old Name", body="x")
    install.create("default", "spec", "Ref", body="see [[adr-001-old-name]]")

    before = install.snapshot()
    proc = install.cli(["record", "rename", old_id, "--title", "Old Name", "--dry-run"])
    assert proc.returncode == 0, proc.stderr
    predicted = proc.stdout.strip()
    assert predicted == "adr/old-name"
    assert "spec/ref" in proc.stderr
    assert install.snapshot() == before

    real = install.cli(["record", "rename", old_id, "--title", "Old Name"])
    assert real.returncode == 0, real.stderr
    assert real.stdout.strip() == predicted


def test_dry_run_predicts_the_collision_suffix(install):
    old_id = install.create("default", "adr", "ADR-001: Old Name", body="x")
    install.create("default", "adr", "Old Name", body="occupant")

    before = install.snapshot()
    proc = install.cli(["record", "rename", old_id, "--title", "Old Name", "--dry-run"])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "adr/old-name-2"
    assert install.snapshot() == before

    real = install.cli(["record", "rename", old_id, "--title", "Old Name"])
    assert real.returncode == 0, real.stderr
    assert real.stdout.strip() == "adr/old-name-2"
    assert install.body("default", "adr/old-name") == "occupant"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_rerunning_the_same_rename_is_idempotent(install):
    old_id = install.create("default", "adr", "ADR-001: Old Name", body="x")
    install.create("default", "spec", "Ref", body="see [[adr-001-old-name]]")

    first = install.cli(["record", "rename", old_id, "--title", "Old Name"])
    assert first.returncode == 0, first.stderr
    after_first = install.snapshot()

    second = install.cli(["record", "rename", old_id, "--title", "Old Name"])
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "adr/old-name"
    assert install.snapshot() == after_first


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_session_kind_is_refused(install):
    before = install.snapshot()
    proc = install.cli(["record", "rename", "session/abc-123", "--title", "Nope"])
    assert proc.returncode != 0
    assert "error:" in proc.stderr
    assert "session" in proc.stderr
    assert install.snapshot() == before


def test_unknown_record_errors_cleanly(install):
    before = install.snapshot()
    proc = install.cli(["record", "rename", "adr/does-not-exist", "--title", "Nope"])
    assert proc.returncode != 0
    assert proc.stderr.startswith("error:")
    assert install.snapshot() == before


def test_malformed_record_id_errors_cleanly(install):
    proc = install.cli(["record", "rename", "no-slash", "--title", "Nope"])
    assert proc.returncode != 0
    assert "error:" in proc.stderr


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_provenance_restamped_on_touched_records_only(install):
    old_id = install.create("default", "adr", "ADR-001: Old Name", body="x")
    install.create("default", "spec", "Ref", body="see [[adr-001-old-name]]")
    install.create("default", "spec", "Untouched", body="nothing here")

    before_ref = install.sidecar("default", "spec/ref")
    before_untouched = install.roots["default"] / "spec" / "untouched.json"
    before_untouched_bytes = before_untouched.read_bytes()

    proc = install.cli(
        ["record", "rename", old_id, "--title", "Old Name"],
        stdin_text=None,
    )
    assert proc.returncode == 0, proc.stderr

    after_ref = install.sidecar("default", "spec/ref")
    assert after_ref["created-at"] == before_ref["created-at"]
    assert after_ref["updated-by"] == "tester@example.com"
    # The rewrite is a real update, so the touched record's clock advanced.
    # (Provenance is second-precision, so a same-second rewrite compares equal —
    # the assertion is that it never goes backwards and is always re-stamped.)
    assert "updated-at" in after_ref
    assert after_ref["updated-at"] >= before_ref["updated-at"]
    # Untouched record is byte-identical.
    assert before_untouched.read_bytes() == before_untouched_bytes


# ---------------------------------------------------------------------------
# Write-order safety (in-process)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _lore_email(monkeypatch):
    """Provenance stamping needs a resolvable committer email in-process."""
    monkeypatch.setenv("LORE_EMAIL", "tester@example.com")


def test_move_order_leaves_no_data_loss_when_the_index_repoint_fails(tmp_path, monkeypatch):
    """copy-new → index-repoint → delete-old: a repoint failure loses no data."""
    store = load_script("lore.record.store")
    index = load_script("lore.search.index")

    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)
    conn = index.open_index(env=env)
    try:
        vault = tmp_path / "vault"
        vault.mkdir()
        loc = store.place_record("Old Name", "adr", None, str(vault))
        old_id = store.validate_and_write(
            loc, {"kind": "adr", "title": "Old Name", "status": "draft"}, "body", conn
        )
        conn.commit()

        new_loc = store.place_record("New Name", "adr", None, str(vault))

        def boom(*a, **kw):
            raise RuntimeError("index repoint failed")

        monkeypatch.setattr(store.index_store, "delete_row", boom)
        with pytest.raises(RuntimeError):
            store.move_record(old_id, new_loc, conn, old_vault_root=str(vault))

        # New copy already durable AND old copy still present — no data loss.
        assert (vault / "adr" / "new-name.md").read_text() == "body"
        assert (vault / "adr" / "old-name.md").read_text() == "body"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Crash-resume identity
# ---------------------------------------------------------------------------


def _strand(install, vault, record_id):
    """Delete a record's artifacts, emulating a move that landed elsewhere."""
    kind, name = record_id.split("/", 1)
    for suffix in (".md", ".json"):
        (install.roots[vault] / kind / f"{name}{suffix}").unlink()


def test_resume_refuses_when_the_landed_record_is_ambiguous(install):
    """The base stem may be held by a stranger; resume must not adopt it.

    Faithful replay of the reported failure: a first run collided with an
    existing ``Target Name``, suffixed to ``target-name-2``, and then failed
    mid-sweep. Re-issuing the command must not silently repoint every inbound
    reference at the stranger sitting on the unsuffixed stem. Two records
    legitimately share the title here, so the landed one cannot be identified —
    the rename refuses rather than guessing.
    """
    install.create("default", "adr", "Target Name", body="the stranger")
    old_id = install.create("default", "adr", "Old Name", body="x")
    install.create("default", "spec", "Ref", body="see [[adr/old-name]]")

    adr_dir = install.roots["default"] / "adr"
    sidecar = json.loads((adr_dir / "old-name.json").read_text())
    sidecar["title"] = "Target Name"
    (adr_dir / "target-name-2.md").write_text("x")
    (adr_dir / "target-name-2.json").write_text(json.dumps(sidecar))
    _strand(install, "default", old_id)

    proc = install.cli(["record", "rename", old_id, "--title", "Target Name"])
    assert proc.returncode != 0
    assert "error:" in proc.stderr
    # Nothing was repointed at the stranger.
    assert install.body("default", "spec/ref") == "see [[adr/old-name]]"
    assert install.body("default", "adr/target-name") == "the stranger"


def test_resume_completes_when_the_landed_record_is_the_renamed_one(install):
    """A genuine crash-resume — same title at the base stem — still repairs."""
    old_id = install.create("default", "adr", "Old Name", body="x")
    install.create("default", "spec", "Ref", body="see [[adr/old-name]]")

    # Emulate a crash after the primary move landed but before the sweep ran.
    adr_dir = install.roots["default"] / "adr"
    sidecar = json.loads((adr_dir / "old-name.json").read_text())
    sidecar["title"] = "New Name"
    (adr_dir / "new-name.md").write_text((adr_dir / "old-name.md").read_text())
    (adr_dir / "new-name.json").write_text(json.dumps(sidecar))
    _strand(install, "default", old_id)

    proc = install.cli(["record", "rename", old_id, "--title", "New Name"])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "adr/new-name"
    assert install.body("default", "spec/ref") == "see [[adr/new-name]]"
    # The stale index row is repointed rather than left as a ghost.
    assert install.index_names("default", "adr") == ["new-name"]


# ---------------------------------------------------------------------------
# Sweep fault isolation
# ---------------------------------------------------------------------------


def test_one_unreadable_record_does_not_abort_the_sweep(install):
    old_id = install.create("default", "adr", "Old Name", body="x")
    install.create("default", "spec", "Ref", body="see [[adr/old-name]]")
    install.create("other", "spec", "Bad", body="see [[adr/old-name]]")
    (install.roots["other"] / "spec" / "bad.md").write_bytes(b"\xff\xfe not utf-8")

    proc = install.cli(["record", "rename", old_id, "--title", "New Name"])

    # The primary move landed and every readable reference was still rewritten.
    assert install.sidecar("default", "adr/new-name")["title"] == "New Name"
    assert install.body("default", "spec/ref") == "see [[adr/new-name]]"

    # The unreadable record is named on stderr alongside what DID land, but as
    # "could not check" — it is not evidence that anything still references the
    # old stem, so it does not fail the run.
    assert proc.returncode == 0, proc.stderr
    assert "spec/bad" in proc.stderr
    assert "spec/ref" in proc.stderr
    unchecked_line = next(
        line for line in proc.stderr.splitlines() if "spec/bad" in line
    )
    assert unchecked_line.startswith("unchecked:")
    assert "could not" in proc.stderr


def test_a_reference_that_cannot_be_rewritten_fails_the_run(install):
    """A record that DOES reference the old stem and cannot be written exits 1.

    This is the real dangling-reference case, distinct from a record the sweep
    could not read at all: here the reference is known to be there and known to
    be un-rewritten, so the operator must be told the rename is incomplete.
    """
    old_id = install.create("default", "adr", "Old Name", body="x")
    install.create("default", "spec", "Ref", body="see [[adr/old-name]]")
    install.create("other", "spec", "Stuck", body="see [[adr/old-name]]")
    # Make the write fail without disturbing the read.
    (install.roots["other"] / "spec").chmod(0o500)
    try:
        proc = install.cli(["record", "rename", old_id, "--title", "New Name"])
    finally:
        (install.roots["other"] / "spec").chmod(0o700)

    assert proc.returncode != 0
    failed_line = next(
        line for line in proc.stderr.splitlines() if "spec/stuck" in line
    )
    assert failed_line.startswith("failed:")
    assert "still reference" in proc.stderr
    # What could land, did.
    assert install.body("default", "spec/ref") == "see [[adr/new-name]]"


def test_a_configured_vault_with_no_directory_is_skipped(install):
    """A configured-but-not-yet-created vault contributes nothing, and is benign."""
    cfg_path = install.config_home / "lore" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["vaults"].append(
        {"name": "ghost", "scope": "team", "path": str(install.tmp_path / "nope")}
    )
    cfg_path.write_text(json.dumps(cfg, indent=2))

    old_id = install.create("default", "adr", "Old Name", body="x")
    install.create("default", "spec", "Ref", body="see [[adr/old-name]]")

    proc = install.cli(["record", "rename", old_id, "--title", "New Name"])
    assert proc.returncode == 0, proc.stderr
    assert install.body("default", "spec/ref") == "see [[adr/new-name]]"


# ---------------------------------------------------------------------------
# Task graph edges
# ---------------------------------------------------------------------------


def test_task_parent_edge_is_rewritten(install):
    parent_id = install.create("default", "task", "Parent Task", body="x")
    install.create(
        "default", "task", "Child", body="x", extra=["--parent", "parent-task"]
    )

    proc = install.cli(["record", "rename", parent_id, "--title", "Renamed Parent"])
    assert proc.returncode == 0, proc.stderr
    assert install.sidecar("default", "task/child")["parent"] == "renamed-parent"


# ---------------------------------------------------------------------------
# --vault
# ---------------------------------------------------------------------------


def test_vault_flag_targets_the_named_source_vault(install):
    install.create("default", "adr", "Dup", body="in default")
    install.create("other", "adr", "Dup", body="in other")

    proc = install.cli(
        ["record", "rename", "adr/dup", "--vault", "other", "--title", "Renamed"]
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "adr/renamed"
    # The named vault's record moved; the same-named record in `default` did not.
    assert install.body("other", "adr/renamed") == "in other"
    assert install.body("default", "adr/dup") == "in default"
    # The source vault is named on the `renamed:` line.
    renamed_line = next(
        line for line in proc.stderr.splitlines() if line.startswith("renamed:")
    )
    assert "other" in renamed_line


def test_vault_flag_refuses_when_another_vault_holds_the_record(install):
    """A narrowed search must never fall through to resume-by-title.

    The record still exists — just not in the named vault. Resume exists to
    finish a move that already landed, so reaching it here would adopt whatever
    record in the named vault happens to carry the new title and repoint every
    inbound reference at that stranger. The rename refuses instead, naming the
    vault that actually holds the record.
    """
    install.create("other", "adr", "Old Name", body="the real record")
    install.create("default", "adr", "New Name", body="an unrelated stranger")
    install.create("default", "spec", "Ref", body="see [[adr/old-name]]")
    before = install.snapshot()

    proc = install.cli(
        ["record", "rename", "adr/old-name", "--vault", "default", "--title", "New Name"]
    )
    assert proc.returncode != 0
    assert "error:" in proc.stderr
    # The refusal names the vault that actually holds it.
    assert "other" in proc.stderr
    assert install.snapshot() == before


def test_structurally_invalid_record_ids_never_reach_resume(install):
    """A malformed ID is refused outright, not resolved by title.

    ``adr/`` and ``adr/../x`` carry a slash, so a bare slash check lets them
    through to the resume path — where a unique title match would be adopted
    and every inbound reference repointed at it.
    """
    install.create("default", "adr", "Nope", body="the stranger")
    install.create("default", "spec", "Ref", body="see [[adr/nope]]")
    before = install.snapshot()

    for bad in ("adr/", "adr/../x", "adr/x/../../escape", "adr/../../etc/passwd"):
        proc = install.cli(["record", "rename", bad, "--title", "Nope"])
        assert proc.returncode != 0, bad
        assert "error:" in proc.stderr, bad
        assert install.snapshot() == before, bad


def test_a_record_in_a_shared_vault_is_still_renamed(install):
    """Shared vaults gate the SWEEP, never the primary rename.

    The operator named that record explicitly, so it moves; only the inbound
    rewrites in shared vaults need the ``--include-shared`` opt-in.
    """
    old_id = install.create("upstream", "adr", "Old Name", body="lives upstream")
    install.create("default", "spec", "Ref", body="see [[adr/old-name]]")

    proc = install.cli(
        ["record", "rename", old_id, "--vault", "upstream", "--title", "New Name"]
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "adr/new-name"
    assert install.body("upstream", "adr/new-name") == "lives upstream"
    assert install.sidecar("upstream", "adr/new-name")["title"] == "New Name"
    assert install.index_names("upstream", "adr") == ["new-name"]
    # The sweep still covers the non-shared vault that references it.
    assert install.body("default", "spec/ref") == "see [[adr/new-name]]"


def test_vault_flag_rejects_a_vault_that_does_not_hold_the_record(install):
    install.create("default", "adr", "Dup", body="in default")
    before = install.snapshot()

    proc = install.cli(
        ["record", "rename", "adr/dup", "--vault", "other", "--title", "Renamed"]
    )
    assert proc.returncode != 0
    assert install.snapshot() == before


# ---------------------------------------------------------------------------
# Locking (in-process)
# ---------------------------------------------------------------------------


def _single_vault_install(tmp_path, monkeypatch):
    """A one-vault install wired through env, for in-process library calls."""
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg_home = tmp_path / "cfg"
    (cfg_home / "lore").mkdir(parents=True)
    (cfg_home / "lore" / "config.json").write_text(
        json.dumps({"vaults": [{"name": "default", "scope": "default",
                                "path": str(vault)}]}),
        encoding="utf-8",
    )
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return vault


def test_placement_and_move_run_under_the_vault_write_lock(tmp_path, monkeypatch):
    """The stem is claimed and consumed inside one held vault lock.

    Otherwise a concurrent create can take the placed stem in the window between
    ``place_record`` returning it and ``move_record`` writing there.
    """
    rename = load_script("lore.record.rename")
    store = rename.store
    index = load_script("lore.search.index")

    vault = _single_vault_install(tmp_path, monkeypatch)
    conn = index.open_index()
    try:
        loc = store.place_record("Old Name", "adr", None, str(vault))
        store.validate_and_write(
            loc, {"kind": "adr", "title": "Old Name", "status": "draft"}, "x", conn
        )
        conn.commit()

        held = {}
        real_place = store.place_record
        real_move = store.move_record

        def lock_depth():
            key = str(store.locking._resolve_key(vault / store.locking.VAULT_LOCK_NAME))
            return store.locking._depths().get(key, 0)

        def spy_place(*a, **kw):
            held["place"] = lock_depth()
            return real_place(*a, **kw)

        def spy_move(*a, **kw):
            held["move"] = lock_depth()
            return real_move(*a, **kw)

        monkeypatch.setattr(store, "place_record", spy_place)
        monkeypatch.setattr(store, "move_record", spy_move)

        rename.rename_record("adr/old-name", "New Name", conn)
        conn.commit()
    finally:
        conn.close()

    assert held["place"] >= 1, "place_record ran outside the vault write lock"
    assert held["move"] >= 1, "move_record ran outside the vault write lock"


def test_session_bodies_are_rewritten_under_the_session_lock(tmp_path, monkeypatch):
    """A session body is appended to by a live agent; the sweep must serialize."""
    rename = load_script("lore.record.rename")
    store = rename.store
    index = load_script("lore.search.index")

    vault = _single_vault_install(tmp_path, monkeypatch)
    conn = index.open_index()
    keys = []
    try:
        loc = store.place_record("Old Name", "adr", None, str(vault))
        store.validate_and_write(
            loc, {"kind": "adr", "title": "Old Name", "status": "draft"}, "x", conn
        )
        sess = store.place_record("sess-guid-1", "session", None, str(vault))
        store.validate_and_write(
            sess,
            {"kind": "session", "title": "A Session", "status": "dirty"},
            "see [[adr/old-name]]",
            conn,
        )
        conn.commit()

        real_lock = rename.locking.session_write_lock

        def spy_lock(vault_root, key, **kw):
            keys.append(key)
            return real_lock(vault_root, key, **kw)

        monkeypatch.setattr(rename.locking, "session_write_lock", spy_lock)

        rename.rename_record("adr/old-name", "New Name", conn)
        conn.commit()
    finally:
        conn.close()

    body = (vault / "session" / "sess-guid-1.md").read_text(encoding="utf-8")
    assert body == "see [[adr/new-name]]"
    assert keys == ["sess-guid-1"]


def test_sweep_commits_each_vault_before_moving_to_the_next(tmp_path, monkeypatch):
    """A vault's rewrites are published before the next vault is touched.

    The sweep spans every configured vault and its rewrites are real index
    upserts. Holding them all in one transaction means an abort partway through
    the last vault discards the index rows for rewrites that are already durable
    on disk in the earlier ones — files and index silently disagree.
    """
    rename = load_script("lore.record.rename")
    store = rename.store
    index = load_script("lore.search.index")

    roots = {}
    entries = []
    for name, scope in (("first", "default"), ("second", "repo")):
        root = tmp_path / "vaults" / name
        root.mkdir(parents=True)
        roots[name] = root
        entries.append({"name": name, "scope": scope, "path": str(root)})
    cfg_home = tmp_path / "cfg"
    (cfg_home / "lore").mkdir(parents=True)
    (cfg_home / "lore" / "config.json").write_text(
        json.dumps({"vaults": entries}), encoding="utf-8"
    )
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))

    conn = index.open_index()
    try:
        def write(vault, kind, title, body):
            loc = store.place_record(title, kind, None, str(roots[vault]))
            store.validate_and_write(
                loc, {"kind": kind, "title": title, "status": "draft"}, body, conn
            )
            return loc

        write("first", "adr", "Old Name", "x")
        write("first", "spec", "Ref A", "see [[adr/old-name]]")
        write("second", "spec", "Ref B", "see [[adr/old-name]]")
        conn.commit()

        real_write = store.validate_and_write

        def boom(location, *a, **kw):
            # An abort the fault-isolated sweep cannot swallow.
            if str(location.body_path).startswith(str(roots["second"])):
                raise KeyboardInterrupt("interrupted mid-sweep")
            return real_write(location, *a, **kw)

        monkeypatch.setattr(store, "validate_and_write", boom)

        with pytest.raises(KeyboardInterrupt):
            rename.rename_record("adr/old-name", "Much Newer Name", conn)
    finally:
        # Closing rolls back anything still uncommitted.
        conn.close()

    ref_a = roots["first"] / "spec" / "ref-a.md"
    assert ref_a.read_text(encoding="utf-8") == "see [[adr/much-newer-name]]"

    fresh = sqlite3.connect(str(state / "lore" / "index.sqlite"))
    try:
        row = fresh.execute(
            "SELECT src_size FROM records WHERE vault=? AND kind='spec' AND name='ref-a'",
            (str(roots["first"]),),
        ).fetchone()
    finally:
        fresh.close()
    assert row is not None
    assert row[0] == ref_a.stat().st_size


def test_resume_adopts_the_suffixed_stem_the_move_actually_landed_on(install):
    """The landed record is found by title, so a collision-suffixed move resumes.

    The base stem is held by a stranger whose own title differs (it merely slugs
    to the same stem), so exactly one record carries the new title and the
    resume is unambiguous — it adopts the ``-2`` stem the move actually used.
    """
    install.create("default", "adr", "Target: Name", body="the stranger")
    old_id = install.create("default", "adr", "Old Name", body="x")
    install.create("default", "spec", "Ref", body="see [[adr/old-name]]")

    # Emulate a crash after the suffixed move landed but before the sweep ran.
    adr_dir = install.roots["default"] / "adr"
    sidecar = json.loads((adr_dir / "old-name.json").read_text())
    sidecar["title"] = "Target Name"
    (adr_dir / "target-name-2.md").write_text((adr_dir / "old-name.md").read_text())
    (adr_dir / "target-name-2.json").write_text(json.dumps(sidecar))
    _strand(install, "default", old_id)

    proc = install.cli(["record", "rename", old_id, "--title", "Target Name"])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "adr/target-name-2"
    assert install.body("default", "spec/ref") == "see [[adr/target-name-2]]"
    # The stranger is untouched.
    assert install.body("default", "adr/target-name") == "the stranger"
