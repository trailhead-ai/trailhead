"""Tests for the reusable record write library ``record_store``.

Covers the test contract:

  - Round-trip: ``validate_and_write`` produces a verbatim ``<kind>/<name>.md``
    body, a pretty-printed sorted-key ``<name>.json`` sidecar, and a matching
    index row — all three consistent.
  - Auto-set provenance: ``created-at``/``updated-at`` are ISO-8601 UTC ``…Z``;
    ``*-by`` == the resolved git email; ``created-*`` is set once, ``updated-*``
    re-stamped on a second write.
  - Empty git email → typed error, **nothing written**.
  - Validation failure → typed error carrying the validator's messages, nothing
    written.
  - Fence neutralization: a body containing ``<external-memory>`` /
    ``</external-memory>`` is stored neutralized; the round-trip cannot
    reconstruct a live fence.
  - Text-wins on index failure: with ``update_index`` forced to raise,
    the body+sidecar are intact on disk (text not rolled back); a subsequent
    ``reindex`` reconciles.
  - Atomic write: a simulated crash between temp-write and rename leaves no
    partial ``<name>.md`` — only the temp file or nothing, never a half-written
    target.
  - ``place_record`` naming: ``_kebab`` slug + ``-2`` on collision; ``session``
    kind keeps the GUID verbatim; returned ID is the vault-relative
    ``<kind>/<name>``.
  - Both-artifact collision: an orphaned ``<stem>.json`` with no ``.md`` still
    makes the stem occupied — the next ``create`` picks ``-2``, never overwrites
    the orphan.
  - Deterministic provenance source: ``resolve_committer_email()`` honors
    ``$LORE_EMAIL`` then ``git config --global user.email``; a repo-LOCAL
    ``user.email`` override does NOT change the stamped ``*-by``.
  - ``move_record``: body+sidecar relocated under the new vault, index rows
    re-keyed, old copy gone, new ID returned; an interrupted move keeps the old
    copy intact (no data loss).
  - ``delete_record``: all three artifacts removed; a missing ID raises a typed
    not-found error.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from conftest import load_script


@pytest.fixture()
def rs():
    return load_script("lore.record.store")


@pytest.fixture()
def index():
    return load_script("lore.search.index")


@pytest.fixture()
def conn(index, tmp_path):
    """An open index connection pointed at a tmp XDG_STATE_HOME."""
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)
    c = index.open_index(env=env)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _clean_email_env(monkeypatch):
    """Default tests to a deterministic LORE_EMAIL unless they override it."""
    monkeypatch.setenv("LORE_EMAIL", "tester@example.com")


def _sidecar(kind="spec", title="My Spec", status="draft", **extra):
    s = {
        "version": "v1",
        "kind": kind,
        "title": title,
        "keywords": ["foo"],
        "status": status,
    }
    s.update(extra)
    return s


# ---------------------------------------------------------------------------
# place_record naming + ID
# ---------------------------------------------------------------------------


def test_place_record_kebab_slug_and_id(rs, tmp_path):
    """place_record slugs the name and returns the vault-relative <kind>/<name> ID."""
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("Lore Search!!", "spec", None, str(vault))
    assert loc.kind == "spec"
    assert loc.name == "lore-search"
    assert loc.record_id == "spec/lore-search"
    assert loc.vault_root == str(vault)


def test_place_record_collision_appends_suffix(rs, tmp_path):
    """A taken stem forces a -2 suffix on the next placement."""
    vault = tmp_path / "vault"
    (vault / "spec").mkdir(parents=True)
    (vault / "spec" / "lore-search.md").write_text("body")

    loc = rs.place_record("Lore Search", "spec", None, str(vault))
    assert loc.name == "lore-search-2"
    assert loc.record_id == "spec/lore-search-2"


def test_place_record_orphan_json_occupies_stem(rs, tmp_path):
    """An orphaned <stem>.json (no .md) still makes the stem occupied."""
    vault = tmp_path / "vault"
    (vault / "spec").mkdir(parents=True)
    # Crash-orphaned sidecar with no matching .md.
    (vault / "spec" / "lore-search.json").write_text("{}")

    loc = rs.place_record("Lore Search", "spec", None, str(vault))
    assert loc.name == "lore-search-2"


def test_place_record_symlink_occupies_stem_without_following_it(rs, tmp_path):
    """A symlink at <stem>.md occupies the stem even when its target is absent.

    Occupancy is decided by the link itself, never by whether it resolves: a
    dangling or escaping symlink planted in a kind directory would otherwise
    read as a free stem, and the placement that followed would write THROUGH it
    to whatever path it names — outside the vault. Testing the link rather than
    its target also keeps placement from reporting whether an arbitrary external
    file exists.
    """
    vault = tmp_path / "vault"
    (vault / "spec").mkdir(parents=True)
    (vault / "spec" / "lore-search.md").symlink_to(tmp_path / "outside" / "absent.md")

    loc = rs.place_record("Lore Search", "spec", None, str(vault))
    assert loc.name == "lore-search-2"


def test_place_record_session_keeps_guid_verbatim(rs, tmp_path):
    """session kind uses the session_id GUID verbatim — no slug, no suffix."""
    vault = tmp_path / "vault"
    vault.mkdir()
    guid = "A1B2C3D4-5E6F-7890-ABCD-1234567890EF"
    loc = rs.place_record(guid, "session", None, str(vault))
    assert loc.name == guid
    assert loc.record_id == f"session/{guid}"


def test_place_record_session_no_suffix_on_collision(rs, tmp_path):
    """session GUID is verbatim even when the stem already exists (no -2)."""
    vault = tmp_path / "vault"
    (vault / "session").mkdir(parents=True)
    guid = "A1B2C3D4-5E6F-7890-ABCD-1234567890EF"
    (vault / "session" / f"{guid}.md").write_text("existing")
    loc = rs.place_record(guid, "session", None, str(vault))
    assert loc.name == guid


# ---------------------------------------------------------------------------
# resolve_committer_email — deterministic provenance source
# ---------------------------------------------------------------------------


def test_resolve_committer_email_honors_lore_email(rs, monkeypatch):
    monkeypatch.setenv("LORE_EMAIL", "override@example.com")
    assert rs.resolve_committer_email() == "override@example.com"


def test_resolve_committer_email_empty_when_unset_and_no_git(rs, monkeypatch, tmp_path):
    """Unset LORE_EMAIL + no global git email → empty string (no fallback)."""
    monkeypatch.delenv("LORE_EMAIL", raising=False)
    # Point HOME at an empty dir so `git config --global user.email` is empty.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    # GIT_CONFIG_GLOBAL pins the global config file to an empty path.
    empty_cfg = tmp_path / "gitconfig-empty"
    empty_cfg.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_cfg))
    assert rs.resolve_committer_email() == ""


def test_resolve_committer_email_ignores_repo_local_override(rs, monkeypatch, tmp_path):
    """A repo-LOCAL user.email does not change the resolved committer email."""
    monkeypatch.delenv("LORE_EMAIL", raising=False)
    # Global config carries the canonical identity.
    global_cfg = tmp_path / "gitconfig-global"
    global_cfg.write_text("[user]\n\temail = global@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))

    # A repo with a LOCAL override; run from inside it.
    repo = tmp_path / "client-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "dev@client.com"], cwd=repo, check=True)
    monkeypatch.chdir(repo)

    # Must read the GLOBAL identity, not the repo-local override.
    assert rs.resolve_committer_email() == "global@example.com"


def test_resolve_committer_email_follows_include_directive(rs, monkeypatch, tmp_path):
    """A global config that only [include]s user.email must still resolve it."""
    monkeypatch.delenv("LORE_EMAIL", raising=False)
    included_cfg = tmp_path / "gitconfig-included"
    included_cfg.write_text("[user]\n\temail = included@example.com\n")

    global_cfg = tmp_path / "gitconfig-global"
    global_cfg.write_text(f"[include]\n\tpath = {included_cfg}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))

    assert rs.resolve_committer_email() == "included@example.com"


def test_resolve_committer_email_follows_matching_includeif_gitdir(rs, monkeypatch, tmp_path):
    """A conditional ``includeIf "gitdir:…"`` whose condition matches cwd is followed.

    ``--includes`` activates conditional includes the same way plain
    ``git config --global`` does — this pins that the resolved identity can
    vary with the invocation directory when a work/personal ``includeIf``
    split is in play, matching plain git's own behavior.
    """
    monkeypatch.delenv("LORE_EMAIL", raising=False)
    work_repo = tmp_path / "work-repo"
    work_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work_repo, check=True)

    conditional_cfg = tmp_path / "gitconfig-work"
    conditional_cfg.write_text("[user]\n\temail = work@example.com\n")

    global_cfg = tmp_path / "gitconfig-global"
    global_cfg.write_text(
        f'[includeIf "gitdir:{work_repo}/"]\n\tpath = {conditional_cfg}\n'
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))
    monkeypatch.chdir(work_repo)

    assert rs.resolve_committer_email() == "work@example.com"


# ---------------------------------------------------------------------------
# validate_and_write — round-trip + provenance
# ---------------------------------------------------------------------------


def test_validate_and_write_round_trip(rs, conn, tmp_path):
    """Body verbatim .md + compact sorted-key .json + matching index row."""
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("My Spec", "spec", None, str(vault))
    body = "# My Spec\n\nsome content\n"

    rid = rs.validate_and_write(loc, _sidecar(), body, conn)
    conn.commit()

    assert rid == "spec/my-spec"
    md = vault / "spec" / "my-spec.md"
    js = vault / "spec" / "my-spec.json"
    assert md.read_text() == body

    raw = js.read_text()
    parsed = json.loads(raw)
    assert parsed["title"] == "My Spec"
    # Compact single-line, sorted keys, no trailing newline.
    assert raw == json.dumps(parsed, sort_keys=True, separators=(",", ":"))

    row = conn.execute(
        "SELECT records.title, record_fts.body FROM records "
        "JOIN record_fts ON record_fts.rowid = records.rowid "
        "WHERE records.vault=? AND records.kind=? AND records.name=?",
        (str(vault), "spec", "my-spec"),
    ).fetchone()
    assert row[0] == "My Spec"
    assert row[1] == body


def test_validate_and_write_sidecar_compact_json(rs, conn, tmp_path):
    """Sidecar is single-line compact JSON, keys sorted, no trailing newline."""
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("My Spec", "spec", None, str(vault))
    rs.validate_and_write(loc, _sidecar(), "body", conn)
    conn.commit()

    raw = (vault / "spec" / "my-spec.json").read_text()
    # Single-line compact: no embedded newlines, no trailing newline.
    assert "\n" not in raw
    # Parseable and round-trips stably.
    parsed = json.loads(raw)
    assert raw == json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def test_validate_and_write_stamps_provenance(rs, conn, tmp_path, monkeypatch):
    """created/updated-at are ISO-8601 UTC Z; *-by == the git email."""
    monkeypatch.setenv("LORE_EMAIL", "alice@example.com")
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("My Spec", "spec", None, str(vault))

    rs.validate_and_write(loc, _sidecar(), "body", conn)
    conn.commit()

    sidecar = json.loads((vault / "spec" / "my-spec.json").read_text())
    assert sidecar["created-by"] == "alice@example.com"
    assert sidecar["updated-by"] == "alice@example.com"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", sidecar["created-at"])
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", sidecar["updated-at"])


def test_validate_and_write_created_once_updated_restamped(rs, conn, tmp_path):
    """A second write keeps created-* and re-stamps updated-*."""
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("My Spec", "spec", None, str(vault))

    rs.validate_and_write(loc, _sidecar(), "body1", conn)
    conn.commit()
    first = json.loads((vault / "spec" / "my-spec.json").read_text())

    # Force a distinguishable second timestamp.
    import time

    time.sleep(1.05)
    rs.validate_and_write(loc, _sidecar(), "body2", conn)
    conn.commit()
    second = json.loads((vault / "spec" / "my-spec.json").read_text())

    assert second["created-at"] == first["created-at"]
    assert second["created-by"] == first["created-by"]
    assert second["updated-at"] != first["updated-at"]


# ---------------------------------------------------------------------------
# Empty git email → typed error, nothing written
# ---------------------------------------------------------------------------


def test_empty_email_raises_typed_error_nothing_written(rs, conn, tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_EMAIL", "")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-such-config"))
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("My Spec", "spec", None, str(vault))

    with pytest.raises(rs.ProvenanceError):
        rs.validate_and_write(loc, _sidecar(), "body", conn)

    assert not (vault / "spec" / "my-spec.md").exists()
    assert not (vault / "spec" / "my-spec.json").exists()
    assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Validation failure → typed error carrying validator messages, nothing written
# ---------------------------------------------------------------------------


def test_validation_failure_raises_typed_error_nothing_written(rs, conn, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("My Spec", "spec", None, str(vault))
    bad = _sidecar(status="not-a-real-status")

    with pytest.raises(rs.RecordValidationError) as ei:
        rs.validate_and_write(loc, bad, "body", conn)

    # Carries the validator's messages.
    assert ei.value.errors
    assert any("status" in m for m in ei.value.errors)

    assert not (vault / "spec" / "my-spec.md").exists()
    assert not (vault / "spec" / "my-spec.json").exists()
    assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Fence neutralization
# ---------------------------------------------------------------------------


def test_neutralize_fences_kills_tokens(rs):
    text = "before <external-memory> mid </external-memory> after"
    out = rs.neutralize_fences(text)
    assert "<external-memory>" not in out
    assert "</external-memory>" not in out


def test_neutralize_fences_kills_mixed_case_and_attrs(rs):
    """A mixed-case or attribute-bearing fence is neutralized too (no live token)."""
    out = rs.neutralize_fences("<External-Memory attr=1>x</EXTERNAL-MEMORY>")
    # No live fence token survives in any case.
    assert "external-memory" not in out.lower()


def test_validate_and_write_neutralizes_fence_in_body(rs, conn, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("My Spec", "spec", None, str(vault))
    body = "intro\n<external-memory>injected</external-memory>\noutro"

    rs.validate_and_write(loc, _sidecar(), body, conn)
    conn.commit()

    stored = (vault / "spec" / "my-spec.md").read_text()
    assert "<external-memory>" not in stored
    assert "</external-memory>" not in stored


# ---------------------------------------------------------------------------
# Text-wins on index failure
# ---------------------------------------------------------------------------


def test_text_wins_when_index_raises(rs, conn, tmp_path, monkeypatch):
    """update_index raising leaves body+sidecar intact (text not rolled back)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("My Spec", "spec", None, str(vault))

    def _boom(*a, **k):
        raise RuntimeError("index down")

    monkeypatch.setattr(rs, "update_index", _boom)

    with pytest.raises(RuntimeError):
        rs.validate_and_write(loc, _sidecar(), "durable body", conn)

    # Text won — it is on disk despite the index failure.
    assert (vault / "spec" / "my-spec.md").read_text() == "durable body"
    assert (vault / "spec" / "my-spec.json").exists()


