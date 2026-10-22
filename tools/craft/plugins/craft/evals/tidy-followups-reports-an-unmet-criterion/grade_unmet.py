#!/usr/bin/env python3
"""Mechanical facts about one unmet-criterion run; the verdict still comes from reading the report.

    grade_unmet.py <work> <cell-arm>

`<cell-arm>` is the run key, such as `unmet-flagged-treat` or `near-miss-r2-base`; a trailing `-rN`
names a repeat and the cell is the name without it. Prints one JSON object (names replaced by the
fixture's roles, `U`, `K`, ... and `PARENT` for the follow-up parent the fixture made, `NEW` for one
the run made):

- `outcome_lines`: every line of the final report that holds `unmet-criterion check:`, with the
  parsed `flagged` count (0 for `none flagged`) and `blocked` count where the line has the shape.
- `final_ac_ids`: every `ACn` identifier in the final report.
- `closing_commands`: each line of the final report that is, or contains, a `/craft:` command or a
  `lore record` command, and whether it stands alone.
- `order`: the position, in the main thread's event stream, of the step 6 list heading, each
  confirmation question, the resume question, each refusal text (a `not unmet` offer), each Agent
  dispatch, `check`, `assemble`, `resume`, `dispose` runs and the first main-thread record write.
- `check_args`, `assemble_children`, `resume_children`, `dispose_files`, `agent_targets`: the roles
  each command or dispatch named.
- `fetched_by_main`: the roles whose body the main thread read with `lore record show`.
- `step6_list`: the lines following the `Unmet acceptance criteria` heading in the assistant text.
- `records`: each task in the vault: status, parent, depends-on, whether the body changed since the
  run started, whether it is new, the `## Dropped` reason, and for a follow-up parent the roles
  its body lists as `### task/<name>` headings.
- `repo`: the fixture repo's HEAD before and after, and its status after.
- `real_vault_hits`: files under the developer's real lore vaults and config that carry the run id.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tidy-followups-builds-one-parent"))
import scrub as capture_scrub  # noqa: E402

PHRASES = {
    "step6_list": "unmet acceptance criteria",
    "confirmation_1": "which listed follow-ups do you want to move onto the shortlist",
    "reason_question": "why drop this follow-up",
    "confirmation_2_plain": "include these, leave these",
    "confirmation_2": "drop, merge, include and leave as shown",
    "scope_question": "nest these under one new parent",
    "resume_question": "make it buildable",
}
HEREDOC = re.compile(r"<<'(DISPOSITIONS_JSON|CHILDREN_JSON)'\n(.*?)\n\1", re.S)
OUTCOME = re.compile(r"unmet-criterion check: (none flagged|(\d+) flagged)(?:; (\d+) blocked not checked)?")
STANDALONE = re.compile(r"^(/craft:\S+( \S+)*|lore record (update|supersede|amend) \S+( \S+)*)$")


def lore(env, *argv):
    return subprocess.run(["lore", *argv], capture_output=True, text=True, env=env)


def cell_of(key: str) -> tuple[str, str]:
    base = "base" if key.endswith("-base") else "treat"
    return re.sub(r"-r\d+$", "", key[: -len(base) - 1]), base


def main() -> int:
    work, key = Path(sys.argv[1]), sys.argv[2]
    meta = work / "meta"
    manifest = json.loads((meta / f"{key}.manifest.json").read_text())
    cell, arm = cell_of(key)
    env = {**manifest["env"], "LORE_BIN": str(work / f"snap-{arm}" / "tools/lore/plugins/lore/bin/lore")}
    roles = {n: r for r, n in manifest["followups"][cell].items()}
    if cell in manifest.get("parents", {}):
        roles[manifest["parents"][cell]] = "PARENT"

    def role(name):
        return roles.get(name, name)

    def rolify(text):
        for name in sorted(roles, key=len, reverse=True):
            text = text.replace(name, roles[name])
        return text

    events, results, final = [], {}, ""
    for line in (meta / f"{key}.stream.jsonl").read_text().splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            final = ev.get("result", "")
        if ev.get("parent_tool_use_id"):
            continue
        msg = ev.get("message", {})
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for b in content:
            if not isinstance(b, dict):
                continue
            if ev.get("type") == "assistant" and b["type"] == "text":
                events.append(("text", b["text"], None))
            elif ev.get("type") == "assistant" and b["type"] == "tool_use":
                events.append(("tool", b["name"], b))
            elif ev.get("type") == "user" and b.get("type") == "tool_result":
                c = b.get("content")
                if isinstance(c, list):
                    c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
                results[b["tool_use_id"]] = c or ""

    order, fetched = {}, set()
    check_args, assemble_children, resume_children, dispose_files, agent_targets = [], [], [], [], []
    step6_list = []
    for i, (kind, a, b) in enumerate(events):
        if kind == "text":
            low = a.lower()
            for k, phrase in PHRASES.items():
                if phrase in low:
                    order.setdefault(k, []).append(i)
            if "not unmet" in low:
                order.setdefault("not_unmet_offer", []).append(i)
            if not step6_list and "unmet acceptance criteria" in low:
                lines = a.splitlines()
                at = next(n for n, ln in enumerate(lines) if "unmet acceptance criteria" in ln.lower())
                step6_list = [rolify(ln.strip()) for ln in lines[at:at + 8]]
        elif kind == "tool" and a == "Agent":
            order.setdefault("agent", []).append(i)
            agent_targets.append(sorted({r for n, r in roles.items() if n in b["input"].get("prompt", "")}))
        elif kind == "tool" and a == "Bash":
            cmd = b["input"].get("command", "")
            if re.search(r"\blore\s+record\s+(create|update|supersede)\b", cmd) and "followup_" not in cmd:
                order.setdefault("main_write", []).append(i)
            for m in re.finditer(r"\blore\s+record\s+show\b[^\n;|&]*", cmd):
                fetched.update(r for n, r in roles.items() if n in m.group(0))
            if "followup_parent.py state" in cmd:
                order.setdefault("state", []).append(i)
            if "followup_parent.py check" in cmd:
                order.setdefault("check", []).append(i)
                tail = cmd.split("followup_parent.py check", 1)[1].split("\n")[0].split(";")[0].split()
                check_args.append(sorted(role(t) for t in tail if t in roles))
            for sub, bucket in (("assemble", assemble_children), ("resume", resume_children)):
                if f"followup_parent.py {sub}" in cmd:
                    order.setdefault(sub, []).append(i)
                    m = next((m for m in HEREDOC.finditer(cmd) if m.group(1) == "CHILDREN_JSON"), None)
                    try:
                        bucket.append(sorted(role(c["name"]) for c in json.loads(m.group(2))) if m else None)
                    except (ValueError, KeyError, TypeError):
                        bucket.append("unparsed")
                    ex = re.search(r"exit: (\d+)", results.get(b["id"], ""))
                    order.setdefault(f"{sub}_exit", []).append(int(ex.group(1)) if ex else None)
            if "followup_dispose.py" in cmd:
                run = {"at": i, "preview": "--preview" in cmd}
                m = next((m for m in HEREDOC.finditer(cmd) if m.group(1) == "DISPOSITIONS_JSON"), None)
                if m:
                    try:
                        doc = json.loads(m.group(2))
                        run["file"] = {
                            "reason": doc.get("reason"),
                            "drops": [role(d["name"]) for d in doc.get("drops", [])],
                            "merges": len(doc.get("merges", [])),
                        }
                    except (ValueError, KeyError, TypeError, AttributeError):
                        run["file"] = "unparsed"
                ex = re.search(r"^exit: (\d+)", results.get(b["id"], ""), re.M)
                run["exit"] = int(ex.group(1)) if ex else None
                dispose_files.append(run)

    outcome = []
    for ln in final.splitlines():
        if "unmet-criterion check:" in ln:
            m = OUTCOME.search(ln)
            outcome.append({
                "line": rolify(ln.strip()),
                "flagged": (0 if m.group(1) == "none flagged" else int(m.group(2))) if m else None,
                "blocked": int(m.group(3)) if m and m.group(3) else None,
            })
    closing = []
    for ln in final.splitlines():
        bare = ln.strip().strip("`").strip()
        if STANDALONE.match(bare):
            closing.append({"line": rolify(bare), "standalone": True})
        elif re.search(r"/craft:\S+|lore record (update|supersede|amend)", ln):
            closing.append({"line": rolify(ln.strip()), "standalone": False})

    before = json.loads((meta / f"{key}.before.json").read_text())
    after = json.loads((meta / f"{key}.after.json").read_text())
    listing = lore(env, "task", "list", "--vault", "default", "--json")
    names = []
    try:
        data = json.loads(listing.stdout)
        names = [t["name"] for t in (data if isinstance(data, list) else data.get("tasks", []))]
    except (ValueError, KeyError, TypeError):
        pass
    records = {}
    for n in names:
        r = lore(env, "record", "show", f"task/{n}", "--vault", "default", "--json")
        if r.returncode != 0:
            continue
        rec = json.loads(r.stdout)
        sc, body = rec["sidecar"], rec["body"]
        path = f"task/{n}.md"
        changed = before["vault"].get(path) != after["vault"].get(path)
        is_new = path not in before["vault"]
        if n not in roles and not changed and not is_new:
            continue
        label = role(n) if n in roles else "NEW"
        dropped = re.search(r"^## Dropped\n\n(.*?)(?:\n\n|\Z)", body, re.M | re.S)
        entry = {
            "status": sc.get("status"),
            "parent": role(sc["parent"].split("/", 1)[-1]) if sc.get("parent") else None,
            "depends-on": [role(x.split("/", 1)[-1]) for x in sc.get("depends-on") or []],
            "body_changed": changed,
            "new": is_new,
            "dropped_note": rolify(dropped.group(1).strip()) if dropped else None,
        }
        if "craft/followup-parent" in sc.get("labels", {}):
            entry["lists"] = [role(h) for h in re.findall(r"^### task/(\S+)", body, re.M)]
        records[label] = entry

    hits = subprocess.run(
        ["grep", "-rIl", "--", manifest["run_id"],
         f"{os.environ['HOME']}/.local/state/lore", f"{os.environ['HOME']}/.config/lore"],
        capture_output=True, text=True,
    ).stdout.strip()
    graded = {
        "cell": cell,
        "outcome_lines": outcome,
        "final_ac_ids": sorted(set(re.findall(r"\bAC\d+\b", final))),
        "closing_commands": closing,
        "order": order,
        "check_args": check_args,
        "assemble_children": assemble_children,
        "resume_children": resume_children,
        "dispose_files": dispose_files,
        "agent_targets": agent_targets,
        "fetched_by_main": sorted(fetched),
        "step6_list": step6_list,
        "records": records,
        "repo": {"head_before": before["repo"]["head"], "head_after": after["repo"]["head"],
                 "status_after": after["repo"]["status"]},
        "real_vault_hits": hits,
    }
    print(json.dumps(capture_scrub.scrub_value(
        graded, work=str(work.resolve()), home=os.environ["HOME"], user=os.environ.get("USER"),
    ), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
