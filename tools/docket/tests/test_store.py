"""The docket store: strict reads, atomic writes under a host lock, confinement, vault order."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from _docket_cli import PLUGIN_ROOT
from trailhead.tests._git_support import hermetic_git_env  # noqa: F401  (fixture)

from docket import store as S
from docket.store import DocketList, Entry, Store, Workspace

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "format"


@pytest.fixture
def vault(tmp_path, hermetic_git_env):  # noqa: F811
    root = tmp_path / "vault"
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=hermetic_git_env)
    return root


@pytest.fixture
def store(vault):
    return Store(vault)


def lists_dir(vault: Path) -> Path:
    return vault / "sites" / "docket" / "lists"


def list_file(vault: Path, name: str = "proj") -> Path:
    return lists_dir(vault) / f"{name}.json"


def put(vault: Path, payload, name: str = "proj") -> Path:
    path = list_file(vault, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload if isinstance(payload, bytes) else payload.encode())
    return path


def snapshot(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            out[rel] = ("link", os.readlink(p))
        elif p.is_file():
            out[rel] = ("file", p.read_bytes())
        else:
            out[rel] = ("other", None)
    return out


def doc(entries=(), version=1):
    return json.dumps({"version": version, "entries": list(entries)})


def ent(slug="a", kind="task"):
    return {"kind": kind, "slug": slug}


ONE = Entry("task", "alpha")
WS = Entry("task", "beta", Workspace("trailhead", "docket"))
MANY = tuple(Entry("task", f"item-{i}", Workspace("g", f"w{i}") if i % 2 else None) for i in range(25))


class FakeClock:
    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.sleeps = 0
        self.on_sleep = on_sleep

    def time(self):
        return self.now

    def sleep(self, dt):
        self.sleeps += 1
        self.now += dt
        if self.on_sleep:
            self.on_sleep(self)


def hold_lock(vault: Path) -> int:
    stage = vault / ".git" / "docket"
    stage.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(stage / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


class TestRoundTrip:
    @pytest.mark.parametrize("entries", [(), (ONE,), (WS,), MANY], ids=["empty", "one", "workspace", "many"])
    def test_a_written_list_reads_back_equal(self, store, vault, entries):
        store.write_list("proj", DocketList(entries))
        assert store.read_list("proj") == DocketList(entries)

    def test_the_file_on_disk_is_the_versioned_format(self, store, vault):
        store.write_list("proj", DocketList((ONE, WS)))
        assert json.loads(list_file(vault).read_text()) == {
            "version": 1,
            "entries": [
                {"kind": "task", "slug": "alpha"},
                {"kind": "task", "slug": "beta", "workspace": {"group": "trailhead", "slug": "docket"}},
            ],
        }

    def test_a_list_that_was_never_written_reads_empty(self, store):
        assert store.read_list("proj") == DocketList(())

    @pytest.mark.parametrize("order", [(), ("b",), ("b", "a", "c")])
    def test_a_written_order_reads_back_equal(self, store, vault, order):
        store.write_order(order)
        assert store.read_order() == order
        assert json.loads((vault / "sites" / "docket" / "vault-order.json").read_text()) == {
            "version": 1,
            "order": list(order),
        }

    def test_update_reports_what_changed_and_writes_nothing_when_nothing_did(self, store, vault):
        store.write_list("proj", DocketList((ONE,)))
        path = list_file(vault)
        os.utime(path, (1, 1))
        before = snapshot(vault)
        change = store.update_list("proj", lambda current: current)
        assert not change.changed
        assert path.stat().st_mtime == 1
        assert snapshot(vault) == before

    def test_update_applies_the_mutation_to_what_is_on_disk(self, store):
        store.write_list("proj", DocketList((ONE,)))
        change = store.update_list("proj", lambda cur: DocketList(cur.entries + (WS,)))
        assert change.changed and change.before == DocketList((ONE,)) and change.after == DocketList((ONE, WS))
        assert store.read_list("proj") == DocketList((ONE, WS))


def _over_cap():
    pad = " " * (S.MAX_FILE_BYTES + 1 - len(doc()))
    return doc() + pad


REFUSALS = [
    ("conflict-markers", "<<<<<<< HEAD\n" + doc() + "\n=======\n" + doc() + "\n>>>>>>> origin\n", S.ConflictedFileError, "resolve"),
    ("invalid-json", "{not json", S.UnreadableListError, "JSON"),
    ("not-utf8", b"\xff\xfe{}", S.UnreadableListError, "UTF-8"),
    ("duplicate-key", '{"version": 1, "version": 1, "entries": []}', S.UnreadableListError, "duplicate"),
    ("nan", '{"version": 1, "entries": [], "x": NaN}', S.UnreadableListError, "NaN"),
    ("infinity", '{"version": Infinity, "entries": []}', S.UnreadableListError, "Infinity"),
    ("not-an-object", "[]", S.UnreadableListError, "object"),
    ("over-cap", _over_cap(), S.UnreadableListError, "256 KiB"),
    ("unknown-key", json.dumps({"version": 1, "entries": [], "extra": 1}), S.UnreadableListError, "extra"),
    ("unknown-entry-key", doc([{**ent(), "title": "x"}]), S.UnreadableListError, "title"),
    ("unknown-workspace-key", doc([{**ent(), "workspace": {"group": "g", "slug": "s", "host": "h"}}]), S.UnreadableListError, "host"),
    ("missing-entries", json.dumps({"version": 1}), S.UnreadableListError, "entries"),
    ("version-zero", doc(version=0), S.UnreadableListError, "version"),
    ("version-string", doc(version="1"), S.UnreadableListError, "version"),
    ("version-bool", doc(version=True), S.UnreadableListError, "version"),
    ("newer-version", doc(version=2), S.NewerVersionError, "newer docket"),
    ("duplicate-entries", doc([ent("a"), ent("a")]), S.UnreadableListError, "duplicate"),
    ("unsafe-slug-dash", doc([ent("-x")]), S.UnreadableListError, "slug"),
    ("unsafe-slug-traversal", doc([ent("../x")]), S.UnreadableListError, "slug"),
    ("unsafe-slug-newline", doc([ent("a\nb")]), S.UnreadableListError, "slug"),
    ("unsafe-kind", doc([ent("a", "Task/x")]), S.UnreadableListError, "kind"),
    ("unsafe-workspace", doc([{**ent(), "workspace": {"group": "-g", "slug": "s"}}]), S.UnreadableListError, "workspace"),
    ("entry-not-object", doc(["task/a"]), S.UnreadableListError, "entry"),
]


class TestRefusals:
    @pytest.mark.parametrize("label,payload,exc,fragment", REFUSALS, ids=[r[0] for r in REFUSALS])
    def test_an_unreadable_list_is_refused_naming_the_file(self, store, vault, label, payload, exc, fragment):
        path = put(vault, payload)
        with pytest.raises(exc) as info:
            store.read_list("proj")
        assert str(path) in str(info.value)
        assert fragment.lower() in str(info.value).lower()

    @pytest.mark.parametrize("label,payload,exc,fragment", REFUSALS, ids=[r[0] for r in REFUSALS])
    def test_an_update_over_an_unreadable_list_refuses_and_leaves_the_bytes(self, store, vault, label, payload, exc, fragment):
        put(vault, payload)
        before = snapshot(vault / "sites")
        with pytest.raises(exc):
            store.update_list("proj", lambda cur: DocketList(cur.entries + (ONE,)))
        assert snapshot(vault / "sites") == before

    def test_a_conflicted_file_says_to_resolve_it_in_the_vault(self, store, vault):
        put(vault, REFUSALS[0][1])
        with pytest.raises(S.ConflictedFileError) as info:
            store.read_list("proj")
        assert f"resolve the conflict in the vault {vault}" in str(info.value)

    def test_a_newer_version_says_to_update_trailhead(self, store, vault):
        put(vault, doc(version=7))
        with pytest.raises(S.NewerVersionError) as info:
            store.read_list("proj")
        assert "written by a newer docket" in str(info.value)
        assert "update trailhead" in str(info.value)

    def test_only_the_unreadable_vaults_list_is_affected(self, store, vault):
        put(vault, "{bad", "broken")
        store.write_list("fine", DocketList((ONE,)))
        assert store.read_list("fine") == DocketList((ONE,))
        with pytest.raises(S.UnreadableListError):
            store.read_list("broken")

    def test_a_file_of_exactly_the_cap_is_read(self, store, vault):
        put(vault, doc() + " " * (S.MAX_FILE_BYTES - len(doc())))
        assert store.read_list("proj") == DocketList(())

    def test_a_file_one_byte_over_the_cap_is_refused(self, store, vault):
        put(vault, doc() + " " * (S.MAX_FILE_BYTES + 1 - len(doc())))
        with pytest.raises(S.UnreadableListError):
            store.read_list("proj")

    def test_a_list_too_large_to_read_back_is_not_written(self, store, vault):
        big = DocketList(tuple(Entry("task", "s" * 120 + str(i)) for i in range(3000)))
        store.write_list("proj", DocketList((ONE,)))
        before = snapshot(vault / "sites")
        with pytest.raises(S.ListTooLargeError):
            store.write_list("proj", big)
        assert snapshot(vault / "sites") == before

    def test_a_symlinked_list_file_is_refused_and_its_target_untouched(self, store, vault, tmp_path):
        victim = tmp_path / "victim.json"
        victim.write_text(doc([ent("v")]))
        lists_dir(vault).mkdir(parents=True)
        list_file(vault).symlink_to(victim)
        for call in (
            lambda: store.read_list("proj"),
            lambda: store.update_list("proj", lambda cur: DocketList(cur.entries + (ONE,))),
        ):
            with pytest.raises(S.UnreadableListError) as info:
                call()
            assert "symlink" in str(info.value)
        assert victim.read_text() == doc([ent("v")])
        assert list_file(vault).is_symlink()

    def test_a_symlinked_lists_dir_is_refused_and_its_target_untouched(self, store, vault, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "proj.json").write_text(doc([ent("v")]))
        (vault / "sites" / "docket").mkdir(parents=True)
        lists_dir(vault).symlink_to(elsewhere)
        before = snapshot(elsewhere)
        with pytest.raises(S.StoreError):
            store.read_list("proj")
        with pytest.raises(S.StoreError):
            store.write_list("proj", DocketList((ONE,)))
        assert snapshot(elsewhere) == before

    def test_a_symlinked_sites_dir_is_refused(self, store, vault, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / "docket" / "lists").mkdir(parents=True)
        (vault / "sites").symlink_to(elsewhere)
        with pytest.raises(S.StoreError):
            store.write_list("proj", DocketList((ONE,)))
        assert snapshot(elsewhere) == {"docket": ("other", None), "docket/lists": ("other", None)}

    def test_a_fifo_is_refused_without_blocking(self, store, vault):
        lists_dir(vault).mkdir(parents=True)
        os.mkfifo(list_file(vault))
        with pytest.raises(S.UnreadableListError) as info:
            store.read_list("proj")
        assert "regular file" in str(info.value)
        with pytest.raises(S.UnreadableListError):
            store.update_list("proj", lambda cur: cur)

    def test_a_directory_in_place_of_the_list_is_refused(self, store, vault):
        list_file(vault).mkdir(parents=True)
        with pytest.raises(S.UnreadableListError):
            store.read_list("proj")

    @pytest.mark.parametrize("payload,fragment", [
        ("<<<<<<< x\n{}\n>>>>>>> y\n", "resolve"),
        ("{bad", "JSON"),
        (json.dumps({"version": 1, "order": ["a", "a"]}), "duplicate"),
        (json.dumps({"version": 1, "order": ["-x"]}), "vault"),
        (json.dumps({"version": 1, "order": [], "x": 1}), "x"),
        (json.dumps({"version": 9, "order": []}), "newer docket"),
    ])
    def test_an_unreadable_order_file_is_refused_and_kept(self, store, vault, payload, fragment):
        path = vault / "sites" / "docket" / "vault-order.json"
        path.parent.mkdir(parents=True)
        path.write_text(payload)
        with pytest.raises(S.UnreadableListError) as info:
            store.read_order()
        assert fragment.lower() in str(info.value).lower() and str(path) in str(info.value)
        with pytest.raises(S.UnreadableListError):
            store.update_order(lambda cur: cur + ("z",))
        assert path.read_text() == payload


class TestStagingDirAndTempFiles:
    def test_the_staging_dir_is_created_private(self, store, vault):
        store.write_list("proj", DocketList((ONE,)))
        assert (vault / ".git" / "docket").stat().st_mode & 0o777 == 0o700

    def test_a_loosely_permissioned_staging_dir_is_tightened(self, store, vault):
        stage = vault / ".git" / "docket"
        stage.mkdir(mode=0o755)
        os.chmod(stage, 0o755)
        store.write_list("proj", DocketList((ONE,)))
        assert stage.stat().st_mode & 0o777 == 0o700

    def test_a_symlinked_staging_dir_is_refused_and_nothing_is_written(self, store, vault, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (vault / ".git" / "docket").symlink_to(elsewhere)
        with pytest.raises(S.StagingDirError):
            store.write_list("proj", DocketList((ONE,)))
        assert list(elsewhere.iterdir()) == []
        assert not list_file(vault).exists()

    def test_a_symlinked_lock_file_is_refused_and_its_target_untouched(self, store, vault, tmp_path):
        stage = vault / ".git" / "docket"
        stage.mkdir(mode=0o700)
        victim = tmp_path / "victim"
        victim.write_text("keep")
        (stage / "lock").symlink_to(victim)
        with pytest.raises(S.StagingDirError):
            store.write_list("proj", DocketList((ONE,)))
        assert victim.read_text() == "keep"
        assert not list_file(vault).exists()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only directory")
    def test_a_git_dir_docket_cannot_create_its_staging_dir_in_is_a_staging_error(self, store, vault):
        os.chmod(vault / ".git", 0o555)
        try:
            with pytest.raises(S.StagingDirError, match="cannot open or create docket"):
                store.write_list("proj", DocketList((ONE,)))
        finally:
            os.chmod(vault / ".git", 0o755)
        assert not list_file(vault).exists()

    def test_a_vault_without_a_git_dir_is_refused(self, tmp_path):
        bare = tmp_path / "plain"
        bare.mkdir()
        with pytest.raises(S.StagingDirError):
            Store(bare).write_list("proj", DocketList((ONE,)))
        assert not (bare / "sites").exists()

    def test_a_successful_write_leaves_only_the_list_under_sites(self, store, vault):
        store.write_list("proj", DocketList((ONE,)))
        assert sorted(snapshot(vault / "sites")) == ["docket", "docket/lists", "docket/lists/proj.json"]
        assert [p.name for p in (vault / ".git" / "docket").iterdir()] == ["lock"]


def _child(body: str, *args: str, env=None) -> subprocess.Popen:
    script = f"import sys; sys.path.insert(0, {str(PLUGIN_ROOT)!r})\n" + textwrap.dedent(body)
    return subprocess.Popen([sys.executable, "-c", script, *args], env=env, stderr=subprocess.PIPE, text=True)


class TestInterruption:
    def test_a_writer_killed_before_the_replace_leaves_the_original_byte_identical(self, store, vault, tmp_path):
        store.write_list("proj", DocketList((ONE,)))
        original = list_file(vault).read_bytes()
        sentinel = tmp_path / "at-replace"
        child = _child(
            """
            import sys, time
            from pathlib import Path
            from docket.store import Store, DocketList, Entry

            def stall(*a, **k):
                Path(sys.argv[2]).write_text("x")
                time.sleep(120)

            Store(Path(sys.argv[1]), replace=stall).write_list(
                "proj", DocketList((Entry("task", "replacement"),)))
            """,
            str(vault), str(sentinel),
        )
        try:
            while not sentinel.exists():
                assert child.poll() is None, child.stderr.read()
        finally:
            child.kill()
            child.wait()
        assert list_file(vault).read_bytes() == original
        assert sorted(snapshot(vault / "sites")) == ["docket", "docket/lists", "docket/lists/proj.json"]
        assert store.read_list("proj") == DocketList((ONE,))

    def test_a_killed_writer_releases_the_lock(self, store, vault, tmp_path):
        sentinel = tmp_path / "at-replace"
        child = _child(
            """
            import sys, time
            from pathlib import Path
            from docket.store import Store, DocketList

            def stall(*a, **k):
                Path(sys.argv[2]).write_text("x")
                time.sleep(120)

            Store(Path(sys.argv[1]), replace=stall).write_list("proj", DocketList(()))
            """,
            str(vault), str(sentinel),
        )
        try:
            while not sentinel.exists():
                assert child.poll() is None, child.stderr.read()
        finally:
            child.kill()
            child.wait()
        store.write_list("proj", DocketList((ONE,)))
        assert store.read_list("proj") == DocketList((ONE,))


class TestLock:
    def test_concurrent_processes_each_appending_an_entry_all_land(self, store, vault):
        n = 8
        body = """
            from pathlib import Path
            from docket.store import Store, DocketList, Entry
            slug = sys.argv[2]
            Store(Path(sys.argv[1])).update_list(
                "proj", lambda cur: DocketList(cur.entries + (Entry("task", slug),)))
            """
        children = [_child(body, str(vault), f"item-{i}") for i in range(n)]
        for c in children:
            assert c.wait(timeout=120) == 0, c.stderr.read()
        assert sorted(e.slug for e in store.read_list("proj").entries) == sorted(f"item-{i}" for i in range(n))

    def test_a_write_waits_for_a_held_lock_then_proceeds_when_it_is_released(self, vault):
        fd = hold_lock(vault)

        def release_on_first_wait(clock):
            if fd is not None and clock.sleeps == 1:
                os.close(fd)

        clock = FakeClock(release_on_first_wait)
        s = Store(vault, clock=clock.time, sleep=clock.sleep, lock_deadline=5)
        s.write_list("proj", DocketList((ONE,)))
        assert clock.sleeps >= 1
        assert s.read_list("proj") == DocketList((ONE,))

    def test_a_write_refuses_at_the_deadline_while_the_lock_stays_held(self, vault):
        fd = hold_lock(vault)
        clock = FakeClock()
        s = Store(vault, clock=clock.time, sleep=clock.sleep, lock_deadline=5)
        try:
            with pytest.raises(S.LockTimeoutError) as info:
                s.write_list("proj", DocketList((ONE,)))
        finally:
            os.close(fd)
        assert 5 <= clock.now < 6
        assert "lock" in str(info.value) and "5" in str(info.value)
        assert not list_file(vault).exists()

    def test_the_default_deadline_is_thirty_seconds(self, vault):
        fd = hold_lock(vault)
        clock = FakeClock()
        s = Store(vault, clock=clock.time, sleep=clock.sleep)
        try:
            with pytest.raises(S.LockTimeoutError):
                s.write_list("proj", DocketList((ONE,)))
        finally:
            os.close(fd)
        assert 30 <= clock.now < 31

    def test_a_lock_held_across_a_read_modify_write_is_taken_once(self, store, vault):
        with store.locked():
            store.update_list("proj", lambda cur: DocketList(cur.entries + (ONE,)))
            store.update_list("proj", lambda cur: DocketList(cur.entries + (WS,)))
        assert store.read_list("proj") == DocketList((ONE, WS))

    def test_the_lock_is_released_after_a_refused_update(self, store, vault):
        put(vault, "{bad")
        with pytest.raises(S.UnreadableListError):
            store.update_list("proj", lambda cur: cur)
        fd = os.open(vault / ".git" / "docket" / "lock", os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(fd)


UNSAFE_VAULTS = ["../x", "-x", "a/b", "", ".", "..", "a\x00b", "x" * 65, "a b", ".hidden", "é"]


class TestConfinement:
    @pytest.mark.parametrize("name", UNSAFE_VAULTS)
    def test_an_unsafe_vault_name_is_refused_on_every_entry_point_and_nothing_changes(self, store, vault, name):
        store.write_list("proj", DocketList((ONE,)))
        before = snapshot(vault.parent)
        for call in (
            lambda: store.read_list(name),
            lambda: store.write_list(name, DocketList((ONE,))),
            lambda: store.update_list(name, lambda cur: cur),
        ):
            with pytest.raises(S.UnsafeNameError):
                call()
        assert snapshot(vault.parent) == before

    @pytest.mark.parametrize("name", UNSAFE_VAULTS)
    def test_an_unsafe_vault_name_is_refused_before_the_lock_or_staging_dir_exist(self, store, vault, name):
        before = snapshot(vault.parent)
        with pytest.raises(S.UnsafeNameError):
            store.update_list(name, lambda cur: cur)
        assert snapshot(vault.parent) == before

    @pytest.mark.parametrize("name", UNSAFE_VAULTS)
    def test_an_unsafe_vault_name_never_enters_the_order_file(self, store, vault, name):
        store.write_order(("a",))
        before = snapshot(vault.parent)
        with pytest.raises(S.UnsafeNameError):
            store.write_order(("a", name))
        assert snapshot(vault.parent) == before

    @pytest.mark.parametrize("name", ["proj", "my-vault", "Levr", "a.b_c", "0x"])
    def test_a_plain_vault_name_is_accepted(self, store, vault, name):
        store.write_list(name, DocketList((ONE,)))
        assert list_file(vault, name).is_file()

    @pytest.mark.parametrize("kind,slug", [
        ("task", "../x"), ("task", "-x"), ("task", "a/b"), ("task", ""), ("Task", "a"), ("-t", "a"),
        ("task", "a\nb"), ("task", "a b"), ("ta/sk", "a"),
    ])
    def test_an_unsafe_entry_cannot_be_built(self, kind, slug):
        with pytest.raises(S.UnsafeNameError):
            Entry(kind, slug)

    def test_an_unsafe_workspace_cannot_be_built(self):
        with pytest.raises(S.UnsafeNameError):
            Workspace("-g", "s")
        with pytest.raises(S.UnsafeNameError):
            Workspace("g", "../s")


class TestVaultOrder:
    @pytest.mark.parametrize("stored,with_list,expected", [
        ((), {"b", "a", "c"}, ["a", "b", "c"]),
        (("c", "a"), {"a", "b", "c", "d"}, ["c", "a", "b", "d"]),
        (("z", "y"), {"a"}, ["z", "y", "a"]),
    ])
    def test_stored_order_first_then_remaining_vaults_with_a_list_alphabetically(self, stored, with_list, expected):
        assert S.resolve_order(stored, with_list) == expected

    def test_the_store_resolves_the_order_from_disk(self, store, vault):
        for name in ("b", "a", "d", "c"):
            store.write_list(name, DocketList((ONE,)))
        store.write_order(("c", "ghost", "a"))
        assert store.effective_order() == ["c", "ghost", "a", "b", "d"]

    def test_with_no_order_file_the_vaults_with_lists_are_alphabetical(self, store):
        for name in ("b", "a"):
            store.write_list(name, DocketList(()))
        assert store.effective_order() == ["a", "b"]

    def test_a_file_that_is_not_a_vault_list_is_not_a_vault(self, store, vault):
        store.write_list("a", DocketList(()))
        (lists_dir(vault) / "notes.txt").write_text("x")
        (lists_dir(vault) / "-bad.json").write_text("{}")
        assert store.effective_order() == ["a"]

    def test_no_data_folder_means_no_vaults(self, store):
        assert store.effective_order() == []


def _vls(entries):
    calls = []

    def runner(argv):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(entries), stderr="")

    return runner, calls


class TestLocateDefaultVault:
    def test_the_vault_whose_scope_is_default_wins_whatever_its_name(self, tmp_path):
        runner, calls = _vls([
            {"name": "default", "scope": "team", "path": str(tmp_path / "team")},
            {"name": "main", "scope": "default", "path": str(tmp_path / "main")},
        ])
        assert S.locate_default_vault(runner) == tmp_path / "main"
        assert calls == [["lore", "vault", "ls", "--json"]]

    @pytest.mark.parametrize("entries", [
        [{"name": "a", "scope": "team", "path": "/x"}],
        [{"name": "a", "scope": "default", "path": "/x"}, {"name": "b", "scope": "default", "path": "/y"}],
        [{"name": "a", "scope": "default", "path": "relative/x"}],
        [{"name": "a", "scope": "default"}],
        {"not": "a list"},
    ])
    def test_a_listing_without_exactly_one_usable_default_is_refused(self, entries):
        runner, _ = _vls(entries)
        with pytest.raises(S.StoreError):
            S.locate_default_vault(runner)

    def test_a_failing_lore_is_refused_with_its_message(self):
        def runner(argv):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="lore exploded")

        with pytest.raises(S.StoreError, match="lore exploded"):
            S.locate_default_vault(runner)

    def test_unparseable_output_is_refused(self):
        def runner(argv):
            return subprocess.CompletedProcess(argv, 0, stdout="not json", stderr="")

        with pytest.raises(S.StoreError):
            S.locate_default_vault(runner)


CASES = json.loads((FIXTURES / "cases.json").read_text())


class TestSharedFixtures:
    @pytest.mark.parametrize("case", [c for c in CASES if c["valid"]], ids=lambda c: c["file"])
    def test_every_valid_fixture_reads_to_its_declared_content(self, store, vault, case):
        text = (FIXTURES / case["file"]).read_text()
        if case["kind"] == "list":
            put(vault, text)
            expected = DocketList(tuple(
                Entry(e["kind"], e["slug"], Workspace(**e["workspace"]) if "workspace" in e else None)
                for e in case["entries"]
            ))
            assert store.read_list("proj") == expected
        else:
            path = vault / "sites" / "docket" / "vault-order.json"
            path.parent.mkdir(parents=True)
            path.write_text(text)
            assert list(store.read_order()) == case["order"]

    @pytest.mark.parametrize("case", [c for c in CASES if not c["valid"]], ids=lambda c: c["file"])
    def test_every_invalid_fixture_is_refused_for_its_declared_reason(self, store, vault, case):
        text = (FIXTURES / case["file"]).read_text()
        exc = {"conflict": S.ConflictedFileError, "newer-version": S.NewerVersionError}.get(
            case["refusal"], S.UnreadableListError)
        if case["kind"] == "list":
            put(vault, text)
            with pytest.raises(exc):
                store.read_list("proj")
        else:
            path = vault / "sites" / "docket" / "vault-order.json"
            path.parent.mkdir(parents=True)
            path.write_text(text)
            with pytest.raises(exc):
                store.read_order()
        assert exc is not S.UnreadableListError or case["refusal"] != "newer-version"
