"""``lore record rename`` — stem/title rename with inbound-reference rewrite.

Covers the rename subcommand end-to-end through the CLI: the primary rename
(stem + sidecar title + index row), the cross-vault inbound-reference sweep
(bare/kind-qualified wikilinks, ``related.<kind>`` sidecar lists, task
``depends-on`` lists), the shared-vault skip/opt-in split, ``--dry-run``,
idempotent re-run, collision suffixing, refusals, and provenance.

The sweep's write-order safety (copy → repoint → delete) is exercised
in-process at the bottom of the file, since the failure it guards against is
an index write that raises mid-move.
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
