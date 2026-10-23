#!/usr/bin/env python3
"""Drive two user turns through one sandboxed `claude` process.

Usage: drive-turns.py <capture-1> <capture-2> <after-1> <sites-dir> <prompt-1> <prompt-2> -- <command...>

<command> is the whole sandboxed invocation (scripts/eval-sandbox ... claude --input-format stream-json
...); this script runs on the host, outside the sandbox, so it can write the captures beside the run.
`claude --resume` cannot find the first turn's session from inside scripts/eval-sandbox (the sandbox
denies reads under ~/.claude/projects), so both turns go to one process over stream-json input: the
first prompt is sent, the stream is read to the first `result` event (written to <capture-1>), the
sites listing is written to <after-1>, the second prompt is sent, and the rest of the stream goes to
<capture-2>. Exit code: the process's, or 3 when the first turn ended in an error or no result.
"""
import json
import subprocess
import sys
from pathlib import Path


def message(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}) + "\n"


def main(argv: list[str]) -> int:
    cap1, cap2, after1, sites, prompt1, prompt2 = argv[:6]
    command = argv[argv.index("--") + 1:]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    proc.stdin.write(message(Path(prompt1).read_text()))
    proc.stdin.flush()
    out, turn, failed = open(cap1, "w"), 1, False
    for line in proc.stdout:
        out.write(line)
        out.flush()
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "result":
            continue
        if turn == 1:
            failed = bool(event.get("is_error"))
            listing = sorted(str(p.relative_to(sites)) for p in Path(sites).rglob("*"))
            Path(after1).write_text("".join(f"./{name}\n" for name in listing))
            if failed:
                break
            out.close()
            out = open(cap2, "w")
            proc.stdin.write(message(Path(prompt2).read_text()))
            proc.stdin.flush()
            turn = 2
        else:
            break
    out.close()
    proc.stdin.close()
    code = proc.wait()
    return 3 if failed or turn == 1 else code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
