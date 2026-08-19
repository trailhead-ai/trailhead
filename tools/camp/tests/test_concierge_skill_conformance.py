"""Conformance gate over the concierge skill document.

`skills/concierge/SKILL.md` is prose: nothing at operator time parses it, so a
renamed verb, an invented flag, or a deleted guarantee would ship silently. This
module is the CI-side check that keeps the document honest against the CLI it
documents. It never runs while an operator is using the skill.

Three groups:

1. **Invocation conformance** — every `camp …` command the document shows names
   a real verb and passes flags that verb accepts. The verb registry is
   `camp.spine.RESERVED`, not `workspace/verb_taxonomy.py`: the taxonomy models
   only aliases, redirects, disabled verbs, and the needs-a-group set, so live
   verbs such as `groups`, `list`, and `status` are absent from it. `RESERVED`
   is documented as every token that must not be dispatched as a bare slug — a
   superset of the taxonomy, pinned by an exact-membership assertion in
   `test_verb_aliases.py` — which makes it the one complete registry of camp's
   verb tokens. `NEEDS_GROUP_VERBS` is cross-checked separately, where the
   document's groupless guarantee depends on it.

2. **Gate conformance** — the load-bearing guarantees are present as required
   anchors. Deleting one fails here rather than on an operator's phone.

3. **Anti-mechanism guard** — the document instructs no direct tmux invocation,
   no direct harness spawn, and no reconstruction of the derived session name.
   The document legitimately *names* `camp-<slug>-<uuid8>` while forbidding its
   reconstruction, so the guard targets constructive spellings (templates,
   interpolation, uuid slicing) and requires every mention to sit in a sentence
   that says where the name comes from. Each guard is exercised against
   synthetic violating text as well as the real document, so a guard that has
   gone vacuous fails too.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_SKILL = _PLUGIN_DIR / "skills" / "concierge" / "SKILL.md"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


@pytest.fixture(scope="module")
def skill_text() -> str:
    assert _SKILL.exists(), f"the concierge skill document is missing: {_SKILL}"
    return _SKILL.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Extraction — command strings and their parsed shape
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```")
_INLINE_RE = re.compile(r"`([^`\n]+)`")


def _command_strings(text: str) -> list[str]:
    """Every command-shaped string in the document.

    Two carriers, both of which an agent reads as "run this": a line inside a
    fenced block, and an inline code span. Prose is deliberately excluded — the
    document describes what it will not do in prose, and that is not an
    instruction to run anything.
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


def _camp_invocations(text: str) -> list[list[str]]:
    """Tokenized `camp …` invocations, deduplicated, in document order."""
    seen: set[str] = set()
    invocations: list[list[str]] = []
    for command in _command_strings(text):
        if not command.startswith("camp ") or command in seen:
            continue
        seen.add(command)
        invocations.append(command.split())
    return invocations


def _flags(tokens: list[str]) -> list[str]:
    return [token.split("=", 1)[0] for token in tokens if token.startswith("--")]


# ---------------------------------------------------------------------------
# The flag surface, derived from the handlers themselves
# ---------------------------------------------------------------------------

# Each verb the document may use, mapped to the function that parses its
# arguments. A rename on either side fails `test_every_mapped_handler_exists`.
_VERB_HANDLERS: dict[str, tuple[str, str]] = {
    "groups": ("camp/cli/group.py", "_cmd_groups_cli"),
    "group": ("camp/cli/group.py", "_parse_init_args"),
    "new": ("camp/cli/group.py", "_cmd_new_group_cli"),
    "launch": ("camp/cli/session.py", "_cmd_launch_group_cli"),
    "sessions": ("camp/cli/session.py", "_cmd_sessions_group_cli"),
    "list": ("camp/cli/workspace.py", "_cmd_ls_group_cli"),
    "status": ("camp/cli/status.py", "_cmd_status_group_cli"),
}

# `--group` is consumed by the dispatcher before a verb is routed, so it is not
# spelled in most handlers. The two group-config verbs are dispatched ahead of
# group resolution and never accept it; `test_group_flag_placement_is_derivable`
# pins both halves against the code.
_GROUP_FLAG = "--group"
_GROUP_FLAG_REFUSED_BY = frozenset({"group", "groups"})

_FLAG_RE = re.compile(r"--[a-z][a-z0-9-]*")


