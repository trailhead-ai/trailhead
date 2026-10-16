#!/usr/bin/env python3
"""Turn a run's stream-json into the captures a grader reads.

    extract.py <meta-dir> <cell-arm> <out-dir> [--redact STRING ...]

Writes, in <out-dir> (every file passed through `scrub.py`):
- `<cell-arm>.txt`           the arm's final report (the result event's text).
- `<cell-arm>.commands.txt`  every Bash command and Agent dispatch, in order, one block each,
                             marked `[main]` or `[sub]` (run inside a subagent). An Agent prompt
                             is cut to its first 600 characters.
- `<cell-arm>.transcript.txt` the main thread's assistant text and tool calls in order, with each
                             tool result cut to 1200 characters, for reading the order of questions,
                             progress lines and writes.
- `<cell-arm>.diff.txt`      the vault and repo files created, removed or changed between the before
                             and after trees.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrub as capture_scrub  # noqa: E402


def blocks(content):
    if isinstance(content, str):
        yield {"type": "text", "text": content}
        return
    yield from content


def cut(text, n):
    text = text if isinstance(text, str) else json.dumps(text)
    return text if len(text) <= n else text[:n] + f"... [cut {len(text) - n} chars]"


def main() -> int:
    meta, key, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    redact = [a for a in sys.argv[4:] if a != "--redact"]
    out.mkdir(parents=True, exist_ok=True)
    final, commands, transcript = "", [], []
    for line in (meta / f"{key}.stream.jsonl").read_text().splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        sub = "[sub]" if ev.get("parent_tool_use_id") else "[main]"
        if ev.get("type") == "result":
            final = ev.get("result", "")
        elif ev.get("type") == "assistant":
            for b in blocks(ev["message"]["content"]):
                if b["type"] == "text":
                    transcript.append(f"{sub} TEXT\n{b['text']}\n")
                elif b["type"] == "tool_use":
                    inp = b["input"]
                    if b["name"] == "Bash":
                        commands.append(f"{sub} BASH\n{inp.get('command', '')}\n")
                        transcript.append(f"{sub} BASH\n{inp.get('command', '')}\n")
                    elif b["name"] == "Agent":
                        commands.append(f"{sub} AGENT\n{cut(inp.get('prompt', ''), 600)}\n")
                        transcript.append(f"{sub} AGENT\n{cut(inp.get('prompt', ''), 600)}\n")
                    else:
                        transcript.append(f"{sub} TOOL {b['name']} {cut(json.dumps(inp), 200)}\n")
        elif ev.get("type") == "user":
            for b in blocks(ev["message"]["content"]):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    if isinstance(c, list):
                        c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
                    transcript.append(f"{sub} RESULT\n{cut(c, 1200)}\n")
    before = json.loads((meta / f"{key}.before.json").read_text())
    after = json.loads((meta / f"{key}.after.json").read_text())
    diff = []
    for area in ("vault", "repo"):
        b = before[area]["tree"] if area == "repo" else before[area]
        a = after[area]["tree"] if area == "repo" else after[area]
        for p in sorted(set(a) | set(b)):
            if p not in b:
                diff.append(f"{area} ADDED {p}")
            elif p not in a:
                diff.append(f"{area} REMOVED {p}")
            elif a[p] != b[p]:
                diff.append(f"{area} CHANGED {p}")
    diff.append(f"repo head before={before['repo']['head']} after={after['repo']['head']}")
    diff.append(f"repo status after={after['repo']['status']!r}")

    def scrub(s):
        for r in redact:
            s = s.replace(r, "[planted-string]")
        return capture_scrub.scrub_text(
            s, work=str(meta.resolve().parent), home=os.environ.get("HOME"), user=os.environ.get("USER")
        )

    (out / f"{key}.txt").write_text(scrub(final) + "\n")
    (out / f"{key}.commands.txt").write_text(scrub("\n".join(commands)))
    (out / f"{key}.transcript.txt").write_text(scrub("\n".join(transcript)))
    (out / f"{key}.diff.txt").write_text(scrub("\n".join(diff) + "\n"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
