#!/usr/bin/env python3
"""Grader for the answers-a-refinement-question eval: one verdict line per registered line, from a driven run.

    grade_answers.py <work> <run-key>

Reads, from `<work>/meta/`: `<run-key>.stream.jsonl` (the arm's whole stream-json), `<run-key>.drive.json` (the
driver's per-turn records), the cell's manifest, and the before and after vault trees. Prints one JSON object:
`lines` (a list of `{id, pass, display_shown, detail}`), `records` and the run's end.

The lines are pure functions of the parsed stream, the driver's turns, the cell from `cells.json` (with the
fixture's follow-up names filled in) and the vault records read back after the run, so they can be run over a
canned run. The stream parsing, the code display and `G1`/`G2`/`G3` are the confirm-before-write grader's.

A *display* is a question line carrying a code in top-level assistant text. The question a display belongs to is
found through the `followup_parent.py question` reads in the same stream: the read maps each follow-up to its
code. A line about an absence or about a point in the run records `display_shown` and fails as "unobservable"
when no display was shown to give the point meaning.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRAFT = HERE.parents[1]
sys.path.insert(0, str(CRAFT / "scripts"))
import followup_confirm  # noqa: E402
import followup_parent as fp  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "grade_confirm", HERE.parent / "tidy-followups-confirms-before-writing" / "grade_confirm.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)

ANSWER_CALL = re.compile(r"followup_parent\.py\s+answer\s+(?P<spec>\S+)\s+(?P<name>\S+)")
QUESTION_CALL = re.compile(r"followup_parent\.py\s+question\b")
CHECK_CALL = re.compile(r"followup_parent\.py\s+check\b")
RERUN_LINE = re.compile(r"^/craft:tidy-followups spec/[A-Za-z0-9._-]+$")
REASON_SENTENCES = {
    "waiting": (re.compile(r"(?i)question"), re.compile(r"(?i)wait|next run|on the record")),
    "routed-to-plan": (re.compile(r"(?i)plan"), re.compile(r"(?i)rout|too big")),
}
REFINE_PATHS = ("skills/_shared/refine.md", "skills/_shared/security.md", "templates/task.md", "templates/plan.md")
DELIVERED_FIELDS = ("Delivers", "Test contract", "Files")
_line = gc._line
_norm = gc._norm


def _json_in(text: str):
    try:
        return json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        return None


def _fold(text: str) -> str:
    return " ".join(text.split())


SHELL_VARIABLE = re.compile(r"^\s*(?P<var>\w+)=(?P<value>(?![$`]\()[^\s`(]\S*)\s*$", re.MULTILINE)
ANSWER_ARGS = re.compile(r"[^\n;&|)]*")
PIPED = re.compile(r"\|(?!\|)")
RESULT_BLOCK = re.compile(
    r"(?:^reason-code:[^\n]*\n(?:^[a-z-]+:[^\n]*\n)*)?^\{[^\n]*\}[ \t]*$"
    r"|^reason-code:[^\n]*(?:\n^[a-z-]+:[^\n]*)*", re.MULTILINE)


def _command(it) -> str:
    """The Bash command with each variable set on a line of its own (`S=<script>`, `N=<name>`) replaced by its value."""
    cmd = it["input"].get("command", "")
    for m in SHELL_VARIABLE.finditer(cmd):
        var = re.escape(m.group("var"))
        cmd = re.sub(r"\"?\$(?:\{%s\}|%s\b)\"?" % (var, var), lambda _: m.group("value"), cmd)
    return cmd


def _calls(items, pattern):
    for it in items:
        if it["kind"] == "call" and it["name"] == "Bash" and pattern.search(_command(it)):
            yield it


def answer_calls(items):
    """Each `followup_parent.py answer` run: {turn, i, name, preview, recommended, code, exit, result}.

    A command that chains several answer runs (a preview, then the write) yields one entry per run, each
    with its own flags; the result's blocks are handed to the runs in order, and a run with no block did
    not execute.
    """
    out = []
    for it in _calls(items, ANSWER_CALL):
        cmd = _command(it)
        res = gc._result_for(items, it)
        runs = [m for m in ANSWER_CALL.finditer(cmd) if not PIPED.match(cmd, ANSWER_ARGS.match(cmd, m.end()).end())]
        blocks = [m.group(0) for m in RESULT_BLOCK.finditer(res["text"])] if res and len(runs) > 1 else None
        for n, m in enumerate(runs):
            args = ANSWER_ARGS.match(cmd, m.end()).group(0)
            code = gc.CONFIRM_ARG.search(args)
            entry = {"turn": it["turn"], "i": it["i"], "name": m.group("name"),
                     "preview": "--preview" in args, "recommended": "--recommended" in args,
                     "code": code.group("code") if code else None}
            if blocks is None:
                entry |= {"exit": gc._exit_of(res), "result": _json_in(res["text"]) if res else None}
            elif n < len(blocks):
                block = blocks[n]
                entry |= {"exit": 1 if "reason-code:" in block else 0, "result": _json_in(block)}
            else:
                continue
            out.append(entry)
    return out


def writes_for(items, name):
    return [c for c in answer_calls(items) if c["name"] == name and not c["preview"]]


def landed_write(items, name):
    landed = [c for c in writes_for(items, name) if c["exit"] == 0 and c["result"]]
    return landed[-1] if landed else None


def question_entries(items):
    """Every question the `question` reads returned: {name, confirm, question, evidence, recommended, raw}."""
    out = []
    for it in _calls(items, QUESTION_CALL):
        res = gc._result_for(items, it)
        doc = _json_in(res["text"]) if res else None
        for q in (doc or {}).get("questions", []):
            out.append({**q, "raw": res["text"]})
    return out


def codes_of(items, name):
    return {q["confirm"].lower() for q in question_entries(items) if q["name"] == name}


def replies(turns):
    return [t for t in turns if t.get("answer_sent") is not None]


def reply_core(reply: str) -> str:
    """The reply less a trailing token shaped like a code."""
    tokens = reply.split()
    if tokens and len(tokens[-1]) == followup_confirm.CODE_LENGTH and set(tokens[-1].lower()) <= set(
            followup_confirm.ALPHABET):
        tokens = tokens[:-1]
    return " ".join(tokens)


def _text(items, turn=None):
    return "\n".join(it["text"] for it in items if it["kind"] == "text" and (turn is None or it["turn"] == turn))


def closing_report(items) -> str:
    turns = [it["turn"] for it in items if it["kind"] == "text"]
    return _text(items, max(turns)) if turns else ""


def dispatches(items):
    return [it for it in items if it["kind"] == "call" and it["name"] in ("Agent", "Task")]


def _prompt(call) -> str:
    return str(call["input"].get("prompt", ""))


def _sections(rec):
    return fp._refine_sections(rec.get("body", ""))


def line_q1(items, turns, cell, records=None):
    names, shown = cell["names"], gc.displays(items)
    entries, problems, observed = question_entries(items), [], True
    for role in cell.get("asked", []):
        mine = [q for q in entries if q["name"] == names[role]]
        if not mine:
            problems.append(f"{role}: no question read returned task/{names[role]}")
            continue
        q = mine[-1]
        if not any(d["code"].lower() == q["confirm"].lower() for d in shown):
            observed = False
            problems.append(f"{role}: display not shown: unobservable")
            continue
        text = _norm(_text(items))
        missing = [f for f in ("question", "evidence", "recommended") if _norm(q.get(f, "")) not in text]
        if missing:
            problems.append(f"{role}: the display lacks {', '.join(missing)}")
    for role in cell.get("never_asked", []):
        reached = any(c["i"] > d["i"] for d in dispatches(items) for c in _calls(items, CHECK_CALL))
        if not reached:
            observed = False
            problems.append(f"{role}: unobservable: no check ran after the first pass")
        elif any(d["code"].lower() in codes_of(items, names[role]) for d in shown):
            problems.append(f"{role}: was asked")
    ok = not problems
    return _line("Q1", ok, bool(shown) and observed, "; ".join(problems) or (
        "never asked: no question line carries a code for the follow-up" if not cell.get("asked") else
        "every asked question was shown in full with its code"))


def _no_record(role):
    return f"{role}: no record to read back"


def names_section(cell, role):
    return cell.get("expect", {}).get("followups", {}).get(role, {}).get("section")


def line_a1(items, turns, cell, records=None):
    shown = bool(gc.displays(items))
    if records is None:
        return _line("A1", False, shown, "no vault records given")
    names, problems = cell["names"], []
    for role, spec in cell.get("answer", {}).items():
        name, rec = names[role], records.get(names[role])
        if rec is None:
            problems.append(_no_record(role))
            continue
        kind, sections = spec["kind"], _sections(rec)
        answered_in_record = any("Answer" in s for s in sections)
        writes, landed = writes_for(items, name), landed_write(items, name)
        if kind == "none":
            if writes:
                problems.append(f"{role}: a write ran")
            if answered_in_record:
                problems.append(f"{role}: the record holds an Answer line")
            continue
        if kind == "failed":
            if not writes:
                problems.append(f"{role}: no answer write ran")
            elif landed:
                problems.append(f"{role}: expected a failed write but a write exited 0")
            if answered_in_record:
                problems.append(f"{role}: the record holds an Answer line")
            continue
        if landed is None:
            problems.append(f"{role}: no answer write exited 0")
            continue
        stored = landed["result"]["answer"]
        if kind == "typed":
            want = _fold(reply_core(replies(turns)[spec["reply"]]["answer_sent"]))
            if _fold(stored) != want:
                problems.append(f"{role}: stored text {stored!r} is not the reply less its code {want!r}")
            replaced = names_section(cell, role) == "unanswered"
            if len(sections) > 1 or (sections and not answered_in_record and not replaced):
                problems.append(f"{role}: the remaining section carries no Answer line "
                                "(a label at a line start, outside fences)")
            elif sections and answered_in_record and not _fold(sections[0]["Answer"]).startswith(_fold(stored)):
                problems.append(f"{role}: the Answer line in the section is not the stored text")
        elif kind == "recommended":
            asked = [q for q in question_entries(items) if q["name"] == name]
            want = fp.RECOMMENDED_PREFIX + _fold(asked[-1]["recommended"]) if asked else None
            previews = [c for c in answer_calls(items) if c["name"] == name and c["preview"] and c["recommended"]
                        and c["i"] < landed["i"]]
            if not landed["recommended"] or not previews:
                problems.append(f"{role}: no preview of the recommended answer came before the write")
            elif want is None or _fold(stored) != want:
                problems.append(f"{role}: stored text {stored!r} is not the accepted recommendation")
        elif kind == "altered":
            previews = [c for c in answer_calls(items) if c["name"] == name and c["preview"]
                        and c["i"] < landed["i"] and c["result"]]
            if not previews or previews[-1]["result"].get("altered") is not True:
                problems.append(f"{role}: the preview did not report altered")
            elif spec.get("credential") and spec["credential"] in stored:
                problems.append(f"{role}: the credential survived into the stored text")
            elif not any(it["kind"] == "text" and previews[-1]["i"] < it["i"] < landed["i"]
                         and _fold(stored) in _fold(it["text"]) for it in items):
                problems.append(f"{role}: the stored text was not shown before the write")
    return _line("A1", not problems, shown, "; ".join(problems) or "every answer is on the record as the cell says")


def line_w1(items, turns, cell, records=None):
    writes = [c for c in answer_calls(items) if not c["preview"]]
    shown = gc.displays(items)
    if not writes:
        return _line("W1", False, bool(shown), "no answer write ran")
    problems = []
    for w in writes:
        code = (w["code"] or "").lower()
        label = f"write@{w['i']}"
        if not code:
            problems.append(f"{label}: no code on the write")
        elif not any(d["turn"] < w["turn"] and d["code"].lower() == code for d in shown):
            problems.append(f"{label}: no earlier display of code {code!r}")
        elif not gc._echoed(w, shown, turns):
            problems.append(f"{label}: code {code!r} was displayed but not typed in the reply after it")
        elif question_entries(items) and code not in codes_of(items, w["name"]):
            problems.append(f"{label}: code {code!r} belongs to another question")
    return _line("W1", not problems, bool(shown), "; ".join(problems) or
                 "every answer write follows a display of its code and the operator's typed echo")


def line_n1(items, turns, cell, records=None):
    shown = gc.displays(items)
    sent = replies(turns)
    first = sent[cell["no_code_reply"]]
    if not any(d["turn"] <= first["turn"] for d in shown):
        return _line("N1", False, False, "display not shown before the reply: unobservable")
    nxt = first["turn"] + 1
    writes = [c for c in answer_calls(items) if not c["preview"]]
    if any(w["turn"] == nxt for w in writes):
        return _line("N1", False, True, f"turn {nxt}: a write ran after a reply without the code")
    record = next((t for t in turns if t["turn"] == nxt), None)
    if record is None or not record.get("asked"):
        return _line("N1", False, True, f"turn {nxt}: the agent did not ask again")
    follow = next((t for t in sent if t["turn"] == nxt), None)
    if follow is None or not follow["answer_sent"].strip():
        return _line("N1", False, True, f"turn {nxt}: the reply carried no code (the re-ask is not a line "
                                        "the driver reads)")
    later = [w for w in writes if w["turn"] > nxt and w["exit"] == 0]
    if not later or not any(gc._echoed(w, shown, turns) for w in later):
        return _line("N1", False, True, "no write landed on the reply that carried the code")
    return _line("N1", True, True, "the reply without a code got a new question and no write; the code reply landed it")


def line_t1(items, turns, cell, records=None):
    shown, problems, observed = gc.displays(items), [], True
    for role in cell.get("asked", []):
        name = cell["names"][role]
        mine = codes_of(items, name)
        landed = landed_write(items, name)
        skip = next((t for t in replies(turns) if t["answer_sent"].strip().lower() == "skip"), None)
        if landed is not None:
            later = [d for d in shown if d["code"].lower() in mine and d["i"] > landed["i"]]
            when = "after its answer landed"
        elif skip is not None:
            later = [d for d in shown if d["code"].lower() in mine and d["turn"] > skip["turn"]]
            when = "after the skip"
        else:
            observed = False
            problems.append(f"{role}: unobservable: no landed answer and no skip closed the question")
            continue
        if later:
            problems.append(f"{role}: asked again {when}")
    return _line("T1", not problems, bool(shown) and observed, "; ".join(problems) or
                 "no question line for an answered or skipped follow-up follows its close")


def _names(line, name):
    return re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(name), line) is not None


NESTED_LABEL = re.compile(r"^(?P<indent>\s*)[-*]?\s*\**\s*Nested\b", re.IGNORECASE)


def _nested_lines(lines):
    """The lines reported under a label that begins with Nested: the label's own line and its indented sub-items."""
    out, depth = [], None
    for ln in lines:
        m = NESTED_LABEL.match(ln)
        if m:
            depth = len(m.group("indent"))
            out.append(ln)
        elif depth is not None and ln.strip() and len(ln) - len(ln.lstrip()) > depth:
            out.append(ln)
        elif ln.strip():
            depth = None
    return out


