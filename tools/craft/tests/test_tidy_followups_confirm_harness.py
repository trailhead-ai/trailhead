"""The confirm-before-write harness builds the sandboxed stream-json command line, fills a cell's
text, and reads the earlier leg's last code, all without starting an arm."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

CASE = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "evals" / "tidy-followups-confirms-before-writing"
sys.path.insert(0, str(CASE.parent / "tidy-followups-builds-one-parent"))
_spec = importlib.util.spec_from_file_location("confirm_harness", CASE / "harness.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


def argv(**over):
    kw = dict(sandbox="/repo/scripts/eval-sandbox", run_dir="/private/tmp/w/runs/c",
              env={"PATH": "/x/bin", "TMPDIR": "/t"}, home="/home/u", user="u",
              claude="/bin/claude", allowed="Bash,Agent")
    kw.update(over)
    return h.build_argv(**kw)


def test_the_command_runs_claude_with_the_stream_flags_under_the_sandbox_and_a_cleared_environment():
    a = argv()
    assert a[:4] == ["/repo/scripts/eval-sandbox", "/private/tmp/w/runs/c", "--", "env"]
    assert a[4] == "-i"
    claude_at = a.index("/bin/claude")
    assert a[4:claude_at] == ["-i", "PATH=/x/bin", "TMPDIR=/t", "HOME=/home/u", "USER=u"]
    assert a[claude_at + 1:claude_at + 7] == [
        "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    assert a[a.index("--allowedTools") + 1] == "Bash,Agent"


def test_the_model_flag_is_present_only_when_one_is_given():
    assert "--model" not in argv()
    a = argv(model="some-model")
    assert a[a.index("--model") + 1] == "some-model"


def test_roles_and_the_earlier_code_are_filled_and_the_drivers_code_placeholder_is_left():
    text = "drop {F2} (code was {prev_code}); reply: yes and the code {code}; keep {unknown}"
    assert h.fill_roles(text, {"F2": "rack-two"}, "k7q2") == (
        "drop rack-two (code was k7q2); reply: yes and the code {code}; keep {unknown}")


def test_the_earlier_legs_last_shown_code_is_the_one_carried_forward():
    turns = [{"code": "aaaa"}, {"code": None}, {"code": "bbbb"}, {"code": None}]
    assert h.last_code({"turns": turns}) == "bbbb"
    assert h.last_code({"turns": [{"code": None}]}) == h.NO_PREV_CODE


def test_a_question_in_the_last_three_paragraphs_counts_as_asked_and_a_statement_or_a_distant_question_does_not():
    assert h.question_near_the_end("Fine.\n\nWhy drop this follow-up? (one line; it covers every drop)")
    assert h.question_near_the_end("Done.\n\nWhich of them?\nAnswer with names, or `none`.")
    assert h.question_near_the_end("Which of them?\n\nYour earlier answer still stands.\n\nIt cannot stand in.")
    assert not h.question_near_the_end("Is this right?\n\nOne.\n\nTwo.\n\nThree.")
    assert not h.question_near_the_end("Nothing was written.\nDone.")
    assert not h.question_near_the_end("")
