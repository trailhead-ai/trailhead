"""Behavioral tests for the scratch-pad skill's bundled script.

``scratch_pad.py`` checks a round's outer shape and builds a pad folder under
Outpost's state directory. Every run is fenced to a ``tmp_path``-scoped state
dir, and the kit is a small fixture directory, never the real kit.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import trailhead.paths as trailhead_paths

SCRIPT_PATH = Path(__file__).parent.parent / "plugins" / "outpost" / "skills" / "scratch-pad" / "scratch_pad.py"

KIT_FILES = {
    "index.html": b"<!doctype html><title>kit page</title>\n",
    "pad.js": b"// pad\n\xff\x00binary-ish\n",
    "ArchitectsDaughter-Regular.ttf": bytes(range(256)) * 4,
}

ROUND_JS_PREFIX = "window.PAD_ROUND = JSON.parse("
ROUND_JS_SUFFIX = ");\n"


def _option(name="Split", cost="One more component", pick=False, layout=None):
    return {"name": name, "cost": cost, "pick": pick, "layout": layout if layout is not None else {"rule": True}}


def _question(prompt="Header", n_options=2):
    opts = [_option(name=f"Opt {i}", pick=(i == 0)) for i in range(n_options)]
    return {"prompt": prompt, "options": opts}


def _round(n_questions=1):
    return {
        "title": "Settings page",
        "intro": "Where should the controls live?",
        "questions": [_question(f"Q{i}") for i in range(n_questions)],
    }


def _load_module():
    spec = importlib.util.spec_from_file_location("scratch_pad", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_kit(kit: Path) -> Path:
    kit.mkdir(parents=True, exist_ok=True)
    for name, data in KIT_FILES.items():
        (kit / name).write_bytes(data)
    return kit


@pytest.fixture
def skill(tmp_path):
    """A copy of the script beside a fixture kit, as it sits in the installed skill."""
    d = tmp_path / "skill"
    d.mkdir()
    shutil.copy2(SCRIPT_PATH, d / "scratch_pad.py")
    _write_kit(d / "kit")
    return d


@pytest.fixture(autouse=True)
def _outside_any_workspace(tmp_path, monkeypatch):
    """Run from a folder that is in no camp workspace, so the pad builds on disk and nothing is published."""
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


def _env(state: Path, tmp_path: Path) -> dict[str, str]:
    return {"OUTPOST_STATE_DIR": str(state), "HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}


def _write_round(tmp_path: Path, data, name="round.json") -> Path:
    p = tmp_path / name
    if isinstance(data, bytes):
        p.write_bytes(data)
    elif isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _run(skill, tmp_path, state, pad, round_path, cwd=None, extra_env=None):
    env = _env(state, tmp_path)
    env.update(extra_env or {})
    cwd = cwd or tmp_path
    return subprocess.run(
        [sys.executable, str(skill / "scratch_pad.py"), f"--pad={pad}", f"--round={round_path}"],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def _snapshot(root: Path):
    """Every path under root with its mode and content (or link target); None if root is absent."""
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


def _load_round_js(path: Path):
    text = path.read_text(encoding="utf-8").splitlines(keepends=True)[0]
    assert text.startswith(ROUND_JS_PREFIX) and text.endswith(ROUND_JS_SUFFIX)
    inner = json.loads(text[len(ROUND_JS_PREFIX) : -len(ROUND_JS_SUFFIX)])
    return json.loads(inner)


# ---------------------------------------------------------------------------
# A valid round
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n_questions", "pad"),
    [(1, "settings-header"), (3, "nav.v2_draft"), (2, "0")],
)
def test_valid_round_builds_the_pad(skill, tmp_path, state, n_questions, pad):
    data = _round(n_questions)
    rp = _write_round(tmp_path, data)

    proc = _run(skill, tmp_path, state, pad, rp)

    assert proc.returncode == 0, proc.stderr
    pad_dir = state / "pads" / pad
    lines = proc.stdout.splitlines()
    assert lines[0] == (pad_dir / "index.html").as_uri()
    assert str(pad_dir) in lines[1]
    assert _load_round_js(pad_dir / "round-1" / "round.js") == data
    for name, body in KIT_FILES.items():
        assert (pad_dir / "round-1" / name).read_bytes() == body
    top = (pad_dir / "index.html").read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' in top
    assert 'content="0; url=round-1/index.html"' in top
    assert 'href="round-1/index.html"' in top
    assert [p.name for p in (state / "pads").iterdir()] == [pad]


def test_round_js_is_ascii_and_parses_back_to_the_round(skill, tmp_path, state):
    data = _round(1)
    data["title"] = "café \U0001f600"
    rp = _write_round(tmp_path, data)

    assert _run(skill, tmp_path, state, "p", rp).returncode == 0

    text = (state / "pads" / "p" / "round-1" / "round.js").read_text(encoding="utf-8")
    assert text.startswith(ROUND_JS_PREFIX) and text.splitlines(keepends=True)[0].endswith(ROUND_JS_SUFFIX)
    assert _load_round_js(state / "pads" / "p" / "round-1" / "round.js") == data
    assert text.isascii()


def test_script_tags_and_line_separators_round_trip_through_round_js(skill, tmp_path, state):
    data = _round(1)
    data["title"] = "</script><script>alert(1)</script>"
    data["intro"] = "a\u2028b\u2029c \" \\ d"
    data["questions"][0]["options"][0]["layout"] = {"text": "</SCRIPT>", "__proto__": {"text": "x"}}
    rp = _write_round(tmp_path, data)

    assert _run(skill, tmp_path, state, "p", rp).returncode == 0

    js = state / "pads" / "p" / "round-1" / "round.js"
    assert _load_round_js(js) == data
    raw = js.read_text(encoding="utf-8")
    assert "\u2028" not in raw and "\u2029" not in raw and raw.isascii()


def test_top_level_page_carries_no_round_text(skill, tmp_path, state):
    data = _round(1)
    data["title"] = "UNIQUE-TITLE-MARKER"
    rp = _write_round(tmp_path, data)

    assert _run(skill, tmp_path, state, "unique-slug-marker", rp).returncode == 0

    top = (state / "pads" / "unique-slug-marker" / "index.html").read_text(encoding="utf-8")
    assert "UNIQUE-TITLE-MARKER" not in top
    assert "unique-slug-marker" not in top


def test_layout_contents_are_never_inspected(skill, tmp_path, state):
    data = _round(1)
    for value in (None, 7, "str", [1, 2], {"slider": "Volume"}, {}):
        data["questions"][0]["options"][0]["layout"] = value
        rp = _write_round(tmp_path, data)
        p = _run(skill, tmp_path, state, f"p{abs(hash(json.dumps(value)))}", rp)
        assert p.returncode == 0, (value, p.stderr)


@pytest.mark.parametrize("number", ["1e400", "-1e400"])
@pytest.mark.parametrize("where", ["layout", "nested-layout-list"])
def test_a_number_too_large_for_json_is_refused_and_nothing_is_written(skill, tmp_path, state, number, where):
    layout = f'{{"text": {number}}}' if where == "layout" else f'{{"row": [{{"rule": true}}, {{"box": [{number}]}}]}}'
    text = json.dumps(_round(1)).replace('{"rule": true}', layout, 1)
    assert number in text
    rp = _write_round(tmp_path, text)
    before = _snapshot(state)

    proc = _run(skill, tmp_path, state, "p", rp)

    assert proc.returncode != 0
    assert proc.stderr.startswith("scratch-pad: ")
    assert "too large" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert _snapshot(state) == before


def test_twenty_six_options_is_accepted(skill, tmp_path, state):
    data = _round(1)
    data["questions"][0] = _question("Q", n_options=26)
    rp = _write_round(tmp_path, data)

    assert _run(skill, tmp_path, state, "p", rp).returncode == 0


def test_runs_as_a_subprocess_from_an_empty_cwd(skill, tmp_path, state):
    empty = tmp_path / "empty-cwd"
    empty.mkdir()
    rp = _write_round(tmp_path, _round(1))

    proc = _run(skill, tmp_path, state, "p", rp, cwd=empty)

    assert proc.returncode == 0, proc.stderr
    assert list(empty.iterdir()) == []
    assert (state / "pads" / "p" / "index.html").is_file()


def test_build_pad_takes_an_injected_env_and_kit(tmp_path, state):
    mod = _load_module()
    kit = _write_kit(tmp_path / "other-kit")
    (kit / "extra.css").write_text("x{}")
    rp = _write_round(tmp_path, _round(1))

    index, pad_dir = mod.build_pad(rp, "p", kit_dir=kit, env={"OUTPOST_STATE_DIR": str(state)})

    assert pad_dir == state / "pads" / "p"
    assert index == pad_dir / "index.html"
    assert (pad_dir / "round-1" / "extra.css").read_text() == "x{}"


def _isolated_env(state, tmp_path):
    return {"OUTPOST_STATE_DIR": str(state), "HOME": str(tmp_path / "home"), "XDG_STATE_HOME": str(tmp_path / "xdg")}


def _fence_process_env(monkeypatch, tmp_path):
    """Point the process's own state lookups at a decoy so a `main` that ignores `env` cannot touch real state."""
    monkeypatch.setenv("OUTPOST_STATE_DIR", str(tmp_path / "decoy"))
    monkeypatch.setenv("HOME", str(tmp_path / "decoy-home"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)


def test_main_builds_in_process_under_the_injected_env(tmp_path, state, capsys, monkeypatch):
    _fence_process_env(monkeypatch, tmp_path)
    mod = _load_module()
    rp = _write_round(tmp_path, _round(1))

    code = mod.main(["--pad", "p", "--round", str(rp)], env=_isolated_env(state, tmp_path))

    out = capsys.readouterr().out.splitlines()
    pad_dir = state / "pads" / "p"
    assert code == 0
    assert out[0] == (pad_dir / "index.html").as_uri()
    assert out[1] == f"pad folder: {pad_dir}"
    assert (pad_dir / "round-1" / "round.js").is_file()
    assert not (tmp_path / "home").exists() and not (tmp_path / "xdg").exists()


def test_main_refuses_in_process_with_a_nonzero_code_and_writes_nothing(tmp_path, state, capsys, monkeypatch):
    _fence_process_env(monkeypatch, tmp_path)
    mod = _load_module()
    rp = _write_round(tmp_path, _without(_round(1), "title"))

    code = mod.main(["--pad", "p", "--round", str(rp)], env=_isolated_env(state, tmp_path))

    captured = capsys.readouterr()
    assert code != 0
    assert captured.out == ""
    assert captured.err.startswith("scratch-pad: ")
    assert not state.exists()


# ---------------------------------------------------------------------------
# Refusals: shape
# ---------------------------------------------------------------------------


def _without(d, *path):
    d = json.loads(json.dumps(d))
    cur = d
    for k in path[:-1]:
        cur = cur[k]
    del cur[path[-1]]
    return d


def _mutated(fn):
    d = _round(1)
    fn(d)
    return d


MALFORMED = {
    "unreadable-json": ("{not json", "round.json"),
    "missing-title": (_without(_round(1), "title"), "title"),
    "empty-questions": (_mutated(lambda d: d.update(questions=[])), "questions"),
    "option-without-cost": (_without(_round(1), "questions", 0, "options", 1, "cost"), "questions[0].options[1]"),
    "no-pick": (
        _mutated(lambda d: d["questions"][0]["options"][0].update(pick=False)),
        "questions[0]",
    ),
    "two-picks": (
        _mutated(lambda d: d["questions"][0]["options"][1].update(pick=True)),
        "questions[0]",
    ),
    "twenty-seven-options": (
        _mutated(lambda d: d["questions"][0].update(options=_question("Q", 27)["options"])),
        "questions[0].options",
    ),
    "unknown-key-costs": (
        _mutated(lambda d: d["questions"][0]["options"][1].update(costs="x")),
        "questions[0].options[1]",
    ),
    "option-without-layout": (_without(_round(1), "questions", 0, "options", 0, "layout"), "questions[0].options[0]"),
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
@pytest.mark.parametrize("fresh", [True, False], ids=["fresh-state", "existing-pads"])
def test_malformed_round_is_refused_naming_the_path_and_writes_nothing(skill, tmp_path, state, case, fresh):
    data, where = MALFORMED[case]
    rp = _write_round(tmp_path, data)
    if not fresh:
        (state / "pads" / "other").mkdir(parents=True)
        (state / "pads" / "other" / "f").write_text("keep")
    before = _snapshot(state)

    proc = _run(skill, tmp_path, state, "p", rp)

    assert proc.returncode != 0
    assert proc.stdout == ""
    assert "Traceback" not in proc.stderr
    lines = proc.stderr.splitlines()
    assert lines and all(line.startswith("scratch-pad: ") for line in lines)
    assert any(where in line for line in lines), proc.stderr
    assert _snapshot(state) == before


def test_each_problem_has_its_documented_wording(skill, tmp_path, state):
    data = _round(3)
    del data["questions"][0]["options"][1]["cost"]
    data["questions"][1]["options"][1]["costs"] = "x"
    data["questions"][2]["options"][0]["pick"] = False

    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))

    assert proc.stderr.splitlines() == [
        'scratch-pad: questions[0].options[1]: no "cost" — every option states what it costs',
        'scratch-pad: questions[1].options[1]: unknown key "costs" — allowed: name, cost, pick, layout',
        'scratch-pad: questions[2]: no option has "pick": true — mark the session\'s own pick',
    ]


