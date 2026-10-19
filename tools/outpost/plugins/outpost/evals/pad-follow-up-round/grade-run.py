#!/usr/bin/env python3
"""Grade one finished run of this case from its fixture filesystem and its capture.

Usage: grade-run.py <run-dir> <capture.jsonl> <variant>

The pad tree on disk decides what was built; the capture's final message decides what
the user was told. Prints one JSON object: per criterion true/false, plus the evidence.
The message criteria are phrase heuristics and are always hand-read against the log.
"""
import json
import re
import sys
from pathlib import Path


_NOUN = r"(link|pad|tab|page)"
_WHEN = r"(again|new round|round 2)"
_OPEN_AGAIN = (
    rf"(open|reopen|re-open|reload|refresh|revisit|click)[^.\n]{{0,60}}{_NOUN}[^.\n]{{0,60}}{_WHEN}",
    rf"{_WHEN}[^.\n]{{0,60}}(open|reopen|reload|refresh)[^.\n]{{0,40}}{_NOUN}",
    rf"(open|reopen|reload|refresh)[^.\n]{{0,40}}{_NOUN}[^.\n]{{0,40}}(again|fresh)",
    r"(tab|page)[^.\n]{0,60}(won.t|will not|doesn.t|does not|not)[^.\n]{0,40}(update|refresh|know|see)",
)


def decode_round_js(path: Path) -> dict:
    first = path.read_text().splitlines()[0]
    literal = first.removeprefix("window.PAD_ROUND = JSON.parse(").removesuffix(");")
    return json.loads(json.loads(literal))


def capture_facts(capture: Path):
    final, tools = "", []
    for line in capture.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "result":
            final = event.get("result") or ""
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tools.append((block.get("name"), block.get("input")))
    return final, tools


def main(run: Path, capture: Path, variant: str) -> dict:
    final, tools = capture_facts(capture)
    pads_dir = run / "xdg-state" / "outpost" / "pads"
    pads = sorted(p.name for p in pads_dir.glob("*") if (p / "round-1" / "round.js").is_file())
    original = pads_dir / "audit-filters"
    rounds = sorted(p.name for p in original.glob("round-*") if p.is_dir())
    commands = [str(i.get("command", "")) for n, i in tools if n == "Bash" and isinstance(i, dict)]
    script_runs = [c for c in commands if "scratch_pad.py" in c]
    out = {"variant": variant, "pads": pads, "audit-filters rounds": rounds,
           "scratch_pad.py commands": script_runs, "tool calls": [n for n, _ in tools]}
    words = final.lower()
    asks = "?" in final
    if variant == "follow-up-same-design":
        settled = []
        if "round-2" in rounds:
            settled = decode_round_js(original / "round-2" / "round.js").get("settled", [])
        out["settled"] = settled
        out["C1 round added to the same pad with --add"] = (
            rounds == ["round-1", "round-2"] and pads == ["audit-filters"]
            and any("--add" in c for c in script_runs))
        out["C2 settled names the earlier answer"] = any("rail" in s.lower() for s in settled)
        out["C3 told to open the link again"] = any(re.search(rx, words) for rx in _OPEN_AGAIN)
        row_styles = ("single line", "one line", "one-line", "two line", "two-line", "card")
        out["C4 asked in words"] = asks and sum(k in words for k in row_styles) >= 2
    else:
        new = [p for p in pads if p != "audit-filters"]
        out["N1 a new pad was made under its own name"] = len(new) == 1
        out["N2 audit-filters unchanged"] = rounds == ["round-1"]
        out["N3 asked in words"] = asks and sum(k in words for k in ("dot", "badge", "strip")) >= 2
    out["final message"] = final
    return out


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]), indent=2, ensure_ascii=False))
