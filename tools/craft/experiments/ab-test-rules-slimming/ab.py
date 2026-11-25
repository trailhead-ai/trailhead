#!/usr/bin/env python3
"""Driver for the test-rules slimming A/B (see expected.md): four temptation scenarios, each run under
one of three rulesets.

  ab.py prepare ARM CASE DEST   a temp craft plugin copy holding CASE, its case file carrying ARM's ruleset
                                (ARM is slimmed, frozen or none)
  ab.py grants                  craft's runner grants plus the wrapper's, shell-quoted, for --allow-tools
  ab.py bar                     the bar text; `ab.py bar | git hash-object --stdin` is its fingerprint

The ruleset reaches a run through the case file's `execution.append_system_prompt`, the one always-loaded
route a run has. Stdlib only. The live plugin tree is never written; `prepare` writes only under DEST."""
from __future__ import annotations

import importlib.util
import json
import shlex
import shutil
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent
_path = EXP.parent / "ab-found-work-branch-load" / "ab.py"
_spec = importlib.util.spec_from_file_location("ab_shared", _path)
shared = importlib.util.module_from_spec(_spec)
sys.modules["ab_shared"] = shared
_spec.loader.exec_module(shared)

ARMS = ("slimmed", "frozen", "none")
CASES = (
    "tests-a-registered-exporter-by-running-it",
    "tests-a-changed-default-through-what-it-changes",
    "adds-a-cli-flag-test-first",
    "fixes-a-reported-bug-test-first",
)
WRAPPER_GRANT = "Bash(./run-tests)"
PROMPT_KEY = "append_system_prompt"


def craft_dir(exp_dir=None) -> Path:
    return Path(exp_dir or EXP).parents[1] / "plugins" / "craft"


def ruleset_text(arm, craft_root=None, exp_dir=None) -> str | None:
    """The text ARM appends to the system prompt: the live rules.md, the frozen copy, or none."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; choose one of {', '.join(ARMS)}")
    exp_dir = Path(exp_dir or EXP)
    if arm == "slimmed":
        return (Path(craft_root or craft_dir(exp_dir)) / "rules.md").read_text()
    if arm == "frozen":
        return (exp_dir / "arms" / "frozen" / "rules.md").read_text()
    return None


def set_append_system_prompt(case_yaml, text: str | None) -> None:
    """Make the case file's `execution.append_system_prompt` TEXT, or absent when TEXT is None; every other
    key stays as it was. The value is a JSON string, which is also a YAML double-quoted scalar."""
    path = Path(case_yaml)
    lines = path.read_text().splitlines(keepends=True)
    head = next((i for i, ln in enumerate(lines) if ln.rstrip() == "execution:"), None)
    if head is not None:
        end = head + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
            end += 1
        body, i = [], head + 1
        while i < end:
            indent = len(lines[i]) - len(lines[i].lstrip())
            if lines[i].lstrip().startswith(f"{PROMPT_KEY}:"):
                i += 1
                while i < end and lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) > indent:
                    i += 1
                continue
            body.append(lines[i])
            i += 1
        lines[head:end] = [lines[head], *body] if any(b.strip() for b in body) or text is not None else []
        if text is not None:
            lines.insert(head + 1, f"  {PROMPT_KEY}: {json.dumps(text, ensure_ascii=False)}\n")
    elif text is not None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += ["execution:\n", f"  {PROMPT_KEY}: {json.dumps(text, ensure_ascii=False)}\n"]
    path.write_text("".join(lines))


def _refuse_symlinks(root: Path) -> None:
    for p in (root, *root.rglob("*")):
        if p.is_symlink():
            raise ValueError(f"{p} is a symlink; the copy would reach outside the experiment")


def build_arm_plugin(arm, case, dest, craft_root=None, exp_dir=None) -> Path:
    """Copy the craft plugin to DEST without following symlinks, leaving out its evals/ and its rules.md,
    and add CASE and the shared scaffold under evals/, the case file carrying ARM's ruleset."""
    exp_dir = Path(exp_dir or EXP)
    craft_root, dest = Path(craft_root or craft_dir(exp_dir)), Path(dest)
    text = ruleset_text(arm, craft_root, exp_dir)
    if not (exp_dir / case / "case.yaml").is_file():
        raise ValueError(f"unknown case {case!r}")
    if dest.is_symlink():
        raise ValueError(f"{dest} is a symlink; writing the copy would reach outside it")
    for part in (exp_dir / case, exp_dir / "scaffold"):
        _refuse_symlinks(part)
    shutil.copytree(
        craft_root, dest, symlinks=True,
        ignore=lambda d, names: [
            n for n in names if n == "__pycache__" or (Path(d) == craft_root and n in ("evals", "rules.md"))],
    )
    copy = dest / "evals" / exp_dir.name
    shutil.copytree(exp_dir / case, copy / case, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(exp_dir / "scaffold", copy / "scaffold", ignore=shutil.ignore_patterns("__pycache__"))
    set_append_system_prompt(copy / case / "case.yaml", text)
    return dest


def runner_grants(craft_root=None) -> list[str]:
    return [*shared.runner_args(Path(craft_root or craft_dir()))[1:], WRAPPER_GRANT]


def bar_text(expected_md=None) -> str:
    return shared.bar_text(Path(expected_md or EXP / "expected.md"))


def main(argv) -> int:
    cmd, args = (argv[1], argv[2:]) if len(argv) > 1 else ("", [])
    if cmd == "grants" and not args:
        print(" ".join(shlex.quote(g) for g in runner_grants()))
        return 0
    if cmd == "bar" and not args:
        sys.stdout.write(bar_text())
        return 0
    if cmd == "prepare" and len(args) == 3:
        try:
            print(build_arm_plugin(*args))
        except ValueError as err:
            print(f"ab.py: {err}", file=sys.stderr)
            return 2
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