def test_empty_questions_wording(skill, tmp_path, state):
    data = _round(1)
    data["questions"] = []
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    assert proc.stderr.splitlines() == ["scratch-pad: questions: empty — a round asks at least one question"]


def test_two_picks_names_how_many(skill, tmp_path, state):
    data = _round(1)
    data["questions"][0]["options"][1]["pick"] = True
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    assert 'questions[0]: 2 options have "pick": true — exactly one' in proc.stderr


def test_unknown_keys_list_the_allowed_keys_at_every_level(skill, tmp_path, state):
    data = _round(1)
    data["titel"] = "x"
    data["questions"][0]["promt"] = "x"
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    assert 'scratch-pad: round: unknown key "titel" — allowed: title, intro, questions' in proc.stderr
    assert 'scratch-pad: questions[0]: unknown key "promt" — allowed: prompt, options' in proc.stderr


@pytest.mark.parametrize(
    ("mutate", "where"),
    [
        (lambda d: d.update(title=""), "title"),
        (lambda d: d.update(title=5), "title"),
        (lambda d: d.update(intro=5), "intro"),
        (lambda d: d.update(questions="x"), "questions"),
        (lambda d: d["questions"].__setitem__(0, "x"), "questions[0]"),
        (lambda d: d["questions"][0].update(prompt=""), "questions[0].prompt"),
        (lambda d: d["questions"][0].update(prompt=3), "questions[0].prompt"),
        (lambda d: d["questions"][0].pop("prompt"), "questions[0]"),
        (lambda d: d["questions"][0].pop("options"), "questions[0]"),
        (lambda d: d["questions"][0].update(options="xyz"), "questions[0].options"),
        (lambda d: d["questions"][0]["options"][0].update(name=5), "questions[0].options[0].name"),
        (lambda d: d["questions"][0]["options"][0].update(cost=5), "questions[0].options[0].cost"),
        (lambda d: d["questions"][0].update(options=[]), "questions[0].options"),
        (lambda d: d["questions"][0]["options"].__setitem__(0, 3), "questions[0].options[0]"),
        (lambda d: d["questions"][0]["options"][0].update(name=""), "questions[0].options[0].name"),
        (lambda d: d["questions"][0]["options"][0].update(cost=""), "questions[0].options[0].cost"),
        (lambda d: d["questions"][0]["options"][0].update(pick="yes"), "questions[0].options[0].pick"),
    ],
)
def test_wrong_typed_or_empty_fields_are_refused_with_their_path(skill, tmp_path, state, mutate, where):
    data = _round(1)
    mutate(data)
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    assert proc.returncode != 0
    assert any(line.startswith(f"scratch-pad: {where}: ") for line in proc.stderr.splitlines()), proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("text", ["[]", "3", "null", '"s"'])
