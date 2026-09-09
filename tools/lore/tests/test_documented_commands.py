"""Every `lore <command>` the plugin's docs spell out is one the CLI registers.

Lore's skills and agents are instructions to run commands. An instruction naming
a command the CLI does not expose — a renamed verb, a removed one still quoted in
a recipe — reads fine and fails at the agent's first attempt to run it.

The documents are the INPUT: their invocations are lifted out of fenced blocks
and inline code spans and looked up against the subcommands `build_parser()`
really registers. That allowlist is derived from the parser, so a removed command
stops being valid the moment it is unregistered — there is no hand-maintained
table of retired names to keep in step.
"""

from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path

import pytest

from lore.argparse_util import find_subparsers_action  # noqa: F401  (import guard)
from lore.cli.dispatch import build_parser

_PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"

_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`(lore [^`]*)`")
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
# A command line: `lore <command> [<subcommand>]`, ignoring flags and operands.
_CALL = re.compile(r"^lore\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?")


def _registered_commands(parser: argparse.ArgumentParser, prefix: str = "") -> set[str]:
    """Every command path the parser accepts, e.g. {"search", "record create"}."""
    commands: set[str] = set()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if not isinstance(action, argparse._SubParsersAction) or not isinstance(
            choices, dict
        ):
            continue
        for name, subparser in choices.items():
            commands.add(prefix + name)
            commands |= _registered_commands(subparser, f"{prefix}{name} ")
    return commands


def _documented_calls() -> dict[str, set[str]]:
    """Every `lore …` invocation the shipped docs spell out, call → doc names.

    Both idioms count: a fenced block an agent copies wholesale, and an inline
    code span naming a command in prose.
    """
    calls: dict[str, set[str]] = {}
    registered = _registered_commands(build_parser())
    for md in sorted(_PLUGIN_ROOT.rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        for snippet in _FENCE.findall(text) + _INLINE.findall(text):
            for line in snippet.splitlines():
                match = _CALL.match(line.strip().lstrip("$ ").strip())
                if not match:
                    continue
                pair = f"{match.group(1)} {match.group(2) or ''}".strip()
                # A two-word call is only a subcommand if the CLI nests one there;
                # otherwise the second word is an operand and the verb stands alone.
                calls.setdefault(
                    pair if pair in registered else match.group(1), set()
                ).add(md.name)
    return calls


def test_every_documented_lore_command_is_registered_on_the_cli():
    registered = _registered_commands(build_parser())
    unknown = {
        call: sorted(docs)
        for call, docs in _documented_calls().items()
        if call not in registered
    }
    assert unknown == {}, (
        f"lore docs invoke commands the CLI does not register: {unknown} "
        f"(registered: {sorted(registered)})"
    )


def test_the_scan_finds_the_commands_the_skills_are_built_around():
    """The check above has teeth only while the extraction still reads the docs."""
    calls = set(_documented_calls())
    assert {"search", "record create", "session candidate", "flush"} <= calls, sorted(
        calls
    )


# ---------------------------------------------------------------------------
# Dispatch targets: an agent named in a skill must be one a tool installs.
# ---------------------------------------------------------------------------

_TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"

_DISPATCH_PATTERNS = (
    re.compile(r'subagent_type:\s*"([A-Za-z0-9_-]+)"'),
    re.compile(r"[Dd]ispatch(?:es|ing|ed)?\s+(?:the\s+)?`([a-z][a-z0-9-]*)`"),
)


def _dispatched_agents() -> dict[str, set[str]]:
    dispatched: dict[str, set[str]] = {}
    for md in sorted(_PLUGIN_ROOT.rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        for pattern in _DISPATCH_PATTERNS:
            for name in pattern.findall(text):
                dispatched.setdefault(name, set()).add(md.name)
    return dispatched


def test_every_dispatched_agent_resolves_to_an_installed_subagent():
    """`/lore:research` routes to `investigator` or `researcher`; a name no tool
    installs dispatches to nothing at runtime."""
    from trailhead.capabilities import load_manifest

    installed = {
        name
        for manifest in sorted(_TOOLS_DIR.glob("*/capabilities.toml"))
        for name in load_manifest(manifest).subagents
    }
    phantom = {
        name: sorted(docs)
        for name, docs in _dispatched_agents().items()
        if name not in installed
    }
    assert phantom == {}, (
        f"lore docs dispatch agents no tool installs: {phantom} "
        f"(installed: {sorted(installed)})"
    )


def test_the_dispatch_scan_finds_the_research_targets():
    """The check above has teeth only while the extraction still reads the docs."""
    assert {"investigator", "researcher"} <= set(_dispatched_agents())


# ---------------------------------------------------------------------------
# Slash commands: a `/tool:skill` a doc points at must be one a tool installs.
# ---------------------------------------------------------------------------

_SLASH_COMMAND = re.compile(r"/([a-z][a-z0-9_-]*):([a-z][a-z0-9_-]*)")


def _referenced_slash_commands() -> dict[str, set[str]]:
    referenced: dict[str, set[str]] = {}
    for md in sorted(_PLUGIN_ROOT.rglob("*.md")):
        for tool, name in _SLASH_COMMAND.findall(md.read_text(encoding="utf-8")):
            referenced.setdefault(f"{tool}:{name}", set()).add(md.name)
    return referenced


def test_every_referenced_slash_command_resolves_to_an_installed_skill():
    """A skill redirecting to `/lore:<name>` points at a dead surface if no tool
    registers that name — the retired `/lore:checkpoint` being the case in point."""
    from trailhead.capabilities import load_manifest

    installed = {
        f"{manifest.parent.name}:{name}"
        for manifest in sorted(_TOOLS_DIR.glob("*/capabilities.toml"))
        for name in load_manifest(manifest).skills
    }
    dead = {
        command: sorted(docs)
        for command, docs in _referenced_slash_commands().items()
        if command not in installed
    }
    assert dead == {}, (
        f"lore docs point at slash commands no tool registers: {dead} "
        f"(installed: {sorted(installed)})"
    )


def test_the_slash_scan_finds_the_skills_lore_cross_references():
    """The check above has teeth only while the extraction still reads the docs."""
    assert {"lore:record", "lore:flush", "lore:search"} <= set(
        _referenced_slash_commands()
    )


# ---------------------------------------------------------------------------
# The whole invocation: subcommand, flags and their arity, parsed for real.
# ---------------------------------------------------------------------------

_COMMENT = re.compile(r"\s+#.*$")
_ANGLE_PLACEHOLDER = re.compile(r"<[^>]*>")


def _runnable_documented_lines() -> list[tuple[str, str]]:
    """Every runnable `lore …` line in a fenced block, as (doc name, command).

    Usage synopses — the `lore pipeline [--vault NAME ...]` shape, which spells
    optionality with brackets rather than being typed as written — are not
    commands and are skipped.
    """
    found: list[tuple[str, str]] = []
    for md in sorted(_PLUGIN_ROOT.rglob("*.md")):
        for block in _FENCE.findall(md.read_text(encoding="utf-8")):
            for line in block.replace("\\\n", " ").splitlines():
                line = _COMMENT.sub("", line.strip().lstrip("$ ").strip())
                if "[" in line:
                    continue
                head, sep, rest = line.partition("lore ")
                if not sep or (head and not head.rstrip().endswith("|")):
                    continue
                found.append((md.name, f"lore {rest}"))
    return found


@pytest.mark.parametrize(
    "doc,command",
    _runnable_documented_lines(),
    ids=[f"{d}::{c}" for d, c in _runnable_documented_lines()],
)
def test_every_documented_invocation_parses_on_the_real_cli(doc: str, command: str):
    """A documented flag the CLI never registered — or one whose arity changed —
    fails the agent at its first attempt to run the recipe. Placeholders are
    filled with a token so the *shape* is what is under test, not the values."""
    argv = shlex.split(_ANGLE_PLACEHOLDER.sub("X", command))[1:]
    try:
        build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on an unknown flag or bad arity
        pytest.fail(f"{doc} documents `{command}`, which the CLI rejects (exit {exc.code})")


def _documented_kinds() -> dict[str, set[str]]:
    """Every concrete `--kind <value>` the docs spell out, kind → doc names."""
    kinds: dict[str, set[str]] = {}
    for md in sorted(_PLUGIN_ROOT.rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        # Code context only — prose like "the `--kind` is optional" names no kind.
        for snippet in _FENCE.findall(text) + _CODE_SPAN.findall(text):
            for value in re.findall(r"--kind\s+([a-z][a-z-]*)", snippet):
                kinds.setdefault(value, set()).add(md.name)
    return kinds


def test_every_documented_kind_is_a_real_record_kind():
    """A recipe naming a retired kind (`backlog`, `plan`) is rejected by
    `lore record create`, so the vocabulary the docs teach must be the
    vocabulary the model defines."""
    from lore.record.model import KINDS

    unknown = {
        kind: sorted(docs)
        for kind, docs in _documented_kinds().items()
        if kind not in KINDS
    }
    assert unknown == {}, (
        f"lore docs spell `--kind` values that are not record kinds: {unknown} "
        f"(kinds: {sorted(KINDS)})"
    )


def test_the_kind_scan_finds_the_kinds_the_recipes_use():
    """The check above has teeth only while the extraction still reads the docs."""
    assert {"blob", "decision"} <= set(_documented_kinds())
