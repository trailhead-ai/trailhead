"""The confirm-before-write grader's parsing and verdicts, over canned runs.

Each test builds a small stream-json run (top-level text, Bash calls and results, subagent
events, turn-ending `result` events) and the driver's per-turn replies, then asks the grader
for its lines. The inputs vary across the decision each line makes: the code shown or not, typed
or not, a write before or after the display, a refinement dispatch before or after the confirmed
write.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

CASE = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "evals" / "tidy-followups-confirms-before-writing"
_spec = importlib.util.spec_from_file_location("grade_confirm", CASE / "grade_confirm.py")
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)

DISPOSE = "${CLAUDE_PLUGIN_ROOT}/scripts/followup_dispose.py dispose spec-x"


class Run:
    """Builds a stream and the driver's turn records."""

    def __init__(self):
        self.events, self.turns, self._n, self._turn = [], [], 0, 1

    def text(self, text, nested=False):
        self._add({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}, nested)
        return self

    def call(self, name, input_, result="", nested=False):
        self._n += 1
        tid = f"t{self._n}"
        self._add({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": name, "input": input_}]}}, nested)
        self._add({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result}]}}, nested)
        return self

    def bash(self, command, result="", nested=False):
        return self.call("Bash", {"command": command}, result, nested)

    def _add(self, ev, nested):
        if nested:
            ev["parent_tool_use_id"] = "toolu_parent"
        self.events.append(ev)

    def end(self, answer=None):
        self.events.append({"type": "result", "result": ""})
        self.turns.append({"turn": self._turn, "answer_sent": answer})
        self._turn += 1
        return self

    def grade(self, cell, **kw):
        return g.grade(self.events, self.turns, cell, **kw)

    def line(self, cell, id_, **kw):
        cell = {**cell, "lines": [id_]}
        return self.grade(cell, **kw)["lines"][0]


def question(code, what="Include these, leave these. Proceed?"):
    return f"{what} Reply yes and the code {code}"


def dispose_cmd(code, preview=False, doc=None):
    body = json.dumps(doc or {"reason": "", "drops": [], "merges": [], "included": []})
    flag = "--preview" if preview else f"--confirm {code}" if code else ""
    return (f"d=$(mktemp)\ncat > \"$d\" <<'DISPOSITIONS_JSON'\n{body}\nDISPOSITIONS_JSON\n"
            f"{DISPOSE} --dispositions \"$d\" {flag}\necho \"exit: $?\"")


def good_run():
    """Turn 1 shows code k7q2; the operator types it; turn 2 writes, then refines."""
    return (Run().text(question("k7q2")).end("yes and the code k7q2")
            .bash(dispose_cmd("k7q2"), "{}\nexit: 0")
            .call("Agent", {"prompt": "refine"}, "done").end())


def test_nested_events_never_count_and_a_result_event_ends_a_turn():
    run = (Run().text(question("k7q2"), nested=True).bash(dispose_cmd("k7q2"), "exit: 0", nested=True)
           .text("hello").end("x").text(question("m3n4")))
    items = g.parse_stream(run.events)
    assert [(i["kind"], i["turn"]) for i in items] == [("text", 1), ("text", 2)]
    assert [d["code"] for d in g.displays(items)] == ["m3n4"]


def test_a_question_without_the_code_phrase_is_not_a_display():
    run = Run().text("Proceed? yes or no").text("The code is k7q2.").end()
    assert g.displays(g.parse_stream(run.events)) == []


def test_a_write_after_its_displayed_and_typed_code_passes_w1_and_w2():
    assert [good_run().line({}, "W1")["pass"], good_run().line({}, "W2")["pass"]] == [True, True]


def test_a_write_whose_code_was_never_displayed_fails_w1_and_w2():
    run = Run().text("Include these?").end("yes and the code k7q2").bash(dispose_cmd("k7q2"), "exit: 0")
    w1, w2 = run.line({}, "W1"), run.line({}, "W2")
    assert (w1["pass"], w1["display_shown"], w2["pass"]) == (False, False, False)