def test_a_round_that_is_not_an_object_is_refused(skill, tmp_path, state, text):
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, text))
    assert proc.returncode != 0
    assert proc.stderr.startswith("scratch-pad: round: ")


def test_json_error_reports_line_and_column(skill, tmp_path, state):
    rp = _write_round(tmp_path, '{\n  "title": "x",\n  oops\n}')
    proc = _run(skill, tmp_path, state, "p", rp)
    assert "line 3, column 3" in proc.stderr
    assert proc.stderr.startswith("scratch-pad: ")


def test_nan_is_refused_as_unreadable_json(skill, tmp_path, state):
    rp = _write_round(tmp_path, '{"title": NaN}')
    proc = _run(skill, tmp_path, state, "p", rp)
    assert proc.returncode != 0
    assert "NaN" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_missing_round_file_is_a_clean_refusal(skill, tmp_path, state):
    proc = _run(skill, tmp_path, state, "p", tmp_path / "nope.json")
    assert proc.returncode != 0
    assert proc.stderr.startswith("scratch-pad: ")
    assert "Traceback" not in proc.stderr
    assert not state.exists()


def test_non_utf8_input_is_refused_without_a_traceback(skill, tmp_path, state):
    rp = _write_round(tmp_path, b'{"title": "\xff\xfe"}')
    proc = _run(skill, tmp_path, state, "p", rp)
    assert proc.returncode != 0
    assert "UTF-8" in proc.stderr
    assert proc.stderr.startswith("scratch-pad: ")
    assert "Traceback" not in proc.stderr
    assert not state.exists()


