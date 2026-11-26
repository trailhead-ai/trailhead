"""Read-only vault scan and index count behind the health report.

Every test runs the real scan against a ``tmp_path`` install, and the parity
tests build the index with the real ``open_index`` + ``rebuild``.
"""

import hashlib
import json
import os

import pytest

from conftest import load_script

MIB = 1024 * 1024


def _sidecar(title, updated="2026-03-01", **extra):
    return {"title": title, "status": "active", "created-at": "2026-01-01",
            "updated-at": updated, **extra}


def _put(root, kind, name, sidecar=None, body="body text\n"):
    d = root / kind
    path = d / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if body is not None:
        path.write_text(body)
    if sidecar is not None:
        (d / f"{name}.json").write_text(
            sidecar if isinstance(sidecar, str) else json.dumps(sidecar)
        )


class Install:
    def __init__(self, tmp_path, monkeypatch, names=("default", "second")):
        self.tmp_path = tmp_path
        self.cfg = tmp_path / "cfg"
        self.state = tmp_path / "state"
        # validate_config mkdirs the vaults root when a vault has an explicit
        # path; a real install already has it, so the fixture does too.
        (self.state / "lore" / "vaults").mkdir(parents=True)
        self.roots = {}
        entries = []
        for n in names:
            root = tmp_path / "vaults" / n
            root.mkdir(parents=True)
            self.roots[n] = root
            entries.append({"name": n, "scope": "default" if n == "default" else "team",
                            "path": str(root)})
        (self.cfg / "lore").mkdir(parents=True)
        (self.cfg / "lore" / "config.json").write_text(json.dumps({"vaults": entries}))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(self.cfg))
        monkeypatch.setenv("XDG_STATE_HOME", str(self.state))
        self.env = {"XDG_STATE_HOME": str(self.state)}

    def index(self, roots=None):
        idx = load_script("lore.search.index")
        conn = idx.open_index(env=self.env)
        try:
            idx.rebuild([str(r) for r in (roots or self.roots.values())], conn)
            conn.commit()
        finally:
            conn.close()

    def index_path(self):
        return load_script("lore.search.index")._resolve_index_path(env=self.env)


@pytest.fixture
def scan():
    return load_script("lore.health.scan")


@pytest.fixture
def inst(tmp_path, monkeypatch):
    return Install(tmp_path, monkeypatch)


def _scan_all(scan, inst):
    return {n: scan.scan_vault(n, r) for n, r in inst.roots.items()}


# --- records ---------------------------------------------------------------


def test_two_vaults_each_return_exactly_their_records(scan, inst):
    _put(inst.roots["default"], "lesson", "alpha", _sidecar("Alpha", "2026-02-02"), "A body")
    _put(inst.roots["default"], "task", "beta", _sidecar("Beta", "2026-02-03"))
    _put(inst.roots["second"], "spec", "gamma", _sidecar("Gamma", "2026-04-04"), "G body")
    res = _scan_all(scan, inst)
    got = {n: sorted((r.kind, r.name, r.updated_at) for r in s.records) for n, s in res.items()}
    assert got == {
        "default": [("lesson", "alpha", "2026-02-02"), ("task", "beta", "2026-02-03")],
        "second": [("spec", "gamma", "2026-04-04")],
    }
    rec = next(r for r in res["second"].records)
    assert (rec.vault, rec.body, rec.sidecar["title"]) == ("second", "G body", "Gamma")


def test_iteration_is_lazy_one_record_at_a_time(scan, inst):
    _put(inst.roots["default"], "lesson", "a", _sidecar("A"))
    _put(inst.roots["default"], "lesson", "b", "{not json")
    result = scan.new_vault_scan("default", inst.roots["default"])
    it = scan.iter_vault_records(result)
    first = next(it)
    assert first.name == "a"
    assert result.unreadable == []  # the second record has not been read yet
    assert list(it) == []
    assert [u.name for u in result.unreadable] == ["b"]


