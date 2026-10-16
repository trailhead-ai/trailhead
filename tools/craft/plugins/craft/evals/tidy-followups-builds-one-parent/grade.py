#!/usr/bin/env python3
"""Mechanical facts a grader needs from one run; the verdict still comes from reading the report.

    grade.py <work> <cell-arm> [<manifest-key> [<spec-key>]]

`<manifest-key>` names the run whose manifest/fixture the cell used (defaults to <cell-arm>;
`rerun` and `slice-pass` use the `chosen` run's). Prints one JSON object:

- `order`: the position, in the main thread's event stream, of the disposition question, each
  Agent dispatch, the first `lore record create|update` run by the main thread, the `check`
  and `assemble` commands, and the scope question. A pass needs every write after the
  disposition question.
- `agents`: how many dispatches, and whether each began only after the previous one's result.
- `check_args`: the names the `check` command was given.
- `verbatim`: for each `check` eligible follow-up, whether its `delivers` and `files` text appear,
  unchanged, in the main thread's assistant text.
- `records`: every task in the fixture vault: status, parent, labels, whether its body changed
  since the run started.
- `parent_body`: for each follow-up parent, the facts the pass condition names.
- `command_lines`: each line of the final report that holds `/craft:` or `lore record update`,
  and whether it stands alone (the whole line, or the only line in a fenced block).
- `real_vault_hits`: files under the developer's real lore vaults and config that carry the run id.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrub as capture_scrub  # noqa: E402


def lore(env, *argv):
    r = subprocess.run(["lore", *argv], capture_output=True, text=True, env=env)
    return r


def main() -> int:
    work, key = Path(sys.argv[1]), sys.argv[2]
    mkey = sys.argv[3] if len(sys.argv) > 3 else key
    spec_key = sys.argv[4] if len(sys.argv) > 4 else None
    meta = work / "meta"
    manifest = json.loads((meta / f"{mkey}.manifest.json").read_text())
    base = "base" if mkey.endswith("-base") else "treat"
    env = {**manifest["env"], "LORE_BIN": str(work / f"snap-{base}" / "tools/lore/plugins/lore/bin/lore")}

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
            if ev.get("type") == "assistant" and b["type"] == "text":
                events.append(("text", b["text"], None))
            elif ev.get("type") == "assistant" and b["type"] == "tool_use":
                events.append(("tool", b["name"], b))
            elif ev.get("type") == "user" and b.get("type") == "tool_result":
                c = b.get("content")
                if isinstance(c, list):
                    c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
                results[b["tool_use_id"]] = c or ""
                events.append(("result", b["tool_use_id"], None))

    order = {}
    agents, seq_ok, open_agent = 0, True, None
    check_args, check_out, assembles = [], [], 0
    for i, (kind, a, b) in enumerate(events):
        if kind == "text":
            if "rewrites each included follow-up" in a and "Proceed?" in a:
                order.setdefault("disposition_question", i)
            if "Nest these under one new parent?" in a:
                order.setdefault("scope_question", i)
        elif kind == "tool" and a == "Agent":
            agents += 1
            order.setdefault("agent_dispatches", []).append(i)
            if open_agent is not None:
                seq_ok = False
            open_agent = b["id"]
        elif kind == "tool" and a == "Bash":
            cmd = b["input"].get("command", "")
            if re.search(r"\blore\s+record\s+(create|update)\b", cmd):
                order.setdefault("first_main_write", i)
            if "followup_parent.py check" in cmd:
                order.setdefault("check", []).append(i)
                check_args.append(cmd.split("followup_parent.py check", 1)[1].split(";")[0].split())
                check_out.append(results.get(b["id"], ""))
            if "followup_parent.py assemble" in cmd:
                assembles += 1
                order.setdefault("assemble", []).append(i)
        elif kind == "result" and open_agent == a:
            open_agent = None

    text = "\n".join(a for k, a, _ in events if k == "text")
    verbatim = []
    for out in check_out:
        try:
            obj = json.loads(out[out.index("{"):out.rindex("}") + 1])
        except ValueError:
            continue
        for e in obj.get("eligible", []):
            verbatim.append({
                "name": e["name"],
                "delivers_verbatim": e["delivers"] in text,
                "files_verbatim": e["files"] in text,
            })

    records = {}
    before = json.loads((meta / f"{key}.before.json").read_text())["vault"]
    after = json.loads((meta / f"{key}.after.json").read_text())["vault"]
    listing = lore(env, "task", "list", "--vault", "default", "--json")
    names = []
    if listing.returncode == 0:
        try:
            data = json.loads(listing.stdout)
            names = [t["name"] for t in (data if isinstance(data, list) else data.get("tasks", []))]
        except (ValueError, KeyError, TypeError):
            names = []
    if not names:
        names = sorted({p.split("/")[1].rsplit(".", 1)[0] for p in after if p.startswith("task/")})
    parent_body = {}
    for n in names:
        r = lore(env, "record", "show", f"task/{n}", "--vault", "default", "--json")
        if r.returncode != 0:
            continue
        rec = json.loads(r.stdout)
        sc = rec["sidecar"]
        path = f"task/{n}.md"
        mine = spec_key and n in manifest["followups"][spec_key].values()
        if spec_key and not (mine or before.get(path) != after.get(path)):
            continue
        records[n] = {
            "status": sc.get("status"),
            "parent": sc.get("parent"),
            "labels": sorted(sc.get("labels", {})),
            "body_changed": before.get(path) != after.get(path),
            "new": path not in before,
        }
        if "craft/followup-parent" in sc.get("labels", {}):
            body = rec["body"]
            parent_body[n] = {
                "status": sc.get("status"),
                "related_spec": sc.get("related", {}).get("spec"),
                "sections": re.findall(r"^### (task/\S+)", body, re.M),
                "has_covers_field": "**Covers:**" in body,
                "has_flow_out": "## Flow-out" in body,
                "selection_reasons": len(re.findall(r"\*\*Selection reason:\*\*", body)),
            }

    lines = final.splitlines()
    command_lines = []
    in_fence, fence_start = False, 0
    for i, ln in enumerate(lines):
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            fence_start = i
            continue
        if "/craft:" in ln or "lore record update" in ln:
            alone = ln.strip().startswith(("/craft:", "lore record update")) and (
                in_fence is False or all(
                    not x.strip() or x.strip().startswith("```") or x is ln for x in lines[fence_start:i + 1]
                )
            )
            command_lines.append({"line": ln, "standalone": bool(alone), "in_fence": in_fence})

    hits = subprocess.run(
        ["grep", "-rIl", "--", manifest["run_id"],
         f"{os.environ['HOME']}/.local/state/lore", f"{os.environ['HOME']}/.config/lore"],
        capture_output=True, text=True,
    ).stdout.strip()
    graded = {
        "order": order,
        "agents": {"count": agents, "sequential": seq_ok},
        "check_args": check_args,
        "assembles": assembles,
        "verbatim": verbatim,
        "records": records,
        "parent_body": parent_body,
        "command_lines": command_lines,
        "real_vault_hits": hits,
    }
    print(json.dumps(capture_scrub.scrub_value(
        graded, work=str(work.resolve()), home=os.environ["HOME"], user=os.environ.get("USER"),
    ), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
