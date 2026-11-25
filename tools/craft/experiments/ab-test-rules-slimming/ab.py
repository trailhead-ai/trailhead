#!/usr/bin/env python3
"""Driver for the test-rules slimming A/B (see expected.md): four temptation scenarios, each run under
one of three rulesets.

  ab.py prepare ARM CASE DEST   a temp craft plugin copy holding CASE, its case file carrying ARM's ruleset
                                (ARM is slimmed, frozen or none)
  ab.py grants                  craft's runner grants, shell-quoted, for --allow-tools
  ab.py bar                     the bar text; `ab.py bar | git hash-object --stdin` is its fingerprint
  ab.py sync                    write the four push-gate cases under the craft plugin's evals/: each scenario's
                                case file, prompt and graders from this folder, the case file carrying the live
                                rules.md; run it after any edit to rules.md or to a scenario
  ab.py check                   exit 1, naming each case, when a committed case differs from what sync writes

The ruleset reaches a run through the case file's `execution.append_system_prompt`, the one always-loaded
route a run has. Stdlib only. The live plugin tree is never written; `prepare` writes only under DEST."""
from __future__ import annotations

import importlib.util
import json
import shlex
import shutil
import sys
import tempfile
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


def _prompt_lines(text: str) -> list[str]:
    """The case file lines holding `append_system_prompt: TEXT`. A block scalar keeps every line break real,
    so a `trailhead core show <id>` pointer ends at its own line and a scan of the file reads the id alone;
    a JSON string, which is also a YAML double-quoted scalar, is the form for text a block cannot hold."""
    stripped = text.rstrip("\n")
    plain = stripped and all(c in "\n\t" or c.isprintable() for c in text)
    if not plain:
        return [f"  {PROMPT_KEY}: {json.dumps(text)}\n"]
    kept = len(text) - len(stripped)
    chomp = "-" if kept == 0 else "" if kept == 1 else "+"
    body = [f"    {ln}\n" if ln else "\n" for ln in stripped.split("\n")]
    return [f"  {PROMPT_KEY}: |2{chomp}\n", *body, *["\n"] * max(kept - 1, 0)]


def get_append_system_prompt(case_yaml) -> str | None:
    """The case file's `execution.append_system_prompt` text, or None when it carries none."""
    lines = Path(case_yaml).read_text().splitlines()
    key = f"  {PROMPT_KEY}: "
    for i, ln in enumerate(lines):
        if not ln.startswith(key):
            continue
        rest = ln[len(key):]
        if rest.startswith('"'):
            return json.loads(rest)
        body = []
        for nxt in lines[i + 1:]:
            if nxt.startswith("    "):
                body.append(nxt[4:])
            elif nxt.strip():
                break
            else:
                body.append("")
        chomp = rest[2:]
        while chomp != "+" and body and body[-1] == "":
            body.pop()
        return "\n".join(body) + ("" if chomp == "-" else "\n")
    return None


def set_append_system_prompt(case_yaml, text: str | None) -> None:
    """Make the case file's `execution.append_system_prompt` TEXT, or absent when TEXT is None; every other
    key stays as it was."""
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
                while i < end and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                    i += 1
                continue
            body.append(lines[i])
            i += 1
        lines[head:end] = [lines[head], *body] if any(b.strip() for b in body) or text is not None else []
        if text is not None:
            lines[head + 1:head + 1] = _prompt_lines(text)
    elif text is not None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += ["execution:\n", *_prompt_lines(text)]
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


def _write_case(src: Path, dest: Path, text: str) -> None:
    """DEST's case file, prompt and graders as SRC's, the case file carrying TEXT. Anything else in DEST
    (expected.md, fixtures/) stays."""
    dest.mkdir(parents=True, exist_ok=True)
    for part in (src, dest):
        _refuse_symlinks(part)
    shutil.copy2(src / "case.yaml", dest / "case.yaml")
    shutil.copy2(src / "prompt.md", dest / "prompt.md")
    shutil.rmtree(dest / "graders", ignore_errors=True)
    shutil.copytree(src / "graders", dest / "graders")
    set_append_system_prompt(dest / "case.yaml", text)


def sync(craft_root=None, exp_dir=None, cases=None) -> None:
    """Write each of CASES under the craft plugin's evals/ from the experiment's scenario and the live rules."""
    exp_dir = Path(exp_dir or EXP)
    craft_root = Path(craft_root or craft_dir(exp_dir))
    text = ruleset_text("slimmed", craft_root, exp_dir)
    for case in cases or CASES:
        _write_case(exp_dir / case, craft_root / "evals" / case, text)


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def check(craft_root=None, exp_dir=None, cases=None) -> list[str]:
    """One message per committed case that differs from what `sync` would write, naming what differs."""
    exp_dir = Path(exp_dir or EXP)
    craft_root = Path(craft_root or craft_dir(exp_dir))
    text = ruleset_text("slimmed", craft_root, exp_dir)
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        for case in cases or CASES:
            committed = craft_root / "evals" / case
            if not (committed / "case.yaml").is_file():
                problems.append(f"{case}: not committed under evals/")
                continue
            want = Path(tmp) / case
            _write_case(exp_dir / case, want, text)
            differs = []
            if get_append_system_prompt(committed / "case.yaml") != text:
                differs.append("append_system_prompt differs from rules.md")
            have_yaml = Path(tmp) / f"{case}.have.yaml"
            shutil.copy2(committed / "case.yaml", have_yaml)
            set_append_system_prompt(have_yaml, None)
            set_append_system_prompt(want / "case.yaml", None)
            if have_yaml.read_bytes() != (want / "case.yaml").read_bytes():
                differs.append("case.yaml differs from the experiment's")
            if _bytes(committed / "prompt.md") != _bytes(want / "prompt.md"):
                differs.append("prompt.md differs from the experiment's")
            if _tree(committed / "graders") != _tree(want / "graders"):
                differs.append("graders/ differ from the experiment's")
            if differs:
                problems.append(f"{case}: {'; '.join(differs)}")
    return problems


def runner_grants(craft_root=None) -> list[str]:
    return shared.runner_args(Path(craft_root or craft_dir()))[1:]


def bar_text(expected_md=None) -> str:
    return shared.bar_text(Path(expected_md or EXP / "expected.md"))


def _dispatch(argv) -> int:
    cmd, args = (argv[1], argv[2:]) if len(argv) > 1 else ("", [])
    if cmd == "grants" and not args:
        print(" ".join(shlex.quote(g) for g in runner_grants()))
        return 0
    if cmd == "bar" and not args:
        sys.stdout.write(bar_text())
        return 0
    if cmd == "sync" and not args:
        sync()
        return 0
    if cmd == "check" and not args:
        problems = check()
        for problem in problems:
            print(f"ab.py check: {problem}", file=sys.stderr)
        if problems:
            print("ab.py check: run `ab.py sync` and commit the result", file=sys.stderr)
        return 1 if problems else 0
    if cmd == "prepare" and len(args) == 3:
        if args[1] not in CASES:
            print(f"ab.py: unknown case {args[1]!r}", file=sys.stderr)
            return 2
        try:
            print(build_arm_plugin(*args))
        except ValueError as err:
            print(f"ab.py: {err}", file=sys.stderr)
            return 2
        return 0
    print(__doc__, file=sys.stderr)
    return 2


def main(argv) -> int:
    try:
        return _dispatch(argv)
    except OSError as err:
        print(f"ab.py: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
