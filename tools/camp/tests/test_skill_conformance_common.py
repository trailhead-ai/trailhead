"""Shared conformance-gate helpers for `skills/*/SKILL.md` documents.

Two of the checks any camp skill-document conformance suite runs are
independent of which skill is under test — they need only the document's
text, the plugin's per-verb argument-parsing handlers, and (for the flag
gate) which verbs refuse `--group`:

1. **Invocation conformance** — every documented `camp …` command names a
   real reserved verb, and every flag it documents is accepted by that verb's
   handler.
2. **Anti-mechanism guard** — no documented command starts with `tmux` or
   `claude`; a skill document drives no mechanism of its own.

Extracted here so `test_concierge_skill_conformance.py` and
`test_fork_skill_conformance.py` share one implementation instead of two that
can silently drift apart. Full parametrization of every conformance test over
every skill in `skills/` is explicitly out of scope — the gate-anchor checks,
the output-shape checks, and the name-reconstruction guard all encode
promises specific to one skill's own prose, and stay in that skill's own test
module.

This module holds no `test_*` functions of its own — it is a library the
per-skill test modules import from, each supplying its own skill text, its
own `_VERB_HANDLERS` map, and its own `--group` posture.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_FENCE_RE = re.compile(r"^\s*```")
_INLINE_RE = re.compile(r"`([^`\n]+)`")
_FLAG_RE = re.compile(r"--[a-z][a-z0-9-]*")

# Programs a skill document must never drive itself: camp's launch path is
# where the environment scrub and trust pre-seeding happen, and a session
# started around it gets neither.
FORBIDDEN_COMMANDS = ("tmux", "claude")


def command_strings(text: str) -> list[str]:
    """Every command-shaped string in the document.

    Two carriers, both of which an agent reads as "run this": a line inside a
    fenced block, and an inline code span. Prose is deliberately excluded —
    a document can describe what it will not do in prose without that being
    an instruction to run anything.
    """
    found: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                found.append(stripped)
            continue
        found.extend(span.strip() for span in _INLINE_RE.findall(line))
    return found


def camp_invocations(text: str) -> list[list[str]]:
    """Tokenized `camp …` invocations, deduplicated, in document order."""
    seen: set[str] = set()
    invocations: list[list[str]] = []
    for command in command_strings(text):
        if not command.startswith("camp ") or command in seen:
            continue
        seen.add(command)
        invocations.append(command.split())
    return invocations


def flags(tokens: list[str]) -> list[str]:
    return [token.split("=", 1)[0] for token in tokens if token.startswith("--")]


def function_source(plugin_dir: Path, relpath: str, func_name: str) -> str:
    source = (plugin_dir / relpath).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{func_name} no longer exists in {relpath}")


def handler_source(
    plugin_dir: Path, verb_handlers: dict[str, tuple[str, str]], verb: str
) -> str:
    """The argument-parsing function's own source — docstring included, since
    that is where several verbs spell their usage."""
    return function_source(plugin_dir, *verb_handlers[verb])


def accepted_flags(
    plugin_dir: Path,
    verb_handlers: dict[str, tuple[str, str]],
    verb: str,
    *,
    group_flag: str = "--group",
    group_flag_refused_by: frozenset[str] = frozenset(),
) -> set[str]:
    found = set(_FLAG_RE.findall(handler_source(plugin_dir, verb_handlers, verb)))
    if verb in group_flag_refused_by:
        found.discard(group_flag)
    else:
        found.add(group_flag)
    return found


def every_mapped_handler_exists(
    plugin_dir: Path, verb_handlers: dict[str, tuple[str, str]]
) -> None:
    for verb in verb_handlers:
        assert handler_source(plugin_dir, verb_handlers, verb), verb


def unreal_verbs(text: str, reserved: frozenset[str]) -> list[str]:
    """Documented verbs that are not real camp verbs at all."""
    return [
        tokens[1]
        for tokens in camp_invocations(text)
        if len(tokens) > 1 and not tokens[1].startswith("-") and tokens[1] not in reserved
    ]


def unmapped_verbs(text: str, verb_handlers: dict[str, tuple[str, str]]) -> list[str]:
    """Documented verbs with no entry in the caller's `_VERB_HANDLERS` map."""
    return sorted(
        {
            tokens[1]
            for tokens in camp_invocations(text)
            if len(tokens) > 1 and tokens[1] not in verb_handlers
        }
    )


def unaccepted_flags(
    text: str,
    plugin_dir: Path,
    verb_handlers: dict[str, tuple[str, str]],
    *,
    group_flag: str = "--group",
    group_flag_refused_by: frozenset[str] = frozenset(),
) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for tokens in camp_invocations(text):
        verb = tokens[1]
        if verb not in verb_handlers:
            continue
        accepted = accepted_flags(
            plugin_dir,
            verb_handlers,
            verb,
            group_flag=group_flag,
            group_flag_refused_by=group_flag_refused_by,
        )
        bad = [flag for flag in flags(tokens) if flag not in accepted]
        if bad:
            offenders.setdefault(verb, []).extend(bad)
    return offenders


def forbidden_mechanism_hits(
    text: str, forbidden: tuple[str, ...] = FORBIDDEN_COMMANDS
) -> list[str]:
    """Every documented command that directly invokes a forbidden program."""
    hits: list[str] = []
    for command in command_strings(text):
        head = command.split()[0] if command.split() else ""
        if head in forbidden:
            hits.append(command)
    return hits
