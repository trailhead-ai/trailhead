#!/usr/bin/env python3
"""Mechanical facts about the dispose half of one run; the verdict still comes from reading the report.

    grade_dispose.py <work> <cell-arm>

Prints one JSON object (names replaced by the fixture's roles, `X1`, `S`, ...). The existing case's
`grade.py` supplies the facts about refinement, `check`, `assemble` and the command lines; this adds:

- `dispose_runs`: each `followup_dispose.py` command the main thread ran, in order, with its position
  in the event stream, whether it was a `--preview`, the dispositions file it staged (drops, merges
  with the intent each carried, the reason, which members carried a revision), its `exit:` line, the
  `reason-code:` and `dependents` lines it printed, and the roles in the result's lists.
- `order`: the position of each of the display texts the prose puts before a write, of the first
  non-preview dispose, of the first `lore record` write run by the main thread, and of each Agent
  dispatch, `check` and `assemble`.
- `children_files`: the roles each `assemble` command staged.
- `fetched_by_main`: the roles whose body the main thread read with `lore record show`.
- `progress`: the progress lines in the assistant's text and in the final report.
- `closing_commands`: each line of the final report that is, or contains, a command, and whether it
  stands alone.
- `records`: each task in the vault: status, parent, supersedes, depends-on, the `## Dropped` reason, each
  `## Merged from` section with its first line, whether the body changed since the run started.
- `repo`: the fixture repo's HEAD before and after, and its status after.
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
    "reason_question": "why drop this follow-up",
    "confirmation_2": "drop, merge, include and leave as shown",
    "title_only": "title only, body not read",
    "possible_overlap": "possible overlap",
    "scope_question": "nest these under one new parent",
}
HEREDOC = re.compile(r"<<'(DISPOSITIONS_JSON|CHILDREN_JSON)'\n(.*?)\n\1", re.S)
STANDALONE = re.compile(
    r"^(/craft:\S+( \S+)*|lore record (update|supersede) task/\S+( \S+)*|merge \S+( \S+)* into \S+)$"
)


def lore(env, *argv):
    return subprocess.run(["lore", *argv], capture_output=True, text=True, env=env)


def main() -> int:
    work, key = Path(sys.argv[1]), sys.argv[2]
    meta = work / "meta"
    manifest = json.loads((meta / f"{key}.manifest.json").read_text())
    base = "base" if key.endswith("-base") else "treat"
    cell = key[: -len(base) - 1]
    env = {**manifest["env"], "LORE_BIN": str(work / f"snap-{base}" / "tools/lore/plugins/lore/bin/lore")}
    roles = {n: r for r, n in {**manifest["followups"][cell], **manifest.get("others", {}).get(cell, {})}.items()}

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

    order, dispose_runs, children_files, fetched, progress = {}, [], [], set(), []
    for i, (kind, a, b) in enumerate(events):
        if kind == "text":
            low = a.lower()
            for k, phrase in PHRASES.items():
                if phrase in low:
                    order.setdefault(k, []).append(i)
            for ln in a.splitlines():
                if re.match(r"\s*(dropped|merged|reset to open:|failed [a-z-]+:)\s.*task/", ln):
                    progress.append({"at": i, "line": rolify(ln.strip())})
        elif kind == "tool" and a == "Agent":
            order.setdefault("agent", []).append(i)
        elif kind == "tool" and a == "Bash":
            cmd = b["input"].get("command", "")
            out = results.get(b["id"], "")
            if re.search(r"\blore\s+record\s+(create|update|supersede)\b", cmd) and "followup_" not in cmd:
                order.setdefault("main_write", []).append(i)
            for m in re.finditer(r"\blore\s+record\s+show\b[^\n;|&]*", cmd):
                fetched.update(role(n) for n in roles if n in m.group(0))
            if "followup_parent.py check" in cmd:
                order.setdefault("check", []).append(i)
            if "followup_parent.py assemble" in cmd:
                order.setdefault("assemble", []).append(i)
                for m in HEREDOC.finditer(cmd):
                    if m.group(1) == "CHILDREN_JSON":
                        try:
                            children_files.append([role(c["name"]) for c in json.loads(m.group(2))])
                        except (ValueError, KeyError, TypeError):
                            children_files.append("unparsed")
            if "followup_dispose.py" in cmd:
                run = {"at": i, "preview": "--preview" in cmd, "file": None}
                m = next((m for m in HEREDOC.finditer(cmd) if m.group(1) == "DISPOSITIONS_JSON"), None)
                if m:
                    try:
                        doc = json.loads(m.group(2))
                        run["file"] = {
                            "reason": doc.get("reason"),
                            "drops": [
                                {"role": role(d["name"]), "revision": "revision" in d}
                                for d in doc.get("drops", [])
                            ],
                            "merges": [
                                {"survivor": role(g["survivor"]["name"]),
                                 "absorbed": [{"role": role(x["name"]), "intent": rolify(x.get("intent", ""))}
                                              for x in g.get("absorbed", [])]}
                                for g in doc.get("merges", [])
                            ],
                        }
                    except (ValueError, KeyError, TypeError, AttributeError):
                        run["file"] = "unparsed"
                ex = re.search(r"^exit: (\d+)", out, re.M)
                run["exit"] = int(ex.group(1)) if ex else None
                run["stderr_lines"] = [rolify(ln.strip()) for ln in out.splitlines()
                                       if ln.startswith(("reason-code:", "subject:", "dependents "))]
                try:
                    obj = json.loads(out[out.index("{"):out.rindex("}") + 1])
                    run["result"] = json.loads(rolify(json.dumps(obj)))
                except ValueError:
                    run["result"] = None
                dispose_runs.append(run)
                if not run["preview"]:
                    order.setdefault("write_dispose", []).append(i)

    closing = []
    for ln in final.splitlines():
        bare = ln.strip().strip("`").strip()
        if STANDALONE.match(bare):
            closing.append({"line": rolify(bare), "standalone": True})
        elif re.search(r"/craft:\S+|lore record (update|supersede)|\bmerge \S+.* into \S+", ln):
            closing.append({"line": rolify(ln.strip()), "standalone": False})
    final_progress = [rolify(ln.strip()) for ln in final.splitlines()
                      if re.match(r"\s*(dropped|merged|reset to open:|failed [a-z-]+:)\s.*task/", ln)]

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
        if n not in roles and not changed:
            continue
        dropped = re.search(r"^## Dropped\n\n(.*?)(?:\n\n|\Z)", body, re.M | re.S)
        records[role(n)] = {
            "status": sc.get("status"),
            "parent": sc.get("parent"),
            "supersedes": [role(x.split("/", 1)[-1]) for x in sc.get("supersedes") or []],
            "depends-on": [role(x.split("/", 1)[-1]) for x in sc.get("depends-on") or []],
            "dropped_note": rolify(dropped.group(1).strip()) if dropped else None,
            "merged_sections": [
                {"from": role(m.group(1)), "first_line": rolify(m.group(2))}
                for m in re.finditer(r"^## Merged from task/(\S+)\n\n(.*)$", body, re.M)
            ],
            "body_changed": changed,
            "new": path not in before["vault"],
            "body_has_5000": "5000" in body,
        }
    hits = subprocess.run(
        ["grep", "-rIl", "--", manifest["run_id"],
         f"{os.environ['HOME']}/.local/state/lore", f"{os.environ['HOME']}/.config/lore"],
        capture_output=True, text=True,
    ).stdout.strip()
    graded = {
        "dispose_runs": dispose_runs,
        "order": order,
        "children_files": children_files,
        "fetched_by_main": sorted(fetched),
        "progress": {"assistant_text": progress, "final_report": final_progress},
        "closing_commands": closing,
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