def test_malformed_sidecar_and_oversize_become_unreadable_entries(scan, inst):
    root = inst.roots["default"]
    _put(root, "lesson", "good", _sidecar("Good"))
    _put(root, "lesson", "broken", "{not json")
    _put(root, "lesson", "listy", "[1, 2]")
    _put(root, "lesson", "huge", _sidecar("Huge"), "x" * (scan.MAX_RECORD_BYTES + 1))
    s = scan.scan_vault("default", root)
    assert [r.name for r in s.records] == ["good"]
    reasons = {u.name: u.reason for u in s.unreadable}
    assert reasons == {
        "broken": scan.REASON_SIDECAR_MALFORMED,
        "listy": scan.REASON_SIDECAR_MALFORMED,
        "huge": scan.REASON_OVERSIZE,
    }
    assert all(u.vault == "default" and u.kind == "lesson" for u in s.unreadable)
    assert not hasattr(s.unreadable[0], "body") and not hasattr(s.unreadable[0], "sidecar")


def test_orphan_body_and_orphan_sidecar_are_neither_records_nor_unreadable(scan, inst):
    root = inst.roots["default"]
    _put(root, "lesson", "orphan-body", None)
    _put(root, "lesson", "orphan-sidecar", _sidecar("OS"), None)
    s = scan.scan_vault("default", root)
    assert (s.records, s.unreadable, s.skipped_escaping) == ([], [], 0)


def test_non_utf8_body_is_unreadable_not_a_crash(scan, inst):
    root = inst.roots["default"]
    _put(root, "lesson", "bin", _sidecar("Bin"), None)
    (root / "lesson" / "bin.md").write_bytes(b"\xff\xfe\x00bad")
    s = scan.scan_vault("default", root)
    assert s.records == []
    assert [(u.name, u.reason) for u in s.unreadable] == [("bin", scan.REASON_BODY_UNREADABLE)]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores chmod")
def test_unreadable_vault_dir_is_marked_and_other_vault_still_scanned(scan, inst):
    _put(inst.roots["default"], "lesson", "a", _sidecar("A"))
    _put(inst.roots["second"], "lesson", "b", _sidecar("B"))
    os.chmod(inst.roots["default"], 0)
    try:
        res = _scan_all(scan, inst)
    finally:
        os.chmod(inst.roots["default"], 0o755)
    assert res["default"].records == []
    assert res["default"].unreadable_reason == scan.REASON_VAULT_UNREADABLE
    assert res["second"].unreadable_reason is None
    assert [r.name for r in res["second"].records] == ["b"]