def test_deeply_nested_input_is_refused_without_a_traceback(skill, tmp_path, state):
    rp = _write_round(tmp_path, "[" * 10000 + "]" * 10000)
    proc = _run(skill, tmp_path, state, "p", rp)
    assert proc.returncode != 0
    assert "nested too deeply" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not state.exists()


def test_a_round_too_deep_to_write_is_refused_cleanly(tmp_path, state, monkeypatch):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    rp = _write_round(tmp_path, _round(1))

    def too_deep(*a, **k):
        raise RecursionError

    monkeypatch.setattr(mod.json, "dumps", too_deep)

    with pytest.raises(mod.Refusal) as exc:
        mod.build_pad(rp, "p", kit_dir=kit, env={"OUTPOST_STATE_DIR": str(state)})

    assert "nested too deeply" in "\n".join(exc.value.lines)
    assert not state.exists()


def test_an_existing_empty_pad_folder_is_refused_and_left_alone(skill, tmp_path, state):
    (state / "pads" / "p").mkdir(parents=True)
    before = _snapshot(state)

    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, _round(1)))

    assert proc.returncode != 0
    assert "already exists" in proc.stderr
    assert _snapshot(state) == before


def test_three_distinct_problems_are_all_reported(skill, tmp_path, state):
    data = _round(2)
    data["title"] = ""
    del data["questions"][0]["options"][0]["name"]
    data["questions"][1]["options"] = []
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    lines = proc.stderr.splitlines()
    assert len(lines) == 3
    assert any("title" in ln for ln in lines)
    assert any("questions[0].options[0]" in ln and "name" in ln for ln in lines)
    assert any("questions[1].options" in ln for ln in lines)