def _handler_source(verb: str) -> str:
    """The argument-parsing function's own source — docstring included, since
    that is where several verbs spell their usage."""
    relpath, func_name = _VERB_HANDLERS[verb]
    source = (_PLUGIN_DIR / relpath).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{func_name} no longer exists in {relpath}")


def _accepted_flags(verb: str) -> set[str]:
    flags = set(_FLAG_RE.findall(_handler_source(verb)))
    if verb in _GROUP_FLAG_REFUSED_BY:
        flags.discard(_GROUP_FLAG)
    else:
        flags.add(_GROUP_FLAG)
    return flags


# ---------------------------------------------------------------------------
# 1. Invocation conformance
# ---------------------------------------------------------------------------


def test_document_shows_the_invocations_it_is_checked_against(skill_text: str) -> None:
    """A guard with nothing to check is not a guard: the document has to carry
    the group read, the two launch paths, and the reads the report is built
    from."""
    assert len(_camp_invocations(skill_text)) >= 5


def test_every_mapped_handler_exists() -> None:
    for verb in _VERB_HANDLERS:
        assert _handler_source(verb), verb


def test_every_documented_verb_is_a_real_camp_verb(skill_text: str) -> None:
    from camp.spine import RESERVED

    offenders = [
        tokens[1]
        for tokens in _camp_invocations(skill_text)
        if len(tokens) > 1 and not tokens[1].startswith("-") and tokens[1] not in RESERVED
    ]
    assert offenders == [], f"not camp verbs: {sorted(set(offenders))}"


def test_every_documented_verb_has_a_mapped_handler(skill_text: str) -> None:
    """A new verb in the document needs its flag surface mapped, or its flags go unchecked."""
    offenders = sorted(
        {
            tokens[1]
            for tokens in _camp_invocations(skill_text)
            if len(tokens) > 1 and tokens[1] not in _VERB_HANDLERS
        }
    )
    assert offenders == [], f"add these verbs to _VERB_HANDLERS: {offenders}"


def test_every_documented_flag_is_accepted_by_its_verb(skill_text: str) -> None:
    offenders: dict[str, list[str]] = {}
    for tokens in _camp_invocations(skill_text):
        verb = tokens[1]
        if verb not in _VERB_HANDLERS:
            continue
        accepted = _accepted_flags(verb)
        bad = [flag for flag in _flags(tokens) if flag not in accepted]
        if bad:
            offenders.setdefault(verb, []).extend(bad)
    assert offenders == {}, f"flags no camp verb accepts: {offenders}"


def test_group_flag_placement_is_derivable() -> None:
    """The `--group` rule the flag check relies on, pinned against the code."""
    dispatch = (_PLUGIN_DIR / "camp" / "cli" / "dispatch.py").read_text(encoding="utf-8")
    assert '"--group"' in dispatch, "the dispatcher no longer parses --group"
    from camp.workspace.verb_taxonomy import NEEDS_GROUP_VERBS

    for verb in _GROUP_FLAG_REFUSED_BY:
        assert verb not in NEEDS_GROUP_VERBS, f"{verb} now needs a group"


def test_groupless_reads_are_documented_without_a_group_flag(skill_text: str) -> None:
    """Group enumeration must stay runnable from a session that is in no group."""
    for tokens in _camp_invocations(skill_text):
        if tokens[1] in _GROUP_FLAG_REFUSED_BY:
            assert _GROUP_FLAG not in _flags(tokens), " ".join(tokens)


def test_group_scoped_verbs_are_documented_with_a_group(skill_text: str) -> None:
    """The skill is driven from a session that resolves no group, so every
    group-scoped invocation has to name one explicitly."""
    from camp.workspace.verb_taxonomy import NEEDS_GROUP_VERBS

    for tokens in _camp_invocations(skill_text):
        if tokens[1] in NEEDS_GROUP_VERBS:
            assert _GROUP_FLAG in _flags(tokens), " ".join(tokens)


# ---------------------------------------------------------------------------
# 2. Gate conformance
# ---------------------------------------------------------------------------

REQUIRED_ANCHORS: dict[str, str] = {
    "launch-confirmation gate": "the confirmation, not the request, is the authorization",
    "play back before asking": "Play the target back before you ask",
    "flag-shaped slug refusal": "refused, never passed through",
    "no camp state file reads": "never read camp's config or state files",
    "one mutating call": "exactly one mutating camp call",
    "group mismatch refusal": "never silently adopt",
    "derived name is not rebuilt": "never reconstructed",
    "blocked flows have an answer": "## Not yet",
}