def test_symlink_escaping_record_is_counted_skipped_and_never_echoed(scan, inst, tmp_path):
    root = inst.roots["default"]
    _put(root, "lesson", "inside", _sidecar("In"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("SECRET")
    (outside / "secret.json").write_text(json.dumps(_sidecar("Secret")))
    (root / "lesson" / "leak.md").symlink_to(outside / "secret.md")
    (root / "lesson" / "leak.json").symlink_to(outside / "secret.json")
    s = scan.scan_vault("default", root)
    assert [r.name for r in s.records] == ["inside"]
    assert s.skipped_escaping == 1
    assert s.unreadable == []
    assert "secret" not in repr(s).lower() and "leak" not in repr(s)


def test_escaping_kind_directory_is_counted_skipped(scan, inst, tmp_path):
    root = inst.roots["default"]
    ext = tmp_path / "extkind"
    _put(ext.parent, "extkind", "x", _sidecar("X"))
    (root / "lesson").symlink_to(ext, target_is_directory=True)
    s = scan.scan_vault("default", root)
    assert s.records == [] and s.skipped_escaping == 1


def test_unresolvable_symlink_is_a_counted_skip_never_a_crash(scan, inst, tmp_path):
    root = inst.roots["default"]
    _put(root, "lesson", "ok", _sidecar("Ok"))
    (root / "lesson" / "loop.md").symlink_to(root / "lesson" / "loop.md")  # self-loop
    (root / "lesson" / "dangling.md").symlink_to(tmp_path / "nowhere.md")
    (root / "lesson" / "dangling.json").symlink_to(tmp_path / "nowhere.json")
    s = scan.scan_vault("default", root)
    assert [r.name for r in s.records] == ["ok"]
    assert s.skipped_escaping + len(s.unreadable) >= 1


# --- resolution -------------------------------------------------------------


def test_resolve_returns_configured_vaults_and_filters_by_name(scan, inst):
    assert [n for n, _ in scan.resolve_vaults()] == ["default", "second"]
    assert [(n, p) for n, p in scan.resolve_vaults("second")] == [("second", inst.roots["second"])]


def test_unknown_vault_name_raises_named_error_listing_configured_names(scan, inst):
    with pytest.raises(scan.HealthResolutionError) as exc:
        scan.resolve_vaults("nope")
    assert exc.value.configured == ("default", "second")
    assert "nope" in str(exc.value) and "default" in str(exc.value)


def test_unreadable_config_takes_the_refusal_path(scan, inst):
    (inst.cfg / "lore" / "config.json").write_text("{broken")
    with pytest.raises(scan.HealthResolutionError) as exc:
        scan.resolve_vaults()
    assert "config.json" in str(exc.value)
    assert exc.value.configured == ()


def test_missing_config_resolves_the_single_floor_vault(scan, inst):
    (inst.cfg / "lore" / "config.json").unlink()
    assert [n for n, _ in scan.resolve_vaults()] == ["default"]
    with pytest.raises(scan.HealthResolutionError) as exc:
        scan.resolve_vaults("second")
    assert exc.value.configured == ("default",)


# --- index parity -----------------------------------------------------------


def _parity_fixture(inst):
    r = inst.roots["default"]
    _put(r, "lesson", "plain", _sidecar("Plain"))
    _put(r, "task", "plain2", _sidecar("Plain2"))
    _put(r, "session", "sess-1", {**_sidecar("S"), "status": "dirty"})
    _put(r, "blob", "b1", _sidecar("Blob"))
    _put(r, "lesson", "orphan-body", None)  # .md, no sidecar
    _put(r, "lesson", "orphan-sidecar", _sidecar("OS"), None)  # sidecar, no .md
    _put(r, "lesson", "nest/ed", _sidecar("Nested"))
    _put(inst.roots["second"], "spec", "other", _sidecar("Other"))


def test_scan_count_equals_index_count_for_the_vault_root(scan, inst):
    _parity_fixture(inst)
    inst.index()
    counts = scan.count_index_rows([str(r) for r in inst.roots.values()], env=inst.env)
    for n, root in inst.roots.items():
        s = scan.scan_vault(n, root)
        assert counts[str(root)].status == scan.INDEX_OK
        assert counts[str(root)].count == len(s.records), n
    assert counts[str(inst.roots["default"])].count == 4  # plain, plain2, session, blob


def test_parity_holds_when_vault_is_configured_through_a_symlinked_path(scan, tmp_path, monkeypatch):
    inst = Install(tmp_path, monkeypatch, names=("default",))
    real = inst.roots["default"]
    _put(real, "lesson", "a", _sidecar("A"))
    _put(real, "session", "s", {**_sidecar("S"), "status": "dirty"})
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    cfg = {"vaults": [{"name": "default", "scope": "default", "path": str(link)}]}
    (inst.cfg / "lore" / "config.json").write_text(json.dumps(cfg))
    (name, path), = scan.resolve_vaults()
    inst.index([path])  # indexer is fed str(v.path), as cli/areas.py does
    count = scan.count_index_rows([str(path)], env=inst.env)[str(path)]
    assert (count.count, len(scan.scan_vault(name, path).records)) == (2, 2)


def test_parity_holds_when_the_root_string_itself_is_a_symlink(scan, tmp_path, monkeypatch):
    inst = Install(tmp_path, monkeypatch, names=("default",))
    real = inst.roots["default"]
    _put(real, "lesson", "a", _sidecar("A"))
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    inst.index([link])
    count = scan.count_index_rows([str(link)], env=inst.env)[str(link)]
    assert (count.count, len(scan.scan_vault("default", link).records)) == (1, 1)


def test_index_count_is_per_vault_and_tracks_a_removed_record(scan, inst):
    _put(inst.roots["default"], "lesson", "a", _sidecar("A"))
    _put(inst.roots["default"], "lesson", "b", _sidecar("B"))
    _put(inst.roots["second"], "lesson", "c", _sidecar("C"))
    inst.index()
    roots = [str(r) for r in inst.roots.values()]
    before = scan.count_index_rows(roots, env=inst.env)
    (inst.roots["second"] / "lesson" / "c.md").unlink()
    (inst.roots["second"] / "lesson" / "c.json").unlink()
    after = scan.count_index_rows(roots, env=inst.env)
    d, s = str(inst.roots["default"]), str(inst.roots["second"])
    assert (before[d].count, before[s].count) == (2, 1)
    assert after[d].count == 2  # index unchanged; disk now differs from it by one
    assert len(scan.scan_vault("second", inst.roots["second"]).records) == before[s].count - 1
    assert len(scan.scan_vault("default", inst.roots["default"]).records) == before[d].count


def test_vault_absent_from_index_counts_zero_and_index_absent_is_a_value(scan, inst):
    roots = [str(r) for r in inst.roots.values()]
    res = scan.count_index_rows(roots, env=inst.env)
    assert {c.status for c in res.values()} == {scan.INDEX_ABSENT}
    assert all(c.count is None for c in res.values())


def test_unreadable_index_is_a_value_not_an_exception(scan, inst):
    p = inst.index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"this is not a sqlite database" * 100)
    res = scan.count_index_rows([str(inst.roots["default"])], env=inst.env)
    assert res[str(inst.roots["default"])].status == scan.INDEX_UNREADABLE


def test_index_path_with_uri_special_characters_still_counts(scan, tmp_path, monkeypatch):
    weird = tmp_path / "we?ird#state%20"
    weird.mkdir()
    inst = Install(tmp_path, monkeypatch, names=("default",))
    inst.state = weird
    inst.env = {"XDG_STATE_HOME": str(weird)}
    _put(inst.roots["default"], "lesson", "a", _sidecar("A"))
    inst.index()
    res = scan.count_index_rows([str(inst.roots["default"])], env=inst.env)
    assert res[str(inst.roots["default"])].count == 1


# --- read-only ----------------------------------------------------------------


def _snap(*dirs):
    out = {}
    for d in dirs:
        for p in sorted(d.rglob("*")):
            if p.is_file():
                st = p.stat()
                out[str(p)] = (hashlib.sha256(p.read_bytes()).hexdigest(), st.st_mtime_ns)
    return out


def _full_run(scan, inst):
    for n, root in scan.resolve_vaults():
        scan.scan_vault(n, root)
    return scan.count_index_rows([str(r) for r in inst.roots.values()], env=inst.env)


@pytest.mark.parametrize("writer_open", [False, True])
def test_scan_and_count_modify_nothing(scan, inst, writer_open):
    _parity_fixture(inst)
    inst.index()
    idx_dir = inst.index_path().parent
    vault_dirs = list(inst.roots.values())
    writer = load_script("lore.search.index").open_index(env=inst.env) if writer_open else None
    try:
        if writer is not None:
            writer.execute("PRAGMA wal_autocheckpoint=0")
        # With a writer held open the db, -wal and -shm all exist; every file in
        # the index directory is compared by bytes and mtime, not only by name.
        index_before = _snap(idx_dir)
        files_before = sorted(p.name for p in idx_dir.iterdir())
        before = _snap(*vault_dirs)
        before_names = {str(p) for d in vault_dirs for p in d.rglob("*")}
        res = _full_run(scan, inst)
        assert all(c.status == scan.INDEX_OK for c in res.values())
        assert _snap(*vault_dirs) == before
        assert {str(p) for d in vault_dirs for p in d.rglob("*")} == before_names
        assert _snap(idx_dir) == index_before
        assert sorted(p.name for p in idx_dir.iterdir()) == files_before
    finally:
        if writer is not None:
            writer.close()


def test_count_creates_nothing_in_an_unwritable_index_dir(scan, inst):
    _put(inst.roots["default"], "lesson", "a", _sidecar("A"))
    inst.index()
    idx_dir = inst.index_path().parent
    before = sorted(p.name for p in idx_dir.iterdir())
    os.chmod(idx_dir, 0o555)
    try:
        res = scan.count_index_rows([str(inst.roots["default"])], env=inst.env)
    finally:
        os.chmod(idx_dir, 0o755)
    assert res[str(inst.roots["default"])].count == 1
    assert sorted(p.name for p in idx_dir.iterdir()) == before


# --- the aggregate budget ---------------------------------------------------


def _fill(root, n, body_bytes=1000):
    for i in range(n):
        _put(root, "lesson", f"r{i}", _sidecar(f"R{i}"), "x" * body_bytes)


def test_records_over_the_budget_leave_the_vault_not_fully_read_with_its_own_reason(scan, inst):
    _fill(inst.roots["default"], 3)
    s = scan.scan_vault("default", inst.roots["default"], max_bytes=2500)
    assert s.unreadable_reason == scan.REASON_OVER_BUDGET
    assert not s.fully_read
    assert s.budget_bytes == 2500


def test_the_same_records_under_a_larger_budget_read_fully(scan, inst):
    _fill(inst.roots["default"], 3)
    s = scan.scan_vault("default", inst.roots["default"], max_bytes=10_000)
    assert s.fully_read and s.unreadable_reason is None
    assert [r.name for r in s.records] == ["r0", "r1", "r2"]


def test_a_vault_exactly_at_the_budget_still_reads_fully(scan, inst):
    _fill(inst.roots["default"], 2)
    total = scan.scan_vault("default", inst.roots["default"], max_bytes=10_000).bytes_read
    assert scan.scan_vault("default", inst.roots["default"], max_bytes=total).fully_read
    assert not scan.scan_vault("default", inst.roots["default"], max_bytes=total - 1).fully_read


def test_reading_stops_at_the_trip_point_and_later_records_are_never_opened(scan, inst, monkeypatch):
    _fill(inst.roots["default"], 6)
    opened = []
    real = scan._read_capped

    def counted(path):
        opened.append(path.name)
        return real(path)

    monkeypatch.setattr(scan, "_read_capped", counted)
    s = scan.scan_vault("default", inst.roots["default"], max_bytes=2500)
    assert s.unreadable_reason == scan.REASON_OVER_BUDGET
    assert opened and not any(n.startswith(("r3", "r4", "r5")) for n in opened)
    assert s.bytes_read <= 2500 + 2 * scan.MAX_RECORD_BYTES


def test_an_oversize_body_keeps_its_parsed_sidecar_on_the_sidecar_only_list_and_stays_unreadable(scan, inst):
    root = inst.roots["default"]
    sc = _sidecar("Huge", labels={"k/v": "x"})
    _put(root, "lesson", "huge", sc, "x" * (scan.MAX_RECORD_BYTES + 1))
    _put(root, "lesson", "good", _sidecar("Good"))
    s = scan.scan_vault("default", root)
    assert [(r.vault, r.kind, r.name, r.sidecar) for r in s.sidecar_only] == [("default", "lesson", "huge", sc)]
    assert [(u.name, u.reason) for u in s.unreadable] == [("huge", scan.REASON_OVERSIZE)]
    assert [r.name for r in s.records] == ["good"]
    assert s.fully_read


def test_an_oversize_sidecar_and_a_malformed_one_are_not_kept_on_the_sidecar_only_list(scan, inst):
    root = inst.roots["default"]
    _put(root, "lesson", "bigsc", _sidecar("Big", pad="y" * (scan.MAX_RECORD_BYTES + 1)))
    _put(root, "lesson", "broken", "{not json")
    s = scan.scan_vault("default", root)
    assert s.sidecar_only == []
    assert sorted(u.name for u in s.unreadable) == ["bigsc", "broken"]


def test_an_oversize_record_is_charged_at_its_capped_size_and_is_not_a_budget_breach(scan, inst):
    root = inst.roots["default"]
    _put(root, "lesson", "huge", _sidecar("Huge"), "x" * (scan.MAX_RECORD_BYTES + 1))
    s = scan.scan_vault("default", root, max_bytes=2 * scan.MAX_RECORD_BYTES)
    assert s.fully_read
    assert [u.reason for u in s.unreadable] == [scan.REASON_OVERSIZE]
    assert scan.MAX_RECORD_BYTES <= s.bytes_read <= scan.MAX_RECORD_BYTES + 200


# --- escaping records are known by kind and name, never by path --------------


def _escape(root, tmp_path, kind, name):
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    (outside / f"{name}.md").write_text("SECRET")
    (outside / f"{name}.json").write_text(json.dumps(_sidecar("Secret")))
    (root / kind).mkdir(exist_ok=True)
    (root / kind / f"{name}.md").symlink_to(outside / f"{name}.md")
    (root / kind / f"{name}.json").symlink_to(outside / f"{name}.json")


def test_an_escaping_record_is_tracked_by_kind_and_name_and_judges_only_its_kind(scan, inst, tmp_path):
    root = inst.roots["default"]
    _put(root, "spec", "ok", _sidecar("Ok"))
    _escape(root, tmp_path, "session", "s")
    s = scan.scan_vault("default", root)
    assert [(e.kind, e.name) for e in s.escaped] == [("session", "s")]
    assert s.skipped_escaping == 1
    assert s.readable_for(("spec", "task"))
    assert not s.readable_for(("session",))
    assert not s.fully_read
    assert str(tmp_path / "outside") not in repr(s) and "SECRET" not in repr(s)


def test_an_escaping_kind_directory_counts_against_every_record_of_its_kind(scan, inst, tmp_path):
    root = inst.roots["default"]
    ext = tmp_path / "extdecision"
    _put(tmp_path, "extdecision", "x", _sidecar("X"))
    (root / "decision").symlink_to(ext, target_is_directory=True)
    _put(root, "task", "t", _sidecar("T"))
    s = scan.scan_vault("default", root)
    assert [(e.kind, e.name) for e in s.escaped] == [("decision", None)]
    assert not s.readable_for(("decision", "adr"))
    assert s.readable_for(("task",))
    assert [r.name for r in s.records] == ["t"]


def test_an_escape_the_scan_could_not_attribute_blocks_every_kind(scan):
    model = load_script("lore.health.model")
    s = model.VaultScan("v", "/nowhere", skipped_escaping=1)
    assert not s.readable_for(("session",))


# --- the index probe counts exactly what the indexer keeps -----------------------


def test_a_record_the_indexer_keeps_despite_a_failing_projection_counts_as_held(scan, inst):
    root = inst.roots["default"]
    _put(root, "lesson", "plain", _sidecar("Plain"))
    _put(root, "lesson", "unbindable", _sidecar("U", labels={"k": {"nested": "dict"}}))
    inst.index()
    counts = scan.count_index_rows([str(root)], env=inst.env)
    s = scan.scan_vault("default", root)
    assert counts[str(root)].count == s.indexed == 2


def test_a_reserved_label_key_the_indexer_drops_does_not_count(scan, inst):
    root = inst.roots["default"]
    _put(root, "lesson", "plain", _sidecar("Plain"))
    _put(root, "lesson", "reserved", _sidecar("R", labels={"related": "z"}))
    inst.index()
    counts = scan.count_index_rows([str(root)], env=inst.env)
    assert counts[str(root)].count == scan.scan_vault("default", root).indexed == 1


@pytest.mark.parametrize("sidecar", [
    _sidecar("Ok"),
    {k: v for k, v in _sidecar("NoCreated").items() if k != "created-at"},
    {k: v for k, v in _sidecar("NoUpdated").items() if k != "updated-at"},
    _sidecar("Reserved", labels={"related": "z"}),
    _sidecar("Unbindable", labels={"k": {"a": 1}}),
])
def test_the_probes_verdict_is_the_same_for_a_real_body_an_empty_one_and_a_dummy(scan, sidecar):
    probe = scan._IndexerProbe()
    verdicts = {probe.holds("lesson", "x", sidecar, b) for b in ("real body\n" * 50, "", "dummy")}
    assert len(verdicts) == 1


def test_the_scan_gives_the_probe_an_empty_body(scan, inst, monkeypatch):
    _put(inst.roots["default"], "lesson", "a", _sidecar("A"), "a real body that is not empty\n")
    bodies = []
    real = scan._IndexerProbe.holds

    def spy(self, kind, name, sidecar, body):
        bodies.append(body)
        return real(self, kind, name, sidecar, body)

    monkeypatch.setattr(scan._IndexerProbe, "holds", spy)
    assert scan.scan_vault("default", inst.roots["default"]).indexed == 1
    assert bodies == [""]


def test_an_oversize_body_whose_sidecar_the_indexer_rejects_does_not_count_as_held(scan, inst):
    root = inst.roots["default"]
    big = "x" * (scan.MAX_RECORD_BYTES + 1)
    _put(root, "lesson", "keeps", _sidecar("Keeps"), big)
    _put(root, "lesson", "drops", {k: v for k, v in _sidecar("Drops").items() if k != "created-at"}, big)
    inst.index()
    counts = scan.count_index_rows([str(root)], env=inst.env)
    s = scan.scan_vault("default", root)
    assert counts[str(root)].count == s.indexed == 1


def _fill_undecodable(root, n, body_bytes=1000):
    for i in range(n):
        _put(root, "lesson", f"u{i}", _sidecar(f"U{i}"), None)
        (root / "lesson" / f"u{i}.md").write_bytes(b"\xff" * body_bytes)


def test_undecodable_bodies_are_charged_to_the_budget_and_trip_it(scan, inst):
    _fill_undecodable(inst.roots["default"], 5)
    s = scan.scan_vault("default", inst.roots["default"], max_bytes=2500)
    assert s.unreadable_reason == scan.REASON_OVER_BUDGET
    assert s.bytes_read > 2500


def test_undecodable_bodies_under_the_budget_leave_the_vault_fully_read_and_are_charged(scan, inst):
    _fill_undecodable(inst.roots["default"], 2)
    s = scan.scan_vault("default", inst.roots["default"], max_bytes=10_000)
    assert s.unreadable_reason is None
    assert [u.reason for u in s.unreadable] == [scan.REASON_BODY_UNREADABLE] * 2
    assert s.bytes_read >= 2 * 1000


def test_an_undecodable_sidecar_is_charged_to_the_budget(scan, inst):
    root = inst.roots["default"]
    for i in range(5):
        _put(root, "lesson", f"s{i}", _sidecar(f"S{i}"), "ok\n")
        (root / "lesson" / f"s{i}.json").write_bytes(b"\xff" * 1000)
    s = scan.scan_vault("default", root, max_bytes=2500)
    assert s.unreadable_reason == scan.REASON_OVER_BUDGET
