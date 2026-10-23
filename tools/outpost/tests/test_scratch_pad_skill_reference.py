"""The scratch-pad skill's reference example and the refusal lines it quotes match the script.

The reference example is the first fenced ``json`` block of SKILL.md that parses as a reference (an object
holding ``drawings``). Its keys are compared with the module's own key constants and it is run through
``check_reference``. The refusal lines the skill quotes for a taken site name and a credential pattern are
produced by running the script against a real pad, and the skill must quote what the script printed (its variable
parts as the placeholders the skill uses).
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).parent.parent / "plugins" / "outpost" / "skills" / "scratch-pad"

_spec = importlib.util.spec_from_file_location("scratch_pad", SKILL_DIR / "scratch_pad.py")
scratch_pad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scratch_pad)


def skill_text() -> str:
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def skill_prose() -> str:
    """The skill with its line wrapping collapsed, so a quoted line can span a wrap."""
    return " ".join(skill_text().split())


def skill_example_reference(text: str | None = None) -> dict:
    text = skill_text() if text is None else text
    for block in re.findall(r"```json\n(.*?)```", text, re.DOTALL):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and "drawings" in data:
            return data
    raise AssertionError("SKILL.md has no reference example")


def test_the_skill_example_is_a_reference_the_script_accepts():
    assert scratch_pad.check_reference(skill_example_reference()) == []


def test_the_skill_example_uses_exactly_the_keys_the_script_reads():
    example = skill_example_reference()
    assert set(example) == set(scratch_pad._REFERENCE_KEYS)
    assert {key for drawing in example["drawings"] for key in drawing} == set(scratch_pad._DRAWING_KEYS)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    env = {
        "OUTPOST_STATE_DIR": str(tmp_path / "state"),
        "CAMP_STATE_DIR": str(tmp_path / "camp-state"),
        "XDG_STATE_HOME": str(tmp_path / "xdg"),
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
    }
    round_file = tmp_path / "round.json"
    round_file.write_text(
        json.dumps(
            {
                "title": "Settings",
                "questions": [
                    {
                        "prompt": "Header",
                        "options": [
                            {"name": "Split", "cost": "More work.", "pick": True, "layout": {"rule": True}},
                            {"name": "password: hunter2hunter2", "cost": "Simple.", "layout": {"rule": True}},
                        ],
                    }
                ],
            }
        )
    )
    assert scratch_pad.main(["--pad", "p", "--round", str(round_file)], env, cwd=work) == 0
    return env, work, tmp_path


def _refusal(rig, capsys, option: str, site: str = "page", title: str = "T") -> str:
    env, work, tmp_path = rig
    vault = tmp_path / "xdg" / "lore" / "vaults" / "acme"
    (vault / "sites" / "taken").mkdir(parents=True, exist_ok=True)
    ref = tmp_path / f"ref-{option}-{site}-{abs(hash(title))}.json"
    drawings = [{"round": 1, "question": 1, "option": option}]
    ref.write_text(json.dumps({"site": site, "title": title, "drawings": drawings}))
    capsys.readouterr()
    code = scratch_pad.main(["--pad", "p", "--reference", str(ref), "--vault-path", str(vault)], env, cwd=work)
    assert code == 1
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    return lines[0]


def test_the_skill_quotes_the_taken_site_name_refusal_as_the_script_prints_it(rig, capsys):
    tail = _refusal(rig, capsys, "A", site="taken").split(": ", 2)[2]
    assert tail in skill_prose()


def test_the_skill_quotes_the_credential_refusal_as_the_script_prints_it(rig, capsys):
    tail = _refusal(rig, capsys, "B").split(": ", 2)[2].split(" ", 1)[1]
    placeholder = re.sub(r"\([a-z0-9-]+(, [a-z0-9-]+)*\)", "(<class>)", tail)
    assert placeholder in skill_prose()


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [("title", {"title": "password: hunter2hunter2"}), ("site", {"site": "a" * 40})],
)
def test_the_skill_quotes_the_credential_refusal_for_the_title_and_the_site_as_the_script_prints_it(
    rig, capsys, field, kwargs
):
    line = _refusal(rig, capsys, "A", **kwargs)
    where = line.split(" matches a credential pattern", 1)[0].removeprefix("scratch-pad: ").split(": ")[0]

    assert where == field
    assert f"scratch-pad: {field}: " in skill_prose()
