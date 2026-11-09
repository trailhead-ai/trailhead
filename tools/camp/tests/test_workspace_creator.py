"""Tests for the opaque creator recorded on a workspace manifest.

Test contract:

1. `camp new <slug> --no-session --created-by <v>` writes `created_by: <v>`
   (verbatim) to the manifest; without the flag, no `created_by` key.
2. `--created-by ""` is refused: non-zero exit, one stderr line, nothing created.
3. Carry-forward at each rebuild site (reconcile_worktree, seed re-run,
   `camp setup` re-run); a keyless manifest gains none.
4. Re-entry: a different value, or any value against a workspace with no
   recorded creator, is refused with the manifest untouched and no session; a
   matching value re-enters.
5. `--created-by` with `--no-session` is allowed.

(`--host` and `camp list --json` are pinned in test_new_host_cli.py and
test_camp_list.py.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ._helpers import init_git_repo

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_AWKWARD = 'two words "quoted" $(touch /tmp/pwned) \'x\''


@pytest.fixture()
def ws(tmp_path):
    repo = tmp_path / "repo_a"
    init_git_repo(repo, origin=True)
    group = {
        "group": {"name": "crg"},
        "members": [
            {"name": "repo_a", "repo_root": str(repo), "tasks": [], "base": "origin/main"}
        ],
        "branch_pattern": "worktree-{slug}",
    }
    env = {
        "CAMP_STATE_DIR": str(tmp_path / "state"),
        "CAMP_CONFIG_DIR": str(tmp_path / "config"),
        "HOME": str(tmp_path / "home"),
    }
    return {"group": group, "env": env}


@pytest.fixture(autouse=True)
def _stub_spawn(monkeypatch):
    import camp.provision.provision as provision

    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)


def _new(ws, slug, *flags, no_session=True):
    from camp.cli import group as group_cli

    args = [slug, *(["--no-session"] if no_session else []), *flags]
    group_cli._cmd_new_group_cli(args, ws["group"], ws["env"], dry_run=False)


def _mpath(ws, slug):
    from camp.group.manifest import manifest_path_for

    return manifest_path_for("crg", slug, env=ws["env"])


def _read(ws, slug):
    from camp.group.manifest import read_central_manifest

    return read_central_manifest(_mpath(ws, slug))


def _write_raw_created_by(ws, slug, value):
    """Rewrite the manifest on disk with `created_by` set to *value*, bypassing camp."""
    mp = _mpath(ws, slug)
    data = json.loads(mp.read_text(encoding="utf-8"))
    data["created_by"] = value
    mp.write_text(json.dumps(data), encoding="utf-8")


class TestStampedAtCreation:
    @pytest.mark.parametrize("value", ["abc", "def-2", _AWKWARD])
    def test_the_value_is_stored_byte_identical(self, ws, value):
        _new(ws, "feat", "--created-by", value)
        assert _read(ws, "feat")["created_by"] == value

    def test_no_flag_writes_no_key(self, ws):
        _new(ws, "feat")
        assert "created_by" not in _read(ws, "feat")

    def test_equals_spelling_is_accepted(self, ws):
        _new(ws, "feat", "--created-by=abc")
        assert _read(ws, "feat")["created_by"] == "abc"

    def test_created_by_is_stamped_alongside_the_declared_owner(self, ws):
        cfg = Path(ws["env"]["CAMP_CONFIG_DIR"])
        cfg.mkdir(parents=True)
        (cfg / "hosts.toml").write_text('self_name = "andromeda"\n', encoding="utf-8")
        _new(ws, "feat", "--created-by", "abc")
        data = _read(ws, "feat")
        assert (data["owner"], data["created_by"]) == ("andromeda", "abc")


class TestEmptyValueRefused:
    @pytest.mark.parametrize("spelling", [["--created-by", ""], ["--created-by="]])
    def test_exits_nonzero_with_one_line_and_creates_nothing(self, ws, capsys, spelling):
        from camp.group.manifest import workspace_dir

        with pytest.raises(SystemExit) as exc:
            _new(ws, "feat", *spelling)

        assert exc.value.code not in (0, None)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.count("\n") == 1 and "non-empty" in captured.err
        assert not workspace_dir("crg", "feat", env=ws["env"]).exists()
        assert not _mpath(ws, "feat").exists()


class TestCarriedForwardAtEveryRebuild:
    def test_reconcile_worktree_keeps_it(self, ws):
        from camp.provision.reconcile import reconcile_worktree

        _new(ws, "feat", "--created-by", "abc")
        reconcile_worktree(ws["group"], "feat", env=ws["env"])
        assert _read(ws, "feat")["created_by"] == "abc"

    def test_rerunning_the_seed_keeps_it(self, ws):
        from camp.provision.provision import seed_pending_workspace

        _new(ws, "feat", "--created-by", "abc")
        seed_pending_workspace(ws["group"], "feat", env=ws["env"])
        assert _read(ws, "feat")["created_by"] == "abc"
        seed_pending_workspace(ws["group"], "feat", env=ws["env"], created_by="abc")
        assert _read(ws, "feat")["created_by"] == "abc"

    def test_rerunning_setup_keeps_it(self, ws):
        from camp.provision.lifecycle import cmd_setup_group

        _new(ws, "feat", "--created-by", "abc")
        cmd_setup_group(ws["group"], "feat", env=ws["env"])
        cmd_setup_group(ws["group"], "feat", env=ws["env"])
        assert _read(ws, "feat")["created_by"] == "abc"

    def test_a_keyless_manifest_gains_none_at_any_of_the_three(self, ws):
        from camp.provision.lifecycle import cmd_setup_group
        from camp.provision.provision import seed_pending_workspace
        from camp.provision.reconcile import reconcile_worktree

        _new(ws, "feat")
        reconcile_worktree(ws["group"], "feat", env=ws["env"])
        assert "created_by" not in _read(ws, "feat")
        seed_pending_workspace(ws["group"], "feat", env=ws["env"])
        assert "created_by" not in _read(ws, "feat")
        cmd_setup_group(ws["group"], "feat", env=ws["env"])
        assert "created_by" not in _read(ws, "feat")

    def test_a_non_string_on_disk_value_reads_as_not_recorded_at_the_rebuilds(self, ws):
        from camp.provision.provision import seed_pending_workspace
        from camp.provision.reconcile import reconcile_worktree

        _new(ws, "feat")
        _write_raw_created_by(ws, "feat", 7)
        seed_pending_workspace(ws["group"], "feat", env=ws["env"])
        assert "created_by" not in _read(ws, "feat")
        _write_raw_created_by(ws, "feat", 7)
        reconcile_worktree(ws["group"], "feat", env=ws["env"])
        assert "created_by" not in _read(ws, "feat")


class TestReentry:
    @pytest.fixture()
    def door(self, monkeypatch):
        from camp.cli import group as group_cli

        calls = []
        monkeypatch.setattr(group_cli, "_door_dispatch_for_new", lambda **kw: calls.append(kw))
        return calls

    def _refused(self, ws, capsys, door, *flags):
        before = _mpath(ws, "feat").read_bytes()
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            _new(ws, "feat", *flags, no_session=False)
        assert exc.value.code not in (0, None)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.count("\n") == 1 and "already exists" in captured.err
        assert "--created-by" in captured.err
        assert _mpath(ws, "feat").read_bytes() == before
        assert door == []

    def test_a_different_value_is_refused(self, ws, capsys, door):
        _new(ws, "feat", "--created-by", "abc")
        self._refused(ws, capsys, door, "--created-by", "other")

    def test_a_workspace_with_no_creator_never_gains_one(self, ws, capsys, door):
        _new(ws, "feat")
        self._refused(ws, capsys, door, "--created-by", "abc")

    def test_the_matching_value_re_enters_through_the_door(self, ws, capsys, door):
        _new(ws, "feat", "--created-by", _AWKWARD)
        before = _mpath(ws, "feat").read_bytes()
        capsys.readouterr()

        _new(ws, "feat", "--created-by", _AWKWARD, no_session=False)

        assert "re-entered workspace" in capsys.readouterr().err
        assert len(door) == 1
        assert _mpath(ws, "feat").read_bytes() == before

    def test_no_flag_re_enters_a_workspace_that_has_a_creator(self, ws, capsys, door):
        _new(ws, "feat", "--created-by", "abc")
        capsys.readouterr()
        _new(ws, "feat", no_session=False)
        assert "re-entered workspace" in capsys.readouterr().err
        assert _read(ws, "feat")["created_by"] == "abc"


class TestSeedRefusesToAddOrChangeACreator:
    def _seed(self, ws, **kw):
        from camp.provision.provision import seed_pending_workspace

        return seed_pending_workspace(ws["group"], "feat", env=ws["env"], **kw)

    def test_a_workspace_with_no_creator_refuses_one(self, ws):
        self._seed(ws)
        before = _mpath(ws, "feat").read_bytes()
        with pytest.raises(Exception, match="creator"):
            self._seed(ws, created_by="S")
        assert _mpath(ws, "feat").read_bytes() == before

    def test_a_different_creator_is_refused(self, ws):
        self._seed(ws, created_by="A")
        before = _mpath(ws, "feat").read_bytes()
        with pytest.raises(Exception, match="creator"):
            self._seed(ws, created_by="B")
        assert _mpath(ws, "feat").read_bytes() == before

    def test_the_same_creator_succeeds_and_is_kept(self, ws):
        self._seed(ws, created_by="A")
        self._seed(ws, created_by="A")
        assert _read(ws, "feat")["created_by"] == "A"

    def test_no_creator_requested_keeps_the_recorded_one(self, ws):
        self._seed(ws, created_by="A")
        self._seed(ws)
        assert _read(ws, "feat")["created_by"] == "A"

    def test_bring_up_refuses_the_same_way(self, ws):
        from camp.provision.provision import bring_up_workspace

        self._seed(ws)
        before = _mpath(ws, "feat").read_bytes()
        with pytest.raises(Exception, match="creator"):
            bring_up_workspace(ws["group"], "feat", env=ws["env"], created_by="S")
        assert _mpath(ws, "feat").read_bytes() == before


class TestRaceThroughTheCli:
    def test_a_manifest_seeded_after_the_exists_check_is_refused_cleanly(
        self, ws, capsys, monkeypatch
    ):
        from camp.cli import group as group_cli
        import camp.provision.provision as provision
        from camp.provision.provision import seed_pending_workspace

        real = provision.bring_up_workspace

        def racing(group, slug, **kw):
            seed_pending_workspace(group, slug, env=kw.get("env"))
            return real(group, slug, **kw)

        monkeypatch.setattr(provision, "bring_up_workspace", racing)
        door = []
        monkeypatch.setattr(group_cli, "_door_dispatch_for_new", lambda **kw: door.append(kw))
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            _new(ws, "feat", "--created-by", "S", no_session=False)
        assert exc.value.code not in (0, None)
        err = capsys.readouterr().err
        assert err.count("\n") == 1 and "already exists" in err and "creator" in err
        assert "bring-up failed" not in err
        assert "created_by" not in _read(ws, "feat")
        assert door == []


class TestReentryRefusalNamesItsCause:
    @pytest.fixture(autouse=True)
    def _door(self, monkeypatch):
        from camp.cli import group as group_cli

        monkeypatch.setattr(group_cli, "_door_dispatch_for_new", lambda **kw: None)

    def _err(self, ws, capsys):
        capsys.readouterr()
        with pytest.raises(SystemExit):
            _new(ws, "feat", "--created-by", "S", no_session=False)
        err = capsys.readouterr().err
        assert err.count("\n") == 1
        assert "Traceback" not in err
        return err

    def test_a_different_recorded_creator(self, ws, capsys):
        _new(ws, "feat", "--created-by", "A")
        assert "different creator" in self._err(ws, capsys)

    def test_no_recorded_creator(self, ws, capsys):
        _new(ws, "feat")
        assert "no recorded creator" in self._err(ws, capsys)

    def test_an_unreadable_manifest(self, ws, capsys):
        _new(ws, "feat")
        _mpath(ws, "feat").write_bytes(b"\xff\xfe\x00bad")
        assert "unreadable" in self._err(ws, capsys)
