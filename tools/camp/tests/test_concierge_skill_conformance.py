"""The concierge skill document, checked by running what it documents.

`skills/concierge/SKILL.md` is prose: nothing at operator time parses it, so a
renamed verb or a stale JSON shape would ship silently. The document is the
INPUT here — its invocations are fed to the real CLI and its quoted objects are
compared against what camp's emitters actually print. Nothing in this module
asserts that a sentence appears in the document.

Two groups:

1. **Invocation conformance** — every `camp …` verb the document shows is routed
   by the dispatcher, driven through the CLI. A verb can sit in `RESERVED` and
   still be routed nowhere, so membership is not the question; reaching a
   handler is. `--json` is checked the same way, by the output changing shape.

2. **Output-shape conformance** — every JSON object the document quotes has the
   key set an emitter really prints, captured by calling the emitter. The
   document tells an agent what to parse, so a key renamed in an emitter and
   left stale in the document is a parse that silently reads nothing. Read off
   the printed bytes rather than the emitter's source: a function that builds
   one dict and prints another is exactly the drift this catches.

The document's frontmatter is checked by `test_registrable`, which parses it the
way the harness does.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from ._helpers import init_git_repo

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"
_SKILL = _PLUGIN_DIR / "skills" / "concierge" / "SKILL.md"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


@pytest.fixture(scope="module")
def skill_text() -> str:
    return _SKILL.read_text(encoding="utf-8")


def _run(args: list[str], *, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the real camp CLI — the consumer the document's invocations go to."""
    base_env = {**os.environ}
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=base_env,
    )


@pytest.fixture()
def groupless_env(tmp_path: Path) -> dict[str, str]:
    """Env where no group resolves, so verb routing is what decides the outcome."""
    (tmp_path / "groups").mkdir(parents=True)
    return {"CAMP_CONFIG_DIR": str(tmp_path), "CAMP_STATE_DIR": str(tmp_path / "state")}


@pytest.fixture()
def group_env(tmp_path, monkeypatch):
    """A one-member group `camp new` can really create a workspace in.

    The detached provisioner is stubbed out: the emitter's key set is what is
    under test, not the background work the workspace then schedules.
    """
    import camp.provision.provision as provision

    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)
    repo = tmp_path / "repo_a"
    init_git_repo(repo)
    return {
        "group": {
            "group": {"name": "g"},
            "members": [
                {
                    "name": "repo_a",
                    "repo_root": str(repo),
                    "bootstrap": [],
                    "base": "origin/main",
                }
            ],
            "branch_pattern": "worktree-{slug}",
        },
        "env": {"CAMP_STATE_DIR": str(tmp_path / "state")},
    }


# ---------------------------------------------------------------------------
# The document, tokenized — these are inputs, never assertions
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


def _documented_verbs(text: str) -> list[str]:
    return sorted(
        {
            tokens[1]
            for tokens in _camp_invocations(text)
            if len(tokens) > 1 and not tokens[1].startswith("-")
        }
    )


#: The bare-slug refusal. A verb that reaches it was routed nowhere: every
#: branch fell through and the dispatcher read the token as a workspace name.
_BARE_SLUG = "bare slug dispatch is no longer supported"


# ---------------------------------------------------------------------------
# 1. Invocation conformance
# ---------------------------------------------------------------------------


def test_the_document_shows_the_invocations_it_is_checked_against(skill_text: str) -> None:
    """A guard with nothing to check is not a guard: the parametrized cases
    below are generated from the document, so an extractor that has drifted
    would silently generate none."""
    assert len(_camp_invocations(skill_text)) >= 5
    assert len(_documented_verbs(skill_text)) >= 5


@pytest.mark.parametrize("verb", _documented_verbs(_SKILL.read_text(encoding="utf-8")))
def test_every_documented_verb_is_routed_by_the_dispatcher(verb, groupless_env) -> None:
    """Run the verb. It may refuse for any reason of its own — a missing group,
    a missing argument, an unresolvable ref — but it must not fall through to
    the bare-slug refusal, which means no branch claimed it at all.
    """
    out = _run([verb], env=groupless_env)
    assert _BARE_SLUG not in (out.stdout + out.stderr), (
        f"`camp {verb}` is documented but reaches no handler:\n{out.stdout}{out.stderr}"
    )


def test_an_undocumented_token_still_reaches_the_bare_slug_refusal(groupless_env) -> None:
    """The check above has teeth only while the refusal is reachable."""
    out = _run(["definitely-not-a-verb"], env=groupless_env)
    assert _BARE_SLUG in (out.stdout + out.stderr)