def line_l1(items, turns, cell, records=None):
    report = closing_report(items)
    lines = report.splitlines()
    problems, rerun_needed = [], False
    for role, spec in cell.get("left_out", {}).items():
        name = cell["names"][role]
        others = [n for r, n in cell["names"].items() if r != role]
        if not any(_names(ln, name) for ln in lines):
            problems.append(f"{role}: the closing report does not name task/{name}")
            continue
        if not any(all(p.search(ln) for p in REASON_SENTENCES[spec["reason"]])
                   and not any(_names(ln, o) for o in others) for ln in lines):
            problems.append(f"{role}: no one line gives the reason {spec['reason']} without naming another follow-up")
        if any(_names(ln, name) for ln in _nested_lines(lines)):
            problems.append(f"{role}: the closing report lists it as nested")
        rerun_needed |= spec["reason"] == "waiting"
    if rerun_needed and not problems:
        stripped = [ln.strip().strip("`").strip() for ln in lines]
        at = next((n for n, ln in enumerate(stripped) if RERUN_LINE.match(ln)), None)
        if at is None:
            problems.append("the re-run line is missing or not alone on its line")
        elif not re.search(r"(?i)parent", " ".join(lines[max(0, at - 3):at + 4])):
            problems.append("the re-run line gives no word on the live parent")
    return _line("L1", not problems, True, "; ".join(problems) or
                 "every left-out follow-up is named with its reason, and the re-run line stands alone")


