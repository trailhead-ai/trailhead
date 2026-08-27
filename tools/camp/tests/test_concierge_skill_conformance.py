"""Conformance gate over the concierge skill document.

`skills/concierge/SKILL.md` is prose: nothing at operator time parses it, so a
renamed verb, an invented flag, or a deleted guarantee would ship silently. This
module is the CI-side check that keeps the document honest against the CLI it
documents. It never runs while an operator is using the skill.

The two checks every skill's conformance suite needs — invocation conformance
and the anti-mechanism guard's forbidden-command check — live in
`test_skill_conformance_common` and are shared with `test_fork_skill_conformance`.
This module supplies its own verb-to-handler map and builds on those shared
checks; everything else here (the gate anchors, the output-shape checks, and
the name-reconstruction half of the anti-mechanism guard) is specific to what
concierge itself promises.

Four groups:

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

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
import test_skill_conformance_common as skill_common

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
# The flag surface, derived from the handlers themselves
# ---------------------------------------------------------------------------

# Each verb the document may use, mapped to the function that parses its
# arguments. A rename on either side fails `test_every_mapped_handler_exists`.
_VERB_HANDLERS: dict[str, tuple[str, str]] = {
    "groups": ("camp/cli/group.py", "_cmd_groups_cli"),
    "group": ("camp/cli/group.py", "_parse_init_args"),
    "new": ("camp/cli/group.py", "_cmd_new_group_cli"),
    "launch": ("camp/cli/session.py", "_cmd_launch_group_cli"),
    "kill": ("camp/cli/session.py", "_cmd_kill_cli"),
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


# ---------------------------------------------------------------------------
# 1. Invocation conformance (shared gate — see test_skill_conformance_common)
# ---------------------------------------------------------------------------


def test_document_shows_the_invocations_it_is_checked_against(skill_text: str) -> None:
    """A guard with nothing to check is not a guard: the document has to carry
    the group read, the two launch paths, and the reads the report is built
    from."""
    assert len(skill_common.camp_invocations(skill_text)) >= 5


def test_every_mapped_handler_exists() -> None:
    skill_common.every_mapped_handler_exists(_PLUGIN_DIR, _VERB_HANDLERS)


def test_every_documented_verb_is_a_real_camp_verb(skill_text: str) -> None:
    from camp.spine import RESERVED

    offenders = skill_common.unreal_verbs(skill_text, RESERVED)
    assert offenders == [], f"not camp verbs: {sorted(set(offenders))}"


def test_every_documented_verb_has_a_mapped_handler(skill_text: str) -> None:
    """A new verb in the document needs its flag surface mapped, or its flags go unchecked."""
    offenders = skill_common.unmapped_verbs(skill_text, _VERB_HANDLERS)
    assert offenders == [], f"add these verbs to _VERB_HANDLERS: {offenders}"


def test_every_documented_flag_is_accepted_by_its_verb(skill_text: str) -> None:
    offenders = skill_common.unaccepted_flags(
        skill_text,
        _PLUGIN_DIR,
        _VERB_HANDLERS,
        group_flag=_GROUP_FLAG,
        group_flag_refused_by=_GROUP_FLAG_REFUSED_BY,
    )
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
    for tokens in skill_common.camp_invocations(skill_text):
        if tokens[1] in _GROUP_FLAG_REFUSED_BY:
            assert _GROUP_FLAG not in skill_common.flags(tokens), " ".join(tokens)


def test_group_scoped_verbs_are_documented_with_a_group(skill_text: str) -> None:
    """The skill is driven from a session that resolves no group, so every
    group-scoped invocation has to name one explicitly.

    With one exception, and the dispatcher is asked what it is rather than told:
    a ref-addressed launch (`camp launch --resume <ref>`) is routed before group
    resolution, because a session that started in a camp workspace names its own
    group through the root recorded in its transcript. Requiring `--group` there
    would make the document contradict the CLI.
    """
    from camp.cli.dispatch import _is_ref_addressed_launch
    from camp.workspace.verb_taxonomy import NEEDS_GROUP_VERBS

    for tokens in skill_common.camp_invocations(skill_text):
        if tokens[1] not in NEEDS_GROUP_VERBS:
            continue
        if _is_ref_addressed_launch(tokens[1], tokens[2:]):
            continue
        assert _GROUP_FLAG in skill_common.flags(tokens), " ".join(tokens)


def test_the_groupless_launch_exemption_is_narrow() -> None:
    """The exemption above covers ref-addressed launches and nothing else."""
    from camp.cli.dispatch import _is_ref_addressed_launch

    assert _is_ref_addressed_launch("launch", ["--resume", "camp-foo"])
    assert not _is_ref_addressed_launch("launch", ["--dir", "/srv/work"])
    assert not _is_ref_addressed_launch("launch", ["myslug"])
    assert not _is_ref_addressed_launch("sessions", ["--resume", "camp-foo"])


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
    # The reuse path refuses by exiting non-zero with nothing on stdout, so a
    # reader who expects JSON on every outcome misses the failure entirely.
    "reuse-path refusal carries no JSON": "prints nothing at all on stdout",
    # The two paths report different directories under the same key.
    "the reported path differs by call": "reports the workspace root",
    # The report requires provisioning state, so the document has to sanction a
    # read that yields it.
    "provisioning state is obtainable": "camp status --name <slug> --group <name> --json",
    # Recovery rediscovers a dead session from the harness's own transcript, so
    # the operator keeps nothing. Telling them to hold on to the report would
    # invent an obligation camp does not impose.
    "a lost report strands nothing": (
        "camp rediscovers a dead session from the harness's own transcript"
    ),
    # `--resume` is the second camp read whose nonzero exit carries information
    # rather than failure, and it is the one an agent is most likely to
    # misreport: an ambiguous ref is a pick-list, not a broken command.
    "the ambiguity exit code is information": "Exit 2 is not a failure",
    "ambiguity goes back to the operator": (
        "relay them and ask the operator which one they mean"
    ),
    # A torn-down root is listed so it can be seen, and refused so it is not
    # offered as if it would work.
    "a vanished root is not offered": (
        "camp refuses to resume one rather than recreating the directory"
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
    # A stop that did not stop anything is the one outcome an agent is most
    # likely to soften into a success, because the memory claim is invisible
    # from the outside. It is a failure and the document has to say so — the
    # "exit N is not a failure" reading must never reach this code.
    "a session still running after a stop is a failure": (
        "still running after the stop is a failure, not a success with a caveat"
    ),
    # Idempotence: re-running a stop after a dropped connection is the ordinary
    # phone case, and reading it as an error would send the operator hunting.
    "stopping an already-down session is success": "already down is success, exit 0",
    # The two exit-0 outcomes are distinguished only in the JSON, so an agent
    # that reports "stopped" off the exit code alone reports a reclaim that may
    # not have happened.
    "the two stop successes are told apart in the payload": (
        "`outcome` field is what tells the two apart"
    ),
    # Park is only recoverable if the operator can still address the session
    # afterwards, and the ref is what they hold.
    "a ref survives a stop": "the reference does not change",
    # The anchor gate is live in this skill specifically: the caller here IS the
    # supervising session.
    "the anchor cannot stop itself": (
        "camp refuses to stop the session this skill is running in"
    ),
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
    # The candidate ROW shape, shared by the recoverable listing and by the rows
    # an ambiguous `--resume` prints — the document quotes it once for both.
    "camp sessions --recoverable --json": ("camp/cli/session.py", "_candidate_payload"),
    # A stop reports through one emitter, and `outcome` is the only thing that
    # tells its two exit-0 results apart — so the document has to quote a shape
    # that carries it.
    "camp kill --json": ("camp/cli/session.py", "_report_stop"),
}

_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}")
_JSON_KEY_RE = re.compile(r'"([a-z_]+)"\s*:')


def _emitted_key_sets(emitter: str) -> set[frozenset[str]]:
    """Every all-string-keyed dict literal the emitter builds."""
    tree = ast.parse(skill_common.function_source(_PLUGIN_DIR, *_EMITTERS[emitter]))
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
    # Direct invocation of a forbidden program (`tmux`, `claude`) is the
    # generic gate shared with every skill's conformance suite.
    direct = skill_common.forbidden_mechanism_hits(text)
    if direct:
        hits["direct invocation"] = direct
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