# ---------------------------------------------------------------------------
# Atomic write: no partial target on a crash between temp-write and rename
# ---------------------------------------------------------------------------


def test_write_temp_then_rename_no_partial_on_crash(rs, tmp_path, monkeypatch):
    """A crash before the rename leaves no partial target file."""
    target = tmp_path / "out.md"

    real_replace = os.replace

    def _crash_replace(*a, **k):
        raise RuntimeError("crash before rename completes")

    monkeypatch.setattr(os, "replace", _crash_replace)

    with pytest.raises(RuntimeError):
        rs.write_temp_then_rename(target, "the body")

    assert not target.exists(), "target must not exist after a pre-rename crash"
    # Restore (monkeypatch will undo, but assert real path works for clarity).
    monkeypatch.setattr(os, "replace", real_replace)


def test_write_temp_then_rename_writes_full_content(rs, tmp_path):
    target = tmp_path / "out.md"
    rs.write_temp_then_rename(target, "complete content")
    assert target.read_text() == "complete content"


# ---------------------------------------------------------------------------
# move_record
# ---------------------------------------------------------------------------


def test_move_record_relocates_rekeys_and_removes_old(rs, conn, tmp_path):
    src_vault = tmp_path / "vault-a"
    dst_vault = tmp_path / "vault-b"
    src_vault.mkdir()
    dst_vault.mkdir()

    loc = rs.place_record("My Spec", "spec", None, str(src_vault))
    old_id = rs.validate_and_write(loc, _sidecar(), "body", conn)
    conn.commit()

    new_loc = rs.place_record("My Spec", "spec", None, str(dst_vault))
    new_id = rs.move_record(old_id, new_loc, conn, old_vault_root=str(src_vault))
    conn.commit()

    # New copy durable under the new vault.
    assert (dst_vault / "spec" / "my-spec.md").read_text() == "body"
    assert (dst_vault / "spec" / "my-spec.json").exists()
    # Old copy gone.
    assert not (src_vault / "spec" / "my-spec.md").exists()
    assert not (src_vault / "spec" / "my-spec.json").exists()
    # Index re-keyed onto the new vault.
    old_rows = conn.execute(
        "SELECT COUNT(*) FROM records WHERE vault=?", (str(src_vault),)
    ).fetchone()[0]
    new_rows = conn.execute(
        "SELECT COUNT(*) FROM records WHERE vault=?", (str(dst_vault),)
    ).fetchone()[0]
    assert old_rows == 0
    assert new_rows == 1
    assert new_id == "spec/my-spec"