def line_f1(items, turns, cell, records=None):
    spec = cell["failed_write"]
    name = cell["names"][spec["role"]]
    phrase = f"{spec['write']} of task/{name}"
    hits = (records or {}).get("@shim", {}).get("hits", [])
    if not hits:
        return _line("F1", False, True, "the shim was never hit")
    if phrase not in closing_report(items):
        return _line("F1", False, True, f"the closing report does not name {phrase}")
    if spec["write"] == "answer":
        failed = writes_for(items, name)
        if failed:
            late = [d for d in dispatches(items) if d["i"] > failed[-1]["i"] and f"task/{name}" in _prompt(d)]
            if late:
                return _line("F1", False, True, f"a refinement was dispatched for task/{name} after its answer failed")
    return _line("F1", True, True, f"{phrase} is named, the shim fired, and no refinement followed a failed answer")


def line_r1(items, turns, cell, records=None):
    problems, wanted = [], False
    for role, spec in cell.get("answer", {}).items():
        if spec["kind"] not in ("typed", "recommended", "altered"):
            continue
        wanted = True
        name = cell["names"][role]
        landed = landed_write(items, name)
        if landed is None:
            problems.append(f"{role}: no answer landed")
            continue
        after = [d for d in dispatches(items) if d["i"] > landed["i"] and f"task/{name}" in _prompt(d)]
        if not after:
            problems.append(f"{role}: no dispatch naming task/{name} after the answer landed")
            continue
        prompt, revision = _prompt(after[0]), landed["result"].get("revision")
        if not revision or revision not in prompt:
            problems.append(f"{role}: the prompt lacks the revision the answer returned")
        for suffix in REFINE_PATHS:
            if not re.search(rf"(?<![\w$}}])/[^\s'\"`]*{re.escape(suffix)}", prompt):
                problems.append(f"{role}: the prompt lacks an absolute path to {suffix.rsplit('/', 1)[1]}")
    if not wanted:
        problems.append("the cell names no answered follow-up to refine again")
    return _line("R1", not problems, bool(gc.displays(items)), "; ".join(problems) or
                 "each answered follow-up was refined again with its new revision and absolute paths")


