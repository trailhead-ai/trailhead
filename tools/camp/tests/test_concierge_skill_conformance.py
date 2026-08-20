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

3. **Output-shape conformance** — every JSON object the document quotes has the
   key set the emitter actually prints. The document tells an agent what to
   parse, so a key renamed in an emitter and left stale in the document is a
   parse that silently reads nothing.

4. **Anti-mechanism guard** — the document instructs no direct tmux invocation,
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


def _function_source(relpath: str, func_name: str) -> str:
    source = (_PLUGIN_DIR / relpath).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{func_name} no longer exists in {relpath}")


def _handler_source(verb: str) -> str:
    """The argument-parsing function's own source — docstring included, since
    that is where several verbs spell their usage."""
    return _function_source(*_VERB_HANDLERS[verb])


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
    # The reuse path refuses by exiting non-zero with nothing on stdout, so a
    # reader who expects JSON on every outcome misses the failure entirely.
    "reuse-path refusal carries no JSON": "prints nothing at all on stdout",
    # The two paths report different directories under the same key.
    "the reported path differs by call": "reports the workspace root",
    # The report requires provisioning state, so the document has to sanction a
    # read that yields it.
    "provisioning state is obtainable": "camp status --name <slug> --group <name> --json",
    # Recovery is not a shipped capability; promising it strands the operator.
    # Scoped to a dead session on purpose: while the session is alive the same
    # facts are still queryable, so an unscoped claim would be false.
    "a lost report is not recoverable": (
        "once the session is dead, what the report carried cannot be recovered from here"
    ),
    # The create path never waits for provisioning, so the status read is
    # nonzero in the ordinary case. An agent applying the usual "nonzero means
    # it broke" rule would report a failure that did not happen.
    "the status exit code is information": (
        "a nonzero code here is the provisioning fact the report has to carry"
    ),
    # A group-scoped listing only ever reports the group it was asked about, so
    # the mismatch refusal has to name a read that can surface a different one.
    "the group-mismatch read is executable": "one listing per group",
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


@pytest.mark.parametrize("label", sorted(REQUIRED_ANCHORS))
def test_anchor_check_reports_a_deleted_guarantee(label: str, skill_text: str) -> None:
    """Deleting a guarantee has to fail — proven for every anchor, one at a
    time. Run over the whole set because an anchor that no longer matches the
    document would otherwise sit there checking nothing."""
    flattened = " ".join(skill_text.split())
    assert _missing_anchors(flattened.replace(REQUIRED_ANCHORS[label], "")) == [label]


# ---------------------------------------------------------------------------
# 3. Output-shape conformance
# ---------------------------------------------------------------------------

# Every JSON object shape the document may quote, mapped to the function that
# prints it. Key sets are read off the dict literals those functions build, so
# a renamed key fails here instead of becoming a lookup that returns nothing.
_EMITTERS: dict[str, tuple[str, str]] = {
    "camp new --launch --json": ("camp/cli/group.py", "_cmd_new_group_cli"),
    # Every launch flavor reports success through one emitter, so that function
    # — not the argument parser that calls it — is where the shape now lives.
    "camp launch --json": ("camp/cli/session.py", "_report_launched"),
    "camp groups --json": ("camp/cli/group.py", "_cmd_groups_cli"),
}

_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}")
_JSON_KEY_RE = re.compile(r'"([a-z_]+)"\s*:')


def _emitted_key_sets(emitter: str) -> set[frozenset[str]]:
    """Every all-string-keyed dict literal the emitter builds."""
    tree = ast.parse(_function_source(*_EMITTERS[emitter]))
    shapes = {
        frozenset(key.value for key in node.keys)
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        and node.keys
        and all(
            isinstance(key, ast.Constant) and isinstance(key.value, str)
            for key in node.keys
        )
    }
    assert shapes, f"{emitter} builds no JSON object at all"
    return shapes


def _documented_key_sets(text: str) -> list[frozenset[str]]:
    """Each JSON object the document quotes, as its set of keys."""
    return [
        frozenset(keys)
        for obj in _JSON_OBJECT_RE.findall(text)
        if (keys := _JSON_KEY_RE.findall(obj))
    ]


def _unemitted_key_sets(text: str) -> list[list[str]]:
    known = {shape for emitter in _EMITTERS for shape in _emitted_key_sets(emitter)}
    return [sorted(shape) for shape in _documented_key_sets(text) if shape not in known]


