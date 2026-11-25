#!/usr/bin/env python3
"""Rebuild fixtures/history.jsonl for the three free-text-answer cases.

Usage: build_history.py            (no arguments; run from anywhere)

The three cases (`slice-carries-out-a-free-text-answer-to-its-question`,
`slice-asks-again-on-a-free-text-answer-that-cannot-be-undone`,
`slice-amends-the-spec-on-a-free-text-answer-that-changes-it`) resume the same
seeded conversation: the operator ran `/craft:slice spec/invoice-summary`, the
session loaded the slice skill, read the spec, and put the blocking prompt about the urgent entry F1.

The one tool_result is the captured output of the real `./lore record show`
command the tool_use names, run in a throwaway vault built by the urgent-entry
case's own scaffold (so the spec revision embedded in it is the one a run sees).
Only the operator's and the agent's prose is written here. The skill load is what a
real launch leaves in a conversation: the Skill call, its launch line, and the
skill's text as the harness shows it (a "Base directory for this skill" line, the
body without its front matter, the plugin root filled in, and the arguments).
The plugin root is `./craft`, the copy of the plugin each case's scaffold installs
in the run directory, because a seeded conversation is resumed literally and an
absolute path would hold only on the machine that built it. The history carries
no text of `skills/_shared/escalation.md`: the resumed session reads it itself.
Run it again whenever the slice skill or the scaffold's spec changes; it writes the
same history.jsonl into all three fixtures directories.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALS = HERE.parents[1]
PLUGIN = EVALS.parent
SLICE_SKILL = PLUGIN / "skills" / "slice" / "SKILL.md"
PLUGIN_IN_RUN = "./craft"
SKILL_ARGS = "spec/invoice-summary"
URGENT = EVALS / "slice-asks-before-dropping-an-urgent-entry"
CASES = [
    EVALS / "slice-carries-out-a-free-text-answer-to-its-question",
    EVALS / "slice-asks-again-on-a-free-text-answer-that-cannot-be-undone",
    EVALS / "slice-amends-the-spec-on-a-free-text-answer-that-changes-it",
]

FAKE_CWD = "/tmp/claude-eval-seed/home/cwd"
SESSION_ID = "c3a8e5f1-6d42-4b97-9a0e-2f1d7b8c4e65"
SHOW = "./lore record show spec/invoice-summary"

QUESTION = """\
One call here is yours to make before I go on. spec/invoice-summary#F1 is an open follow-up: a \
security finding at `billing-service/export.py:88`, outside this spec's code (`summary/`), so it \
fits no slice of this spec.

What is at stake: if F1 stays open, the spec cannot be reported complete; if it moves to a task, the\
 work stays tracked there but is no longer part of this spec's loop.

1. Move it to task/billing-export-hardening (recommended). F1 is dropped with a reason naming that \
task, and the work stays tracked there. Cost: the spec no longer shows it, so it is only as visible \
as that task.
2. Move it to task/billing-service-audit. F1 is dropped with a reason naming that task. Cost: that \
task is broader, so the finding may wait behind other audit work.
3. Keep it open and stop. F1 stays open and I stop here. Cost: the spec cannot be reported complete \
until someone builds or drops F1.