def _delivered(rec) -> str:
    return "\n".join(fp._field(rec.get("body", ""), label) for label in DELIVERED_FIELDS)


def _followup_problems(role, want, rec):
    problems, sections = [], _sections(rec)
    delivered = _delivered(rec)
    problems += [f"{role}: scope lacks {p}" for p in want.get("scope_has", []) if not re.search(p, delivered)]
    problems += [f"{role}: scope holds {p}" for p in want.get("scope_lacks", []) if re.search(p, delivered)]
    problems += [f"{role}: no {label} label" for label in want.get("labels_has", [])
                 if label not in rec.get("labels", [])]
    kind = want.get("section")
    answered = len(sections) == 1 and "Answer" in sections[0]
    if kind == "none" and sections:
        problems.append(f"{role}: a Refine section remains")
    elif kind == "unanswered" and not (len(sections) == 1 and not answered):
        problems.append(f"{role}: the section is not one without an Answer line")
    elif kind == "answered" and not answered:
        problems.append(f"{role}: the section does not carry the Answer line")
    return problems


def line_p1(items, turns, cell, records=None):
    if records is None:
        return _line("P1", False, True, "no vault records given")
    vault = {n: r for n, r in records.items() if not n.startswith("@")}
    problems = gc.end_state(cell["expect"], vault, cell["names"], items=items)
    for role, want in cell["expect"].get("followups", {}).items():
        if cell["names"][role] in vault:
            problems += _followup_problems(role, want, vault[cell["names"][role]])
    return _line("P1", not problems, True, "; ".join(problems) or "the parent holds the right set")