@pytest.mark.parametrize(
    "evil_old_id",
    ["../other-vault/spec/victim", "spec/../../other-vault/spec/victim", "/etc/passwd"],
)
def test_move_record_confines_old_id_against_traversal(rs, conn, tmp_path, evil_old_id):
    """A traversal old_id is confined at the library boundary.

    move_record is a directly callable library API; a crafted old_id must not read/unlink
    files outside the source vault → InvalidRecordIdError, victim untouched.
    """
    src_vault = tmp_path / "vault-a"
    dst_vault = tmp_path / "vault-b"
    src_vault.mkdir()
    dst_vault.mkdir()
    # A victim record outside the source vault.
    victim_dir = tmp_path / "other-vault" / "spec"
    victim_dir.mkdir(parents=True)
    (victim_dir / "victim.md").write_text("important other-vault content\n")
    (victim_dir / "victim.json").write_text('{"kind": "spec"}\n')

    new_loc = rs.place_record("Dest", "spec", None, str(dst_vault))
    with pytest.raises(rs.InvalidRecordIdError):
        rs.move_record(evil_old_id, new_loc, conn, old_vault_root=str(src_vault))

    # Victim survived; nothing copied to the destination.
    assert (victim_dir / "victim.md").read_text() == "important other-vault content\n"
    assert not (dst_vault / "spec" / "victim.md").exists()