def test_control_characters_in_echoed_round_text_are_escaped(skill, tmp_path, state):
    data = _round(1)
    data["questions"][0]["options"][0]["\x1b[2J"] = 1
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    assert proc.returncode != 0
    assert "\x1b" not in proc.stderr
    assert "\\x1b[2J" in proc.stderr


def test_control_characters_in_the_round_path_are_escaped(skill, tmp_path, state):
    rp = tmp_path / "bad\x1b[2Jname.json"
    proc = _run(skill, tmp_path, state, "p", rp)
    assert proc.returncode != 0
    assert "\x1b" not in proc.stderr


# ---------------------------------------------------------------------------
# Refusals: pad name, existing pad, kit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["../x", "Upper", "", "-lead", ".hidden", "a/b", "ok\n", "sp ace"])
def test_bad_pad_slug_is_refused_before_anything_is_written(skill, tmp_path, state, slug):
    rp = _write_round(tmp_path, _round(1))
    proc = _run(skill, tmp_path, state, slug, rp)
    assert proc.returncode != 0
    assert proc.stderr.startswith("scratch-pad: --pad: ")
    assert "Traceback" not in proc.stderr
    assert not state.exists()
    assert not (tmp_path / "x").exists()


def test_a_slug_of_the_maximum_length_builds(skill, tmp_path, state):
    slug = "a" * 64
    rp = _write_round(tmp_path, _round(1))
    proc = _run(skill, tmp_path, state, slug, rp)
    assert proc.returncode == 0, proc.stderr
    assert (state / "pads" / slug / "index.html").is_file()


@pytest.mark.parametrize("length", [65, 300])
def test_an_over_long_slug_is_refused_before_anything_is_written(skill, tmp_path, state, length):
    rp = _write_round(tmp_path, _round(1))
    proc = _run(skill, tmp_path, state, "a" * length, rp)
    assert proc.returncode != 0
    assert proc.stderr.startswith("scratch-pad: --pad: ")
    assert "too long" in proc.stderr
    assert "64" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert not state.exists()


def test_a_bad_slug_and_a_bad_round_are_reported_together(skill, tmp_path, state):
    data = _round(1)
    data["title"] = ""
    proc = _run(skill, tmp_path, state, "Bad", _write_round(tmp_path, data))
    lines = proc.stderr.splitlines()
    assert any(ln.startswith("scratch-pad: --pad: ") for ln in lines)
    assert any(ln.startswith("scratch-pad: title: ") for ln in lines)


def test_existing_pad_is_refused_and_left_untouched(skill, tmp_path, state):
    rp = _write_round(tmp_path, _round(1))
    assert _run(skill, tmp_path, state, "p", rp).returncode == 0
    before = _snapshot(state)

    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, _round(3), "r2.json"))

    assert proc.returncode != 0
    assert proc.stdout == ""
    assert proc.stderr.splitlines() == ['scratch-pad: pad "p" already exists — choose another --pad name']
    assert _snapshot(state) == before


def test_a_dangling_symlink_named_like_the_pad_counts_as_existing(skill, tmp_path, state):
    (state / "pads").mkdir(parents=True, mode=0o700)
    (state / "pads" / "p").symlink_to(tmp_path / "nowhere")
    before = _snapshot(state)

    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, _round(1)))

    assert proc.returncode != 0
    assert "already exists" in proc.stderr
    assert _snapshot(state) == before


def test_missing_kit_is_a_clean_refusal(skill, tmp_path, state):
    shutil.rmtree(skill / "kit")
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, _round(1)))
    assert proc.returncode != 0
    assert proc.stderr.startswith("scratch-pad: kit: ")
    assert "Traceback" not in proc.stderr
    assert not state.exists()