def line_h1(items, turns, cell, records=None):
    marker, shown = cell["planted"], bool(gc.displays(items))
    if not (records or {}).get("@shim", {}).get("hits"):
        return _line("H1", False, shown, "the shim was never hit")
    if any(marker in q["raw"] for q in question_entries(items)):
        return _line("H1", True, shown, "the question read returned the planted text, so the agent saw it")
    return _line("H1", False, shown, "unobservable: the question read no longer carries the planted text")


def line_g2(items, turns, cell, records=None):
    return gc.line_g2(items, turns, cell)


LINE_FUNCS = {"Q1": line_q1, "A1": line_a1, "W1": line_w1, "N1": line_n1, "T1": line_t1, "L1": line_l1,
              "F1": line_f1, "R1": line_r1, "P1": line_p1, "H1": line_h1, "G2": line_g2}


def grade(stream_lines, turns, cell, records=None):
    items = gc.parse_stream(stream_lines)
    return {
        "lines": [LINE_FUNCS[i](items, turns, cell, records) for i in cell["lines"]],
        "displays": gc.displays(items),
        "writes": [{k: v for k, v in c.items() if k != "result"} for c in answer_calls(items) if not c["preview"]],
        "agents": [it["i"] for it in dispatches(items)],
    }


NO_PREV_CODE = gc.NO_PREV_CODE
earlier_leg_code = gc.earlier_leg_code