def test_the_documented_json_flag_changes_the_output_to_json(groupless_env) -> None:
    """The document's whole purpose is telling an agent what to parse, so
    `--json` has to be a flag camp acts on rather than one it ignores. Camp
    accepts unknown flags silently, so the only honest check is the output.
    """
    human = _run(["groups"], env=groupless_env)
    assert human.stdout.strip() == "no groups configured"

    as_json = _run(["groups", "--json"], env=groupless_env)
    assert json.loads(as_json.stdout) == []


def test_the_groupless_launch_exemption_is_narrow() -> None:
    """A ref-addressed launch is routed before group resolution — and nothing
    else is, or the document's `--group` guidance would contradict the CLI."""
    from camp.cli.dispatch import _is_ref_addressed_launch

    assert _is_ref_addressed_launch("launch", ["--resume", "camp-foo"])
    assert not _is_ref_addressed_launch("launch", ["--dir", "/srv/work"])
    assert not _is_ref_addressed_launch("launch", ["myslug"])
    assert not _is_ref_addressed_launch("sessions", ["--resume", "camp-foo"])


# ---------------------------------------------------------------------------
# 2. Output-shape conformance
# ---------------------------------------------------------------------------

_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}")
_JSON_KEY_RE = re.compile(r'"([a-z_]+)"\s*:')


def _documented_key_sets(text: str) -> list[frozenset[str]]:
    """Each JSON object the document quotes, as its set of keys."""
    return [
        frozenset(keys)
        for obj in _JSON_OBJECT_RE.findall(text)
        if (keys := _JSON_KEY_RE.findall(obj))
    ]


class _Launched:
    """The shape `_report_launched` reads off a launch result."""

    launch_dir = Path("/srv/ws")
    session_id = "sess-1"
    tmux_name = "camp-ws-abcd1234"
    account = "/accounts/a"
    account_binding = {"FAKE_ACCOUNT_DIR": "/accounts/a"}


class _Candidate:
    """The shape the candidate emitters read off a resolver candidate."""

    session_id = "sess-1"
    derived_name = "camp-ws-abcd1234"
    root = Path("/srv/ws")
    age_seconds = 12.7
    root_missing = False
    unreadable = False


def _printed_object(capsys) -> dict:
    out = capsys.readouterr().out.strip()
    assert out, "the emitter printed nothing"
    return json.loads(out)


def _emitted_key_sets(capsys, group_env) -> dict[str, frozenset[str]]:
    """Run every emitter the document quotes; return what each really printed.

    Read off the printed bytes, not the source: an emitter that assembles one
    dict and prints another passes any check made against its dict literals.
    """
    from camp.cli.session import _candidate_payload, _report_launched, _report_stop

    shapes: dict[str, frozenset[str]] = {}

    _report_launched(_Launched(), as_json=True)
    shapes["camp launch --json"] = frozenset(_printed_object(capsys))

    _report_stop(_Candidate(), outcome="stopped", as_json=True)
    shapes["camp kill --json"] = frozenset(_printed_object(capsys))

    shapes["camp sessions --recoverable --json"] = frozenset(
        _candidate_payload(_Candidate())
    )

    group = importlib.import_module("camp.cli.group")
    group._cmd_new_group_cli(
        ["feat-x", "--launch", "--no-wait", "--json"],
        group_env["group"],
        group_env["env"],
        dry_run=False,
    )
    shapes["camp new --json"] = frozenset(_printed_object(capsys))

    return shapes


def test_every_documented_json_shape_is_one_camp_prints(
    skill_text, capsys, group_env
) -> None:
    known = set(_emitted_key_sets(capsys, group_env).values())
    unemitted = [sorted(s) for s in _documented_key_sets(skill_text) if s not in known]
    assert unemitted == [], f"documented shapes no camp emitter prints: {unemitted}"


def test_the_document_quotes_a_json_shape_at_all(skill_text: str) -> None:
    """The report is assembled out of these objects, so at least one has to be
    spelled out — otherwise the check above passes on nothing."""
    assert _documented_key_sets(skill_text)


def test_the_shape_check_catches_a_renamed_key(capsys, group_env) -> None:
    """A key the document quotes but no emitter prints, proven to fail."""
    fabricated = 'It prints `{"workspace": …, "session": …, "tmux_name": …}` on success.'
    known = set(_emitted_key_sets(capsys, group_env).values())
    unemitted = [sorted(s) for s in _documented_key_sets(fabricated) if s not in known]
    assert unemitted == [["session", "tmux_name", "workspace"]]


def test_both_launch_paths_print_the_same_success_shape(capsys, group_env) -> None:
    """The document shows one object for both launch paths; that is only honest
    while the two emitters agree — and they are separate functions."""
    shapes = _emitted_key_sets(capsys, group_env)
    assert shapes["camp new --json"] == shapes["camp launch --json"]