def test_move_record_interrupted_keeps_old_intact(rs, conn, tmp_path, monkeypatch):
    """An interrupted move (crash before delete-old) keeps the old copy — no loss."""
    src_vault = tmp_path / "vault-a"
    dst_vault = tmp_path / "vault-b"
    src_vault.mkdir()
    dst_vault.mkdir()

    loc = rs.place_record("My Spec", "spec", None, str(src_vault))
    old_id = rs.validate_and_write(loc, _sidecar(), "body", conn)
    conn.commit()

    new_loc = rs.place_record("My Spec", "spec", None, str(dst_vault))

    # Simulate a crash during the delete-old phase: the old artifact survives.
    real_unlink = Path.unlink

    def _crash_unlink(self, *a, **k):
        if str(self).startswith(str(src_vault)):
            raise RuntimeError("crash during delete-old")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _crash_unlink)

    with pytest.raises(RuntimeError):
        rs.move_record(old_id, new_loc, conn, old_vault_root=str(src_vault))

    monkeypatch.setattr(Path, "unlink", real_unlink)

    # Old copy intact — the safe direction means no data loss.
    assert (src_vault / "spec" / "my-spec.md").read_text() == "body"


# ---------------------------------------------------------------------------
# move_record — in-memory overrides (single durable write at dest)
# ---------------------------------------------------------------------------


