#!/usr/bin/env python3
"""Driver for the Found-work branch-load A/B (see expected.md).

  ab.py check                        the arm diff is exactly the move plus the load lines
  ab.py prepare ARM CASE DEST        a temp craft plugin copy with ARM's procedure swapped in
  ab.py count-reads TRACE            how many Reads of the branch file a kept trace holds
  ab.py evidence TRACE ROOT ARM      did the run read ARM's own procedure text, from ROOT
  ab.py grants                       craft's runner grants, shell-quoted, for --allow-tools
  ab.py bar                          the bar text; `ab.py bar | git hash-object --stdin` is its fingerprint

Stdlib only. The live skills/ tree is never written."""
from __future__ import annotations

import difflib
import json
import re
import shlex
import shutil
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent
CRAFT = EXP.parents[1]
REPO = EXP.parents[5]
ARMS = EXP / "arms"
PROCEDURE = "execute.md"
BRANCH_FILE = "found-work.md"
BRANCH_READ_GRADER = "branch-file-was-read.md"
LOAD = "read `../_shared/found-work.md` now and follow it in full."
CITATION = "(#found-work)"
_SECTION = re.compile(r"^## Found work\n.*?(?=^## )", re.S | re.M)


def check_arms(inline_dir, branch_dir) -> list[str]:
    """Problems with the branch arm; empty when its diff against the inline arm is exactly the
    Found work section moved byte-for-byte plus a load line at each citation of it."""
    inline_dir, branch_dir = Path(inline_dir), Path(branch_dir)
    inline = (inline_dir / PROCEDURE).read_text()
    branch = (branch_dir / PROCEDURE).read_text()
    moved = (branch_dir / BRANCH_FILE).read_text()
    found = _SECTION.search(inline)
    if not found:
        return ["inline arm has no '## Found work' section"]
    section = found.group(0)
    rest = inline[:found.start()] + inline[found.end():]
    problems: list[str] = []

    opener, blank, body = (moved.split("\n", 2) + ["", ""])[:3]
    if not opener.strip() or blank != "":
        problems.append("branch file does not open with one line followed by a blank line")
    if body != section:
        problems.append("the moved text differs from the inline arm's Found work section")

    old_lines, new_lines = rest.splitlines(), branch.splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        old, new = "\n".join(old_lines[i1:i2]), "\n".join(new_lines[j1:j2])
        if LOAD not in new or CITATION not in old:
            problems.append(f"change outside a load line at inline line {i1 + 1}: {old[:60]!r} -> {new[:60]!r}")
    cited, loaded = rest.count(CITATION), branch.count(LOAD)
    if loaded != cited:
        problems.append(f"{loaded} load lines for {cited} citations")
    if CITATION in branch:
        problems.append("a citation of the moved section survives in the branch arm")
    return problems


def _blocks(trace):
    for entry in trace:
        if isinstance(entry, str):
            entry = json.loads(entry)
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, list):
            yield from (b for b in content if isinstance(b, dict))


def _front_matter(path) -> dict[str, str]:
    lines = Path(path).read_text().split("\n")
    assert lines[0] == "---", f"{path}: no front matter"
    out = {}
    for ln in lines[1:lines.index("---", 1)]:
        key, _, value = ln.partition(":")
        out[key.strip()] = value.strip().strip("'\"")
    return out


def grade_tool_used(grader_path, trace) -> bool:
    """Apply a `tool_used` grader file to a trace the way the runner reads it: count the
    tool_use blocks of that tool whose string inputs match, and hold the count to min..max."""
    g = _front_matter(grader_path)
    assert g["type"] == "tool_used", g
    rx = re.compile(g.get("input_match", ""))
    n = sum(
        1 for b in _blocks(trace)
        if b.get("type") == "tool_use" and b.get("name") == g["tool"]
        and rx.search("\n".join(v for v in (b.get("input") or {}).values() if isinstance(v, str)))
    )
    return int(g.get("min", 0)) <= n <= int(g.get("max", 10**9))