def test_two_concurrent_runs_on_one_slug_yield_one_pad(skill, tmp_path, state):
    rp1 = _write_round(tmp_path, _round(1), "r1.json")
    rp3 = _write_round(tmp_path, _round(3), "r3.json")
    results = {}

    def go(key, rp):
        results[key] = _run(skill, tmp_path, state, "race", rp)

    threads = [threading.Thread(target=go, args=(k, r)) for k, r in (("a", rp1), ("b", rp3))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    codes = sorted(r.returncode == 0 for r in results.values())
    assert codes == [False, True]
    loser = next(r for r in results.values() if r.returncode != 0)
    assert "Traceback" not in loser.stderr
    assert "already exists" in loser.stderr
    assert [p.name for p in (state / "pads").iterdir()] == ["race"]


def test_loser_of_the_final_rename_is_refused_and_leaves_no_temp_folder(tmp_path, state, monkeypatch):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    rp = _write_round(tmp_path, _round(1))
    env = {"OUTPOST_STATE_DIR": str(state)}
    real_rename = os.rename

    def winner_lands_first(src, dst):
        Path(dst).mkdir()
        (Path(dst) / "winner").write_text("w")
        real_rename(src, dst)

    monkeypatch.setattr(mod.os, "rename", winner_lands_first)

    with pytest.raises(mod.Refusal) as exc:
        mod.build_pad(rp, "race", kit_dir=kit, env=env)

    assert "already exists" in "\n".join(exc.value.lines)
    assert [p.name for p in (state / "pads").iterdir()] == ["race"]
    assert (state / "pads" / "race" / "winner").read_text() == "w"


def _lose_the_pads_root_race(mod, monkeypatch, other_run):
    """Make this run's create of the pads folder lose to ``other_run``, which lands first."""
    real_mkdir = Path.mkdir

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        if self.name == "pads" and not parents and not exist_ok:
            other_run(self)
            raise FileExistsError(17, "File exists", str(self))
        return real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(mod.Path, "mkdir", mkdir)


def test_pads_folder_created_by_another_run_mid_create_is_used(tmp_path, state, monkeypatch):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    rp = _write_round(tmp_path, _round(1))
    env = {"OUTPOST_STATE_DIR": str(state)}
    _lose_the_pads_root_race(mod, monkeypatch, lambda p: os.mkdir(p, 0o700))

    index, folder = mod.build_pad(rp, "fresh", kit_dir=kit, env=env)

    assert folder == state / "pads" / "fresh"
    assert index.is_file()
    assert [p.name for p in (state / "pads").iterdir()] == ["fresh"]


def test_pads_folder_that_became_a_symlink_mid_create_is_refused(tmp_path, state, monkeypatch):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    rp = _write_round(tmp_path, _round(1))
    env = {"OUTPOST_STATE_DIR": str(state)}
    target = tmp_path / "elsewhere"
    target.mkdir()
    _lose_the_pads_root_race(mod, monkeypatch, lambda p: p.symlink_to(target))

    with pytest.raises(mod.Refusal) as exc:
        mod.build_pad(rp, "fresh", kit_dir=kit, env=env)

    assert "is a symlink" in "\n".join(exc.value.lines)
    assert list(target.iterdir()) == []


def test_pads_folder_that_became_a_file_mid_create_is_refused(tmp_path, state, monkeypatch):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    rp = _write_round(tmp_path, _round(1))
    env = {"OUTPOST_STATE_DIR": str(state)}
    _lose_the_pads_root_race(mod, monkeypatch, lambda p: p.write_text("x"))

    with pytest.raises(mod.Refusal) as exc:
        mod.build_pad(rp, "fresh", kit_dir=kit, env=env)

    assert "not a directory" in "\n".join(exc.value.lines)


def test_other_failures_creating_the_pads_folder_stay_refusals(tmp_path, state, monkeypatch):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    rp = _write_round(tmp_path, _round(1))
    env = {"OUTPOST_STATE_DIR": str(state)}
    real_mkdir = Path.mkdir

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        if self.name == "pads" and not parents and not exist_ok:
            real_mkdir(self, mode=mode)
            raise PermissionError(13, "Permission denied", str(self))
        return real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(mod.Path, "mkdir", mkdir)

    with pytest.raises(mod.Refusal) as exc:
        mod.build_pad(rp, "fresh", kit_dir=kit, env=env)

    assert "cannot create the pads folder: Permission denied" in "\n".join(exc.value.lines)


def test_concurrent_first_runs_with_different_slugs_both_succeed(skill, tmp_path):
    rp = _write_round(tmp_path, _round(1))
    for attempt in range(8):
        state = tmp_path / f"state{attempt}"
        results = {}

        def go(slug):
            results[slug] = _run(skill, tmp_path, state, slug, rp)

        threads = [threading.Thread(target=go, args=(s,)) for s in ("alpha", "beta")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert [r.returncode for r in results.values()] == [0, 0], {k: r.stderr for k, r in results.items()}
        assert sorted(p.name for p in (state / "pads").iterdir()) == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Pads root: symlink, permissions
# ---------------------------------------------------------------------------


def test_symlinked_pads_root_is_refused(skill, tmp_path, state):
    target = tmp_path / "elsewhere"
    target.mkdir()
    state.mkdir()
    (state / "pads").symlink_to(target)

    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, _round(1)))

    assert proc.returncode != 0
    assert proc.stderr.startswith("scratch-pad: ")
    assert "is a symlink" in proc.stderr
    assert list(target.iterdir()) == []


def test_pads_root_that_is_a_file_is_refused(skill, tmp_path, state):
    state.mkdir()
    (state / "pads").write_text("x")
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, _round(1)))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert (state / "pads").read_text() == "x"


