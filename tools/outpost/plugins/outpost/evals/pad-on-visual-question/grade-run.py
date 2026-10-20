#!/usr/bin/env python3
"""Grade one finished run of this case from its fixture filesystem and its capture.

Usage: grade-run.py <run-dir> <capture.jsonl> <variant>

The pad tree on disk decides what was built (and where, and whether its options are
flows); the capture's final message and tool calls are reported for hand-reading. Prints
one JSON object. Message criteria are never graded here: every final message is hand-read.
"""
import json
import sys
from pathlib import Path


def is_plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def decode_round_js(path: Path) -> dict:
    lines = path.read_text().splitlines()
    if not lines:
        raise ValueError("round.js is empty")
    first = lines[0]
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


def pads_in(run: Path):
    """Every pad directory holding round-1/round.js, relative to the run directory."""
    found = run.glob("xdg-state/outpost/pads/*/round-1/round.js")
    found = list(found) + list(run.glob("xdg-state/camp/*/worktrees/*/sites/*/round-1/round.js"))
    return sorted(p.parent.parent for p in found if is_plain_file(p))


def option_keys(pad: Path):
    keys = []
    for round_js in sorted(pad.glob("round-*/round.js")):
        if not is_plain_file(round_js):
            continue
        for question in decode_round_js(round_js).get("questions", []):
            keys += [sorted(k for k in o if k in ("layout", "flow")) for o in question.get("options", [])]
    return keys


def main(run: Path, capture: Path, variant: str) -> dict:
    final, tools = capture_facts(capture)
    pads = pads_in(run)
    keys, failures = [], []
    for pad in pads:
        try:
            keys += option_keys(pad)
        except (ValueError, AttributeError, TypeError) as err:
            failures.append({"pad": pad.name, "reason": f"{type(err).__name__}: {err}"})
    return {
        "variant": variant,
        "pads": [p.name for p in pads],
        "pad locations": [str(p.relative_to(run)) for p in pads],
        "flow options": sum(1 for k in keys if "flow" in k),
        "layout options": sum(1 for k in keys if "layout" in k),
        "grading failures": failures,
        "tool calls": [n for n, _ in tools],
        "final message": final,
    }


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]), indent=2, ensure_ascii=False))
