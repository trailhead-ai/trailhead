"""Behavioral tests for adding a round to a pad on disk (``scratch_pad.py --add``).

Every run is fenced to ``tmp_path``: the state dirs and HOME are staged under it, the working directory
is a folder outside any camp workspace, and the kit is a small fixture copy beside the script.
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "plugins" / "outpost" / "skills" / "scratch-pad" / "scratch_pad.py"
ROUND_JS_PREFIX = "window.PAD_ROUND = JSON.parse("
ROUNDS_JS_PREFIX = "window.PAD_ROUNDS = JSON.parse("
WAITING = "waiting for another session's add"
WAY_ON = "start a new pad with a different --pad name"
MOVE_OUT = "move it out of the pad folder and rerun --add"


def _load_module():
    spec = importlib.util.spec_from_file_location("scratch_pad_rounds_under_test", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_kit(kit: Path, marker: str = "one") -> Path:
    kit.mkdir(parents=True, exist_ok=True)
    (kit / "index.html").write_text(f"<!doctype html><title>kit {marker}</title>\n", encoding="utf-8")
    (kit / "pad.js").write_bytes(b"// pad " + marker.encode() + b"\n\xff\x00binary-ish\n")
    return kit


def _round(title: str, n_questions: int = 1) -> dict:
    return {
        "title": title,
        "questions": [
            {
                "prompt": f"Q{i}",
                "options": [
                    {"name": "A", "cost": "c", "pick": True, "layout": {"rule": True}},
                    {"name": "B", "cost": "c", "layout": {"rule": False}},
                ],
            }
            for i in range(n_questions)
        ],
    }


@pytest.fixture
def skill(tmp_path):
    d = tmp_path / "skill"
    d.mkdir()
    shutil.copy2(SCRIPT_PATH, d / "scratch_pad.py")
    _write_kit(d / "kit")
    return d


@pytest.fixture
def env(tmp_path):
    work = tmp_path / "cwd"
    work.mkdir()
    return {
        "OUTPOST_STATE_DIR": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        "CAMP_STATE_DIR": str(tmp_path / "camp"),
        "PATH": "/usr/bin:/bin",
    }


@pytest.fixture
def cwd(tmp_path):
    return tmp_path / "cwd"


@pytest.fixture
def pads(env):
    return Path(env["OUTPOST_STATE_DIR"]) / "pads"


def _round_file(tmp_path: Path, data, name: str = "round.json") -> Path:
    p = tmp_path / hashlib.sha1(name.encode()).hexdigest()
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return p


def _argv(skill, pad, round_path, add=False):
    argv = [sys.executable, str(skill / "scratch_pad.py"), f"--pad={pad}", f"--round={round_path}"]
    return [*argv, "--add"] if add else argv


def _cli(skill, env, cwd, pad, round_path, add=False):
    return subprocess.run(_argv(skill, pad, round_path, add), capture_output=True, text=True, env=env, cwd=cwd)


def _new(skill, env, cwd, tmp_path, pad, title):
    proc = _cli(skill, env, cwd, pad, _round_file(tmp_path, _round(title), f"{title}.json"))
    assert proc.returncode == 0, proc.stderr
    return proc


def _add(skill, env, cwd, tmp_path, pad, title):
    proc = _cli(skill, env, cwd, pad, _round_file(tmp_path, _round(title), f"{title}.json"), add=True)
    assert proc.returncode == 0, proc.stderr
    return proc


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _snapshot(root: Path):
    if not os.path.lexists(root):
        return None
    out = {}
    for p in sorted([root, *root.rglob("*")]):
        st = p.lstat()
        if stat.S_ISLNK(st.st_mode):
            body = os.readlink(p)
        elif stat.S_ISREG(st.st_mode):
            body = p.read_bytes()
        else:
            body = None
        out[str(p.relative_to(root))] = (stat.S_IMODE(st.st_mode), body)
    return out


def _leftovers(pads: Path) -> list[str]:
    return sorted(p.name for p in pads.iterdir() if p.name.startswith((".tmp-", ".aside-")))


def _decode_literal(line: str, prefix: str):
    line = line.rstrip("\n")
    assert line.startswith(prefix) and line.endswith(");")
    return json.loads(json.loads(line[len(prefix) : -2]))


def _titles(pad_dir: Path) -> list[str]:
    listing = _decode_literal((pad_dir / "rounds.js").read_text(encoding="utf-8"), ROUNDS_JS_PREFIX)
    return [r["title"] for r in listing]


def _lock_path(env, pad_dir: Path) -> Path:
    digest = hashlib.sha256(str(pad_dir.resolve()).encode()).hexdigest()
    return Path(env["OUTPOST_STATE_DIR"]) / "pad-locks" / f"{digest}.lock"


def _hold(lock: Path) -> int:
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _inproc(mod, argv, env, cwd, **kw):
    return mod.main(argv, env=env, cwd=cwd, **kw)


# --- the happy path ---------------------------------------------------------------------------------


def test_adding_rounds_two_and_three_keeps_every_earlier_round_byte_for_byte(skill, env, cwd, tmp_path, pads):
    first = _new(skill, env, cwd, tmp_path, "p", "One")
    pad_dir = pads / "p"
    r1 = _hashes(pad_dir / "round-1")

    second = _add(skill, env, cwd, tmp_path, "p", "Two")
    r2 = _hashes(pad_dir / "round-2")
    assert _hashes(pad_dir / "round-1") == r1
    third = _add(skill, env, cwd, tmp_path, "p", "Three")

    assert _hashes(pad_dir / "round-1") == r1
    assert _hashes(pad_dir / "round-2") == r2
    assert _titles(pad_dir) == ["One", "Two", "Three"]
    assert "round-3/" in (pad_dir / "index.html").read_text(encoding="utf-8")
    assert "round-1/" not in (pad_dir / "index.html").read_text(encoding="utf-8")
    assert first.stdout.splitlines()[0] == second.stdout.splitlines()[0] == third.stdout.splitlines()[0]
    assert third.stdout.splitlines()[0] == (pad_dir / "index.html").as_uri()
    assert third.stdout.splitlines()[1] == f"pad folder: {pad_dir}"
    assert _leftovers(pads) == []


def test_each_round_is_numbered_by_its_own_folder(skill, env, cwd, tmp_path, pads):
    _new(skill, env, cwd, tmp_path, "p", "One")
    _add(skill, env, cwd, tmp_path, "p", "Two")
    for k in (1, 2):
        lines = (pads / "p" / f"round-{k}" / "round.js").read_text(encoding="utf-8").splitlines()
        assert lines[1] == f"window.PAD_ROUND_NUMBER = {k};"
        assert _decode_literal(lines[0], ROUND_JS_PREFIX)["title"] == ["One", "Two"][k - 1]


def test_an_earlier_round_keeps_its_own_kit_and_the_new_round_gets_the_current_one(
    skill, env, cwd, tmp_path, pads
):
    _new(skill, env, cwd, tmp_path, "p", "One")
    before = _hashes(pads / "p" / "round-1")
    _write_kit(skill / "kit", marker="changed")

    _add(skill, env, cwd, tmp_path, "p", "Two")

    assert _hashes(pads / "p" / "round-1") == before
    assert "kit one" in (pads / "p" / "round-1" / "index.html").read_text(encoding="utf-8")
    assert "kit changed" in (pads / "p" / "round-2" / "index.html").read_text(encoding="utf-8")


def _legacy_pad(pads: Path, title: str = "Old") -> Path:
    pad_dir = pads / "old"
    (pad_dir / "round-1").mkdir(parents=True)
    (pad_dir / "round-1" / "index.html").write_text("<!doctype html><title>old kit</title>\n")
    literal = json.dumps(json.dumps(_round(title)))
    (pad_dir / "round-1" / "round.js").write_text(ROUND_JS_PREFIX + literal + ");\n")
    (pad_dir / "index.html").write_text('<meta http-equiv="refresh" content="0; url=round-1/index.html">\n')
    return pad_dir


def test_a_pad_built_before_rounds_js_existed_takes_a_second_round(skill, env, cwd, tmp_path, pads):
    pad_dir = _legacy_pad(pads)
    before = _hashes(pad_dir / "round-1")

    _add(skill, env, cwd, tmp_path, "old", "New")

    assert _hashes(pad_dir / "round-1") == before
    assert _titles(pad_dir) == ["Old", "New"]
    assert (pad_dir / "round-2" / "round.js").read_text().splitlines()[1] == "window.PAD_ROUND_NUMBER = 2;"


@pytest.mark.parametrize("title", ['Say "hi"); alert(1); (" é', "line sep \\ back", "</script><b>"])
def test_titles_come_back_as_decoded_data_whatever_they_contain(skill, env, cwd, tmp_path, pads, title):
    _new(skill, env, cwd, tmp_path, "p", title)
    _add(skill, env, cwd, tmp_path, "p", "Second")
    assert _titles(pads / "p") == [title, "Second"]


def test_ten_and_more_rounds_keep_their_numeric_order(tmp_path, env, cwd, pads):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    first = _round_file(tmp_path, _round("R1"), "r1.json")
    mod.build_pad(first, "p", kit_dir=kit, env=env, cwd=cwd)
    for k in range(2, 12):
        rp = _round_file(tmp_path, _round(f"R{k}"), f"r{k}.json")
        assert _inproc(mod, ["--pad", "p", "--round", str(rp), "--add"], env, cwd) == 0
    assert _titles(pads / "p") == [f"R{k}" for k in range(1, 12)]


def test_without_add_a_pad_built_by_add_is_still_refused_as_existing(skill, env, cwd, tmp_path, pads):
    _new(skill, env, cwd, tmp_path, "p", "One")
    _add(skill, env, cwd, tmp_path, "p", "Two")
    before = _snapshot(pads / "p")

    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round("Three")))

    assert proc.returncode == 1
    assert "already exists" in proc.stderr
    assert _snapshot(pads / "p") == before


# --- refusals ---------------------------------------------------------------------------------------


def _built_pad(skill, env, cwd, tmp_path, pads, rounds=2):
    _new(skill, env, cwd, tmp_path, "p", "One")
    for k in range(2, rounds + 1):
        _add(skill, env, cwd, tmp_path, "p", f"Title{k}")
    return pads / "p"


def _gap(pad_dir):
    shutil.rmtree(pad_dir / "round-2")


def _no_round_js(pad_dir):
    (pad_dir / "round-2" / "round.js").unlink()


def _no_prefix(pad_dir):
    (pad_dir / "round-2" / "round.js").write_text("alert(1);\n")


def _no_index(pad_dir):
    (pad_dir / "round-2" / "index.html").unlink()


def _foreign_prefix(pad_dir):
    # same length as the real prefix, and the rest decodes: only the prefix itself is wrong
    round_js = pad_dir / "round-2" / "round.js"
    round_js.write_text(round_js.read_text().replace("window.PAD_ROUND =", "window.PAD_ROUNX =", 1))


def _undecodable(pad_dir):
    (pad_dir / "round-2" / "round.js").write_text(ROUND_JS_PREFIX + "not a literal);\n")


def _symlink_inside(pad_dir):
    (pad_dir / "round-1" / "link").symlink_to("index.html")


def _fifo_inside(pad_dir):
    os.mkfifo(pad_dir / "round-1" / "fifo")


@pytest.mark.parametrize(
    "mutate, rounds, fragment",
    [
        (_gap, 3, "round-2"),
        (_no_round_js, 2, "round-2/round.js"),
        (_no_prefix, 2, "round-2/round.js"),
        (_foreign_prefix, 2, "round-2/round.js"),
        (_no_index, 2, "round-2/index.html"),
        (_undecodable, 2, "round-2/round.js"),
        (_symlink_inside, 2, "round-1/link"),
        (_fifo_inside, 2, "round-1/fifo"),
    ],
    ids=["gap", "no-round-js", "no-prefix", "foreign-prefix", "no-index", "undecodable", "symlink", "fifo"],
)
def test_a_pad_that_cannot_take_a_round_is_refused_and_left_exactly_as_it_was(
    skill, env, cwd, tmp_path, pads, mutate, rounds, fragment
):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, rounds)
    mutate(pad_dir)
    before = _snapshot(pad_dir)

    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round("Next"), "next.json"), add=True)

    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    err = proc.stderr.strip().splitlines()
    assert len(err) == 1 and err[0].startswith('scratch-pad: pad "p" can\'t take another round:')
    assert fragment in err[0]
    assert err[0].endswith(f"— {WAY_ON}")
    assert _snapshot(pad_dir) == before
    assert _leftovers(pads) == []


def test_every_problem_in_the_pad_is_named_in_the_one_refusal(skill, env, cwd, tmp_path, pads):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 2)
    _no_prefix(pad_dir)
    _symlink_inside(pad_dir)

    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round("Next")), add=True)

    assert "round-2/round.js" in proc.stderr and "round-1/link" in proc.stderr


def test_a_pad_folder_that_is_a_symlink_is_refused(skill, env, cwd, tmp_path, pads):
    real = tmp_path / "elsewhere"
    real.mkdir()
    pads.mkdir(parents=True)
    (pads / "p").symlink_to(real)

    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round("Next")), add=True)

    assert proc.returncode == 1
    assert "the pad folder is a symlink" in proc.stderr and proc.stderr.strip().endswith(WAY_ON)
    assert list(real.iterdir()) == []
    assert _leftovers(pads) == []


def test_adding_to_a_missing_pad_says_to_drop_add(skill, env, cwd, tmp_path, pads):
    proc = _cli(skill, env, cwd, "nope", _round_file(tmp_path, _round("Next")), add=True)

    assert proc.returncode == 1
    assert 'scratch-pad: pad "nope": does not exist — drop --add to start a new pad' in proc.stderr
    assert not (pads / "nope").exists()
    assert _leftovers(pads) == []


def test_a_badly_shaped_round_is_refused_before_the_pad_is_read(skill, env, cwd, tmp_path, pads):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 2)
    _gap(pad_dir)  # an unreadable pad: if it were read first, the refusal would be about the pad
    before = _snapshot(pad_dir)
    bad = _round("Next")
    bad["questions"][0]["options"][1]["pick"] = True

    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, bad), add=True)

    assert proc.returncode == 1
    assert "pick" in proc.stderr and "can't take another round" not in proc.stderr
    assert _snapshot(pad_dir) == before
    assert _leftovers(pads) == []


def test_a_title_equal_to_an_earlier_rounds_is_refused(skill, env, cwd, tmp_path, pads):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 2)
    before = _snapshot(pad_dir)

    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round("One")), add=True)

    assert proc.returncode == 1
    assert 'scratch-pad: title: "One" is the title of round 1 — give each round its own title' in proc.stderr
    assert _snapshot(pad_dir) == before
    assert _leftovers(pads) == []


def test_a_title_that_differs_from_every_earlier_one_is_accepted(skill, env, cwd, tmp_path, pads):
    _built_pad(skill, env, cwd, tmp_path, pads, 2)
    _add(skill, env, cwd, tmp_path, "p", "one")
    assert _titles(pads / "p") == ["One", "Title2", "one"]


def test_a_symlinked_pad_locks_folder_is_refused_and_nothing_changes(skill, env, cwd, tmp_path, pads):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 1)
    state = Path(env["OUTPOST_STATE_DIR"])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(state / "pad-locks")
    (state / "pad-locks").symlink_to(elsewhere)
    before = _snapshot(pad_dir)

    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round("Next")), add=True)

    assert proc.returncode == 1
    assert "the pad-locks folder is a symlink" in proc.stderr
    assert list(elsewhere.iterdir()) == []
    assert _snapshot(pad_dir) == before


def test_a_symlinked_lock_file_is_refused_not_followed(skill, env, cwd, tmp_path, pads):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 1)
    victim = tmp_path / "victim"
    victim.write_text("keep")
    lock = _lock_path(env, pad_dir)
    lock.unlink()
    lock.symlink_to(victim)
    before = _snapshot(pad_dir)

    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round("Next")), add=True)

    assert proc.returncode == 1
    assert "lock" in proc.stderr and "Traceback" not in proc.stderr
    assert victim.read_text() == "keep"
    assert _snapshot(pad_dir) == before


@pytest.mark.parametrize("umask", [0o000, 0o022])
def test_pad_locks_folder_and_lock_file_are_owner_only(tmp_path, env, cwd, pads, umask):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    old = os.umask(umask)
    try:
        mod.build_pad(_round_file(tmp_path, _round("One")), "p", kit_dir=kit, env=env, cwd=cwd)
    finally:
        os.umask(old)

    locks = Path(env["OUTPOST_STATE_DIR"]) / "pad-locks"
    assert stat.S_IMODE(locks.stat().st_mode) == 0o700
    files = list(locks.iterdir())
    assert len(files) == 1 and files[0].suffix == ".lock"
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600


def test_the_lock_is_named_for_the_pads_resolved_path_so_an_alias_shares_it(skill, env, cwd, tmp_path, pads):
    _new(skill, env, cwd, tmp_path, "p", "One")
    alias = tmp_path / "state-alias"
    alias.symlink_to(env["OUTPOST_STATE_DIR"])
    via_alias = {**env, "OUTPOST_STATE_DIR": str(alias)}

    _add(skill, via_alias, cwd, tmp_path, "p", "Two")

    expected = hashlib.sha256(str((pads / "p").resolve()).encode()).hexdigest() + ".lock"
    assert [p.name for p in (Path(env["OUTPOST_STATE_DIR"]) / "pad-locks").iterdir()] == [expected]


def test_a_missing_round_js_is_named_as_missing(skill, env, cwd, tmp_path, pads):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 2)
    _no_round_js(pad_dir)

    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round("Next")), add=True)

    assert "round-2/round.js is missing" in proc.stderr


def test_a_special_file_where_round_js_belongs_is_refused_without_being_opened(skill, env, cwd, tmp_path, pads):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 2)
    (pad_dir / "round-2" / "round.js").unlink()
    os.mkfifo(pad_dir / "round-2" / "round.js")
    argv = _argv(skill, "p", _round_file(tmp_path, _round("Next")), add=True)

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=cwd, timeout=20)
    except subprocess.TimeoutExpired:
        pytest.fail("the add hung opening a special file instead of refusing the pad")

    assert proc.returncode == 1
    assert "round-2/round.js is not a regular file or folder" in proc.stderr
    assert _leftovers(pads) == []


# --- the copy and the swap --------------------------------------------------------------------------


def test_a_symlink_planted_after_the_read_back_is_not_followed_and_the_add_is_refused(
    tmp_path, env, cwd, pads, monkeypatch, capsys
):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    mod.build_pad(_round_file(tmp_path, _round("One")), "p", kit_dir=kit, env=env, cwd=cwd)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    real_read = mod._read_pad

    def read_then_plant(final, pad):
        titles = real_read(final, pad)
        (final / "round-1" / "planted").symlink_to(secret)
        return titles

    monkeypatch.setattr(mod, "_read_pad", read_then_plant)
    pad_dir = pads / "p"
    rp = _round_file(tmp_path, _round("Two"), "two.json")

    code = _inproc(mod, ["--pad", "p", "--round", str(rp), "--add"], env, cwd)

    assert code == 1
    assert "symlink" in capsys.readouterr().err
    assert not (pad_dir / "round-2").exists()
    assert os.path.islink(pad_dir / "round-1" / "planted")
    assert _leftovers(pads) == []


def test_copying_a_round_keeps_a_symlink_as_a_symlink(tmp_path):
    mod = _load_module()
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_bytes(b"data\x00")
    (src / "sub" / "link").symlink_to(tmp_path / "outside.txt")
    (tmp_path / "outside.txt").write_text("outside")

    mod._copy_round(src, tmp_path / "dst")

    assert (tmp_path / "dst" / "a.txt").read_bytes() == b"data\x00"
    assert os.readlink(tmp_path / "dst" / "sub" / "link") == str(tmp_path / "outside.txt")


def test_copying_a_round_refuses_a_special_file_without_opening_it(tmp_path):
    mod = _load_module()
    src = tmp_path / "src"
    src.mkdir()
    os.mkfifo(src / "fifo")

    with pytest.raises(mod.Refusal) as exc:
        mod._copy_round(src, tmp_path / "dst")

    assert "fifo" in exc.value.lines[0]


def _adder(tmp_path, env, cwd, monkeypatch=None):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    mod.build_pad(_round_file(tmp_path, _round("One")), "p", kit_dir=kit, env=env, cwd=cwd)
    rp = _round_file(tmp_path, _round("Two"), "two.json")
    return mod, ["--pad", "p", "--round", str(rp), "--add"]


def _fail_second_rename(mod, monkeypatch, exc):
    real = os.rename

    def rename(src, dst, *a, **k):
        if Path(src).name.startswith(".tmp-"):
            raise exc
        return real(src, dst, *a, **k)

    monkeypatch.setattr(mod.os, "rename", rename)


@pytest.mark.parametrize(
    "exc, expect",
    [(RuntimeError("boom"), RuntimeError), (KeyboardInterrupt(), KeyboardInterrupt)],
    ids=["exception", "keyboard-interrupt"],
)
def test_a_failure_between_the_two_renames_leaves_the_old_pad_whole(
    tmp_path, env, cwd, pads, monkeypatch, exc, expect
):
    mod, argv = _adder(tmp_path, env, cwd)
    before = _snapshot(pads / "p")
    _fail_second_rename(mod, monkeypatch, exc)

    with pytest.raises(expect):
        _inproc(mod, argv, env, cwd)

    assert _snapshot(pads / "p") == before
    assert _leftovers(pads) == []


def test_an_os_error_between_the_two_renames_is_a_clean_refusal_with_the_old_pad_whole(
    tmp_path, env, cwd, pads, monkeypatch, capsys
):
    mod, argv = _adder(tmp_path, env, cwd)
    before = _snapshot(pads / "p")
    _fail_second_rename(mod, monkeypatch, OSError(28, "No space left on device"))

    assert _inproc(mod, argv, env, cwd) == 1

    assert "could not build the pad" in capsys.readouterr().err
    assert _snapshot(pads / "p") == before
    assert _leftovers(pads) == []


def test_the_put_in_place_step_is_the_only_part_a_caller_can_swap(tmp_path, env, cwd, pads):
    mod, argv = _adder(tmp_path, env, cwd)
    seen = {}

    def put(tmp, final, aside):
        seen["titles"] = _titles(tmp)
        seen["round"] = sorted(p.name for p in tmp.glob("round-*"))
        shutil.rmtree(tmp / "round-2")

    pads_root = pads
    mod._add_round(pads_root, "p", tmp_path / "kit", mod._check_and_encode(Path(argv[3]), "p", tmp_path / "kit"), put)

    assert seen == {"titles": ["One", "Two"], "round": ["round-1", "round-2"]}


# --- recovery ---------------------------------------------------------------------------------------


def _interrupted(skill, env, cwd, tmp_path, pads, with_tmp=True):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 1)
    before = _hashes(pad_dir)
    pad_dir.rename(pads / ".aside-p")
    if with_tmp:
        (pads / ".tmp-p-x").mkdir()
        (pads / ".tmp-p-x" / "half").write_text("half built")
    return before


def test_the_next_add_after_a_kill_between_the_renames_restores_the_pad_and_adds_its_round(
    skill, env, cwd, tmp_path, pads
):
    before = _interrupted(skill, env, cwd, tmp_path, pads)

    _add(skill, env, cwd, tmp_path, "p", "Two")

    kept = {k[len("round-1/") :]: v for k, v in before.items() if k.startswith("round-1/")}
    assert _hashes(pads / "p" / "round-1") == kept
    assert _titles(pads / "p") == ["One", "Two"]
    assert _leftovers(pads) == []


def test_the_next_new_pad_run_after_such_a_kill_restores_the_pad_and_refuses_as_existing(
    skill, env, cwd, tmp_path, pads
):
    before = _interrupted(skill, env, cwd, tmp_path, pads)

    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round("Other")))

    assert proc.returncode == 1 and "already exists" in proc.stderr
    assert _hashes(pads / "p") == before
    assert _leftovers(pads) == []


def test_a_kill_after_the_second_rename_leaves_an_aside_copy_that_the_next_run_removes(
    skill, env, cwd, tmp_path, pads
):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 2)
    shutil.copytree(pad_dir, pads / ".aside-p")
    before = _hashes(pad_dir)

    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round("Other")))

    assert "already exists" in proc.stderr
    assert _hashes(pad_dir) == before
    assert _leftovers(pads) == []


def test_a_stale_temp_folder_is_removed_by_the_next_run(skill, env, cwd, tmp_path, pads):
    _built_pad(skill, env, cwd, tmp_path, pads, 1)
    (pads / ".tmp-p-x").mkdir()
    (pads / ".tmp-p-x" / "half").write_text("half built")

    _add(skill, env, cwd, tmp_path, "p", "Two")

    assert _leftovers(pads) == []


def test_recovery_leaves_another_pads_temp_folder_alone(skill, env, cwd, tmp_path, pads):
    _built_pad(skill, env, cwd, tmp_path, pads, 1)
    (pads / ".tmp-p-x-abcd1234").mkdir()  # the build folder of a pad named "p-x"

    _add(skill, env, cwd, tmp_path, "p", "Two")

    assert (pads / ".tmp-p-x-abcd1234").is_dir()


# --- two adds at once -------------------------------------------------------------------------------


def test_two_adds_racing_on_one_pad_both_land_as_consecutive_rounds(skill, env, cwd, tmp_path, pads):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 1)
    held = _hold(_lock_path(env, pad_dir))
    procs = []
    for title in ("Alpha", "Beta"):
        argv = _argv(skill, "p", _round_file(tmp_path, _round(title), title), add=True)
        procs.append(
            subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=cwd)
        )
    time.sleep(0.5)
    os.close(held)
    outs = [p.communicate(timeout=60) for p in procs]

    assert [p.returncode for p in procs] == [0, 0], outs
    assert sorted(_titles(pad_dir)[1:]) == ["Alpha", "Beta"]
    assert len(_titles(pad_dir)) == 3
    for k, title in enumerate(_titles(pad_dir), 1):
        round_js = (pad_dir / f"round-{k}" / "round.js").read_text().splitlines()
        assert _decode_literal(round_js[0], ROUND_JS_PREFIX)["title"] == title
        assert round_js[1] == f"window.PAD_ROUND_NUMBER = {k};"
    for _, err in outs:
        assert err.count(WAITING) == 1
    assert _leftovers(pads) == []


def test_a_run_that_has_to_wait_prints_one_waiting_line_then_proceeds(tmp_path, env, cwd, pads, capsys):
    mod, argv = _adder(tmp_path, env, cwd)
    held = _hold(_lock_path(env, pads / "p"))
    threading.Timer(0.4, os.close, args=(held,)).start()

    code = _inproc(mod, argv, env, cwd)

    err = capsys.readouterr().err
    assert code == 0
    assert err.count(WAITING) == 1
    assert _titles(pads / "p") == ["One", "Two"]


def test_a_run_that_does_not_wait_prints_no_waiting_line(tmp_path, env, cwd, pads, capsys):
    mod, argv = _adder(tmp_path, env, cwd)

    assert _inproc(mod, argv, env, cwd) == 0

    assert WAITING not in capsys.readouterr().err


def test_a_lock_held_past_the_wait_limit_refuses_the_add_and_changes_nothing(tmp_path, env, cwd, pads, capsys):
    mod, argv = _adder(tmp_path, env, cwd)
    before = _snapshot(pads / "p")
    held = _hold(_lock_path(env, pads / "p"))
    result = {}
    runner = threading.Thread(
        target=lambda: result.update(code=_inproc(mod, argv, env, cwd, publish_timeout=0.15)),  # limit: twice this
        daemon=True,
    )
    try:
        started = time.monotonic()
        runner.start()
        runner.join(10)
        waited = time.monotonic() - started
        refused_while_held = not runner.is_alive()
    finally:
        os.close(held)
    runner.join(10)
    assert refused_while_held, "the add kept waiting past its limit instead of being refused"
    code = result["code"]

    err = capsys.readouterr().err
    assert code == 1
    assert err.count(WAITING) == 1
    assert 'scratch-pad: another session is adding a round to pad "p"; try again' in err
    assert 0.25 <= waited < 5
    assert _snapshot(pads / "p") == before
    assert _leftovers(pads) == []


def test_the_wait_limit_follows_the_publish_timeout(tmp_path, env, cwd, pads, capsys):
    mod, argv = _adder(tmp_path, env, cwd)
    held = _hold(_lock_path(env, pads / "p"))
    threading.Timer(0.35, os.close, args=(held,)).start()

    code = _inproc(mod, argv, env, cwd, publish_timeout=0.25)  # limit 0.5s: waits out the 0.35s hold

    assert code == 0, capsys.readouterr().err


def test_building_a_new_pad_waits_on_the_same_lock(tmp_path, env, cwd, pads, capsys):
    mod = _load_module()
    held = _hold(_lock_path(env, pads / "fresh"))
    try:
        rp = _round_file(tmp_path, _round("One"))
        code = _inproc(mod, ["--pad", "fresh", "--round", str(rp)], env, cwd, publish_timeout=0.1)
    finally:
        os.close(held)

    assert code == 1
    assert "another session is adding a round" in capsys.readouterr().err
    assert not (pads / "fresh").exists()


# --- what the read-back refuses -----------------------------------------------------------------------

needs_unprivileged = pytest.mark.skipif(os.geteuid() == 0, reason="permissions do not bind root")


def _refused_add(skill, env, cwd, tmp_path, title="Two"):
    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round(title), f"{title}.json"), add=True)
    assert proc.returncode == 1
    assert proc.stdout == ""
    return proc.stderr


@needs_unprivileged
def test_a_folder_inside_the_pad_that_cannot_be_listed_is_named_in_the_cannot_take_refusal(
    skill, env, cwd, tmp_path, pads
):
    _new(skill, env, cwd, tmp_path, "p", "One")
    locked = pads / "p" / "round-1" / "x"
    locked.mkdir()
    locked.chmod(0)
    try:
        before = _snapshot(pads / "p")
        err = _refused_add(skill, env, cwd, tmp_path)
        assert _snapshot(pads / "p") == before
    finally:
        locked.chmod(0o700)

    assert "Traceback" not in err
    assert err.splitlines() == [
        'scratch-pad: pad "p" can\'t take another round: round-1/x can\'t be read: Permission denied — ' + WAY_ON
    ]
    assert _leftovers(pads) == []
    assert _try_lock(_lock_path(env, pads / "p"))


def _try_lock(lock: Path) -> bool:
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


@needs_unprivileged
def test_an_unwritable_pads_folder_refuses_a_new_pad_the_way_it_refuses_an_add(skill, env, cwd, tmp_path, pads):
    _new(skill, env, cwd, tmp_path, "p", "One")
    pads.chmod(0o500)
    try:
        new = _cli(skill, env, cwd, "fresh", _round_file(tmp_path, _round("A"), "a.json"))
        add = _cli(skill, env, cwd, "p", _round_file(tmp_path, _round("Two"), "two.json"), add=True)
    finally:
        pads.chmod(0o700)

    assert new.returncode == 1 and new.stdout == ""
    assert "Traceback" not in new.stderr
    assert new.stderr.splitlines() == [
        f'scratch-pad: "{pads / "fresh"}": could not build the pad: Permission denied'
    ]
    assert add.stderr.splitlines() == [
        f'scratch-pad: "{pads / "p"}": could not build the pad: Permission denied'
    ]
    assert not (pads / "fresh").exists()
    assert _leftovers(pads) == []


def test_a_stray_top_level_file_stops_an_add_and_is_named(skill, env, cwd, tmp_path, pads):
    _new(skill, env, cwd, tmp_path, "p", "One")
    (pads / "p" / "notes.txt").write_text("mine", encoding="utf-8")
    before = _snapshot(pads / "p")

    err = _refused_add(skill, env, cwd, tmp_path)

    assert err.splitlines() == [
        'scratch-pad: pad "p" can\'t take another round: notes.txt is not part of a pad this script builds — '
        + MOVE_OUT
    ]
    assert _snapshot(pads / "p") == before
    assert _leftovers(pads) == []


def test_a_round_01_folder_stops_an_add_and_is_named(skill, env, cwd, tmp_path, pads):
    _new(skill, env, cwd, tmp_path, "p", "One")
    (pads / "p" / "round-01").mkdir()
    (pads / "p" / "stray.html").write_text("x", encoding="utf-8")
    before = _snapshot(pads / "p")

    err = _refused_add(skill, env, cwd, tmp_path)

    assert err.splitlines() == [
        'scratch-pad: pad "p" can\'t take another round: round-01 is not part of a pad this script builds; '
        "stray.html is not part of a pad this script builds — " + MOVE_OUT
    ]
    assert _snapshot(pads / "p") == before


def test_a_pad_without_rounds_js_still_takes_a_round_and_gets_one(skill, env, cwd, tmp_path, pads):
    _new(skill, env, cwd, tmp_path, "p", "One")
    (pads / "p" / "rounds.js").unlink()

    _add(skill, env, cwd, tmp_path, "p", "Two")

    assert _titles(pads / "p") == ["One", "Two"]


@pytest.mark.parametrize("name", [".DS_Store", "._index.html", "._"])
def test_an_os_metadata_file_at_the_top_is_ignored_and_not_carried_into_the_rebuilt_pad(
    skill, env, cwd, tmp_path, pads, name
):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 1)
    (pad_dir / name).write_bytes(b"finder")
    round_one = _hashes(pad_dir / "round-1")

    _add(skill, env, cwd, tmp_path, "p", "Two")

    assert sorted(e.name for e in pad_dir.iterdir()) == ["index.html", "round-1", "round-2", "rounds.js"]
    assert _hashes(pad_dir / "round-1") == round_one
    assert _titles(pad_dir) == ["One", "Two"]
    assert _leftovers(pads) == []


def test_os_metadata_files_inside_a_round_folder_are_copied_byte_for_byte(skill, env, cwd, tmp_path, pads):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 1)
    (pad_dir / "round-1" / ".DS_Store").write_bytes(b"inside")

    _add(skill, env, cwd, tmp_path, "p", "Two")

    assert (pad_dir / "round-1" / ".DS_Store").read_bytes() == b"inside"


def test_a_stray_file_beside_os_metadata_is_named_alone_with_the_move_it_out_way_on(
    skill, env, cwd, tmp_path, pads
):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 1)
    (pad_dir / ".DS_Store").write_bytes(b"finder")
    (pad_dir / "notes.txt").write_text("mine", encoding="utf-8")
    before = _snapshot(pad_dir)

    err = _refused_add(skill, env, cwd, tmp_path)

    assert err.splitlines() == [
        'scratch-pad: pad "p" can\'t take another round: notes.txt is not part of a pad this script builds — '
        + MOVE_OUT
    ]
    assert _snapshot(pad_dir) == before


def test_a_stray_file_and_a_damaged_pad_get_the_damaged_pad_way_on(skill, env, cwd, tmp_path, pads):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 3)
    _gap(pad_dir)
    (pad_dir / "notes.txt").write_text("mine", encoding="utf-8")

    err = _refused_add(skill, env, cwd, tmp_path)

    assert err.splitlines() == [
        'scratch-pad: pad "p" can\'t take another round: round-2 is missing; '
        "notes.txt is not part of a pad this script builds — " + WAY_ON
    ]


@pytest.mark.parametrize("make", ["symlink", "fifo"])
def test_an_os_metadata_name_that_is_not_a_regular_file_is_still_refused_as_a_damaged_pad(
    skill, env, cwd, tmp_path, pads, make
):
    pad_dir = _built_pad(skill, env, cwd, tmp_path, pads, 1)
    if make == "symlink":
        (pad_dir / ".DS_Store").symlink_to("index.html")
        what = "is a symlink"
    else:
        os.mkfifo(pad_dir / ".DS_Store")
        what = "is not a regular file or folder"
    before = _snapshot(pad_dir)

    err = _refused_add(skill, env, cwd, tmp_path)

    assert err.splitlines() == [f'scratch-pad: pad "p" can\'t take another round: .DS_Store {what} — ' + WAY_ON]
    assert _snapshot(pad_dir) == before


@needs_unprivileged
def test_a_file_that_cannot_be_examined_inside_a_listable_folder_is_named_not_a_traceback(
    skill, env, cwd, tmp_path, pads
):
    _new(skill, env, cwd, tmp_path, "p", "One")
    locked = pads / "p" / "round-1" / "x"
    locked.mkdir()
    (locked / "f").write_text("a", encoding="utf-8")
    locked.chmod(0o600)
    try:
        err = _refused_add(skill, env, cwd, tmp_path)
    finally:
        locked.chmod(0o700)

    assert "Traceback" not in err
    assert err.splitlines() == [
        'scratch-pad: pad "p" can\'t take another round: round-1/x/f can\'t be read: Permission denied — ' + WAY_ON
    ]


@needs_unprivileged
def test_a_pad_folder_that_cannot_be_listed_is_named_without_a_false_missing_round(
    skill, env, cwd, tmp_path, pads
):
    _new(skill, env, cwd, tmp_path, "p", "One")
    pads_p = pads / "p"
    pads_p.chmod(0o300)
    try:
        err = _refused_add(skill, env, cwd, tmp_path)
    finally:
        pads_p.chmod(0o700)

    assert err.splitlines() == [
        'scratch-pad: pad "p" can\'t take another round: the pad folder can\'t be read: Permission denied — ' + WAY_ON
    ]


@needs_unprivileged
def test_a_pad_whose_parent_cannot_be_searched_is_a_refusal_not_a_traceback(skill, tmp_path):
    mod = _load_module()
    parent = tmp_path / "parent"
    (parent / "p").mkdir(parents=True)
    parent.chmod(0)
    try:
        with pytest.raises(mod.Refusal) as caught:
            mod._read_pad(parent / "p", "p")
    finally:
        parent.chmod(0o700)

    assert caught.value.lines == [
        'scratch-pad: pad "p" can\'t take another round: the pad folder can\'t be read: Permission denied — '
        + WAY_ON
    ]


def _flow_round(title: str) -> dict:
    data = _round(title)
    data["questions"][0]["options"][1] = {"name": "F", "cost": "c", "flow": "flowchart LR\n a --> b"}
    return data


def _add_round(skill, env, cwd, tmp_path, pad, data):
    proc = _cli(skill, env, cwd, pad, _round_file(tmp_path, data, f"{data['title']}.json"), add=True)
    assert proc.returncode == 0, proc.stderr
    return proc


def test_a_round_with_a_flow_added_to_a_layout_only_pad_leaves_round_one_byte_for_byte(
    skill, env, cwd, tmp_path, pads
):
    _new(skill, env, cwd, tmp_path, "p", "One")
    r1 = _hashes(pads / "p" / "round-1")

    _add_round(skill, env, cwd, tmp_path, "p", _flow_round("Two"))

    assert _hashes(pads / "p" / "round-1") == r1
    first = (pads / "p" / "round-2" / "round.js").read_text(encoding="utf-8").splitlines(keepends=True)[0]
    two = _decode_literal(first, "window.PAD_ROUND = JSON.parse(")
    assert two["questions"][0]["options"][1]["flow"] == "flowchart LR\n a --> b"


def test_a_layout_only_round_added_to_a_pad_whose_round_one_has_a_flow_leaves_it_byte_for_byte(
    skill, env, cwd, tmp_path, pads
):
    proc = _cli(skill, env, cwd, "p", _round_file(tmp_path, _flow_round("One")))
    assert proc.returncode == 0, proc.stderr
    r1 = _hashes(pads / "p" / "round-1")

    _add(skill, env, cwd, tmp_path, "p", "Two")

    assert _hashes(pads / "p" / "round-1") == r1
    assert _titles(pads / "p") == ["One", "Two"]


ROUND_ID_LINE = re.compile(r'^window\.PAD_ROUND_ID = "[0-9a-f]{32}";$')


def _round_id(pad_dir: Path, k: int) -> str:
    lines = (pad_dir / f"round-{k}" / "round.js").read_text(encoding="utf-8").splitlines()
    assert ROUND_ID_LINE.match(lines[2]), lines
    return json.loads(lines[2].removeprefix("window.PAD_ROUND_ID = ").removesuffix(";"))


def test_a_new_round_carries_a_hex_id_as_its_third_line_after_the_two_existing_lines(
    skill, env, cwd, tmp_path, pads
):
    _new(skill, env, cwd, tmp_path, "p", "One")

    lines = (pads / "p" / "round-1" / "round.js").read_text(encoding="utf-8").splitlines()

    assert len(lines) == 3
    assert lines[0].startswith(ROUND_JS_PREFIX)
    assert lines[1] == "window.PAD_ROUND_NUMBER = 1;"
    assert ROUND_ID_LINE.match(lines[2])


def test_the_same_round_under_two_names_gets_two_ids(skill, env, cwd, tmp_path, pads):
    round_path = _round_file(tmp_path, _round("Same"))
    for name in ("a", "b"):
        assert _cli(skill, env, cwd, name, round_path).returncode == 0

    assert _round_id(pads / "a", 1) != _round_id(pads / "b", 1)


def test_a_pad_deleted_and_built_again_under_the_same_name_gets_a_new_id(skill, env, cwd, tmp_path, pads):
    _new(skill, env, cwd, tmp_path, "p", "One")
    first = _round_id(pads / "p", 1)
    shutil.rmtree(pads / "p")

    _new(skill, env, cwd, tmp_path, "p", "One")

    assert _round_id(pads / "p", 1) != first


def test_an_add_keeps_round_one_byte_for_byte_and_gives_round_two_its_own_id(skill, env, cwd, tmp_path, pads):
    _new(skill, env, cwd, tmp_path, "p", "One")
    before = _hashes(pads / "p" / "round-1")
    first = _round_id(pads / "p", 1)

    _add(skill, env, cwd, tmp_path, "p", "Two")

    assert _hashes(pads / "p" / "round-1") == before
    assert _round_id(pads / "p", 1) == first
    assert _round_id(pads / "p", 2) != first


def test_an_add_onto_a_round_with_no_id_line_leaves_it_alone_and_only_the_new_round_has_one(
    skill, env, cwd, tmp_path, pads
):
    pad_dir = _legacy_pad(pads)
    before = _hashes(pad_dir / "round-1")

    _add(skill, env, cwd, tmp_path, "old", "New")

    assert _hashes(pad_dir / "round-1") == before
    assert "PAD_ROUND_ID" not in (pad_dir / "round-1" / "round.js").read_text(encoding="utf-8")
    assert _round_id(pad_dir, 2)