def test_every_documented_json_shape_is_one_camp_prints(skill_text: str) -> None:
    assert _unemitted_key_sets(skill_text) == []


def test_document_quotes_a_json_shape_at_all(skill_text: str) -> None:
    """The report is assembled out of these objects, so at least one has to be
    spelled out — otherwise the check above passes on nothing."""
    assert _documented_key_sets(skill_text)


def test_json_shape_check_catches_a_renamed_key() -> None:
    """A key the document quotes but no emitter prints, proven to fail."""
    fabricated = 'It prints `{"workspace": …, "session": …, "tmux_name": …}` on success.'
    assert _unemitted_key_sets(fabricated) == [["session", "tmux_name", "workspace"]]


def test_both_launch_paths_print_the_same_success_shape() -> None:
    """The document shows one object for both launch paths; that is only honest
    while the two emitters agree."""
    assert _emitted_key_sets("camp new --launch --json") == _emitted_key_sets(
        "camp launch --json"
    )


# ---------------------------------------------------------------------------
# 4. Anti-mechanism guard
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

# Verbs that describe ASSEMBLING the name. A sourcing cue does not excuse these:
# a sentence can name where the value comes from and still tell the reader to
# build it ("... built from the resolved slug and the session uuid8 output"),
# which is the instruction this guard exists to reject. Only an explicit
# negation in the same sentence clears one.
# Matched on word boundaries so the ADJECTIVE "derived" — as in "the derived
# session name", the document's own neutral term for the value — is not read as
# an instruction to derive it.
_CONSTRUCTIVE_RE = re.compile(
    r"\b(?:build|builds|building|built"
    r"|construct(?:s|ing|ed)?"
    r"|assembl(?:e|es|ing|ed)"
    r"|concatenat(?:e|es|ing|ed)"
    r"|compos(?:e|es|ing|ed)"
    r"|deriv(?:e|es|ing)"
    r"|format(?:s|ting|ted)?"
    r"|templat(?:e|es|ing|ed)"
    r"|interpolat(?:e|es|ing|ed)"
    r"|join(?:s|ing|ed)?)\b",
    re.IGNORECASE,
)
_NEGATIONS = ("never", "not ", "no ", "without", "rather than", "instead of")

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
        if _DERIVED_NAME not in sentence:
            continue
        lowered = sentence.lower()
        if not any(cue in lowered for cue in _SOURCING_CUES):
            hits.setdefault("unsourced derived name", []).append(sentence)
        # Checked independently of the sourcing cue: a cue word elsewhere in the
        # sentence must not license an instruction to assemble the name.
        if _CONSTRUCTIVE_RE.search(sentence) and not any(
            negation in lowered for negation in _NEGATIONS
        ):
            hits.setdefault("name assembled rather than read", []).append(sentence)
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
        # A sourcing cue in the sentence must not excuse an instruction to
        # assemble the name: each of these carries one ("output", "printed",
        # "read") and still tells the reader to put the name together.
        (
            "The pattern is camp-<slug>-<uuid8>, built from the resolved slug "
            "and the session uuid8 output value.",
            "name assembled rather than read",
        ),
        (
            "Read the output, then form camp-<slug>-<uuid8> by concatenating "
            "the slug and the uuid.",
            "name assembled rather than read",
        ),
        (
            "You may derive camp-<slug>-<uuid8> yourself from what camp printed.",
            "name assembled rather than read",
        ),
    ],
)
def test_guard_catches_a_real_violation(violation: str, expected_key: str) -> None:
    """Each forbidden shape, proven to trip the guard on synthetic text."""
    assert expected_key in _forbidden_mechanism_hits(violation)


@pytest.mark.parametrize(
    "permitted",
    [
        # The document's own neutral term for the value. "derived" is an
        # adjective here, not an instruction to derive anything.
        "camp's normalized slug appears in the derived session name "
        "camp-<slug>-<uuid8>, so it is always reported.",
        # Naming the forbidden act in order to forbid it.
        "`tmux_name` is read from camp's output — the derived name "
        "camp-<slug>-<uuid8> is never reconstructed.",
        "Report camp-<slug>-<uuid8> as camp printed it, rather than building "
        "it from the slug.",
    ],
)
def test_guard_permits_naming_the_pattern_without_instructing_it(permitted: str) -> None:
    """The guard distinguishes describing the name from assembling it; without
    this the strict reading above would forbid the document from discussing the
    value at all."""
    assert _forbidden_mechanism_hits(permitted) == {}


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