def count_branch_reads(trace) -> int:
    return sum(
        1 for b in _blocks(trace)
        if b.get("type") == "tool_use" and b.get("name") == "Read"
        and re.search(r"_shared/found-work\.md", str((b.get("input") or {}).get("file_path", "")))
    )


def _result_text(block) -> str:
    c = block.get("content")
    return c if isinstance(c, str) else "\n".join(x.get("text", "") for x in c or [] if isinstance(x, dict))


def procedure_evidence(trace, plugin_root, arm) -> list[str]:
    """Problems showing the run did not read ARM's own procedure from the arm's plugin copy."""
    want = f"{str(plugin_root).rstrip('/')}/skills/_shared/{PROCEDURE}"
    blocks = list(_blocks(trace))
    reads = {b["id"]: b["input"].get("file_path", "") for b in blocks
             if b.get("type") == "tool_use" and b.get("name") == "Read"}
    problems = [f"read {p} instead of the arm copy's procedure"
                for p in reads.values() if p.endswith(f"skills/_shared/{PROCEDURE}") and p != want]
    text = "\n".join(_result_text(b) for b in blocks
                     if b.get("type") == "tool_result" and reads.get(b.get("tool_use_id")) == want)
    if not text:
        return problems + [f"the run never read {want}"]
    if arm == "inline" and (CITATION not in text or LOAD in text):
        problems.append("the procedure read is not the inline arm's text")
    if arm == "branch" and (LOAD not in text or CITATION in text):
        problems.append("the procedure read is not the branch arm's text")
    return problems


def build_arm_plugin(arm, case, dest, craft_root=None) -> Path:
    """Copy the craft plugin to DEST without following symlinks, swap in ARM's procedure, and
    keep only CASE (with the branch-read grader only where it applies)."""
    craft_root, dest = Path(craft_root or CRAFT), Path(dest)
    shutil.copytree(
        craft_root, dest, symlinks=True,
        ignore=lambda d, names: [n for n in names if n == "__pycache__" or (Path(d) == craft_root and n == "evals")],
    )
    shared = dest / "skills" / "_shared"
    shared.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ARMS / arm / PROCEDURE, shared / PROCEDURE)
    if arm == "branch":
        shutil.copyfile(ARMS / arm / BRANCH_FILE, shared / BRANCH_FILE)
    if case:
        exp = dest / "evals" / EXP.name
        shutil.copytree(EXP / case, exp / case, symlinks=True)
        (exp / "repo-root").write_text(f"{REPO}\n")
        if arm == "inline":
            (exp / case / "graders" / BRANCH_READ_GRADER).unlink(missing_ok=True)
    return dest


def runner_args(craft_root=None) -> list[str]:
    mapping = json.loads((Path(craft_root or CRAFT) / "evals" / "eval-gate.json").read_text())
    return ["--allow-tools", *mapping["runner"]["allow_tools"]]


def bar_text(expected_md=None) -> str:
    """The text between the bar's delimiters in expected.md, one trailing newline per line."""
    text = Path(expected_md or EXP / "expected.md").read_text()
    found = re.search(r"^<!-- bar-begin -->\n(.*?)^<!-- bar-end -->\n", text, re.S | re.M)
    return found.group(1) if found else ""


def _trace(path):
    return Path(path).read_text().splitlines()


def main(argv) -> int:
    cmd, args = (argv[1], argv[2:]) if len(argv) > 1 else ("", [])
    if cmd == "check":
        problems = check_arms(ARMS / "inline", ARMS / "branch")
        print("\n".join(problems) or "arm diff ok")
        return 1 if problems else 0
    if cmd == "grants":
        print(" ".join(shlex.quote(g) for g in runner_args()[1:]))
        return 0
    if cmd == "bar":
        sys.stdout.write(bar_text())
        return 0
    if cmd == "prepare" and len(args) == 3:
        print(build_arm_plugin(args[0], args[1], args[2]))
        return 0
    if cmd == "count-reads" and len(args) == 1:
        print(count_branch_reads(_trace(args[0])))
        return 0
    if cmd == "evidence" and len(args) == 3:
        problems = procedure_evidence(_trace(args[0]), args[1], args[2])
        print("\n".join(problems) or "procedure evidence ok")
        return 1 if problems else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
