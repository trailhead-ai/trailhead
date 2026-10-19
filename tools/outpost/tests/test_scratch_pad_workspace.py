"""Behavioral tests for ``scratch_pad.enclosing_workspace``.

The function names the camp workspace a working directory sits inside, by
path arithmetic against camp's state root, so the pad script can tell when it
runs inside a workspace. Camp's own resolver and the publisher's state-root
mirror are loaded by path so the copies cannot drift apart.
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

import trailhead.paths as trailhead_paths

TOOLS = Path(__file__).parent.parent.parent
SCRIPT_PATH = TOOLS / "outpost/plugins/outpost/skills/scratch-pad/scratch_pad.py"
PUBLISH_PATH = TOOLS / "outpost/plugins/outpost/skills/publish-site/publish_site.py"
INJECT_PATH = TOOLS / "camp/plugins/camp/camp/launch/inject.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def pad():
    return _load("scratch_pad", SCRIPT_PATH)


@pytest.fixture
def camp(tmp_path):
    root = tmp_path / "camp-state"
    root.mkdir()
    return root, {"CAMP_STATE_DIR": str(root), "HOME": str(tmp_path / "home")}


def _member(root: Path, group="grp", slug="ws", member="repo") -> Path:
    d = root / group / "worktrees" / slug / member
    d.mkdir(parents=True)
    return d


def test_workspace_root_member_and_deep_dirs_give_the_workspace(pad, camp):
    root, env = camp
    member = _member(root)
    deep = member / "a" / "b" / "c"
    deep.mkdir(parents=True)
    expected = root.resolve() / "grp" / "worktrees" / "ws"
    for cwd in (expected, member, deep):
        assert pad.enclosing_workspace(cwd, env) == expected


def test_dirs_that_are_not_inside_a_workspace_give_none(pad, camp, tmp_path):
    root, env = camp
    _member(root)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    for cwd in (root / "grp" / "worktrees", root / "grp", root, outside):
        assert pad.enclosing_workspace(cwd, env) is None, cwd


def test_a_group_without_the_worktrees_segment_gives_none(pad, camp):
    root, env = camp
    d = root / "grp" / "other" / "ws" / "repo"
    d.mkdir(parents=True)
    assert pad.enclosing_workspace(d, env) is None


def test_symlink_from_outside_into_a_member_resolves_to_the_workspace(pad, camp, tmp_path):
    root, env = camp
    member = _member(root)
    link = tmp_path / "link"
    link.symlink_to(member)
    assert pad.enclosing_workspace(link, env) == root.resolve() / "grp" / "worktrees" / "ws"


def test_symlink_from_a_workspace_to_outside_gives_none(pad, camp, tmp_path):
    root, env = camp
    member = _member(root)
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = member / "escape"
    link.symlink_to(target)
    assert pad.enclosing_workspace(link, env) is None


def test_symlinked_state_root_yields_the_resolved_workspace_path(pad, tmp_path):
    real = tmp_path / "real-state"
    (real / "grp" / "worktrees" / "ws" / "repo").mkdir(parents=True)
    alias = tmp_path / "alias-state"
    alias.symlink_to(real)
    env = {"CAMP_STATE_DIR": str(alias), "HOME": str(tmp_path / "home")}
    cwd = alias / "grp" / "worktrees" / "ws" / "repo"
    assert pad.enclosing_workspace(cwd, env) == real.resolve() / "grp" / "worktrees" / "ws"


def _env_table(tmp_path):
    return [
        {"CAMP_STATE_DIR": str(tmp_path / "custom"), "HOME": str(tmp_path / "home")},
        {
            "CAMP_STATE_DIR": str(tmp_path / "custom"),
            "XDG_STATE_HOME": str(tmp_path / "xdg"),
            "HOME": str(tmp_path / "home"),
        },
        {"XDG_STATE_HOME": str(tmp_path / "xdg"), "HOME": str(tmp_path / "home")},
        {"HOME": str(tmp_path / "home")},
    ]


def test_each_state_root_source_is_honoured_and_agrees_with_trailhead(pad, tmp_path):
    for env in _env_table(tmp_path):
        expected = trailhead_paths.state_dir("camp", env=env, platform=sys.platform)
        member = expected / "grp" / "worktrees" / "ws" / "repo"
        member.mkdir(parents=True, exist_ok=True)
        assert pad._camp_state_root(env) == expected, env
        assert pad.enclosing_workspace(member, env) == expected.resolve() / "grp" / "worktrees" / "ws", env


def test_state_root_agrees_with_the_publisher_mirror(pad, tmp_path):
    publisher = _load("publish_site", PUBLISH_PATH)
    for env in _env_table(tmp_path):
        assert pad._camp_state_root(env) == publisher._camp_state_root(env), env


def test_names_the_same_group_and_slug_as_camp(pad, camp, tmp_path):
    inject = _load("camp_inject", INJECT_PATH)
    root, env = camp
    member = _member(root, "g1", "s1")
    (root / "g2" / "worktrees").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(member)
    cwds = [
        root / "g1" / "worktrees" / "s1",
        member,
        member.parent,
        root / "g2" / "worktrees",
        root / "g1",
        root,
        outside,
        link,
    ]
    for cwd in cwds:
        theirs = inject.resolve_group_slug_from_cwd(cwd, root)
        ours = pad.enclosing_workspace(cwd, env)
        if theirs is None:
            assert ours is None, cwd
        else:
            group, slug = theirs
            assert ours == root.resolve() / group / "worktrees" / slug, cwd


# --- publishing the pad into the enclosing workspace -------------------------

SKILL_DIR = SCRIPT_PATH.parent
ROUND_JS_PREFIX = "window.PAD_ROUND = JSON.parse("
ROUNDS_JS_PREFIX = "window.PAD_ROUNDS = JSON.parse("
ROUND_JS_SUFFIX = ");\n"
WAITING = "waiting for another session's add"
KIT_FILES = {"index.html": b"<!doctype html><title>kit</title>\n", "pad.js": b"// pad\n"}


def _round():
    option = {"name": "Split", "cost": "More", "pick": True, "layout": {"rule": True}}
    other = {"name": "Stacked", "cost": "Simple", "pick": False, "layout": {"rule": True}}
    return {"title": "Settings", "questions": [{"prompt": "Header", "options": [option, other]}]}


def _kit(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name, body in KIT_FILES.items():
        (path / name).write_bytes(body)
    return path


def _round_file(tmp_path: Path, data=None, name="round.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(_round() if data is None else data))
    return p


class Rig:
    def __init__(self, pad, tmp_path, monkeypatch):
        self.pad = pad
        self.tmp_path = tmp_path
        self.root = tmp_path / "camp-state"
        self.member = _member(self.root)
        self.ws = self.root.resolve() / "grp" / "worktrees" / "ws"
        self.outpost_state = tmp_path / "outpost-state"
        self.env = {
            "CAMP_STATE_DIR": str(self.root),
            "CAMP_CONFIG_DIR": str(tmp_path / "camp-config"),
            "HOME": str(tmp_path / "home"),
            "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
            "OUTPOST_STATE_DIR": str(self.outpost_state),
        }
        self.kit = _kit(tmp_path / "kit")
        self.tmpdir = tmp_path / "scratch-tmp"
        self.tmpdir.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(self.tmpdir))
        self.outside = tmp_path / "outside"
        self.outside.mkdir()

    def argv(self, pad_name="p", round_path=None, add=False):
        argv = ["--pad", pad_name, "--round", str(round_path or _round_file(self.tmp_path))]
        return [*argv, "--add"] if add else argv

    def run(self, pad_name="p", round_path=None, cwd=None, add=False, **kw):
        return self.pad.main(self.argv(pad_name, round_path, add), env=self.env, cwd=cwd or self.member, **kw)

    def add(self, title, pad_name="p", **kw):
        path = _round_file(self.tmp_path, {**_round(), "title": title}, f"{title}.json")
        return self.run(pad_name, path, add=True, **kw)

    def leftovers(self):
        return sorted(p.name for p in self.tmpdir.iterdir())


@pytest.fixture
def rig(pad, tmp_path, monkeypatch):
    return Rig(pad, tmp_path, monkeypatch)


def _snapshot(root: Path):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


def _decode_literal(line: str, prefix: str):
    """The data in a ``<prefix><JSON string literal>);`` line as the script writes it."""
    assert line.startswith(prefix) and line.endswith(ROUND_JS_SUFFIX)
    return json.loads(json.loads(line[len(prefix) : -len(ROUND_JS_SUFFIX)]))


def _round_js_data(path: Path):
    return _decode_literal(path.read_text(encoding="utf-8").splitlines(keepends=True)[0], ROUND_JS_PREFIX)


def test_run_from_a_member_folder_publishes_the_pad_as_a_workspace_site(rig, capsys):
    code = rig.run()

    out = capsys.readouterr().out.splitlines()
    site = rig.ws / "sites" / "p"
    assert code == 0
    assert out[0] == "http://127.0.0.1:7314/grp-ws/p/"
    assert out[1].startswith("Local to this machine only")
    assert out[-1] == f"pad folder: {site}"
    assert len(out) == 3
    assert (site / "index.html").is_file()
    assert (site / "round-1" / "index.html").read_bytes() == (SKILL_DIR / "kit" / "index.html").read_bytes()
    assert _round_js_data(site / "round-1" / "round.js") == _round()
    assert not (rig.outpost_state / "pads").exists()
    assert rig.leftovers() == []


def test_the_same_round_from_outside_any_workspace_still_builds_on_disk(rig, capsys):
    code = rig.run(cwd=rig.outside)

    out = capsys.readouterr().out.splitlines()
    folder = rig.outpost_state / "pads" / "p"
    assert code == 0
    assert out == [(folder / "index.html").as_uri(), f"pad folder: {folder}"]
    assert (folder / "round-1" / "round.js").is_file()
    assert not (rig.ws / "sites").exists()


def test_a_pad_name_already_published_is_refused_without_calling_the_publisher(rig, capsys):
    assert rig.run() == 0
    capsys.readouterr()
    before = _snapshot(rig.ws / "sites")

    def never(cmd, env, timeout):
        raise AssertionError("the publisher must not run for a name that is already taken")

    code = rig.run(publish_runner=never)

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.splitlines() == ['scratch-pad: pad "p" already exists — choose another --pad name']
    assert _snapshot(rig.ws / "sites") == before
    assert rig.leftovers() == []


def test_a_dangling_symlink_at_the_pad_name_counts_as_taken(rig, capsys):
    (rig.ws / "sites").mkdir()
    (rig.ws / "sites" / "p").symlink_to(rig.tmp_path / "nowhere")

    code = rig.run(publish_runner=lambda *a: pytest.fail("publisher ran"))

    assert code == 1
    assert capsys.readouterr().err.splitlines() == ['scratch-pad: pad "p" already exists — choose another --pad name']


def test_a_different_name_in_the_same_workspace_publishes(rig, capsys):
    assert rig.run("one") == 0
    assert rig.run("two") == 0
    assert (rig.ws / "sites" / "one" / "index.html").is_file()
    assert (rig.ws / "sites" / "two" / "index.html").is_file()


def test_the_publisher_alone_refuses_a_taken_name_when_the_early_check_is_bypassed(rig, capsys):
    assert rig.run() == 0
    capsys.readouterr()
    before = _snapshot(rig.ws / "sites")
    real_lexists = os.path.lexists

    def blind(path):
        return False if str(path) == str(rig.ws / "sites" / "p") else real_lexists(path)

    original = rig.pad.os.path.lexists
    rig.pad.os.path.lexists = blind
    try:
        code = rig.run()
    finally:
        rig.pad.os.path.lexists = original

    err = capsys.readouterr().err.splitlines()
    assert code == 1
    assert err[0].startswith("scratch-pad: publish: error: ") and "already exists" in err[0]
    assert _snapshot(rig.ws / "sites") == before
    assert rig.leftovers() == []


def _wait_for(predicate, what, limit=15.0):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {what}")


def test_two_concurrent_runs_on_one_name_yield_one_site_and_one_refusal(rig, capsys):
    first_inside, release = threading.Event(), threading.Event()
    real = rig.pad._run_publisher
    calls = []

    def first_parks_inside_the_publisher(cmd, env, timeout):
        calls.append(cmd)
        first_inside.set()
        assert release.wait(timeout=30)
        return real(cmd, env, timeout)

    results = []

    def attempt():
        try:
            rig.pad.build_pad(
                _round_file(rig.tmp_path),
                "race",
                kit_dir=rig.kit,
                env=rig.env,
                cwd=rig.member,
                publish_runner=first_parks_inside_the_publisher,
            )
            results.append("built")
        except rig.pad.Refusal as exc:
            results.append(exc.lines)

    first = threading.Thread(target=attempt)
    first.start()
    assert first_inside.wait(timeout=15)
    second = threading.Thread(target=attempt)
    second.start()
    err = []
    _wait_for(lambda: err.append(capsys.readouterr().err) or any(WAITING in e for e in err), "the second run to wait")
    release.set()
    for t in (first, second):
        t.join(timeout=60)
        assert not t.is_alive()

    refusals = [r for r in results if r != "built"]
    taken = 'scratch-pad: pad "race" already exists — choose another --pad name'
    assert results.count("built") == 1 and refusals == [[taken]]
    assert len(calls) == 1
    assert sorted(p.name for p in (rig.ws / "sites").iterdir()) == ["race"]
    assert (rig.ws / "sites" / "race" / "round-1" / "round.js").is_file()
    assert rig.leftovers() == []


@pytest.mark.parametrize("bad", [{"title": "", "questions": []}, {"nope": 1}])
def test_a_badly_shaped_round_gets_the_same_lines_inside_a_workspace_as_on_disk(rig, capsys, bad):
    rp = _round_file(rig.tmp_path, bad)
    assert rig.run("p", rp, cwd=rig.outside) == 1
    on_disk = capsys.readouterr().err

    assert rig.run("p", rp, publish_runner=lambda *a: pytest.fail("publisher ran")) == 1
    in_workspace = capsys.readouterr().err

    assert on_disk.count("scratch-pad:") >= 2
    assert in_workspace == on_disk
    assert not (rig.ws / "sites").exists()
    assert rig.leftovers() == []


def test_a_bad_pad_name_is_refused_inside_a_workspace_before_anything_is_written(rig, capsys):
    code = rig.run("Bad Name", publish_runner=lambda *a: pytest.fail("publisher ran"))

    assert code == 1
    assert "not a valid pad name" in capsys.readouterr().err
    assert not (rig.ws / "sites").exists() and rig.leftovers() == []


def test_a_publisher_failure_is_relayed_under_publish_and_leaves_nothing(rig, capsys):
    elsewhere = rig.tmp_path / "elsewhere"
    elsewhere.mkdir()
    (rig.ws / "sites").symlink_to(elsewhere)

    code = rig.run()

    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert code == 1
    assert captured.out == ""
    assert lines and all(line.startswith("scratch-pad: publish: ") for line in lines)
    assert "symlink" in lines[0]
    assert list(elsewhere.iterdir()) == []
    assert rig.leftovers() == []


def test_the_publishers_stderr_lines_are_relayed_escaped_one_by_one(rig, capsys):
    def failing(cmd, env, timeout):
        return subprocess.CompletedProcess(cmd, 1, stdout="ignored on failure\n", stderr="error: one\n\x1b[31mtwo\n")

    assert rig.run(publish_runner=failing) == 1

    captured = capsys.readouterr()
    assert captured.err.splitlines() == ["scratch-pad: publish: error: one", "scratch-pad: publish: \\x1b[31mtwo"]
    assert captured.out == ""


def test_a_publisher_that_raises_leaves_no_temp_folder_and_no_site(rig):
    def boom(cmd, env, timeout):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        rig.run(publish_runner=boom)

    assert rig.leftovers() == []
    assert not (rig.ws / "sites").exists()


def test_a_publisher_that_cannot_be_started_is_refused_under_publish_not_as_a_site_build_failure(rig, capsys):
    def missing(cmd, env, timeout):
        raise FileNotFoundError(2, "No such file or directory")

    assert rig.run(publish_runner=missing) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "scratch-pad: publish: could not start the publisher: No such file or directory"
    ]
    assert rig.leftovers() == []
    assert not (rig.ws / "sites").exists()


def test_a_failure_filling_the_temp_folder_is_a_refusal_and_leaves_nothing(rig, capsys, monkeypatch):
    def full_disk(folder, kit_dir, round_js):
        (folder / "half-written").write_text("x")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(rig.pad, "_write_pad", full_disk)
    called = []

    def runner(cmd, env, timeout):
        called.append(cmd)

    assert rig.run(publish_runner=runner) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert captured.err.startswith("scratch-pad: ")
    assert "could not build the pad: No space left on device" in captured.err
    assert called == []
    assert rig.leftovers() == []
    assert not (rig.ws / "sites").exists()


def test_a_temp_folder_that_cannot_be_created_is_a_refusal(rig, capsys, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(rig.tmp_path / "no-such-tmp"))

    assert rig.run() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("scratch-pad: ")
    assert "could not build the pad" in captured.err
    assert not (rig.ws / "sites").exists()


def test_a_working_directory_that_cannot_be_read_counts_as_outside_any_workspace(pad, tmp_path, monkeypatch, capsys):
    gone = tmp_path / "gone"
    gone.mkdir()
    keep = Path.cwd()
    monkeypatch.chdir(gone)
    try:
        gone.rmdir()
        env = {
            "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
            "CAMP_CONFIG_DIR": str(tmp_path / "camp-config"),
            "HOME": str(tmp_path / "home"),
            "OUTPOST_STATE_DIR": str(tmp_path / "outpost-state"),
        }
        code = pad.main(["--pad", "p", "--round", str(_round_file(tmp_path))], env=env)
    finally:
        os.chdir(keep)

    out = capsys.readouterr().out.splitlines()
    pad_dir = (tmp_path / "outpost-state" / "pads" / "p").resolve()
    assert code == 0
    assert out[-1] == f"pad folder: {pad_dir}"
    assert (pad_dir / "index.html").is_file()


def test_a_publisher_that_outlives_the_timeout_after_the_site_landed_is_reported_unconfirmed(rig, capsys):
    seen = {}

    def lands_then_hangs(cmd, env, timeout):
        seen["timeout"] = timeout
        site = rig.ws / "sites" / "p"
        site.mkdir(parents=True)
        (site / "index.html").write_text("x")
        raise subprocess.TimeoutExpired(cmd, timeout)

    code = rig.run(publish_runner=lands_then_hangs, publish_timeout=7)

    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert seen["timeout"] == 7
    assert out[0].startswith("note: the pad was published but the publisher did not confirm")
    assert out[-1] == f"pad folder: {rig.ws / 'sites' / 'p'}"
    assert rig.leftovers() == []


def test_a_publisher_that_outlives_the_timeout_before_anything_landed_is_a_refusal(rig, capsys):
    def hangs(cmd, env, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    code = rig.run(publish_runner=hangs, publish_timeout=7)

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.splitlines()[0].startswith("scratch-pad: publish: ")
    assert "7" in captured.err
    assert rig.leftovers() == []


# Lands nothing, so the outcome does not hang on how fast the interpreter starts under
# load: the run ends at the timeout whatever the machine's speed. The landed-before-timeout
# branch is pinned with an injected runner above.
STUB_PUBLISHER = (
    "import time\n"
    "time.sleep(10)\n"
    "print('http://stub/')\n"
)


def _skill_tree(tmp_path: Path, publisher: str | None) -> Path:
    tree = tmp_path / "skills"
    skill = tree / "scratch-pad"
    skill.mkdir(parents=True)
    shutil.copy(SCRIPT_PATH, skill / "scratch_pad.py")
    _kit(skill / "kit")
    if publisher is not None:
        (tree / "publish-site").mkdir()
        (tree / "publish-site" / "publish_site.py").write_text(publisher)
    return skill / "scratch_pad.py"


def test_the_default_runner_enforces_the_wall_clock_timeout(rig, capsys):
    script = _skill_tree(rig.tmp_path, STUB_PUBLISHER)
    mod = _load("scratch_pad_stubbed", script)
    started = time.monotonic()

    code = mod.main(rig.argv(), env=rig.env, cwd=rig.member, publish_timeout=0.5)

    captured = capsys.readouterr()
    assert time.monotonic() - started < 5
    assert code == 1
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "scratch-pad: publish: the publisher did not finish within 0.5 seconds and no pad was published"
    ]
    assert rig.leftovers() == []


def test_without_the_publish_site_skill_a_run_in_a_workspace_is_refused_and_writes_nothing(rig, capsys):
    script = _skill_tree(rig.tmp_path, None)
    mod = _load("scratch_pad_alone", script)

    code = mod.main(rig.argv(), env=rig.env, cwd=rig.member, publish_runner=lambda *a: pytest.fail("publisher ran"))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1 and "publish-site skill is missing" in captured.err
    assert not (rig.ws / "sites").exists()
    assert not rig.outpost_state.exists()
    assert rig.leftovers() == []


def test_without_the_publish_site_skill_the_disk_path_is_unchanged(rig, capsys):
    script = _skill_tree(rig.tmp_path, None)
    mod = _load("scratch_pad_alone_disk", script)

    assert mod.main(rig.argv(), env=rig.env, cwd=rig.outside) == 0
    assert (rig.outpost_state / "pads" / "p" / "index.html").is_file()


def test_the_publisher_gets_a_private_prefixed_build_outside_the_workspace(rig):
    seen = {}

    def inspect(cmd, env, timeout):
        source = Path(cmd[2])
        seen["cmd"] = cmd
        seen["env"] = env
        seen["name"] = source.name
        seen["parent"] = source.parent
        seen["mode"] = stat.S_IMODE(source.stat().st_mode)
        seen["index"] = (source / "index.html").is_file()
        seen["round"] = _round_js_data(source / "round-1" / "round.js")
        seen["kit"] = (source / "round-1" / "index.html").read_bytes()
        return subprocess.CompletedProcess(cmd, 0, stdout="link\n", stderr="")

    assert rig.run(publish_runner=inspect) == 0

    publisher = SKILL_DIR.parent / "publish-site" / "publish_site.py"
    source = str(seen["parent"] / seen["name"])
    assert seen["cmd"] == [sys.executable, str(publisher), source, "p", "--workspace-path", str(rig.ws)]
    assert seen["env"]["CAMP_STATE_DIR"] == str(rig.root)
    assert seen["name"].startswith("scratch-pad-")
    assert seen["parent"] == rig.tmpdir
    assert rig.ws not in seen["parent"].parents
    assert seen["mode"] == 0o700
    assert seen["index"] and seen["round"] == _round()
    assert seen["kit"] == (SKILL_DIR / "kit" / "index.html").read_bytes()
    assert rig.leftovers() == []


UNDECODABLE_OK = (
    "import sys\n"
    "sys.stdout.buffer.write(b'http://127.0.0.1:7314/x/\\xff/\\n')\n"
)
UNDECODABLE_FAIL = (
    "import sys\n"
    "sys.stderr.buffer.write(b'error: bad \\xff byte\\n')\n"
    "sys.exit(3)\n"
)


def test_a_publisher_line_with_undecodable_bytes_is_relayed_escaped_without_a_traceback(rig, capsys):
    mod = _load("scratch_pad_badbytes_ok", _skill_tree(rig.tmp_path, UNDECODABLE_OK))

    code = mod.main(rig.argv(), env=rig.env, cwd=rig.member)

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.splitlines()[0] == "http://127.0.0.1:7314/x/\\xff/"
    assert "Traceback" not in captured.err
    assert rig.leftovers() == []


def test_a_publisher_failure_with_undecodable_stderr_is_refused_cleanly(rig, capsys):
    mod = _load("scratch_pad_badbytes_fail", _skill_tree(rig.tmp_path, UNDECODABLE_FAIL))

    code = mod.main(rig.argv(), env=rig.env, cwd=rig.member)

    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.splitlines() == ["scratch-pad: publish: error: bad \\\\xff byte"]
    assert rig.leftovers() == []


# --- adding a round to a pad published in a workspace ------------------------------------------------


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _site_titles(site: Path) -> list[str]:
    return [r["title"] for r in _decode_literal((site / "rounds.js").read_text(encoding="utf-8"), ROUNDS_JS_PREFIX)]


def _lands(rig, cmd):
    """Stand in for the publisher: the site becomes the payload."""
    site = rig.ws / "sites" / cmd[3]
    shutil.rmtree(site)
    shutil.copytree(cmd[2], site)


def _lands_ok(rig, link="http://stub/\n"):
    def runner(cmd, env, timeout):
        _lands(rig, cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=link, stderr="")

    return runner


def _published(rig, capsys, name="p"):
    assert rig.run(name) == 0
    return capsys.readouterr().out.splitlines()


def test_adding_a_round_through_the_real_publisher_keeps_round_one_and_relays_its_lines(rig, capsys):
    first_out = _published(rig, capsys)
    site = rig.ws / "sites" / "p"
    before = _file_hashes(site)

    code = rig.add("Pricing")

    out = capsys.readouterr().out.splitlines()
    after = _file_hashes(site)
    assert code == 0
    assert {k: v for k, v in after.items() if k.startswith("round-1/")} == {
        k: v for k, v in before.items() if k.startswith("round-1/")
    }
    assert "round-2/index.html" in after and "round-2/round.js" in after
    assert after["rounds.js"] != before["rounds.js"]
    assert after["index.html"] != before["index.html"]
    assert _site_titles(site) == ["Settings", "Pricing"]
    assert "round-2/" in (site / "index.html").read_text(encoding="utf-8")
    assert _round_js_data(site / "round-2" / "round.js")["title"] == "Pricing"
    assert out[:-1] == first_out[:-1]
    assert out[-1] == f"pad folder: {site}"
    assert rig.leftovers() == []


def _legacy_pad(site: Path) -> None:
    folder = site / "round-1"
    folder.mkdir(parents=True)
    for name, body in KIT_FILES.items():
        (folder / name).write_bytes(body)
    literal = json.dumps(json.dumps(_round()))
    (folder / "round.js").write_text(ROUND_JS_PREFIX + literal + ");\n", encoding="utf-8")
    (site / "index.html").write_text('<meta http-equiv="refresh" content="0; url=round-1/index.html">\n')


def test_a_workspace_pad_built_before_rounds_js_existed_takes_a_second_round(rig, capsys):
    site = rig.ws / "sites" / "p"
    _legacy_pad(site)
    before = _file_hashes(site / "round-1")

    code = rig.add("Pricing")

    assert code == 0, capsys.readouterr().err
    assert _file_hashes(site / "round-1") == before
    assert _site_titles(site) == ["Settings", "Pricing"]
    assert rig.leftovers() == []


def test_adding_from_deep_inside_a_member_folder_gives_the_same_result(rig, capsys):
    _published(rig, capsys)
    deep = rig.member / "a" / "b" / "c"
    deep.mkdir(parents=True)
    path = _round_file(rig.tmp_path, {**_round(), "title": "Pricing"}, "deep.json")

    code = rig.run("p", path, cwd=deep, add=True)

    assert code == 0, capsys.readouterr().err
    assert _site_titles(rig.ws / "sites" / "p") == ["Settings", "Pricing"]
    assert rig.leftovers() == []


def test_the_publisher_gets_overwrite_only_when_a_round_is_added(rig, capsys):
    seen = []

    def spy(cmd, env, timeout):
        seen.append(
            (list(cmd), stat.S_IMODE(Path(cmd[2]).stat().st_mode), Path(cmd[2]).parent, sorted(os.listdir(cmd[2])))
        )
        if (rig.ws / "sites" / "p").exists():
            _lands(rig, cmd)
        else:
            shutil.copytree(cmd[2], rig.ws / "sites" / "p")
        return subprocess.CompletedProcess(cmd, 0, stdout="link\n", stderr="")

    assert rig.run(publish_runner=spy) == 0
    assert rig.add("Pricing", publish_runner=spy) == 0

    publisher = str(SKILL_DIR.parent / "publish-site" / "publish_site.py")
    new_cmd, add_cmd = seen[0][0], seen[1][0]
    assert "--overwrite" not in new_cmd
    assert add_cmd == [sys.executable, publisher, add_cmd[2], "p", "--workspace-path", str(rig.ws), "--overwrite"]
    assert Path(add_cmd[2]).name.startswith("scratch-pad-")
    assert seen[1][1] == 0o700 and seen[1][2] == rig.tmpdir
    assert seen[1][3] == ["index.html", "round-1", "round-2", "rounds.js"]
    assert rig.leftovers() == []


def test_adding_to_a_pad_that_is_not_in_the_workspace_is_refused_and_changes_nothing(rig, capsys):
    (rig.ws / "sites" / "other").mkdir(parents=True)
    (rig.ws / "sites" / "other" / "index.html").write_text("x")
    before = _snapshot(rig.ws / "sites")

    code = rig.add("Pricing", publish_runner=lambda *a: pytest.fail("publisher ran"))

    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.splitlines() == ['scratch-pad: pad "p": does not exist — drop --add to start a new pad']
    assert _snapshot(rig.ws / "sites") == before
    assert rig.leftovers() == []


def test_adding_to_a_site_that_is_not_a_pad_is_refused_and_changes_nothing(rig, capsys):
    (rig.ws / "sites" / "p").mkdir(parents=True)
    (rig.ws / "sites" / "p" / "index.html").write_text("a plain site")
    before = _snapshot(rig.ws / "sites")

    code = rig.add("Pricing", publish_runner=lambda *a: pytest.fail("publisher ran"))

    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert "can't take another round" in captured.err and "round-1" in captured.err
    assert _snapshot(rig.ws / "sites") == before
    assert rig.leftovers() == []


def test_a_title_the_pad_already_has_is_refused_before_the_publisher_runs(rig, capsys):
    _published(rig, capsys)
    before = _snapshot(rig.ws / "sites")

    code = rig.add("Settings", publish_runner=lambda *a: pytest.fail("publisher ran"))

    assert code == 1
    assert "give each round its own title" in capsys.readouterr().err
    assert _snapshot(rig.ws / "sites") == before
    assert rig.leftovers() == []


def test_a_publisher_failure_on_an_add_is_relayed_and_leaves_the_site_and_no_temp_folder(rig, capsys):
    _published(rig, capsys)
    before = _snapshot(rig.ws / "sites")

    def refuses(cmd, env, timeout):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: nope\n\nsecond line\n")

    code = rig.add("Pricing", publish_runner=refuses)

    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.splitlines() == ["scratch-pad: publish: error: nope", "scratch-pad: publish: second line"]
    assert _snapshot(rig.ws / "sites") == before
    assert rig.leftovers() == []


def test_a_publisher_that_cannot_be_started_on_an_add_is_refused_under_publish(rig, capsys):
    _published(rig, capsys)
    before = _snapshot(rig.ws / "sites")

    def cannot_start(cmd, env, timeout):
        raise FileNotFoundError(2, "No such file or directory")

    code = rig.add("Pricing", publish_runner=cannot_start)

    assert code == 1
    assert capsys.readouterr().err.splitlines() == [
        "scratch-pad: publish: could not start the publisher: No such file or directory"
    ]
    assert _snapshot(rig.ws / "sites") == before
    assert rig.leftovers() == []


def test_without_the_publish_site_skill_an_add_in_a_workspace_is_refused_and_writes_nothing(rig, capsys):
    _published(rig, capsys)
    before = _snapshot(rig.ws / "sites")
    mod = _load("scratch_pad_alone_add", _skill_tree(rig.tmp_path, None))
    path = _round_file(rig.tmp_path, {**_round(), "title": "Pricing"}, "x.json")

    code = mod.main(
        rig.argv("p", path, add=True), env=rig.env, cwd=rig.member, publish_runner=lambda *a: pytest.fail("ran")
    )

    err = capsys.readouterr().err
    assert code == 1 and "publish-site skill is missing" in err
    assert _snapshot(rig.ws / "sites") == before
    assert rig.leftovers() == []


def _timeout_with(rig, capsys, outcome):
    """Publish round one, then add a round whose publisher times out after *outcome* lands on disk."""
    _published(rig, capsys)
    site = rig.ws / "sites" / "p"

    def hangs_after(cmd, env, timeout):
        outcome(cmd, site)
        raise subprocess.TimeoutExpired(cmd, timeout)

    code = rig.add("Pricing", publish_runner=hangs_after, publish_timeout=7)
    return code, capsys.readouterr(), site


def test_a_timeout_with_the_whole_pad_and_exactly_one_more_round_is_reported_unconfirmed(rig, capsys):
    code, captured, site = _timeout_with(rig, capsys, lambda cmd, site: _lands(rig, cmd))

    assert code == 0
    assert captured.out.splitlines()[0].startswith("note: the pad was published but the publisher did not confirm")
    assert captured.out.splitlines()[-1] == f"pad folder: {site}"
    assert _site_titles(site) == ["Settings", "Pricing"]
    assert rig.leftovers() == []


def test_a_timeout_with_the_site_unchanged_is_a_refusal(rig, capsys):
    code, captured, site = _timeout_with(rig, capsys, lambda cmd, site: None)

    assert code == 1 and captured.out == ""
    assert captured.err.splitlines() == [
        "scratch-pad: publish: the publisher did not finish within 7 seconds and the round was not added"
    ]
    assert _site_titles(site) == ["Settings"]
    assert rig.leftovers() == []


def test_a_timeout_with_a_pad_of_the_wrong_round_count_is_a_refusal(rig, capsys):
    def two_more(cmd, site):
        _lands(rig, cmd)
        shutil.copytree(site / "round-2", site / "round-3")

    code, captured, site = _timeout_with(rig, capsys, two_more)

    assert code == 1 and captured.out == ""
    assert captured.err.splitlines() == [
        "scratch-pad: publish: the publisher did not finish within 7 seconds and the pad is not as expected"
    ]
    assert rig.leftovers() == []


def test_a_timeout_with_a_pad_that_is_no_longer_whole_is_a_refusal(rig, capsys):
    def half(cmd, site):
        _lands(rig, cmd)
        (site / "round-1" / "index.html").unlink()

    code, captured, site = _timeout_with(rig, capsys, half)

    assert code == 1 and captured.out == ""
    assert "can't take another round" in captured.err and "round-1/index.html is missing" in captured.err
    assert rig.leftovers() == []


def test_a_timeout_with_the_pad_gone_is_a_refusal(rig, capsys):
    code, captured, site = _timeout_with(rig, capsys, lambda cmd, site: shutil.rmtree(site))

    assert code == 1 and captured.out == ""
    assert captured.err.splitlines() == [
        "scratch-pad: publish: the publisher did not finish within 7 seconds and the pad is no longer there"
    ]
    assert rig.leftovers() == []


def test_a_timeout_with_a_pad_missing_its_top_level_files_is_a_refusal(rig, capsys):
    def no_listing(cmd, site):
        _lands(rig, cmd)
        (site / "rounds.js").unlink()

    code, captured, site = _timeout_with(rig, capsys, no_listing)

    assert code == 1 and captured.out == ""
    assert "scratch-pad: publish: the publisher did not finish within 7 seconds" in captured.err
    assert rig.leftovers() == []


HALF_SWAP_THEN_NEVER_RETURN = (
    "import os, sys, time\n"
    "site, marker = sys.argv[1:3]\n"
    "os.remove(os.path.join(site, 'round-1', 'index.html'))\n"
    "open(marker, 'w').close()\n"
    "time.sleep(300)\n"
)


def test_a_second_add_during_a_publisher_timeout_waits_and_never_reads_mid_swap(rig, capsys):
    _published(rig, capsys)
    site = rig.ws / "sites" / "p"
    marker = rig.tmp_path / "half-swapped"

    def never_returns(cmd, env, timeout):
        script = [sys.executable, "-c", HALF_SWAP_THEN_NEVER_RETURN, str(site), str(marker)]
        try:
            return subprocess.run(script, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            _lands(rig, cmd)
            raise

    codes = {}
    first = threading.Thread(
        target=lambda: codes.__setitem__("first", rig.add("Pricing", publish_runner=never_returns, publish_timeout=2))
    )
    first.start()
    _wait_for(marker.exists, "the publisher to half-swap the site")
    assert (site / "round-1" / "index.html").exists() is False
    second = threading.Thread(
        target=lambda: codes.__setitem__("second", rig.add("Costs", publish_runner=_lands_ok(rig), publish_timeout=2))
    )
    second.start()
    for t in (first, second):
        t.join(timeout=60)
        assert not t.is_alive()

    err = capsys.readouterr().err
    assert codes == {"first": 0, "second": 0}, err
    assert WAITING in err
    assert _site_titles(site) == ["Settings", "Pricing", "Costs"]
    assert (site / "round-1" / "index.html").is_file()
    assert rig.leftovers() == []


def test_two_racing_adds_on_one_workspace_pad_land_as_consecutive_rounds(rig, capsys):
    _published(rig, capsys)
    site = rig.ws / "sites" / "p"
    round_one = _file_hashes(site / "round-1")
    inside, release = threading.Event(), threading.Event()
    real = rig.pad._run_publisher
    calls = []

    def first_parks_inside_the_publisher(cmd, env, timeout):
        calls.append(cmd)
        if len(calls) == 1:
            inside.set()
            assert release.wait(timeout=30)
        return real(cmd, env, timeout)

    codes = []
    threads = [
        threading.Thread(target=lambda t=t: codes.append(rig.add(t, publish_runner=first_parks_inside_the_publisher)))
        for t in ("Pricing", "Costs")
    ]
    threads[0].start()
    assert inside.wait(timeout=15)
    threads[1].start()
    err = []
    _wait_for(lambda: err.append(capsys.readouterr().err) or any(WAITING in e for e in err), "the second add to wait")
    release.set()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive()

    assert codes == [0, 0]
    assert sorted(_site_titles(site)) == ["Costs", "Pricing", "Settings"] and _site_titles(site)[0] == "Settings"
    assert sorted(p.name for p in site.glob("round-*")) == ["round-1", "round-2", "round-3"]
    assert _file_hashes(site / "round-1") == round_one
    assert rig.leftovers() == []


def _lock_file(rig, pad_name):
    digest = hashlib.sha256(str(rig.ws / "sites" / pad_name).encode()).hexdigest()
    return rig.outpost_state / "pad-locks" / f"{digest}.lock"


@pytest.mark.parametrize("add", [False, True])
def test_a_run_on_a_workspace_pad_waits_for_the_pads_lock_and_is_refused_when_it_never_frees(rig, capsys, add):
    if add:
        _published(rig, capsys)
    before = _snapshot(rig.ws / "sites") if add else None
    lock = _lock_file(rig, "p")
    lock.parent.mkdir(parents=True, exist_ok=True)
    held = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        started = time.monotonic()
        if add:
            code = rig.add("Pricing", publish_runner=lambda *a: pytest.fail("publisher ran"), publish_timeout=0.3)
        else:
            code = rig.run(publish_runner=lambda *a: pytest.fail("publisher ran"), publish_timeout=0.3)
        waited = time.monotonic() - started
    finally:
        os.close(held)

    err = capsys.readouterr().err
    assert code == 1 and 0.5 < waited < 10
    assert WAITING in err
    assert 'scratch-pad: another session is adding a round to pad "p"; try again' in err
    if add:
        assert _snapshot(rig.ws / "sites") == before
    else:
        assert not (rig.ws / "sites" / "p").exists()
    assert rig.leftovers() == []


def test_an_add_in_a_workspace_recovers_a_pad_found_aside_by_the_script_itself(rig, capsys):
    _published(rig, capsys)
    sites = rig.ws / "sites"
    (sites / "p").rename(sites / ".aside-p")
    (sites / ".tmp-p-x").mkdir()
    (sites / ".tmp-pad-other").mkdir()

    code = rig.add("Pricing", publish_runner=_lands_ok(rig))

    assert code == 0, capsys.readouterr().err
    assert sorted(p.name for p in sites.iterdir()) == [".tmp-pad-other", "p"]
    assert _site_titles(sites / "p") == ["Settings", "Pricing"]


def test_a_new_workspace_pad_recovers_leftovers_of_its_own_name_only(rig, capsys):
    sites = rig.ws / "sites"
    (sites / ".tmp-p-x").mkdir(parents=True)
    (sites / ".tmp-pad-other").mkdir()

    assert rig.run() == 0

    assert sorted(p.name for p in sites.iterdir()) == [".tmp-pad-other", "p"]


def test_a_timeout_that_leaves_the_old_site_under_the_publishers_previous_name_says_where_it_is(rig, capsys):
    def killed_mid_swap(cmd, site):
        site.rename(site.parent / ".p.previous-abc123")
        (site.parent / ".p.stage-xyz").mkdir()

    code, captured, site = _timeout_with(rig, capsys, killed_mid_swap)

    previous = site.parent / ".p.previous-abc123"
    assert code == 1 and captured.out == ""
    assert captured.err.splitlines() == [
        "scratch-pad: publish: the publisher did not finish within 7 seconds and the pad is no longer there",
        f"scratch-pad: publish: {previous} holds the pad as it was before this add; "
        f"rename it back to {site} to restore it",
    ]
    assert _site_titles(previous) == ["Settings"]
    assert rig.leftovers() == []


@pytest.mark.skipif(os.geteuid() == 0, reason="permissions do not bind root")
def test_an_unlistable_folder_in_a_workspace_pad_refuses_the_add_and_changes_nothing(rig, capsys):
    _published(rig, capsys)
    site = rig.ws / "sites" / "p"
    locked = site / "round-1" / "x"
    locked.mkdir()
    locked.chmod(0)
    try:
        before = sorted(p.name for p in (rig.ws / "sites").iterdir())
        code = rig.add("Pricing", publish_runner=lambda *a: pytest.fail("publisher ran"))
        captured = capsys.readouterr()
        after = sorted(p.name for p in (rig.ws / "sites").iterdir())
    finally:
        locked.chmod(0o700)

    assert code == 1 and captured.out == ""
    assert captured.err.splitlines() == [
        'scratch-pad: pad "p" can\'t take another round: round-1/x can\'t be read: Permission denied — '
        "start a new pad with a different --pad name"
    ]
    assert after == before == ["p"]
    assert rig.leftovers() == []