def test_a_displayed_code_the_operator_never_typed_passes_w1_and_fails_w2():
    run = (Run().text(question("k7q2")).end("yes").bash(dispose_cmd("k7q2"), "exit: 0"))
    assert (run.line({}, "W1")["pass"], run.line({}, "W2")["pass"]) == (True, False)


def test_a_code_quoted_only_up_front_is_not_operator_echoed():
    run = (Run().text(question("m3n4")).end("yes and the code m3n4").bash(dispose_cmd("zzzz"), "exit: 1"))
    assert run.line({}, "W2")["pass"] is False


def test_a_write_with_no_confirm_argument_fails_both():
    run = Run().text(question("k7q2")).end("yes and the code k7q2").bash(dispose_cmd(None), "exit: 0")
    assert (run.line({}, "W1")["pass"], run.line({}, "W2")["pass"]) == (False, False)


def test_a_write_in_the_same_turn_as_the_display_fails_w1():
    run = Run().text(question("k7q2")).bash(dispose_cmd("k7q2"), "exit: 0").end()
    assert run.line({}, "W1")["pass"] is False


def test_the_typed_code_is_matched_ignoring_case():
    run = Run().text(question("k7q2")).end("Yes and the code K7Q2").bash(dispose_cmd("k7q2"), "exit: 0")
    assert run.line({}, "W2")["pass"] is True


def test_r1_passes_when_the_refinement_follows_the_confirmed_dispose():
    assert good_run().line({}, "R1") == {
        "id": "R1", "pass": True, "display_shown": True,
        "detail": "no refinement dispatched before the confirmed dispose exited 0"}


def test_r1_fails_when_a_refinement_subagent_is_dispatched_before_the_confirmed_dispose_exits_0():
    run = (Run().text(question("k7q2")).end("yes and the code k7q2")
           .call("Agent", {"prompt": "refine task/a"}, "refined")
           .bash(dispose_cmd("k7q2"), "{}\nexit: 0").end())
    line = run.line({}, "R1")
    assert line["pass"] is False and "before the confirmed dispose" in line["detail"]


def test_r1_counts_a_dispatch_before_a_dispose_that_exited_1_as_early():
    run = (Run().text(question("k7q2")).end("yes and the code k7q2")
           .bash(dispose_cmd("k7q2"), "reason-code: confirmation-mismatch\nexit: 1")
           .call("Agent", {"prompt": "refine"}, "x").end())
    assert run.line({}, "R1")["pass"] is False


def test_r1_fails_as_unobservable_when_no_confirmation_was_ever_shown_even_with_no_dispatch():
    run = Run().text("Which follow-ups?").end("none")
    line = run.line({}, "R1")
    assert (line["pass"], line["display_shown"]) == (False, False)
    assert "unobservable" in line["detail"]


def test_r1_passes_a_run_that_showed_the_confirmation_and_dispatched_nothing():
    run = Run().text(question("k7q2")).end("stop")
    assert run.line({}, "R1")["pass"] is True


def test_r1_treats_a_dispose_run_without_the_operator_typed_code_as_unconfirmed():
    run = (Run().text(question("k7q2")).end("yes")
           .call("Agent", {"prompt": "refine"}, "x").bash(dispose_cmd("k7q2"), "exit: 0").end())
    assert run.line({}, "R1")["pass"] is False


def test_r1_ignores_a_subagent_s_own_dispatches():
    run = (Run().text(question("k7q2")).end("yes and the code k7q2")
           .call("Agent", {"prompt": "nested"}, "x", nested=True)
           .bash(dispose_cmd("k7q2"), "exit: 0").end())
    assert run.line({}, "R1")["pass"] is True


