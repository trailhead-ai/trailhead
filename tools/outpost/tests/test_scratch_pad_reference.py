"""Behavioral tests for ``scratch_pad.read_reference``.

A reference file names drawings on a pad. ``read_reference`` checks the file's shape, finds the pad where
``--add`` finds it, reads it back under the pad's lock, decodes each named round's data without evaluating it,
runs a credential-pattern check over every string bound for the page, and returns the chosen options in the
order named. Every test runs from a tmp cwd with a tmp HOME and tmp state dirs, never inside a live camp
workspace.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

import chromium_harness as ch
from test_scratch_pad import _snapshot, _without_locks, _write_kit
from test_scratch_pad_rounds import _hold, _lock_path, _try_lock

SCRIPT_PATH = Path(__file__).parent.parent / "plugins" / "outpost" / "skills" / "scratch-pad" / "scratch_pad.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("scratch_pad", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load_module()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    state = tmp_path / "state"
    env = {
        "OUTPOST_STATE_DIR": str(state),
        "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
        "LORE_STATE_DIR": str(tmp_path / "lore-state"),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
    }
    kit = _write_kit(tmp_path / "kit")
    return env, work, state, kit


def _opt(name, cost="Cheap", pick=False, **shape):
    return {"name": name, "cost": cost, "pick": pick, **(shape or {"layout": {"rule": True}})}


ROUND_ONE = {
    "title": "Header round",
    "questions": [
        {"prompt": "Where does the logo go?", "options": [_opt("Logo left", pick=True), _opt("Logo centre")]},
        {
            "prompt": "How do controls sit?",
            "options": [
                _opt("Controls inline", layout={"rule": "inline"}),
                _opt("Controls stacked", cost="Taller", pick=True, layout={"rule": "stacked"}),
            ],
        },
    ],
}
ROUND_TWO = {
    "title": "Sign-in round",
    "questions": [
        {
            "prompt": "What happens on a wrong password?",
            "options": [
                _opt("Retry in place", pick=True, flow="(Open) -> [Form]"),
                _opt("Lock out", flow="(Open) -> [Locked]"),
                _opt("Hint first", flow="(Open) -> [Hint]"),
            ],
        }
    ],
}


def _write_json(path: Path, data) -> Path:
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def _build(mod, rig, tmp_path, pad="p", rounds=(ROUND_ONE, ROUND_TWO)):
    env, work, _state, kit = rig
    for i, data in enumerate(rounds):
        rp = _write_json(tmp_path / f"r{i}-{pad}.json", data)
        mod.build_pad(rp, pad, kit_dir=kit, env=env, cwd=work, add=i > 0)
    return Path(env["OUTPOST_STATE_DIR"]) / "pads" / pad


def _ref(drawings, site="header-and-sign-in", title="What we are building"):
    return {"site": site, "title": title, "drawings": drawings}


def _d(rnd, q, o):
    return {"round": rnd, "question": q, "option": o}


def _read(mod, rig, tmp_path, ref, pad="p", **kw):
    env, work, *_ = rig
    path = _write_json(tmp_path / "ref.json", ref)
    return mod.read_reference(path, pad, env=env, cwd=work, **kw)


def _refusal(mod, rig, tmp_path, ref, pad="p", **kw):
    with pytest.raises(mod.Refusal) as exc:
        _read(mod, rig, tmp_path, ref, pad, **kw)
    return exc.value.lines


# --- what comes back --------------------------------------------------------------------------------


def test_the_named_options_come_back_in_the_order_named_each_with_its_round_title_and_prompt(mod, rig, tmp_path):
    _build(mod, rig, tmp_path)

    got = _read(mod, rig, tmp_path, _ref([_d(2, 1, "A"), _d(1, 2, "B")]))

    assert got.site == "header-and-sign-in"
    assert got.title == "What we are building"
    assert [(d.round, d.question, d.letter) for d in got.drawings] == [(2, 1, "A"), (1, 2, "B")]
    assert [d.round_title for d in got.drawings] == ["Sign-in round", "Header round"]
    assert [d.prompt for d in got.drawings] == ["What happens on a wrong password?", "How do controls sit?"]
    assert got.drawings[0].option == ROUND_TWO["questions"][0]["options"][0]
    assert got.drawings[1].option == ROUND_ONE["questions"][1]["options"][1]


def test_other_letters_give_the_other_options(mod, rig, tmp_path):
    _build(mod, rig, tmp_path)

    got = _read(mod, rig, tmp_path, _ref([_d(2, 1, "C"), _d(1, 2, "A"), _d(1, 1, "B")]))

    assert [d.option["name"] for d in got.drawings] == ["Hint first", "Controls inline", "Logo centre"]
    assert [d.letter for d in got.drawings] == ["C", "A", "B"]


def test_two_options_of_one_question_may_both_be_named(mod, rig, tmp_path):
    _build(mod, rig, tmp_path)

    got = _read(mod, rig, tmp_path, _ref([_d(1, 1, "A"), _d(1, 1, "B")]))

    assert [d.option["name"] for d in got.drawings] == ["Logo left", "Logo centre"]


def test_a_pad_in_a_workspace_is_read_from_the_workspace_sites_from_a_member_folder(mod, rig, tmp_path):
    env, _work, _state, kit = rig
    disk = _build(mod, rig, tmp_path)
    ws = Path(env["CAMP_STATE_DIR"]) / "grp" / "worktrees" / "ws"
    (ws / "sites").mkdir(parents=True)
    shutil.copytree(disk, ws / "sites" / "p")
    shutil.rmtree(disk)
    member = ws / "repo" / "deep"
    member.mkdir(parents=True)

    got = mod.read_reference(_write_json(tmp_path / "ref.json", _ref([_d(2, 1, "B")])), "p", env=env, cwd=member)

    assert got.drawings[0].option["name"] == "Lock out"
    assert got.drawings[0].round_title == "Sign-in round"


def test_a_workspace_run_does_not_fall_back_to_a_pad_on_disk(mod, rig, tmp_path):
    env, _work, *_ = rig
    _build(mod, rig, tmp_path)
    ws = Path(env["CAMP_STATE_DIR"]) / "grp" / "worktrees" / "ws"
    (ws / "sites").mkdir(parents=True)

    with pytest.raises(mod.Refusal) as exc:
        mod.read_reference(_write_json(tmp_path / "ref.json", _ref([_d(1, 1, "A")])), "p", env=env, cwd=ws)

    assert exc.value.lines == ['scratch-pad: pad "p": does not exist — build it first, or name the pad it is on']


def _older_kit(pad_dir: Path):
    for k in sorted(pad_dir.glob("round-*")):
        lines = (k / "round.js").read_text(encoding="utf-8").splitlines(keepends=True)
        assert lines[-1].startswith("window.PAD_ROUND_ID")
        (k / "round.js").write_text("".join(lines[:-1]), encoding="utf-8")
    (pad_dir / "rounds.js").unlink()


def test_a_pad_built_by_an_older_kit_reads_back_the_same(mod, rig, tmp_path):
    ref = _ref([_d(2, 1, "A"), _d(1, 2, "B")])
    new = _read(mod, rig, tmp_path, ref) if _build(mod, rig, tmp_path) else None
    _older_kit(Path(rig[0]["OUTPOST_STATE_DIR"]) / "pads" / "p")

    old = _read(mod, rig, tmp_path, ref)

    assert old == new


# --- shape refusals ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate, expected",
    [
        pytest.param(lambda r: r.pop("drawings"), "scratch-pad: drawings: name at least one drawing", id="missing"),
        pytest.param(lambda r: r.update(drawings=[]), "scratch-pad: drawings: name at least one drawing", id="empty"),
        pytest.param(lambda r: r.update(drawings="A"), "scratch-pad: drawings: must be a list", id="not-a-list"),
        pytest.param(
            lambda r: r.update(bogus=1),
            'scratch-pad: reference: unknown key "bogus" — allowed: site, title, drawings',
            id="unknown-top-key",
        ),
        pytest.param(
            lambda r: r["drawings"][0].update(extra=1),
            'scratch-pad: drawings[0]: unknown key "extra" — allowed: round, question, option',
            id="unknown-drawing-key",
        ),
        pytest.param(
            lambda r: r.update(site="Not A Slug"),
            'scratch-pad: site: "Not A Slug" is not a valid site name',
            id="bad-site",
        ),
        pytest.param(
            lambda r: r.update(site="a" * 65),
            "scratch-pad: site: the site name is too long (65 characters) — the limit is 64",
            id="long-site",
        ),
        pytest.param(
            lambda r: r["drawings"][0].update(round=True),
            "scratch-pad: drawings[0].round: must be a whole number from 1",
            id="round-bool",
        ),
        pytest.param(
            lambda r: r["drawings"][0].pop("round"),
            'scratch-pad: drawings[0]: no "round" — name the round\'s number',
            id="no-round",
        ),
        pytest.param(
            lambda r: r["drawings"][0].pop("question"),
            'scratch-pad: drawings[0]: no "question" — name the question\'s number in that round',
            id="no-question",
        ),
        pytest.param(lambda r: r.update(site=7), "scratch-pad: site: must be a string", id="site-not-string"),
        pytest.param(lambda r: r.update(title=7), "scratch-pad: title: must be a string", id="title-not-string"),
        pytest.param(lambda r: r.pop("site"), 'scratch-pad: site: no "site" — name the page', id="no-site"),
        pytest.param(lambda r: r.update(title="  "), "scratch-pad: title: empty — name the page", id="empty-title"),
        pytest.param(lambda r: r.pop("title"), 'scratch-pad: title: no "title" — name the page', id="no-title"),
        pytest.param(
            lambda r: r["drawings"][0].update(round=0),
            "scratch-pad: drawings[0].round: must be a whole number from 1",
            id="round-zero",
        ),
        pytest.param(
            lambda r: r["drawings"][0].update(question="1"),
            "scratch-pad: drawings[0].question: must be a whole number from 1",
            id="question-string",
        ),
        pytest.param(
            lambda r: r["drawings"][0].update(option="b"),
            "scratch-pad: drawings[0].option: must be one capital letter, A to Z",
            id="option-lowercase",
        ),
        pytest.param(
            lambda r: r["drawings"][0].pop("option"),
            'scratch-pad: drawings[0]: no "option" — name the option\'s letter',
            id="no-option",
        ),
    ],
)
def test_a_reference_of_the_wrong_shape_is_refused_naming_the_fault_and_nothing_is_read(
    mod, rig, tmp_path, mutate, expected
):
    ref = _ref([_d(1, 1, "A")])
    mutate(ref)

    lines = _refusal(mod, rig, tmp_path, ref, pad="no-such-pad")

    assert expected in "\n".join(lines)
    assert not any("does not exist" in line for line in lines), "the shape is refused before the pad is looked at"
    assert not (Path(rig[0]["OUTPOST_STATE_DIR"]) / "pads").exists()


def test_a_site_name_of_exactly_the_limit_passes(mod, rig, tmp_path):
    _build(mod, rig, tmp_path)

    got = _read(mod, rig, tmp_path, _ref([_d(1, 1, "A")], site="a-" * 32))

    assert got.site == "a-" * 32


def test_every_problem_in_the_reference_is_named_in_one_pass(mod, rig, tmp_path):
    ref = {"title": "", "bogus": 1, "drawings": [_d(0, 1, "A"), {"round": 1, "question": 1, "option": "A", "x": 1}]}

    lines = _refusal(mod, rig, tmp_path, ref)

    text = "\n".join(lines)
    assert all(line.startswith("scratch-pad: ") for line in lines)
    for fragment in (
        'unknown key "bogus"',
        'no "site"',
        "title: empty",
        "drawings[0].round",
        'drawings[1]: unknown key "x"',
    ):
        assert fragment in text, fragment


def test_an_over_long_or_invalid_pad_name_is_refused(mod, rig, tmp_path):
    lines = _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A")]), pad="Bad Pad")

    assert lines[0].startswith('scratch-pad: --pad: "Bad Pad" is not a valid pad name')


@pytest.mark.parametrize(
    "content, fragment",
    [
        pytest.param("{not json", "invalid JSON at line 1", id="bad-json"),
        pytest.param("[1]", "reference: must be a JSON object", id="not-an-object"),
        pytest.param(b"\xff\xfe", "not valid UTF-8", id="not-utf8"),
    ],
)
def test_a_reference_file_that_cannot_be_read_as_one_is_refused_in_reference_wording(
    mod, rig, tmp_path, content, fragment
):
    env, work, *_ = rig
    path = tmp_path / "ref.json"
    path.write_bytes(content if isinstance(content, bytes) else content.encode())

    with pytest.raises(mod.Refusal) as exc:
        mod.read_reference(path, "p", env=env, cwd=work)

    assert fragment in "\n".join(exc.value.lines)


def test_a_missing_reference_file_is_refused_as_a_reference_not_a_round(mod, rig, tmp_path):
    env, work, *_ = rig

    with pytest.raises(mod.Refusal) as exc:
        mod.read_reference(tmp_path / "nope.json", "p", env=env, cwd=work)

    (line,) = exc.value.lines
    assert "cannot read the reference: No such file or directory" in line
    assert "round" not in line


# --- refusals against the pad -----------------------------------------------------------------------


def _untouched(rig, tmp_path, run):
    env, *_ = rig
    state = Path(env["OUTPOST_STATE_DIR"])
    vaults = Path(env["LORE_STATE_DIR"])
    before = (_without_locks(_snapshot(state) or {}), _snapshot(vaults))
    result = run()
    assert (_without_locks(_snapshot(state) or {}), _snapshot(vaults)) == before
    return result


@pytest.mark.parametrize(
    "drawing, expected",
    [
        pytest.param(_d(3, 1, "A"), "scratch-pad: drawings[0].round: 3 — the pad has 2 rounds", id="round-past-last"),
        pytest.param(
            _d(1, 3, "A"), "scratch-pad: drawings[0].question: 3 — round 1 has 2 questions", id="question-past-last"
        ),
        pytest.param(
            _d(2, 1, "D"),
            "scratch-pad: drawings[0].option: D — question 1 of round 2 has 3 options (A to C)",
            id="letter-past-last",
        ),
    ],
)
def test_a_drawing_the_pad_does_not_have_is_refused_naming_which_part(mod, rig, tmp_path, drawing, expected):
    _build(mod, rig, tmp_path)

    lines = _untouched(rig, tmp_path, lambda: _refusal(mod, rig, tmp_path, _ref([drawing])))

    assert lines == [expected]


def test_the_same_drawing_twice_is_refused_naming_both_places(mod, rig, tmp_path):
    _build(mod, rig, tmp_path)

    lines = _untouched(
        rig, tmp_path, lambda: _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A"), _d(2, 1, "B"), _d(1, 1, "A")]))
    )

    assert lines == ["scratch-pad: drawings[2]: the same drawing as drawings[0] (round 1, question 1, option A)"]


def test_every_missing_drawing_is_named_in_one_pass(mod, rig, tmp_path):
    _build(mod, rig, tmp_path)

    lines = _refusal(mod, rig, tmp_path, _ref([_d(9, 1, "A"), _d(1, 1, "B"), _d(1, 9, "A"), _d(2, 1, "Z")]))

    assert [line.split(":")[1].strip() for line in lines] == [
        "drawings[0].round",
        "drawings[2].question",
        "drawings[3].option",
    ]


def test_a_pad_that_does_not_exist_is_refused_and_not_created(mod, rig, tmp_path):
    lines = _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A")]), pad="ghost")

    assert lines == ['scratch-pad: pad "ghost": does not exist — build it first, or name the pad it is on']
    assert not (Path(rig[0]["OUTPOST_STATE_DIR"]) / "pads").exists()


def test_a_pad_the_read_back_refuses_has_its_reasons_relayed_in_reference_wording(mod, rig, tmp_path):
    pad_dir = _build(mod, rig, tmp_path)
    (pad_dir / "round-2" / "round.js").unlink()
    (pad_dir / "notes.txt").write_text("hand-made")

    lines = _untouched(rig, tmp_path, lambda: _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A")])))

    (line,) = lines
    assert line.startswith('scratch-pad: pad "p" can\'t be referenced: ')
    assert "round-2/round.js is missing" in line
    assert "notes.txt is not part of a pad this script builds" in line
    assert "another round" not in line and "--add" not in line and "--pad" not in line


def test_a_pad_folder_that_cannot_be_read_is_refused_in_reference_wording(mod, rig, tmp_path):
    pad_dir = _build(mod, rig, tmp_path)
    shutil.rmtree(pad_dir)
    pad_dir.symlink_to(tmp_path)

    lines = _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A")]))

    assert lines[0].startswith('scratch-pad: pad "p" can\'t be referenced: the pad folder is a symlink')


NOT_A_ROUND = "scratch-pad: drawings[0].round: 2 — round 2's data does not hold a list of questions"
NOT_A_QUESTION = (
    "scratch-pad: drawings[0].question: 1 — question 1 of round 2 does not hold a prompt and a list of options"
)


@pytest.mark.parametrize(
    "questions, expected",
    [
        pytest.param(5, NOT_A_ROUND, id="questions-not-a-list"),
        pytest.param([7], NOT_A_QUESTION, id="question-not-an-object"),
        pytest.param([{"prompt": 3, "options": [{}]}], NOT_A_QUESTION, id="prompt-not-a-string"),
        pytest.param([{"prompt": "P", "options": 5}], NOT_A_QUESTION, id="options-not-a-list"),
    ],
)
def test_a_round_whose_data_is_not_a_round_is_refused_naming_the_round(mod, rig, tmp_path, questions, expected):
    pad_dir = _build(mod, rig, tmp_path)
    js = pad_dir / "round-2" / "round.js"
    lines = js.read_text(encoding="utf-8").splitlines(keepends=True)
    broken = {"title": "Sign-in round", "questions": questions}
    lines[0] = "window.PAD_ROUND = JSON.parse(" + json.dumps(json.dumps(broken)) + ");\n"
    js.write_text("".join(lines), encoding="utf-8")

    lines = _refusal(mod, rig, tmp_path, _ref([_d(2, 1, "A")]))

    assert lines == [expected]


def _break_round_one(mod, rig, tmp_path, broken):
    pad_dir = _build(mod, rig, tmp_path)
    js = pad_dir / "round-1" / "round.js"
    lines = js.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[0] = "window.PAD_ROUND = JSON.parse(" + json.dumps(json.dumps(broken)) + ");\n"
    js.write_text("".join(lines), encoding="utf-8")


def _round_one_with_option_b_of_question_one(replacement):
    broken = copy.deepcopy(ROUND_ONE)
    broken["questions"][0]["options"][1] = replacement
    return broken


def test_a_question_the_reference_does_not_name_may_be_malformed(mod, rig, tmp_path):
    broken = copy.deepcopy(ROUND_ONE)
    broken["questions"][1] = 7
    _break_round_one(mod, rig, tmp_path, broken)

    got = _read(mod, rig, tmp_path, _ref([_d(1, 1, "A")]))

    assert [d.option["name"] for d in got.drawings] == ["Logo left"]


def test_an_option_the_reference_does_not_name_may_be_malformed(mod, rig, tmp_path):
    _break_round_one(mod, rig, tmp_path, _round_one_with_option_b_of_question_one(None))

    got = _read(mod, rig, tmp_path, _ref([_d(2, 1, "A"), _d(1, 1, "A")]))

    assert [d.option["name"] for d in got.drawings] == ["Retry in place", "Logo left"]


def test_a_named_option_that_is_not_an_object_is_refused_naming_that_drawing(mod, rig, tmp_path):
    _break_round_one(mod, rig, tmp_path, _round_one_with_option_b_of_question_one(None))

    lines = _refusal(mod, rig, tmp_path, _ref([_d(2, 1, "A"), _d(1, 1, "B")]))

    assert lines == ["scratch-pad: drawings[1] (round 1, question 1, option B): must be an object"]


# --- the lock ---------------------------------------------------------------------------------------


def test_a_pad_with_an_add_in_flight_makes_the_reference_wait_for_it(mod, rig, tmp_path, capsys):
    pad_dir = _build(mod, rig, tmp_path)
    held = _hold(_lock_path(rig[0], pad_dir))
    box: dict = {}

    def run():
        box["got"] = _read(mod, rig, tmp_path, _ref([_d(1, 1, "A")]), publish_timeout=5)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        err = ""
        while "waiting for another session's add" not in err and time.monotonic() < deadline:
            err += capsys.readouterr().err
            time.sleep(0.02)
        assert "waiting for another session's add" in err
        assert "got" not in box, "the reference returned while the add held the lock"
    finally:
        os.close(held)
        thread.join(timeout=5)

    assert box["got"].drawings[0].option["name"] == "Logo left"


def test_a_lock_held_past_the_wait_limit_refuses_and_changes_nothing(mod, rig, tmp_path):
    pad_dir = _build(mod, rig, tmp_path)
    held = _hold(_lock_path(rig[0], pad_dir))
    try:
        lines = _untouched(
            rig, tmp_path, lambda: _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A")]), publish_timeout=0.05)
        )
    finally:
        os.close(held)

    assert lines == ['scratch-pad: another session is adding a round to pad "p"; try again']


def test_the_lock_is_released_once_the_pad_is_read(mod, rig, tmp_path):
    pad_dir = _build(mod, rig, tmp_path)

    _read(mod, rig, tmp_path, _ref([_d(1, 1, "A")]))

    assert _try_lock(_lock_path(rig[0], pad_dir))


def test_the_lock_is_released_when_the_reference_is_refused_after_the_read(mod, rig, tmp_path):
    pad_dir = _build(mod, rig, tmp_path)

    _refusal(mod, rig, tmp_path, _ref([_d(7, 1, "A")]))

    assert _try_lock(_lock_path(rig[0], pad_dir))


# --- the credential-pattern check -------------------------------------------------------------------

AKIA = "AKIA" + "IOSFODNN7EXAMPLE"
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
B64_32 = "Zm9vYmFyYmF6cXV4" * 2
HEX_40 = "0123456789abcdef" * 2 + "01234567"


@pytest.mark.parametrize(
    "text, family",
    [
        pytest.param("pass" + "word: hunter2hunter2", "key-like", id="key-like"),
        pytest.param("sec" + "ret=abc", "key-like", id="key-like-secret"),
        pytest.param("see " + AKIA, "vendor-prefix", id="vendor-aws"),
        pytest.param("gh" + "p_" + "A1" * 18, "vendor-prefix", id="vendor-github"),
        pytest.param("Bear" + "er abc.def-123", "bearer", id="bearer"),
        pytest.param("x_api" + '_key": "' + "k" * 16 + '"', "api-key-shape", id="api-key-shape"),
        pytest.param(B64_32, "high-entropy-base64", id="base64"),
        pytest.param(HEX_40, "high-entropy-hex", id="hex"),
        pytest.param("pre " + PEM, "pem-private-key", id="pem"),
    ],
)
def test_each_credential_pattern_family_flags_a_positive_string(mod, text, family):
    assert family in mod.credential_patterns(text)


@pytest.mark.parametrize(
    "text, family",
    [
        pytest.param("the pass" + "word field", "key-like", id="key-like-no-value"),
        pytest.param("pass" + "word:", "key-like", id="key-like-empty-value"),
        pytest.param("AKIA" + "IOSFODNN7EXAMPL", "vendor-prefix", id="vendor-one-short"),
        pytest.param("gh" + "p_" + "A1" * 17, "vendor-prefix", id="vendor-github-short"),
        pytest.param("Bear" + "er", "bearer", id="bearer-alone"),
        pytest.param("x_api" + '_key": "' + "k" * 15 + '"', "api-key-shape", id="api-key-shape-15"),
        pytest.param(B64_32[:31], "high-entropy-base64", id="base64-31"),
        pytest.param(HEX_40[:39], "high-entropy-hex", id="hex-39"),
        pytest.param("-----BEGIN " + "PUBLIC KEY-----", "pem-private-key", id="pem-public"),
    ],
)
def test_each_credential_pattern_family_passes_its_near_miss(mod, text, family):
    assert family not in mod.credential_patterns(text)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("aws_" + AKIA, id="aws-after-underscore"),
        pytest.param("key" + AKIA, id="aws-after-letters"),
        pytest.param("x_gh" + "p_" + "A1" * 18, id="github-after-underscore"),
        pytest.param("gh" + "p_" + "A1" * 18 + "_old", id="github-before-underscore"),
    ],
)
def test_a_vendor_token_glued_to_other_text_by_an_underscore_or_letters_is_still_flagged(mod, text):
    assert "vendor-prefix" in mod.credential_patterns(text)


def test_a_vendor_prefix_inside_a_plain_word_is_not_flagged(mod):
    assert mod.credential_patterns("MAKIAbility and ghp_andsoon") == []


def test_a_vendor_token_one_character_too_long_is_not_flagged(mod):
    assert "vendor-prefix" not in mod.credential_patterns(AKIA + "X")


@pytest.mark.parametrize("glue", ["x_", "_"])
def test_a_hex_run_glued_to_an_underscore_is_still_flagged(mod, glue):
    assert "high-entropy-hex" in mod.credential_patterns(glue + HEX_40)


def test_a_long_string_of_repeated_key_words_is_scanned_in_linear_time(mod):
    text = "to" + "ken" + ("token" * 40000)
    started = time.monotonic()
    mod.credential_patterns(text)
    assert time.monotonic() - started < 2.0


def test_a_key_word_run_longer_than_the_scan_bound_with_a_value_after_it_is_still_flagged(mod):
    text = ("token" * 40000) + "=abc"
    assert "key-like" in mod.credential_patterns(text)


def test_a_url_path_and_ordinary_words_match_no_family(mod):
    for text in ("/settings/account/profile", "https://example.com/docs", "Retry in place", "(Open page) → [Form]"):
        assert mod.credential_patterns(text) == [], text


def _with_pad_text(**fields):
    """Round data with credential-shaped text swapped into the named page-bound spots."""
    one = copy.deepcopy(ROUND_ONE)
    one["title"] = fields.get("round_title", one["title"])
    q = one["questions"][0]
    q["prompt"] = fields.get("prompt", q["prompt"])
    opt = q["options"][0]
    opt["name"] = fields.get("name", opt["name"])
    opt["cost"] = fields.get("cost", opt["cost"])
    if "layout" in fields:
        opt["layout"] = fields["layout"]
    if "flow" in fields:
        del opt["layout"]
        opt["flow"] = fields["flow"]
    return one


KEYLIKE = "pass" + "word=hunter2hunter2"
FIELD_CASES = [
    pytest.param({"round_title": KEYLIKE}, {}, "round title", id="round-title"),
    pytest.param({"prompt": KEYLIKE}, {}, "prompt", id="prompt"),
    pytest.param({"name": KEYLIKE}, {}, "name", id="name"),
    pytest.param({"cost": KEYLIKE}, {}, "cost", id="cost"),
    pytest.param({"layout": {"rule": True, "text": "x " + AKIA}}, {}, "layout", id="layout-value"),
    pytest.param({"layout": {"kids": [{"label": [KEYLIKE]}]}}, {}, "layout", id="layout-deep-value"),
    pytest.param({"layout": {AKIA: "value"}}, {}, "layout", id="layout-key"),
    pytest.param({"flow": "(Open) -> [" + AKIA + "]"}, {}, "flow", id="flow"),
    pytest.param({}, {"title": KEYLIKE}, "title", id="reference-title"),
    pytest.param({}, {"site": HEX_40}, "site", id="site"),
]


@pytest.mark.parametrize("pad_text, ref_over, field", FIELD_CASES)
def test_a_credential_in_any_page_bound_string_refuses_the_reference_naming_drawing_and_field_not_the_text(
    mod, rig, tmp_path, pad_text, ref_over, field
):
    _build(mod, rig, tmp_path, rounds=(_with_pad_text(**pad_text), ROUND_TWO))
    ref = {**_ref([_d(2, 1, "A"), _d(1, 1, "A")]), **ref_over}

    lines = _untouched(rig, tmp_path, lambda: _refusal(mod, rig, tmp_path, ref))

    (line,) = lines
    where = "drawings[1] (round 1, question 1, option A)" if field not in ("title", "site") else field
    assert line.startswith(f"scratch-pad: {where}: ") and field in line and "credential pattern" in line
    for secret in (KEYLIKE, AKIA, HEX_40, "hunter2"):
        assert secret not in line


@pytest.mark.parametrize("pad_text, ref_over, field", FIELD_CASES)
def test_the_same_reference_with_the_credential_string_removed_passes(mod, rig, tmp_path, pad_text, ref_over, field):
    clean = {k: ("Fine" if isinstance(v, str) else {"rule": True}) for k, v in pad_text.items()}
    if "flow" in clean:
        clean["flow"] = "(Open) -> [Form]"
    _build(mod, rig, tmp_path, rounds=(_with_pad_text(**clean), ROUND_TWO))
    clean_ref = {k: "fine-name" if k == "site" else "A title" for k in ref_over}

    got = _read(mod, rig, tmp_path, {**_ref([_d(2, 1, "A"), _d(1, 1, "A")]), **clean_ref})

    assert len(got.drawings) == 2


def test_a_credential_in_a_drawing_that_is_not_named_does_not_refuse(mod, rig, tmp_path):
    _build(mod, rig, tmp_path, rounds=(_with_pad_text(cost=KEYLIKE), ROUND_TWO))

    got = _read(mod, rig, tmp_path, _ref([_d(1, 2, "A")]))

    assert got.drawings[0].option["name"] == "Controls inline"


def test_every_credential_match_is_named_in_one_pass(mod, rig, tmp_path):
    _build(mod, rig, tmp_path, rounds=(_with_pad_text(cost=KEYLIKE, name=AKIA), ROUND_TWO))

    lines = _refusal(mod, rig, tmp_path, {**_ref([_d(1, 1, "A")]), "title": PEM})

    assert [line.split(":")[1].strip().split(" ")[0] for line in lines] == ["title", "drawings[0]", "drawings[0]"]


# --- a chosen drawing the page cannot be built from -------------------------------------------------


def _hand_edit_option(pad_dir: Path, rnd: int, q: int, letter: str, option):
    """Replace one option in a round's round.js, as a hand edit would, keeping the file's other lines."""
    js = pad_dir / f"round-{rnd}" / "round.js"
    lines = js.read_text(encoding="utf-8").splitlines(keepends=True)
    prefix = "window.PAD_ROUND = JSON.parse("
    data = json.loads(json.loads(lines[0][len(prefix) : -3]))
    data["questions"][q - 1]["options"][ord(letter) - ord("A")] = option
    lines[0] = prefix + json.dumps(json.dumps(data)) + ");\n"
    js.write_text("".join(lines), encoding="utf-8")


WHERE = "scratch-pad: drawings[0] (round 1, question 1, option A)"
NO_SHAPE = 'no "layout" or "flow" — give it either a layout or a flow'
MALFORMED = [
    pytest.param({"name": "n", "cost": "c"}, [f"{WHERE}: {NO_SHAPE}"], id="no-shape"),
    pytest.param(
        {},
        [
            f'{WHERE}: no "name" — every option has a name',
            f'{WHERE}: no "cost" — every option states what it costs',
            f"{WHERE}: {NO_SHAPE}",
        ],
        id="empty-object",
    ),
    pytest.param(
        {"cost": "c", "layout": {}}, [f'{WHERE}: no "name" — every option has a name'], id="missing-name"
    ),
    pytest.param(
        {"name": "n", "layout": {}}, [f'{WHERE}: no "cost" — every option states what it costs'], id="missing-cost"
    ),
    pytest.param({"name": 7, "cost": "c", "layout": {}}, [f"{WHERE}.name: must be a string"], id="name-not-a-string"),
    pytest.param(
        {"name": "n", "cost": None, "layout": {}}, [f"{WHERE}.cost: must be a string"], id="cost-not-a-string"
    ),
    pytest.param(
        {"name": "n", "cost": "c", "flow": ["(A) -> [B]"]}, [f"{WHERE}.flow: must be a string"], id="flow-a-list"
    ),
]


@pytest.mark.parametrize("option, expected", MALFORMED)
def test_a_chosen_option_the_page_cannot_be_built_from_is_refused_naming_the_drawing_and_the_fault(
    mod, rig, tmp_path, option, expected
):
    pad_dir = _build(mod, rig, tmp_path)
    _hand_edit_option(pad_dir, 1, 1, "A", option)

    lines = _untouched(rig, tmp_path, lambda: _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A")])))

    assert lines == expected


def test_the_same_pad_with_a_valid_chosen_option_reads_back_and_the_page_builds_from_it(mod, rig, tmp_path):
    pad_dir = _build(mod, rig, tmp_path)
    _hand_edit_option(pad_dir, 1, 1, "A", {"name": "n", "cost": "c", "layout": {"rule": True}})

    got = _read(mod, rig, tmp_path, _ref([_d(1, 1, "A")]))
    out = tmp_path / "page"
    mod.build_reference_page(out, got)

    assert got.drawings[0].option["name"] == "n"
    literal = (out / "reference-data.js").read_text(encoding="utf-8").split("JSON.parse(", 1)[1].rsplit(");", 1)[0]
    (page_drawing,) = json.loads(json.loads(literal))["drawings"]
    assert (page_drawing["name"], page_drawing["cost"], page_drawing["layout"]) == ("n", "c", {"rule": True})


@pytest.mark.parametrize("layout", [None, "x"])
def test_a_layout_the_add_path_accepts_is_accepted_by_the_reference_too(mod, rig, tmp_path, layout):
    pad_dir = _build(mod, rig, tmp_path)
    option = {"name": "n", "cost": "c", "layout": layout}
    _hand_edit_option(pad_dir, 1, 1, "A", option)
    round_data = {"title": "T", "questions": [{"prompt": "P", "options": [{**option, "pick": True}]}]}
    assert mod.check_round(round_data) == []

    got = _read(mod, rig, tmp_path, _ref([_d(1, 1, "A")]))
    mod.build_reference_page(tmp_path / "page", got)

    assert got.drawings[0].option["layout"] == layout


def test_a_malformed_option_the_reference_does_not_name_does_not_refuse_it(mod, rig, tmp_path):
    pad_dir = _build(mod, rig, tmp_path)
    _hand_edit_option(pad_dir, 1, 1, "B", {})

    got = _read(mod, rig, tmp_path, _ref([_d(1, 1, "A"), _d(2, 1, "A")]))

    assert [d.option["name"] for d in got.drawings] == ["Logo left", "Retry in place"]


def test_two_malformed_chosen_drawings_are_both_reported_in_one_pass(mod, rig, tmp_path):
    pad_dir = _build(mod, rig, tmp_path)
    _hand_edit_option(pad_dir, 1, 1, "A", {"name": "n", "cost": "c"})
    _hand_edit_option(pad_dir, 2, 1, "C", {"name": 7, "cost": "c", "flow": "(A) -> [B]"})

    lines = _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A"), _d(2, 1, "B"), _d(2, 1, "C")]))

    assert lines == [
        f"{WHERE}: {NO_SHAPE}",
        "scratch-pad: drawings[2] (round 2, question 1, option C).name: must be a string",
    ]


def test_a_malformed_chosen_drawing_is_refused_for_its_shape_before_its_text_is_checked_for_credentials(
    mod, rig, tmp_path
):
    pad_dir = _build(mod, rig, tmp_path)
    _hand_edit_option(pad_dir, 1, 1, "A", {"name": KEYLIKE, "cost": "c"})

    lines = _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A")]))

    assert lines == [f"{WHERE}: {NO_SHAPE}"]


# --- a chosen drawing holding a number JSON has no way to write -------------------------------------------

NON_FINITE = {"NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("-inf")}


def _non_finite_line(constant: str, where: str = WHERE, path: str = "") -> str:
    return f"{where}{path}: holds {constant}, which is not valid JSON — a number must be finite"


@pytest.mark.parametrize("constant", NON_FINITE)
def test_a_chosen_layout_holding_a_non_finite_number_is_refused_naming_the_drawing(mod, rig, tmp_path, constant):
    pad_dir = _build(mod, rig, tmp_path)
    option = {"name": "n", "cost": "c", "layout": {"rule": True, "w": NON_FINITE[constant]}}
    _hand_edit_option(pad_dir, 1, 1, "A", option)
    assert constant in (pad_dir / "round-1" / "round.js").read_text(encoding="utf-8")

    lines = _untouched(rig, tmp_path, lambda: _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A")])))

    assert lines == [_non_finite_line(constant)]


@pytest.mark.parametrize("constant", NON_FINITE)
def test_a_non_finite_number_deep_in_a_chosen_layout_is_refused(mod, rig, tmp_path, constant):
    pad_dir = _build(mod, rig, tmp_path)
    deep = {"a": [{"b": [1, {"c": [NON_FINITE[constant]]}]}]}
    _hand_edit_option(pad_dir, 1, 1, "A", {"name": "n", "cost": "c", "layout": deep})

    lines = _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A")]))

    assert lines == [_non_finite_line(constant)]


def test_a_non_finite_number_in_an_option_the_reference_does_not_name_does_not_refuse_it(mod, rig, tmp_path):
    pad_dir = _build(mod, rig, tmp_path)
    for letter, constant in (("B", "NaN"), ("A", "Infinity")):
        _hand_edit_option(
            pad_dir, 2 if letter == "A" else 1, 1, letter,
            {"name": "n", "cost": "c", "layout": {"w": NON_FINITE[constant]}},
        )

    got = _read(mod, rig, tmp_path, _ref([_d(1, 1, "A"), _d(1, 2, "A")]))

    assert [d.option["name"] for d in got.drawings] == ["Logo left", "Controls inline"]


def test_two_chosen_drawings_with_non_finite_numbers_are_both_reported_in_one_pass(mod, rig, tmp_path):
    pad_dir = _build(mod, rig, tmp_path)
    _hand_edit_option(pad_dir, 1, 1, "A", {"name": "n", "cost": "c", "layout": {"w": NON_FINITE["NaN"]}})
    _hand_edit_option(pad_dir, 2, 1, "A", {"name": "n", "cost": "c", "layout": [NON_FINITE["-Infinity"]]})

    lines = _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A"), _d(2, 1, "A")]))

    assert lines == [
        _non_finite_line("NaN"),
        _non_finite_line("-Infinity", "scratch-pad: drawings[1] (round 2, question 1, option A)"),
    ]


def test_a_chosen_drawing_with_a_non_finite_number_is_refused_before_its_text_is_checked_for_credentials(
    mod, rig, tmp_path
):
    pad_dir = _build(mod, rig, tmp_path)
    _hand_edit_option(
        pad_dir, 1, 1, "A", {"name": KEYLIKE, "cost": "c", "layout": {"w": NON_FINITE["NaN"]}}
    )

    lines = _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A")]))

    assert lines == [_non_finite_line("NaN")]


def test_an_add_to_a_pad_holding_a_non_finite_option_still_builds_and_the_fixed_pad_builds_the_page(
    mod, rig, tmp_path
):
    env, work, _state, kit = rig
    pad_dir = _build(mod, rig, tmp_path)
    _hand_edit_option(pad_dir, 1, 1, "A", {"name": "n", "cost": "c", "layout": {"w": NON_FINITE["NaN"]}})

    third = _write_json(tmp_path / "r3.json", {"title": "Third", "questions": ROUND_ONE["questions"]})
    mod.build_pad(third, "p", kit_dir=kit, env=env, cwd=work, add=True)
    assert (pad_dir / "round-3" / "round.js").is_file()
    assert mod._round_title(pad_dir / "round-1" / "round.js") == "Header round"
    assert _refusal(mod, rig, tmp_path, _ref([_d(1, 1, "A")])) == [_non_finite_line("NaN")]

    _hand_edit_option(pad_dir, 1, 1, "A", {"name": "n", "cost": "c", "layout": {"w": 1.5}})
    got = _read(mod, rig, tmp_path, _ref([_d(1, 1, "A"), _d(3, 1, "B")]))
    out = tmp_path / "page"
    mod.build_reference_page(out, got)

    literal = (out / "reference-data.js").read_text(encoding="utf-8").split("JSON.parse(", 1)[1].rsplit(");", 1)[0]
    drawings = json.loads(json.loads(literal))["drawings"]
    assert drawings[0]["layout"] == {"w": 1.5}
    assert drawings[1]["name"] == "Logo centre"


# --- publishing the reference to the vault ------------------------------------------------------------

PUBLISHER = SCRIPT_PATH.parent.parent / "publish-site" / "publish_site.py"
SERVED_SITES_PORT = 7314
REF = _ref([_d(2, 1, "A"), _d(1, 2, "B")])


def _stub_lore(bin_dir: Path, record: Path, *, exit_code=0, out="", err="", hold: Path | None = None):
    bin_dir.mkdir(parents=True, exist_ok=True)
    lines = ["#!/bin/sh", f'echo "$@" >> "{record}"']
    if out:
        lines.append(f'echo "{out}"')
    if err:
        lines.append(f'echo "{err}" >&2')
    if hold is not None:
        lines.append(f'n=0; while [ ! -e "{hold}" ] && [ $n -lt 600 ]; do sleep 0.05; n=$((n+1)); done')
    lines.append(f"exit {exit_code}")
    stub = bin_dir / "lore"
    stub.write_text("\n".join(lines) + "\n")
    stub.chmod(0o755)


class Vault:
    def __init__(self, tmp_path, rig, name="acme"):
        self.env, self.work, self.state, self.kit = rig
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.record = tmp_path / "lore-argv.txt"
        self.scratch = tmp_path / "scratch-tmp"
        self.scratch.mkdir()
        self.name = name
        self.path = tmp_path / "xdg" / "lore" / "vaults" / name
        self.path.mkdir(parents=True)
        self.env = {
            **self.env,
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "TMPDIR": str(self.scratch),
        }
        _stub_lore(self.bin, self.record)

    def ref_file(self, ref=REF):
        return _write_json(self.tmp / "ref.json", ref)

    def argv(self, ref=REF, *, vault=True, vault_path=None):
        out = ["--pad", "p", "--reference", str(self.ref_file(ref)), "--vault-path", str(vault_path or self.path)]
        return out + (["--vault", self.name] if vault else [])

    def sites(self, site="header-and-sign-in"):
        return self.path / "sites" / site

    def lore_calls(self):
        return self.record.read_text().splitlines() if self.record.exists() else []


@pytest.fixture
def vault(mod, rig, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "scratch-tmp"))
    v = Vault(tmp_path, rig)
    _build(mod, rig, tmp_path)
    return v


def _run_main(mod, vault, argv=None, **kw):
    return mod.main(argv or vault.argv(), env=vault.env, cwd=vault.work, **kw)


def _link(vault, site="header-and-sign-in"):
    return f"http://127.0.0.1:{SERVED_SITES_PORT}/{vault.name}/{site}/"


def test_a_reference_run_publishes_the_page_and_syncs_that_vault_and_prints_the_link_last(mod, vault, capfd):
    assert _run_main(mod, vault) == 0

    out = capfd.readouterr().out.splitlines()
    assert (vault.sites() / "index.html").is_file()
    data = (vault.sites() / "reference-data.js").read_text(encoding="utf-8")
    assert "Retry in place" in data and "Controls stacked" in data
    assert vault.lore_calls() == ["sync --vault acme"]
    assert out[-1] == _link(vault)


def test_a_reference_run_with_no_vault_name_syncs_bare(mod, vault, capfd):
    assert _run_main(mod, vault, vault.argv(vault=False)) == 0

    assert vault.lore_calls() == ["sync"]
    assert capfd.readouterr().out.splitlines()[-1] == _link(vault)


def test_the_published_reference_opens_from_the_vault_folder_and_draws(mod, rig, vault, tmp_path):
    env, work, _state, kit = rig
    drawable = {
        "title": "Drawn",
        "questions": [
            {
                "prompt": "Where?",
                "options": [
                    _opt("Boxy", pick=True, layout={"box": [{"text": "Alpha"}, {"button": "Go"}]}),
                    _opt("Other"),
                ],
            }
        ],
    }
    mod.build_pad(_write_json(tmp_path / "d.json", drawable), "d", kit_dir=kit, env=env, cwd=work)
    argv = vault.argv(_ref([_d(1, 1, "A")], site="drawn"))
    argv[1] = "d"

    assert _run_main(mod, vault, argv) == 0

    got = ch.run_probe(
        vault.sites("drawn"),
        "await new Promise((r) => setTimeout(r, 200));"
        " return {cards: document.querySelectorAll('.ref-card').length,"
        " strokes: document.querySelectorAll('svg.pad-ink path').length};",
    )
    assert got["cards"] == 1
    assert got["strokes"] > 0


def test_one_line_says_it_is_publishing_to_the_vault_and_the_sync_can_wait_before_the_link(mod, vault, capfd):
    assert _run_main(mod, vault) == 0

    out = capfd.readouterr().out.splitlines()
    assert out[0] == mod.PUBLISHING_LINE
    assert out.count(mod.PUBLISHING_LINE) == 1


def test_a_reference_from_a_workspace_member_reads_the_workspace_pad_and_writes_nothing_else_there(
    mod, rig, vault, tmp_path, capfd
):
    env = vault.env
    ws = Path(env["CAMP_STATE_DIR"]) / "grp" / "worktrees" / "ws"
    member = ws / "repo"
    member.mkdir(parents=True)
    env["CAMP_CONFIG_DIR"] = str(tmp_path / "camp-config")
    shutil.rmtree(vault.state / "pads" / "p")
    first = _write_json(tmp_path / "r0.json", ROUND_ONE)
    assert mod.main(["--pad", "p", "--round", str(first)], env=env, cwd=member) == 0
    assert mod.main(
        ["--pad", "p", "--round", str(_write_json(tmp_path / "r1.json", ROUND_TWO)), "--add"], env=env, cwd=member
    ) == 0
    capfd.readouterr()
    before = _snapshot(ws.resolve() / "sites")

    assert mod.main(vault.argv(), env=env, cwd=member) == 0

    assert (vault.sites() / "index.html").is_file()
    assert _snapshot(ws.resolve() / "sites") == before
    assert list((ws.resolve() / "sites").iterdir()) == [ws.resolve() / "sites" / "p"]
    assert capfd.readouterr().out.splitlines()[-1] == _link(vault)


def _no_runner(*_a):
    pytest.fail("the publisher ran")


def test_a_site_name_already_in_the_vault_is_refused_before_the_publisher_runs(mod, vault, capsys):
    vault.sites().mkdir(parents=True)
    (vault.sites() / "mine.txt").write_text("keep")

    assert _run_main(mod, vault, publish_runner=_no_runner) == 1

    err = capsys.readouterr().err.splitlines()
    assert err == [mod._line('site "header-and-sign-in"', "already exists in the vault — choose another site name")]
    assert (vault.sites() / "mine.txt").read_text() == "keep"
    assert vault.lore_calls() == []


def test_a_dangling_symlink_at_the_site_name_counts_as_taken(mod, vault, capsys):
    (vault.path / "sites").mkdir()
    vault.sites().symlink_to(vault.path / "nowhere")

    assert _run_main(mod, vault, publish_runner=_no_runner) == 1

    assert "already exists in the vault" in capsys.readouterr().err


def test_a_different_site_name_publishes_while_the_taken_one_stays(mod, vault, capfd):
    vault.sites().mkdir(parents=True)
    (vault.sites() / "mine.txt").write_text("keep")

    assert _run_main(mod, vault, vault.argv(_ref([_d(1, 1, "A")], site="another-name"))) == 0

    assert (vault.sites("another-name") / "index.html").is_file()
    assert (vault.sites() / "mine.txt").read_text() == "keep"
    assert capfd.readouterr().out.splitlines()[-1] == _link(vault, "another-name")


def _failing_sync(vault):
    _stub_lore(vault.bin, vault.record, exit_code=1, err="remote said no")


def test_a_failed_sync_exits_non_zero_and_relays_the_not_synced_lines(mod, vault, capfd):
    _failing_sync(vault)

    assert _run_main(mod, vault) == 1

    err = capfd.readouterr().err.splitlines()
    assert "scratch-pad: publish: remote said no" in err
    not_synced = "published locally but NOT synced \\u2014 `lore sync` exited 1 (its output is above)."
    assert f"scratch-pad: publish: error: {not_synced}" in err
    assert all(line.startswith("scratch-pad: publish: ") for line in err)


def test_a_failed_sync_prints_no_link(mod, vault, capfd):
    _failing_sync(vault)

    _run_main(mod, vault)

    assert "http://" not in capfd.readouterr().out


def test_a_failed_sync_leaves_the_page_in_the_vault(mod, vault):
    _failing_sync(vault)

    _run_main(mod, vault)

    assert (vault.sites() / "index.html").is_file()


def test_after_a_failed_sync_the_same_site_is_refused_as_taken(mod, vault, capsys):
    _failing_sync(vault)
    _run_main(mod, vault)
    capsys.readouterr()

    assert _run_main(mod, vault, publish_runner=_no_runner) == 1

    assert "already exists in the vault" in capsys.readouterr().err


def test_after_the_site_folder_is_removed_the_same_rerun_publishes(mod, vault, capfd):
    _failing_sync(vault)
    _run_main(mod, vault)
    shutil.rmtree(vault.sites())
    _stub_lore(vault.bin, vault.record)
    capfd.readouterr()

    assert _run_main(mod, vault) == 0

    assert capfd.readouterr().out.splitlines()[-1] == _link(vault)
    assert (vault.sites() / "index.html").is_file()


def _start_cli(vault, argv):
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT_PATH), *argv],
        env=vault.env,
        cwd=vault.work,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines: queue.Queue = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return proc, lines


def _next_line(lines, seconds=20):
    try:
        return lines.get(timeout=seconds)
    except queue.Empty:
        pytest.fail("no output line arrived in time")


def test_the_wait_line_and_the_syncs_own_line_are_readable_before_the_run_exits_and_an_add_completes_meanwhile(
    mod, vault, tmp_path
):
    release = tmp_path / "release"
    _stub_lore(vault.bin, vault.record, out="signing in to the remote", hold=release)
    proc, lines = _start_cli(vault, vault.argv())
    try:
        assert _next_line(lines) == mod.PUBLISHING_LINE
        assert _next_line(lines) == "signing in to the remote"
        assert proc.poll() is None

        add = _write_json(tmp_path / "r3.json", {**ROUND_TWO, "title": "Third round"})
        argv = ["--pad", "p", "--round", str(add), "--add"]
        assert mod.main(argv, env=vault.env, cwd=vault.work, publish_timeout=1) == 0
        assert proc.poll() is None

        release.write_text("go")
        assert _next_line(lines) == _link(vault)
        assert proc.wait(timeout=20) == 0
    finally:
        release.write_text("go")
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def _pump(stream):
    lines: queue.Queue = queue.Queue()

    def run():
        for line in stream:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(target=run, daemon=True).start()
    return lines


def test_the_syncs_stderr_wait_line_is_readable_before_the_run_exits(mod, vault, tmp_path):
    release = tmp_path / "release"
    _stub_lore(vault.bin, vault.record, err="lore: waiting for the vault write lock", hold=release)
    proc, _ = _start_cli(vault, vault.argv())
    errs = _pump(proc.stderr)
    try:
        assert _next_line(errs) == "scratch-pad: publish: lore: waiting for the vault write lock"
        assert proc.poll() is None
        release.write_text("go")
        assert proc.wait(timeout=20) == 0
        assert _next_line(errs) is None
    finally:
        release.write_text("go")
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def test_a_failing_sync_shows_each_of_its_stderr_lines_exactly_once_in_the_relay_form(mod, vault, capfd):
    _stub_lore(vault.bin, vault.record, exit_code=1, err="lore: waiting for the vault write lock\nremote said no")

    assert _run_main(mod, vault) == 1

    err = capfd.readouterr().err.splitlines()
    assert err.count("scratch-pad: publish: lore: waiting for the vault write lock") == 1
    assert err.count("scratch-pad: publish: remote said no") == 1
    assert all(line.startswith("scratch-pad: publish: ") for line in err)


def test_a_streamed_publisher_that_fails_with_no_stderr_still_reports_its_exit_status(mod, vault, capfd):
    def silent_failure(cmd, env, timeout):
        return mod._stream_publisher([sys.executable, "-c", "import sys; sys.exit(3)"], env, timeout)

    assert _run_main(mod, vault, publish_runner=silent_failure) == 1

    assert capfd.readouterr().err.splitlines() == ["scratch-pad: publish: the publisher exited with status 3"]


def test_a_publisher_that_exits_leaves_no_wait_on_a_descendant_still_holding_its_stderr(mod, tmp_path, capfd):
    pidfile = tmp_path / "child.pid"
    script = f'echo "said before exit" >&2; sleep 6 >/dev/null & echo $! > "{pidfile}"; exit 0'
    env = {"PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path)}
    started = time.monotonic()
    try:
        result = mod._stream_publisher(["/bin/sh", "-c", script], env)
        elapsed = time.monotonic() - started
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), 9)
            except ProcessLookupError:
                pass

    assert result.returncode == 0
    assert elapsed < 3.0
    assert capfd.readouterr().err.splitlines() == ["scratch-pad: publish: said before exit"]
    assert result.echoed is True


def _kill_pidfile(pidfile):
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text()), 9)
        except ProcessLookupError:
            pass


def test_no_relay_line_prints_after_the_streamed_call_returns_while_a_descendant_writes_stderr(
    mod, tmp_path, capfd, monkeypatch
):
    real_line = mod._line

    def slow_line(*args):
        time.sleep(0.03)
        return real_line(*args)

    monkeypatch.setattr(mod, "_line", slow_line)
    pidfile = tmp_path / "child.pid"
    script = f'(while :; do echo tick >&2; done) & echo $! > "{pidfile}"; exit 0'
    env = {"PATH": "/usr/bin:/bin", "TMPDIR": str(tmp_path)}
    late = []
    for _ in range(25):
        capfd.readouterr()
        try:
            mod._stream_publisher(["/bin/sh", "-c", script], env, drain=0.02)
            print("MARKER", file=sys.stderr, flush=True)
            time.sleep(0.1)
        finally:
            _kill_pidfile(pidfile)
        err = capfd.readouterr().err
        late.append(err[err.index("MARKER") + len("MARKER") :].strip())
    assert [tail for tail in late if tail] == []


def test_a_failed_publish_whose_last_stderr_line_missed_the_drain_still_gives_its_exit_status(
    mod, vault, tmp_path, capfd
):
    pidfile = tmp_path / "child.pid"
    script = f'echo "early line" >&2; (sleep 5; echo "error: NOT synced" >&2) & echo $! > "{pidfile}"; exit 4'

    def runner(cmd, env, timeout):
        return mod._stream_publisher(["/bin/sh", "-c", script], env, timeout, drain=0.2)

    try:
        assert _run_main(mod, vault, publish_runner=runner) == 1
    finally:
        _kill_pidfile(pidfile)

    assert capfd.readouterr().err.splitlines() == [
        "scratch-pad: publish: early line",
        "scratch-pad: publish: the publisher exited with status 4",
    ]


def test_a_failed_publish_whose_stderr_was_all_relayed_adds_no_second_line(mod, vault, capfd):
    def runner(cmd, env, timeout):
        return mod._stream_publisher(
            [sys.executable, "-c", "import sys; print('only line', file=sys.stderr); sys.exit(4)"], env, timeout
        )

    assert _run_main(mod, vault, publish_runner=runner) == 1

    assert capfd.readouterr().err.splitlines() == ["scratch-pad: publish: only line"]


def test_a_publish_that_waits_longer_than_the_publish_timeout_is_not_killed(mod, vault, tmp_path, capfd):
    release = tmp_path / "release"
    _stub_lore(vault.bin, vault.record, hold=release)

    timer = threading.Timer(1.0, lambda: release.write_text("go"))
    timer.start()
    try:
        assert _run_main(mod, vault, publish_timeout=0.2) == 0
    finally:
        release.write_text("go")
        timer.cancel()

    assert capfd.readouterr().out.splitlines()[-1] == _link(vault)


def test_a_publisher_refusal_is_relayed_and_nothing_is_left_in_the_vault(mod, vault, tmp_path, capsys):
    elsewhere = tmp_path / "not-under-the-vaults-root"
    elsewhere.mkdir()

    assert _run_main(mod, vault, vault.argv(vault_path=elsewhere)) == 1

    err = capsys.readouterr().err.splitlines()
    assert err and all(line.startswith("scratch-pad: publish: error: ") for line in err)
    assert "is not a direct child of the vaults root" in err[0]
    assert list(elsewhere.iterdir()) == []
    assert vault.lore_calls() == []


def test_a_publisher_that_cannot_be_started_is_refused_under_publish(mod, vault, capsys):
    def cannot(cmd, env, timeout):
        raise FileNotFoundError(2, "No such file or directory")

    assert _run_main(mod, vault, publish_runner=cannot) == 1

    assert capsys.readouterr().err.splitlines() == [
        "scratch-pad: publish: could not start the publisher: No such file or directory"
    ]


def test_a_publisher_that_exits_non_zero_with_no_stderr_is_still_refused_visibly(mod, vault, capsys):
    def silent(cmd, env, timeout):
        return subprocess.CompletedProcess(cmd, 3, None, "")

    assert _run_main(mod, vault, publish_runner=silent) == 1

    assert capsys.readouterr().err.splitlines() == ["scratch-pad: publish: the publisher exited with status 3"]


def test_publisher_stderr_is_escaped_when_relayed(mod, vault, capsys):
    def noisy(cmd, env, timeout):
        return subprocess.CompletedProcess(cmd, 1, None, "bad \x1b[31m red\n")

    assert _run_main(mod, vault, publish_runner=noisy) == 1

    assert capsys.readouterr().err.splitlines() == ["scratch-pad: publish: bad \\x1b[31m red"]


def test_the_publisher_stderr_of_a_successful_publish_is_still_shown(mod, vault, capfd):
    _stub_lore(vault.bin, vault.record, err="no remote configured")

    assert _run_main(mod, vault) == 0

    assert "no remote configured" in capfd.readouterr().err


def _residue(vault):
    return sorted(p.name for p in vault.scratch.iterdir())


def test_no_temp_folder_survives_a_successful_publish(mod, vault):
    assert _run_main(mod, vault) == 0
    assert _residue(vault) == []


def test_no_temp_folder_survives_a_publisher_refusal(mod, vault, tmp_path):
    elsewhere = tmp_path / "not-under-the-vaults-root"
    elsewhere.mkdir()
    assert _run_main(mod, vault, vault.argv(vault_path=elsewhere)) == 1
    assert _residue(vault) == []


def test_no_temp_folder_survives_an_exception_raised_by_the_publisher_runner(mod, vault):
    seen = []

    def boom(cmd, env, timeout):
        seen.append(Path(cmd[2]))
        assert seen[0].is_dir()
        raise RuntimeError("runner blew up")

    with pytest.raises(RuntimeError):
        _run_main(mod, vault, publish_runner=boom)

    assert seen and not seen[0].exists()
    assert _residue(vault) == []


def test_no_temp_folder_survives_an_exception_raised_while_building_the_page(mod, vault, monkeypatch):
    def boom(target, reference, *a, **k):
        assert Path(target).is_dir()
        raise RecursionError("too deep")

    monkeypatch.setattr(mod, "build_reference_page", boom)

    with pytest.raises(RecursionError):
        _run_main(mod, vault, publish_runner=_no_runner)

    assert _residue(vault) == []


def test_the_temp_folder_is_owner_only_and_outside_the_vault(mod, vault):
    seen = []

    def look(cmd, env, timeout):
        folder = Path(cmd[2])
        seen.append((folder.stat().st_mode & 0o777, folder.resolve()))
        return subprocess.CompletedProcess(cmd, 0, None, "")

    assert _run_main(mod, vault, publish_runner=look) == 0

    mode, folder = seen[0]
    assert mode == 0o700
    assert vault.path.resolve() not in folder.parents


def test_the_publisher_is_given_the_vault_and_site_and_never_overwrite_or_a_timer(mod, vault):
    seen = []

    def look(cmd, env, timeout):
        seen.append((list(cmd), timeout))
        return subprocess.CompletedProcess(cmd, 0, None, "")

    assert _run_main(mod, vault, publish_runner=look) == 0

    cmd, timeout = seen[0]
    assert cmd[1] == str(PUBLISHER)
    assert cmd[3:] == ["header-and-sign-in", "--vault-path", str(vault.path), "--vault", "acme"]
    assert "--overwrite" not in cmd
    assert timeout is None


def test_a_missing_pad_is_refused_with_nothing_published_and_no_wait_line(mod, vault, capfd):
    shutil.rmtree(vault.state / "pads" / "p")

    assert _run_main(mod, vault, publish_runner=_no_runner) == 1

    got = capfd.readouterr()
    assert got.out == ""
    assert got.err.splitlines() == ['scratch-pad: pad "p": does not exist — build it first, or name the pad it is on']
    assert not (vault.path / "sites").exists()
    assert _residue(vault) == []


def test_the_reference_run_leaves_the_pad_unchanged_and_takes_no_lock_that_outlives_it(mod, vault):
    pad = vault.state / "pads" / "p"
    before = _snapshot(pad)

    assert _run_main(mod, vault) == 0

    assert _snapshot(pad) == before
    lock = _lock_path(vault.env, pad)
    assert _try_lock(lock)


def _usage(mod, argv, capsys):
    with pytest.raises(SystemExit) as exc:
        mod.main(argv, env={})
    assert exc.value.code == 2
    return capsys.readouterr().err


def test_a_reference_run_is_refused_alongside_a_round_or_add(mod, capsys):
    for extra in (["--round", "r.json"], ["--add"]):
        err = _usage(mod, ["--pad", "p", "--reference", "x.json", "--vault-path", "v", *extra], capsys)
        assert err.splitlines()[-1] == f"scratch_pad.py: error: --reference cannot be used with {extra[0]}"


def test_a_reference_run_needs_a_vault_path(mod, capsys):
    err = _usage(mod, ["--pad", "p", "--reference", "x.json"], capsys)
    assert err.splitlines()[-1] == "scratch_pad.py: error: --reference needs --vault-path"


def test_a_vault_path_or_name_without_a_reference_is_refused(mod, capsys):
    for flag in (["--vault-path", "v"], ["--vault", "n"]):
        err = _usage(mod, ["--pad", "p", "--round", "r.json", *flag], capsys)
        assert err.splitlines()[-1] == f"scratch_pad.py: error: {flag[0]} is only valid with --reference"


def test_without_a_reference_the_round_stays_required_with_its_old_wording(mod, capsys):
    err = _usage(mod, ["--pad", "p"], capsys)
    assert err.splitlines()[-1] == "scratch_pad.py: error: the following arguments are required: --round"
    err = _usage(mod, [], capsys)
    assert err.splitlines()[-1] == "scratch_pad.py: error: the following arguments are required: --pad, --round"