def test_move_record_overrides_write_mutated_record_at_dest_only(rs, conn, tmp_path):
    """``new_sidecar``/``new_body`` write the mutated record AT the destination only.

    Single-durable-write requirement: the mutated
    sidecar must NEVER appear at the old location, only at the destination. The
    overrides bypass the verbatim disk re-read so the already-mutated record lands
    at the destination directly.
    """
    src_vault = tmp_path / "vault-a"
    dst_vault = tmp_path / "vault-b"
    src_vault.mkdir()
    dst_vault.mkdir()

    loc = rs.place_record("My Spec", "spec", None, str(src_vault))
    old_id = rs.validate_and_write(loc, _sidecar(team="alpha"), "old body", conn)
    conn.commit()

    new_loc = rs.place_record("My Spec", "spec", None, str(dst_vault))
    # The caller (the update CLI handler) hands move_record an already
    # validated + provenance-stamped sidecar; move_record writes it verbatim.
    mutated = _sidecar(team="beta")
    mutated["created-at"] = "2026-06-21T00:00:00Z"
    mutated["created-by"] = "tester@example.com"
    mutated["updated-at"] = "2026-06-21T00:00:01Z"
    mutated["updated-by"] = "tester@example.com"
    new_id = rs.move_record(
        old_id,
        new_loc,
        conn,
        old_vault_root=str(src_vault),
        new_sidecar=mutated,
        new_body="new body",
    )
    conn.commit()

    # The destination holds the MUTATED record (the new value).
    dst_sidecar = json.loads((dst_vault / "spec" / "my-spec.json").read_text(encoding="utf-8"))
    assert dst_sidecar["team"] == "beta"
    assert (dst_vault / "spec" / "my-spec.md").read_text() == "new body"

    # The mutated record never touched the old location — old artifacts are gone.
    assert not (src_vault / "spec" / "my-spec.md").exists()
    assert not (src_vault / "spec" / "my-spec.json").exists()
    assert new_id == "spec/my-spec"