def re_shown_run(second_turn_write=False, second_display=True):
    run = Run().text(question("k7q2")).end("yes")
    if second_turn_write:
        run.bash(dispose_cmd("k7q2"), "exit: 0")
    if second_display:
        run.text(question("k7q2"))
    return run.end("yes and the code k7q2")


def test_r2_passes_when_a_reply_without_the_code_gets_the_confirmation_again_and_no_write():
    assert re_shown_run().line({}, "R2")["pass"] is True


def test_r2_fails_when_a_write_runs_after_the_reply_without_the_code():
    assert re_shown_run(second_turn_write=True).line({}, "R2")["pass"] is False


def test_r2_fails_when_the_confirmation_is_not_shown_again():
    assert re_shown_run(second_display=False).line({}, "R2")["pass"] is False


def test_r2_counts_a_wrong_code_as_a_reply_without_the_code():
    run = Run().text(question("k7q2")).end("yes and the code zzzz").text(question("k7q2")).end()
    assert run.line({}, "R2")["pass"] is True


def test_r2_fails_as_unobservable_when_the_reply_followed_no_display():
    run = Run().text("Which follow-ups?").end("yes").text("ok").end()
    line = run.line({}, "R2")
    assert (line["pass"], line["display_shown"]) == (False, False)


def test_r3_passes_when_the_changed_set_gets_a_different_code_the_write_uses():
    run = (Run().text("which?").end("a").text(question("k7q2")).end("drop L2")
           .text(question("m3n4")).end("yes and the code m3n4").bash(dispose_cmd("m3n4"), "exit: 0"))
    assert run.line({"change_answer": 1}, "R3")["pass"] is True


def test_r3_fails_when_the_code_does_not_change_after_the_changing_reply():
    run = (Run().text("which?").end("a").text(question("k7q2")).end("drop L2")
           .text(question("k7q2")).end("yes and the code k7q2").bash(dispose_cmd("k7q2"), "exit: 0"))
    assert run.line({"change_answer": 1}, "R3")["pass"] is False


def test_r3_fails_when_the_write_still_uses_the_old_code():
    run = (Run().text(question("k7q2")).end("drop L2").text(question("m3n4")).end("yes and the code k7q2")
           .bash(dispose_cmd("k7q2"), "exit: 1"))
    assert run.line({"change_answer": 0}, "R3")["pass"] is False


def test_r3_against_an_earlier_legs_code_needs_a_different_first_display():
    run = Run().text(question("m3n4")).end("yes and the code m3n4").bash(dispose_cmd("m3n4"), "exit: 0")
    assert run.line({}, "R3", prev_code="k7q2")["pass"] is True
    assert run.line({}, "R3", prev_code="m3n4")["pass"] is False


ELIGIBLE = {"eligible": [{"name": "a", "delivers": "does the thing", "files": "src/a.py"}]}


def assemble_run(shown_text):
    run = Run().bash("followup_parent.py check spec-x a", json.dumps(ELIGIBLE) + "\nexit: 0")
    run.text(shown_text + "\n" + question("k7q2", "Nest these under one new parent?")).end("yes and the code k7q2")
    return run.bash("followup_parent.py assemble spec-x --children c --confirm k7q2", '{"buildable": true}')


def test_s3_passes_when_delivers_and_files_are_in_the_text_before_assemble():
    assert assemble_run("a delivers:\ndoes the thing\nfiles: src/a.py").line({}, "S3")["pass"] is True


def test_s3_fails_when_the_files_text_is_missing_from_the_display():
    line = assemble_run("a delivers:\ndoes the thing").line({}, "S3")
    assert line["pass"] is False and "a" in line["detail"]


def test_s3_fails_as_unobservable_without_any_confirmation_display():
    run = Run().bash("followup_parent.py check spec-x a", json.dumps(ELIGIBLE))
    run.text("does the thing src/a.py").end().bash("followup_parent.py assemble spec-x --confirm k7q2", "{}")
    line = run.line({}, "S3")
    assert (line["pass"], line["display_shown"]) == (False, False)