def _missing_anchors(text: str) -> list[str]:
    """Anchors are matched against whitespace-normalized text, so rewrapping a
    paragraph is ordinary editing rather than a build failure."""
    flat = " ".join(text.split())
    return sorted(label for label, anchor in REQUIRED_ANCHORS.items() if anchor not in flat)


def test_every_load_bearing_guarantee_is_present(skill_text: str) -> None:
    assert _missing_anchors(skill_text) == []


def test_anchor_check_survives_rewrapping(skill_text: str) -> None:
    """Reflowing the document must not fail the gate."""
    assert _missing_anchors(skill_text.replace("\n", " ")) == []


def test_anchor_check_reports_a_deleted_guarantee(skill_text: str) -> None:
    """Deleting a guarantee has to fail — proven by deleting one."""
    label, anchor = next(iter(REQUIRED_ANCHORS.items()))
    flattened = " ".join(skill_text.split())
    assert _missing_anchors(flattened.replace(anchor, "")) == [label]


# ---------------------------------------------------------------------------
# 3. Anti-mechanism guard
# ---------------------------------------------------------------------------

# Programs the skill must never drive itself: camp's launch path is where the
# environment scrub and trust pre-seeding happen, and a session started around
# it gets neither.
_FORBIDDEN_COMMANDS = ("tmux", "claude")

# Spellings that BUILD the derived session name instead of reading it.
_RECONSTRUCTION_SPELLINGS = (
    "camp-{",
    "camp-$",
    'f"camp-',
    "f'camp-",
    "[:8]",
    "first 8",
    "first eight",
)

_DERIVED_NAME = "camp-<slug>-<uuid8>"
# A mention of the derived name is fine only alongside a statement of where it
# comes from, or that it is not to be built.
_SOURCING_CUES = ("never", "not ", "read", "reads", "reported", "prints", "printed", "output")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?:])\s+")


def _sentences(text: str) -> list[str]:
    return _SENTENCE_SPLIT_RE.split(" ".join(text.split()))


def _forbidden_mechanism_hits(text: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for command in _command_strings(text):
        head = command.split()[0] if command.split() else ""
        if head in _FORBIDDEN_COMMANDS:
            hits.setdefault("direct invocation", []).append(command)
    for spelling in _RECONSTRUCTION_SPELLINGS:
        if spelling in text:
            hits.setdefault("name reconstruction", []).append(spelling)
    for sentence in _sentences(text):
        if _DERIVED_NAME in sentence and not any(cue in sentence.lower() for cue in _SOURCING_CUES):
            hits.setdefault("unsourced derived name", []).append(sentence)
    return hits


def test_document_drives_no_mechanism_of_its_own(skill_text: str) -> None:
    assert _forbidden_mechanism_hits(skill_text) == {}


def test_document_names_the_derived_name_while_forbidding_its_reconstruction(
    skill_text: str,
) -> None:
    """The clean document mentions the pattern — the guard must not be passing
    just because there is nothing to catch."""
    assert _DERIVED_NAME in skill_text


@pytest.mark.parametrize(
    ("violation", "expected_key"),
    [
        ("Attach with `tmux attach -t <name>`.", "direct invocation"),
        ("Run `claude --resume <uuid>` in the workspace.", "direct invocation"),
        ("The name is `camp-{slug}-{uuid8}`.", "name reconstruction"),
        ("Take the first 8 characters of the session id.", "name reconstruction"),
        ("The handle is camp-<slug>-<uuid8> for later reference.", "unsourced derived name"),
    ],
)
def test_guard_catches_a_real_violation(violation: str, expected_key: str) -> None:
    """Each forbidden shape, proven to trip the guard on synthetic text."""
    assert expected_key in _forbidden_mechanism_hits(violation)


# ---------------------------------------------------------------------------
# Document shape
# ---------------------------------------------------------------------------


def test_document_carries_skill_frontmatter(skill_text: str) -> None:
    lines = skill_text.splitlines()
    assert lines[0] == "---"
    closing = lines.index("---", 1)
    frontmatter = "\n".join(lines[1:closing])
    assert "name: concierge" in frontmatter
    assert "description:" in frontmatter