def test_move_record_rejects_dest_outside_dest_vault_root(rs, conn, tmp_path):
    """``move_record`` confines the DESTINATION to its declared vault root.

    Dest-confinement guard: a destination whose
    realpath escapes the declared dest vault root is rejected
    (``InvalidRecordIdError``), mirroring the existing source ``_confine_record_id``.
    The library offers no guard if a direct caller hands it an unconfined dest;
    this closes that.
    """
    src_vault = tmp_path / "vault-a"
    dst_vault = tmp_path / "vault-b"
    src_vault.mkdir()
    dst_vault.mkdir()

    loc = rs.place_record("My Spec", "spec", None, str(src_vault))
    old_id = rs.validate_and_write(loc, _sidecar(), "body", conn)
    conn.commit()

    # A dest location whose paths point OUTSIDE the declared dest vault root.
    outside_dir = tmp_path / "outside" / "spec"
    outside_dir.mkdir(parents=True)
    bad_loc = rs.RecordLocation(
        vault_root=str(dst_vault),
        kind="spec",
        name="my-spec",
        record_id="spec/my-spec",
        body_path=outside_dir / "my-spec.md",
        sidecar_path=outside_dir / "my-spec.json",
    )

    with pytest.raises(rs.InvalidRecordIdError):
        rs.move_record(old_id, bad_loc, conn, old_vault_root=str(src_vault))

    # Nothing written outside; the old record survives (move did not proceed).
    assert not (outside_dir / "my-spec.md").exists()
    assert (src_vault / "spec" / "my-spec.md").read_text() == "body"