Reply with a number, or tell me in your own words what to do."""


def launch_text(skill_md: Path, args: str) -> str:
    """The text a real skill launch adds to the conversation, for the plugin copy in the run."""
    text = skill_md.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        text = text.split("\n---\n", 1)[1]
    body = text.replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN_IN_RUN)
    base = f"{PLUGIN_IN_RUN}/skills/{skill_md.parent.name}"
    return f"Base directory for this skill: {base}\n\n{body}\n\nARGUMENTS: {args}"


def clean_env(home: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("LORE_CONFIG_DIR", "LORE_STATE_DIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "LORE_PUBLISH_DISABLE")
    }
    env["HOME"] = str(home)
    return env


def operator_prompt() -> str:
    text = (URGENT / "prompt.md").read_text(encoding="utf-8")
    parts = text.split("---\n")
    return parts[-1].rstrip("\n")


def captured_show() -> str:
    with tempfile.TemporaryDirectory(prefix="build-history-", dir=os.environ.get("TMPDIR")) as tmp:
        home = Path(tmp) / "home"
        cwd = home / "cwd"
        cwd.mkdir(parents=True)
        env = clean_env(home)
        for argv in (["bash", str(URGENT / "fixtures" / "scaffold.sh")], ["bash", "-c", SHOW]):
            done = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if done.returncode != 0:
                sys.exit(f"build_history: `{' '.join(argv[-1:])}` exited {done.returncode}:\n{done.stdout}")
        return done.stdout.replace(str(cwd), FAKE_CWD).replace(str(home), FAKE_CWD.rsplit("/", 1)[0])


class Writer:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.counter = 0
        self.parent: str | None = None

    def _base(self, kind: str, message: dict, extra: dict | None = None) -> None:
        self.counter += 1
        uuid = f"c4000000-0000-0000-0000-{self.counter:012d}"
        event = {
            "parentUuid": self.parent, "isSidechain": False, "sessionId": SESSION_ID,
            "version": "2.1.304", "userType": "external", "entrypoint": "cli", "cwd": FAKE_CWD,
            "gitBranch": "HEAD", "type": kind, "uuid": uuid,
            "timestamp": f"2026-11-10T10:{self.counter // 6:02d}:{(self.counter % 6) * 10:02d}.000Z",
            "message": message,
        }
        event.update(extra or {})
        self.events.append(event)
        self.parent = uuid

    def user(self, text: str) -> None:
        self._base("user", {"role": "user", "content": text})

    def meta(self, text: str, source_tool_id: str) -> None:
        self._base("user", {"role": "user", "content": [{"type": "text", "text": text}]},
                   {"isMeta": True, "sourceToolUseID": source_tool_id})

    def assistant(self, text: str, command: str | None = None, description: str = "",
                  tool: tuple[str, dict] | None = None) -> str | None:
        content: list[dict] = [{"type": "text", "text": text}] if text else []
        tool_id = None
        if command is not None:
            tool = ("Bash", {"command": command, "description": description})
        if tool is not None:
            tool_id = f"toolu_free{self.counter + 1:02d}"
            content.append({"type": "tool_use", "id": tool_id, "name": tool[0], "input": tool[1]})
        self._base("assistant", {
            "id": f"msg_free{self.counter}", "type": "message", "role": "assistant",
            "model": "claude-opus-5-5", "content": content,
            "stop_reason": "tool_use" if tool is not None else "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 100},
        })
        return tool_id

    def result(self, tool_id: str, output: str, tool_use_result: dict | None = None) -> None:
        self._base(
            "user",
            {
                "role": "user",
                "content": [{"tool_use_id": tool_id, "type": "tool_result", "content": output, "is_error": False}],
            },
            {"toolUseResult": tool_use_result
             or {"stdout": output, "stderr": "", "interrupted": False, "isImage": False}},
        )

    def dump(self) -> str:
        return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.events)


def build() -> str:
    w = Writer()
    w.user(operator_prompt())
    skill_id = w.assistant("", tool=("Skill", {"skill": "craft:slice", "args": SKILL_ARGS}))
    w.result(skill_id, "Launching skill: craft:slice", {"success": True, "commandName": "craft:slice"})
    w.meta(launch_text(SLICE_SKILL, SKILL_ARGS), skill_id)
    tool_id = w.assistant("I'll read the spec first.", SHOW, "Read the spec")
    w.result(tool_id, captured_show())
    w.assistant(QUESTION)
    return w.dump()


def main() -> int:
    history = build()
    for case in CASES:
        (case / "fixtures").mkdir(exist_ok=True)
        (case / "fixtures" / "history.jsonl").write_text(history, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