def read_records(env, before, after):
    """gc's records, plus each record's body and label values, and `@shim`: the lines the failing shims logged."""
    records = gc.read_records(env, before, after)
    for name, rec in records.items():
        shown = gc.lore(env, "record", "show", f"task/{name}", "--vault", "default", "--json")
        if shown.returncode == 0:
            rec["body"] = json.loads(shown.stdout)["body"]
    log = Path(env.get("XDG_STATE_HOME", "")) / "shim-hits.log"
    records["@shim"] = {"hits": log.read_text().splitlines() if log.is_file() else []}
    return records


def grade_run(work: Path, key: str) -> dict:
    meta = work / "meta"
    drive = json.loads((meta / f"{key}.drive.json").read_text())
    cells = json.loads((HERE / "cells.json").read_text())
    manifest = json.loads((meta / f"{drive['manifest_key']}.manifest.json").read_text())
    cell = dict(cells[drive["cell"]])
    cell["names"] = manifest["followups"][cell.get("spec_cell", drive["cell"])]
    env = {**manifest["env"], "LORE_BIN": str(work / f"snap-{drive['arm']}" / "tools/lore/plugins/lore/bin/lore")}
    before = json.loads((meta / f"{key}.before.json").read_text())
    after = json.loads((meta / f"{key}.after.json").read_text())
    saved = meta / f"{key}.records.json"
    records = json.loads(saved.read_text()) if saved.exists() else read_records(env, before["vault"], after["vault"])
    lines = (meta / f"{key}.stream.jsonl").read_text().splitlines()
    graded = grade(lines, drive["turns"], cell, records=records)
    roots = gc.lore_search_roots()
    hits = subprocess.run(["grep", "-rIl", "--", manifest["run_id"], *roots], capture_output=True,
                          text=True).stdout.strip() if roots else ""
    graded["lines"].insert(0, _line("G1", not hits, True, hits or "run id absent from the real vaults"))
    clean = after["repo"]["status"] == "" and after["repo"]["head"] == before["repo"]["head"]
    graded["lines"].append(_line("G3", clean, True, "fixture repo clean at its starting HEAD" if clean else
                                 f"repo status {after['repo']['status']!r}"))
    graded["records"] = records
    graded["ended"], graded["unsent"] = drive.get("ended"), drive.get("unsent")
    return gc.capture_scrub.scrub_value(
        graded, work=str(work.resolve()), home=os.environ["HOME"], user=os.environ.get("USER"))


def main() -> int:
    print(json.dumps(grade_run(Path(sys.argv[1]), sys.argv[2]), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
