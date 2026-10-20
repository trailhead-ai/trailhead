#!/usr/bin/env python3
"""Rebuild fixtures/history.jsonl for the two dependents-answer cases.

Usage: build_history.py            (no arguments; run from anywhere)

`yes-to-dependent-fixes-corrects-restating-records-only` and
`no-to-dependent-fixes-leaves-every-record-as-it-was` resume the same seeded
conversation: the operator ruled that a power tool is lent for 6 days, not 4,
the agent amended the corrected decision, searched for the records that link to
or name it, read each hit, and put one yes/no question listing the two that
restate the old figure.

Every tool_result in that conversation is the captured output of the real `lore`
command the tool_use names, run in a throwaway vault seeded from the case's own
records.json (with the corrected decision's body put back to the old figure, the
state the agent read before it amended). Only the operator's and the agent's
prose is written here. Run it again whenever a record body in records.json
changes, so the `revision:` values and search output match what a run sees.

The two cases must carry byte-identical records.json files; the script refuses
otherwise, and writes the same history.jsonl into both fixtures directories.
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
REPO = HERE.parents[6]
YES_CASE = EVALS / "yes-to-dependent-fixes-corrects-restating-records-only"
NO_CASE = EVALS / "no-to-dependent-fixes-leaves-every-record-as-it-was"

FAKE_CWD = "/tmp/claude-eval-seed/home/cwd"
SESSION_ID = "7b2f4e90-3c1d-4a58-8e6b-9d0a5c7e1f34"
FIRST = "decision/set-the-loan-period-for-power-tools"
RESTATING_FROZEN = "decision/keep-a-wipe-down-step-on-every-tool-return"
RESTATING_AREA = "area/tool-desk"
LINK_ONLY = "task/check-the-drill-batteries-when-a-loan-comes-back"
OLD_PERIOD = "4 days"
NEW_PERIOD = "6 days"
CUE = (
    "The lore CLI is installed in this directory as `./lore`. "
    "Wherever a document or instruction says `lore`, run `./lore` instead."
)
OPERATOR_TURN = (
    f"In `{FIRST}` a power tool may be kept for 6 days, not 4; just fix that one decision.\n\n{CUE}"
)


def python_for_seeding() -> str:
    candidate = REPO / ".venv" / "bin" / "python"
    return str(candidate) if os.access(candidate, os.X_OK) else "python3"


def clean_env(home: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("LORE_CONFIG_DIR", "LORE_STATE_DIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "LORE_PUBLISH_DISABLE")
    }
    env["HOME"] = str(home)
    return env


class Vault:
    def __init__(self, root: Path, records: list[dict]) -> None:
        self.root = root
        self.cwd = root / "home" / "cwd"
        self.cwd.mkdir(parents=True)
        self.env = clean_env(root / "home")
        pre = root / "pre-records.json"
        pre.write_text(json.dumps(records), encoding="utf-8")
        seed = subprocess.run(
            [python_for_seeding(), str(REPO / "tools/lore/scripts/seed-eval-vault"), str(pre)],
            cwd=self.cwd, env=self.env, capture_output=True, text=True,
        )
        if seed.returncode != 0:
            sys.exit(f"build_history: seeding failed: {seed.stderr.strip()}")

    def run(self, command: str) -> str:
        done = subprocess.run(
            ["bash", "-c", command], cwd=self.cwd, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        if done.returncode != 0:
            sys.exit(f"build_history: `{command.splitlines()[0]}` exited {done.returncode}:\n{done.stdout}")
        return done.stdout.replace(str(self.cwd), FAKE_CWD).replace(str(self.root / "home"), FAKE_CWD.rsplit("/", 1)[0])


def revision_of(shown: str) -> str:
    first = shown.splitlines()[0]
    assert first.startswith("revision: "), first
    return first.removeprefix("revision: ")


def body_of(records: list[dict], record_id: str) -> str:
    kind, _, name = record_id.partition("/")
    for r in records:
        if r["kind"] == kind and slug(r["title"]) == name:
            return r["body"]
    sys.exit(f"build_history: no seeded record {record_id}")


def slug(title: str) -> str:
    out, dash = [], False
    for ch in title.lower():
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash:
            out.append("-")
            dash = True
    return "".join(out).strip("-")


def sentence_with(body: str, needle: str) -> str:
    for line in body.splitlines():
        for part in line.replace(". ", ".\n").splitlines():
            if needle in part:
                return part.strip()
    sys.exit(f"build_history: no sentence with {needle!r}")


class Writer:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.counter = 0
        self.parent: str | None = None
        self.call = 0

    def _uuid(self) -> str:
        self.counter += 1
        return f"b2000000-0000-0000-0000-{self.counter:012d}"

    def _base(self, kind: str, message: dict, extra: dict | None = None) -> dict:
        uuid = self._uuid()
        event = {
            "parentUuid": self.parent, "isSidechain": False, "sessionId": SESSION_ID,
            "version": "2.1.304", "userType": "external", "entrypoint": "cli", "cwd": FAKE_CWD,
            "gitBranch": "HEAD", "type": kind, "uuid": uuid,
            "timestamp": f"2026-10-20T10:{self.counter // 6:02d}:{(self.counter % 6) * 10:02d}.000Z",
            "message": message,
        }
        event.update(extra or {})
        self.events.append(event)
        self.parent = uuid
        return event

    def user(self, text: str) -> None:
        self._base("user", {"role": "user", "content": text})

    def assistant(self, text: str, command: str | None = None, description: str = "") -> str | None:
        content: list[dict] = [{"type": "text", "text": text}]
        tool_id = None
        if command is not None:
            self.call += 1
            tool_id = f"toolu_dep{self.call:02d}"
            content.append({
                "type": "tool_use", "id": tool_id, "name": "Bash",
                "input": {"command": command, "description": description},
            })
        self._base("assistant", {
            "id": f"msg_dep{self.counter}", "type": "message", "role": "assistant",
            "model": "claude-opus-5-5", "content": content,
            "stop_reason": "tool_use" if command is not None else "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 100},
        })
        return tool_id

    def result(self, tool_id: str, output: str) -> None:
        self._base(
            "user",
            {
                "role": "user",
                "content": [{"tool_use_id": tool_id, "type": "tool_result", "content": output, "is_error": False}],
            },
            {"toolUseResult": {"stdout": output, "stderr": "", "interrupted": False, "isImage": False}},
        )

    def step(self, vault: Vault, text: str, command: str, description: str) -> str:
        tool_id = self.assistant(text, command, description)
        output = vault.run(command)
        self.result(tool_id, output)
        return output

    def dump(self) -> str:
        return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.events)


def build() -> str:
    records = json.loads((YES_CASE / "fixtures" / "records.json").read_text(encoding="utf-8"))
    if (YES_CASE / "fixtures" / "records.json").read_bytes() != (NO_CASE / "fixtures" / "records.json").read_bytes():
        sys.exit("build_history: the two cases' records.json files differ")
    corrected = body_of(records, FIRST)
    if NEW_PERIOD not in corrected or OLD_PERIOD in corrected:
        sys.exit(f"build_history: the first record must carry {NEW_PERIOD!r}, the state the history leaves it in")
    pre_body = corrected.replace(NEW_PERIOD, OLD_PERIOD)
    first_kind, first_name = FIRST.split("/")
    pre_records = [
        dict(r, body=pre_body) if r["kind"] == first_kind and slug(r["title"]) == first_name else r for r in records
    ]

    with tempfile.TemporaryDirectory(prefix="build-history-") as tmp:
        vault = Vault(Path(tmp), pre_records)
        w = Writer()
        w.user(OPERATOR_TURN)

        shown = w.step(vault, "I'll read the decision first.", f"./lore record show {FIRST}", "Read the decision")
        revision = revision_of(shown)
        amend = (
            f"./lore record amend {FIRST} --reason \"The loan period is 6 days, not 4, as you ruled.\" "
            f"--initiated-by operator --base {revision} <<'EOF'\n{corrected}EOF"
        )
        w.step(
            vault,
            "Active decisions are frozen. This is a corrected figure and nothing else, "
            "so I will amend it where it stands.",
            amend,
            "Amend the decision",
        )

        linked = w.step(
            vault, "Next, any record linked to this decision.",
            f"./lore search 'related-decision:{FIRST.split('/')[1]}'", "Find records that link to it",
        )
        named = w.step(
            vault, "Then any record that cites it by name.",
            f"./lore search '\"{FIRST}\"'", "Find records that name it",
        )
        for record_id in (RESTATING_FROZEN, LINK_ONLY, RESTATING_AREA):
            w.step(vault, f"Reading {record_id}.", f"./lore record show {record_id} --json", f"Read {record_id}")

        frozen_old = sentence_with(body_of(records, RESTATING_FROZEN), OLD_PERIOD)
        area_old = sentence_with(body_of(records, RESTATING_AREA), OLD_PERIOD)
        frozen_new = frozen_old.replace(OLD_PERIOD, NEW_PERIOD)
        area_new = area_old.replace(OLD_PERIOD, NEW_PERIOD)
        checked = [h for h in (linked + named).splitlines() if h.strip().startswith("/")]
        if len(checked) != 3:
            sys.exit(f"build_history: expected 3 search hits, found {len(checked)}")
        w.assistant(
            f"Done: {FIRST} now gives the corrected period; "
            "the earlier wording remains in the version history. "
            "Three records link to it or cite it: two repeat the old figure and one only links "
            f"({LINK_ONLY}, which I have left alone). "
            "Records with no link to it and no mention of it were not searched.\n\n"
            "Shall I make the same correction in both of those records?\n\n"
            f"1. {RESTATING_FROZEN} (frozen, so it would be amended; last updated by eval@example.invalid)\n"
            f"   Old: {frozen_old}\n"
            f"   New: {frozen_new}\n"
            f"2. {RESTATING_AREA} (not frozen, so it would be edited directly; last updated by eval@example.invalid)\n"
            f"   Old: {area_old}\n"
            f"   New: {area_new}\n\n"
            "I will hold until you reply yes or no; neither record has been changed."
        )
        return w.dump()


def main() -> int:
    history = build()
    for case in (YES_CASE, NO_CASE):
        (case / "fixtures" / "history.jsonl").write_text(history, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