@pytest.mark.parametrize("umask", [0o022, 0o000])
def test_fresh_pads_root_is_owner_only(skill, tmp_path, state, umask):
    old = os.umask(umask)
    try:
        proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, _round(1)))
    finally:
        os.umask(old)
    assert proc.returncode == 0, proc.stderr
    assert stat.S_IMODE((state / "pads").stat().st_mode) == 0o700


def test_the_temp_folder_is_owner_only_and_carries_the_tmp_prefix(tmp_path, state, monkeypatch):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    rp = _write_round(tmp_path, _round(1))
    seen = {}
    real_copytree = shutil.copytree

    def spy(src, dst, *a, **k):
        tmp = Path(dst).parent
        seen["name"] = tmp.name
        seen["mode"] = stat.S_IMODE(tmp.stat().st_mode)
        return real_copytree(src, dst, *a, **k)

    monkeypatch.setattr(mod.shutil, "copytree", spy)
    old = os.umask(0o000)
    try:
        mod.build_pad(rp, "p", kit_dir=kit, env={"OUTPOST_STATE_DIR": str(state)})
    finally:
        os.umask(old)

    assert seen["name"].startswith(".tmp-")
    assert seen["mode"] == 0o700


def test_failure_partway_through_the_build_leaves_no_pad_and_no_temp_folder(tmp_path, state, monkeypatch):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    rp = _write_round(tmp_path, _round(1))
    (state / "pads").mkdir(parents=True, mode=0o700)
    before = _snapshot(state)

    def boom(src, dst, *a, **k):
        Path(dst).mkdir()
        (Path(dst) / "half").write_text("x")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(mod.shutil, "copytree", boom)

    with pytest.raises(mod.Refusal) as exc:
        mod.build_pad(rp, "p", kit_dir=kit, env={"OUTPOST_STATE_DIR": str(state)})

    assert "No space left" in "\n".join(exc.value.lines)
    assert _snapshot(state) == before


@pytest.mark.parametrize("failure", [RuntimeError("boom"), KeyboardInterrupt()], ids=["exception", "interrupt"])
def test_failure_after_the_temp_folder_exists_leaves_no_pad_and_no_temp_folder(tmp_path, state, monkeypatch, failure):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    rp = _write_round(tmp_path, _round(1))
    (state / "pads").mkdir(parents=True, mode=0o700)
    before = _snapshot(state)
    seen = {}

    def boom(src, dst, *a, **k):
        seen["tmp_existed"] = Path(dst).parent.is_dir()
        Path(dst).mkdir()
        (Path(dst) / "half").write_text("x")
        raise failure

    monkeypatch.setattr(mod.shutil, "copytree", boom)

    with pytest.raises(type(failure)):
        mod.build_pad(rp, "p", kit_dir=kit, env={"OUTPOST_STATE_DIR": str(state)})

    assert seen["tmp_existed"]
    assert _snapshot(state) == before


# ---------------------------------------------------------------------------
# State dir resolution
# ---------------------------------------------------------------------------


def test_pads_root_agrees_with_trailhead_paths_state_dir(tmp_path):
    mod = _load_module()
    matrix = [
        {"OUTPOST_STATE_DIR": str(tmp_path / "custom-outpost"), "HOME": str(tmp_path / "home")},
        {
            "OUTPOST_STATE_DIR": str(tmp_path / "custom-outpost"),
            "XDG_STATE_HOME": str(tmp_path / "xdg"),
            "HOME": str(tmp_path / "home"),
        },
        {"XDG_STATE_HOME": str(tmp_path / "xdg-state"), "HOME": str(tmp_path / "home")},
        {"HOME": str(tmp_path / "home")},
    ]
    for env in matrix:
        expected = trailhead_paths.state_dir("outpost", env=env, platform=sys.platform)
        assert mod._pads_root(env) == expected / "pads", env


def test_build_uses_each_state_dir_source(tmp_path):
    mod = _load_module()
    kit = _write_kit(tmp_path / "kit")
    rp = _write_round(tmp_path, _round(1))
    envs = {
        "override": {"OUTPOST_STATE_DIR": str(tmp_path / "o"), "XDG_STATE_HOME": str(tmp_path / "x")},
        "xdg": {"XDG_STATE_HOME": str(tmp_path / "x2"), "HOME": str(tmp_path / "h")},
        "home": {"HOME": str(tmp_path / "h2")},
    }
    roots = {k: mod.build_pad(rp, "p", kit_dir=kit, env=e)[1] for k, e in envs.items()}
    assert roots["override"] == tmp_path / "o" / "pads" / "p"
    assert roots["xdg"] == tmp_path / "x2" / "outpost" / "pads" / "p"
    assert roots["home"] == tmp_path / "h2" / ".local" / "state" / "outpost" / "pads" / "p"