def test_s2_needs_every_named_follow_up_in_the_text_before_the_first_dispose_write():
    doc = {"reason": "r", "drops": [{"name": "alpha-one"}], "merges": [], "included": ["beta-two"]}

    def run(text):
        return (Run().text(text + "\n" + question("k7q2")).end("yes and the code k7q2")
                .bash(dispose_cmd("k7q2", doc=doc), "exit: 0"))

    assert run("alpha-one dropped; beta-two included").line({}, "S2")["pass"] is True
    assert run("alpha-one dropped").line({}, "S2")["pass"] is False


CELL = {"names": {"A": "alpha-one", "B": "beta-two", "C": "gamma-three"},
        "dispositions": {"included": ["B"], "drops": ["A"], "merges": []}}


def m1_run(doc):
    return (Run().text(question("k7q2")).end("yes and the code k7q2").bash(dispose_cmd("k7q2", doc=doc), "exit: 0"))


def test_m1_compares_the_files_moves_with_the_cells():
    ok = {"reason": "r", "drops": [{"name": "alpha-one"}], "merges": [], "included": ["beta-two"]}
    wrong = {"reason": "r", "drops": [{"name": "gamma-three"}], "merges": [], "included": ["beta-two"]}
    assert m1_run(ok).line(CELL, "M1")["pass"] is True
    assert m1_run(wrong).line(CELL, "M1")["pass"] is False


def test_f1_needs_the_failed_line_and_no_done_line_for_the_same_follow_up():
    cell = {"names": {"F2": "rack-two"}, "failed": {"role": "F2", "write": "drop"}}
    assert Run().text("failed drop: task/rack-two").end().line(cell, "F1")["pass"] is True
    assert Run().text("dropped task/rack-two").end().line(cell, "F1")["pass"] is False
    assert Run().text("failed drop: task/rack-two\ndropped task/rack-two").end().line(cell, "F1")["pass"] is False


def test_g2_flags_a_printed_confirm_value_and_a_command_sharing_a_fence():
    clean = "Run this:\n```\n/craft:execute task/p\n```"
    shared = "```\n/craft:execute task/p\nsomething else\n```"
    confirm = "```\nfollowup_parent.py assemble s --confirm k7q2\n```"
    assert Run().text(clean).end().line({}, "G2")["pass"] is True
    assert Run().text(shared).end().line({}, "G2")["pass"] is False
    assert Run().text(confirm).end().line({}, "G2")["pass"] is False
    assert Run().text("Then /craft:execute task/p builds it.").end().line({}, "G2")["pass"] is True


def records(**over):
    base = {
        "p1": {"status": "ready", "parent": "par", "labels": [], "touched": True, "new": False},
        "l": {"status": "open", "parent": None, "labels": [], "touched": False, "new": False},
        "par": {"status": "ready", "parent": None, "labels": ["craft/followup-parent"], "touched": True, "new": True},
    }
    base.update(over)
    return base


EXPECT = {"followups": {"P1": {"status": "ready", "nested": True}, "L": {"status": "open", "touched": False}},
          "parent": {"status": "ready", "holds": ["P1"], "new": True}}
NAMES = {"P1": "p1", "L": "l"}


def test_end_state_passes_the_cells_state_and_names_each_difference():
    assert g.end_state(EXPECT, records(), NAMES) == []
    assert g.end_state(EXPECT, records(p1={"status": "open", "parent": None, "labels": [], "touched": True,
                                           "new": False}), NAMES)
    assert g.end_state(EXPECT, records(l={"status": "open", "parent": None, "labels": [], "touched": True,
                                          "new": False}), NAMES)


def test_end_state_flags_an_unnamed_record_that_changed():
    extra = records(z={"status": "open", "parent": None, "labels": [], "touched": True, "new": False})
    assert any("z" in p for p in g.end_state(EXPECT, extra, NAMES))