def test_realpath_is_descendant_guard(rs, tmp_path):
    """``_realpath_is_descendant`` is the shared dest-confinement predicate.

    True for the root itself and a strict descendant; False for a sibling sharing a
    name prefix (the ``+ os.sep`` guard) and for a path that escapes via realpath.
    """
    root = tmp_path / "vault"
    root.mkdir()
    root_real = os.path.realpath(root)

    assert rs._realpath_is_descendant(root, root_real)
    assert rs._realpath_is_descendant(root / "spec" / "x.md", root_real)
    # A sibling dir sharing a name prefix is NOT a descendant.
    sibling = tmp_path / "vault2"
    sibling.mkdir()
    assert not rs._realpath_is_descendant(sibling / "x.md", root_real)


# ---------------------------------------------------------------------------
# delete_record
# ---------------------------------------------------------------------------


def test_delete_record_removes_all_three(rs, conn, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("My Spec", "spec", None, str(vault))
    rid = rs.validate_and_write(loc, _sidecar(), "body", conn)
    conn.commit()

    rs.delete_record(rid, conn, vault_root=str(vault))
    conn.commit()

    assert not (vault / "spec" / "my-spec.md").exists()
    assert not (vault / "spec" / "my-spec.json").exists()
    assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0


def test_delete_record_missing_id_raises_not_found(rs, conn, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(rs.RecordNotFoundError):
        rs.delete_record("spec/nope", conn, vault_root=str(vault))


# ---------------------------------------------------------------------------
# labels/annotations round-trip
# ---------------------------------------------------------------------------


def test_labels_round_trip_byte_stable_sorted_inner_keys(rs, conn, tmp_path):
    """labels map written compact with inner keys sorted; round-trips byte-stable."""
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("Label Test", "spec", None, str(vault))
    sidecar = _sidecar(labels={"worktree": "s5", "claude-code/model": "x"})

    rs.validate_and_write(loc, sidecar, "body", conn)
    conn.commit()

    raw = (vault / "spec" / "label-test.json").read_text()
    parsed = json.loads(raw)
    assert "labels" in parsed
    assert parsed["labels"] == {"claude-code/model": "x", "worktree": "s5"}
    # Compact single-line and byte-stable.
    assert raw == json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def test_annotations_round_trip_byte_stable(rs, conn, tmp_path):
    """annotations map written compact; round-trips byte-stable."""
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("Annot Test", "spec", None, str(vault))
    sidecar = _sidecar(annotations={"note": "executor slice-1"})

    rs.validate_and_write(loc, sidecar, "body", conn)
    conn.commit()

    raw = (vault / "spec" / "annot-test.json").read_text()
    parsed = json.loads(raw)
    assert "annotations" in parsed
    assert raw == json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def test_labels_absent_not_written_to_sidecar(rs, conn, tmp_path):
    """When labels is absent from the sidecar, no 'labels' key is written (omit-when-absent)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    loc = rs.place_record("No Labels", "spec", None, str(vault))
    rs.validate_and_write(loc, _sidecar(), "body", conn)
    conn.commit()

    raw = (vault / "spec" / "no-labels.json").read_text()
    assert "labels" not in raw
    assert "annotations" not in raw


# ---------------------------------------------------------------------------
# index_transaction — shared open/close wrapper for the write handlers
# ---------------------------------------------------------------------------


def test_index_transaction_yields_open_connection(rs, tmp_path, monkeypatch):
    """The context manager yields a usable index connection."""
    import sqlite3

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    with rs.index_transaction() as conn:
        assert isinstance(conn, sqlite3.Connection)
        conn.execute("SELECT 1")


def test_index_transaction_closes_on_clean_exit(rs, tmp_path, monkeypatch):
    """The connection is closed once the block exits normally."""
    import sqlite3

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    with rs.index_transaction() as conn:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_index_transaction_closes_and_propagates_on_error(rs, tmp_path, monkeypatch):
    """A raise inside the block still closes the connection and propagates."""
    import sqlite3

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    captured = {}
    with pytest.raises(RuntimeError):
        with rs.index_transaction() as conn:
            captured["conn"] = conn
            raise RuntimeError("boom")
    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")