def test_a_bad_slug_and_an_unreadable_round_are_reported_together(skill, tmp_path, state):
    proc = _run(skill, tmp_path, state, "Bad", _write_round(tmp_path, "{nope"))
    lines = proc.stderr.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("scratch-pad: --pad: ")
    assert "invalid JSON" in lines[1]


def test_a_round_without_questions_says_so(skill, tmp_path, state):
    data = _round(1)
    del data["questions"]
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    assert proc.stderr.splitlines() == ['scratch-pad: questions: no "questions" — a round asks at least one question']


# ---------------------------------------------------------------------------
# Settled notes and the pad's list of rounds
# ---------------------------------------------------------------------------

ROUNDS_JS_PREFIX = "window.PAD_ROUNDS = JSON.parse("


def _load_rounds_js(path: Path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith(ROUNDS_JS_PREFIX) and text.endswith(ROUND_JS_SUFFIX)
    return json.loads(json.loads(text[len(ROUNDS_JS_PREFIX) : -len(ROUND_JS_SUFFIX)]))


def test_settled_notes_reach_the_built_round(skill, tmp_path, state):
    data = _round(1)
    data["settled"] = ["tabs stay", "sidebar goes"]
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    assert proc.returncode == 0, proc.stderr
    assert _load_round_js(state / "pads" / "p" / "round-1" / "round.js")["settled"] == ["tabs stay", "sidebar goes"]


@pytest.mark.parametrize("settled", [[], "absent"])
def test_an_empty_or_missing_settled_builds(skill, tmp_path, state, settled):
    data = _round(1)
    if settled != "absent":
        data["settled"] = settled
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    assert proc.returncode == 0, proc.stderr
    assert _load_round_js(state / "pads" / "p" / "round-1" / "round.js").get("settled", []) == []


@pytest.mark.parametrize(
    ("settled", "line"),
    [
        ("tabs stay", "scratch-pad: settled: must be a list"),
        ({"a": 1}, "scratch-pad: settled: must be a list"),
        (["ok", 3], "scratch-pad: settled[1]: must be a string"),
        (["ok", None], "scratch-pad: settled[1]: must be a string"),
        (["ok", ""], "scratch-pad: settled[1]: empty"),
        (["ok", "  \t"], "scratch-pad: settled[1]: empty"),
    ],
)
def test_a_malformed_settled_is_refused_by_name_and_nothing_is_built(skill, tmp_path, state, settled, line):
    data = _round(1)
    data["settled"] = settled
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    assert proc.returncode != 0
    lines = proc.stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith(line)
    assert not (state / "pads").exists() or list((state / "pads").iterdir()) == []


def test_settled_problems_are_reported_with_every_other_problem(skill, tmp_path, state):
    data = _round(1)
    data["settled"] = [1, " "]
    del data["questions"][0]["options"][0]["cost"]
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    lines = proc.stderr.splitlines()
    assert len(lines) == 3
    assert any(ln.startswith("scratch-pad: settled[0]: must be a string") for ln in lines)
    assert any(ln.startswith("scratch-pad: settled[1]: empty") for ln in lines)
    assert any("questions[0].options[0]" in ln and "cost" in ln for ln in lines)


def test_a_new_pad_lists_its_one_round_and_numbers_it(skill, tmp_path, state):
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, _round(1)))
    assert proc.returncode == 0, proc.stderr
    pad = state / "pads" / "p"
    assert _load_rounds_js(pad / "rounds.js") == [{"title": "Settings page"}]
    text = (pad / "round-1" / "round.js").read_text(encoding="utf-8")
    assert text.startswith(ROUND_JS_PREFIX)
    assert text.splitlines()[-1] == "window.PAD_ROUND_NUMBER = 1;"
    assert _load_round_js(pad / "round-1" / "round.js")["title"] == "Settings page"


@pytest.mark.parametrize(
    "title",
    ['say "hi" \\ there', "</script><script>alert(1)</script>", "café \U0001f600  "],
)
def test_rounds_js_title_is_escaped_like_round_js(skill, tmp_path, state, title):
    data = _round(1)
    data["title"] = title
    data["questions"][0]["options"][0]["layout"] = {"__proto__": {"x": 1}}
    proc = _run(skill, tmp_path, state, "p", _write_round(tmp_path, data))
    assert proc.returncode == 0, proc.stderr
    js = state / "pads" / "p" / "rounds.js"
    assert _load_rounds_js(js) == [{"title": title}]
    raw = js.read_bytes()
    assert all(b < 0x80 for b in raw)


def test_write_rounds_js_lists_several_titles(tmp_path):
    mod = _load_module()
    target = tmp_path / "rounds.js"
    mod._write_rounds_js(tmp_path, ["One", 'Two "quoted"', "Té"])
    assert _load_rounds_js(target) == [{"title": "One"}, {"title": 'Two "quoted"'}, {"title": "Té"}]
    assert target.read_bytes().isascii()
