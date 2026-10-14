"""Every `lore record …` recipe craft documents is one the CLI accepts, and none records a supersession by hand.

`superseded` is reachable only through `lore record supersede`; a plain
`create`/`update` into it is refused, and the raw `--supersedes` edge is the
verb's internal half, not a recipe. A craft skill, agent or template that spells
either teaches an agent a command that fails or leaves a supersession half done.

The documents are the INPUT: invocations are lifted out of fenced blocks and
inline code spans (the lifting `lore`'s own `test_documented_commands.py` uses)
and put to lore's real `build_parser()` and its own refusal predicate. Nothing
here asserts what a document says; the expected set derives from the code.

The second half drives each documented supersession route, in its documented
order, through the real CLI against tmp vaults.
"""

from __future__ import annotations

import contextlib
import difflib
import io
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
CRAFT_PLUGIN = REPO_ROOT / "plugins" / "craft"
_LORE_PLUGIN = REPO_ROOT.parent / "lore" / "plugins" / "lore"
_LORE_CLI = _LORE_PLUGIN / "cli" / "lore"
if str(_LORE_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_LORE_PLUGIN))

from lore.cli.dispatch import build_parser  # noqa: E402
from lore.record.guards import refuses_superseded_write  # noqa: E402

_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`(lore [^`\n]*)`")
_FLAG_SPAN = re.compile(r"`(--[a-z][^`\n]*)`")
_COMMENT = re.compile(r"\s+#.*$")
_PLACEHOLDER = re.compile(r"<[^>]*>")
_RECORD_WRITE = re.compile(r"^lore\s+record\s+(?:create|update)\b")

# A lesson has no `superseded` status, so consolidate's carry-over of a folded
# record's `supersedes` targets onto the survivor is the one raw-edge recipe.
RAW_EDGE_ALLOWLIST = {"skills/consolidate/SKILL.md"}
RAW_EDGE_FLAGS = {"--supersedes", "--unset-supersedes"}


def _documents() -> dict[str, str]:
    """Every shipped skill, agent and template, relative path -> text (evals are frozen prose)."""
    paths = [
        *(CRAFT_PLUGIN / "skills").rglob("*.md"),
        *(CRAFT_PLUGIN / "agents").glob("*.md"),
        *(CRAFT_PLUGIN / "templates").glob("*.md"),
    ]
    return {
        str(p.relative_to(CRAFT_PLUGIN)): p.read_text(encoding="utf-8")
        for p in sorted(paths)
    }


def _runnable_lines(text: str) -> list[str]:
    """Every runnable `lore …` line in a fenced block; synopses (`[--flag ...]`) are skipped."""
    found = []
    for block in _FENCE.findall(text):
        for line in block.replace("\\\n", " ").splitlines():
            line = _COMMENT.sub("", line.strip().lstrip("$ ").strip())
            if "[" in line:
                continue
            head, sep, rest = line.partition("lore ")
            if not sep or (head and not head.rstrip().endswith("|")):
                continue
            found.append(f"lore {rest}")
    return found


def _invocations(text: str) -> list[str]:
    """Every complete `lore record …` invocation: fenced lines plus inline spans. An inline
    span spelling a synopsis (`[`), an elision (`…`, `...`), or no record id after the
    verb (`lore record update --diff`) is a mention, not typed as written."""
    spans = [
        s.strip()
        for s in _INLINE.findall(text)
        if s.startswith("lore record ")
        and any("/" in token for token in s.split()[3:])
        and "[" not in s
        and "…" not in s
        and "..." not in s
    ]
    return [*_runnable_lines(text), *spans]


def _record_writes(text: str) -> list[str]:
    """Every `lore record create|update` invocation: fenced lines, inline spans, and
    bare flag spans (`--status …`) read as the update they are the flags of."""
    spans = [s.strip() for s in _INLINE.findall(text) if "[" not in s]
    flags = [f"lore record update X {s.strip()}" for s in _FLAG_SPAN.findall(text)]
    return [c for c in [*_runnable_lines(text), *spans, *flags] if _RECORD_WRITE.match(c)]


_SHELL_OPERATORS = {"|", "||", "&&", ";", ">", ">>", "<", "<<", "&"}


def _argv(command: str) -> list[str]:
    """The lore argv of *command*, cut at the first shell operator (a pipe into a gate, a redirect)."""
    lexer = shlex.shlex(_PLACEHOLDER.sub("X", command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    argv = []
    for token in lexer:
        if token in _SHELL_OPERATORS:
            break
        argv.append(token)
    return argv[1:]


def _unparseable(documents: dict[str, str]) -> list[tuple[str, str]]:
    bad = []
    for name, text in documents.items():
        for command in _invocations(text):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    build_parser().parse_args(_argv(command))
            except SystemExit:
                bad.append((name, command))
    return bad


def _refused_superseded_writes(documents: dict[str, str]) -> list[tuple[str, str]]:
    refused = []
    for name, text in documents.items():
        for command in _record_writes(text):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    args = build_parser().parse_args(_argv(command))
            except SystemExit:
                continue  # a bare flag span need not parse alone; fenced lines are checked above
            if refuses_superseded_write(getattr(args, "status", None), None):
                refused.append((name, command))
    return refused


def _raw_edge_recipes(documents: dict[str, str]) -> list[tuple[str, str]]:
    raw = []
    for name, text in documents.items():
        if name in RAW_EDGE_ALLOWLIST:
            continue
        for command in _record_writes(text):
            if RAW_EDGE_FLAGS & set(_argv(command)):
                raw.append((name, command))
    return raw


def test_every_documented_lore_invocation_parses_on_the_real_cli():
    assert _unparseable(_documents()) == []


def test_no_documented_create_or_update_writes_superseded():
    """Check (b): the verb is the only door into `superseded`."""
    assert _refused_superseded_writes(_documents()) == []


def test_no_documented_recipe_uses_a_raw_supersedes_edge_outside_the_allowlist():
    """Check (c): a site swapping the verb for a raw edge edit goes red."""
    assert _raw_edge_recipes(_documents()) == []


@pytest.mark.parametrize(
    "snippet,refused,raw",
    [
        ("`lore record update task/x --status superseded --related spec=y`", True, False),
        ("```\nlore record create --kind adr --title T --status superseded\n```", True, False),
        ("then `--status superseded --diff` with the note", True, False),
        ("`lore record update task/x --status dropped --diff`", False, False),
        ("`lore record supersede task/x --by spec/y`", False, False),
        ("`lore record create --kind spec --title T --supersedes spec/y`", False, True),
        ("`lore record update spec/x --unset-supersedes spec/y`", False, True),
        ("`lore record update spec/x --unset-label craft/supersedes`", False, False),
    ],
)
def test_the_checks_report_exactly_the_invocations_they_should(snippet, refused, raw):
    docs = {"skills/injected/SKILL.md": snippet}
    assert bool(_refused_superseded_writes(docs)) is refused
    assert bool(_raw_edge_recipes(docs)) is raw


@pytest.mark.parametrize(
    "snippet,unparseable",
    [
        ("`lore record supersede adr/old --bye adr/new`", True),
        ("`lore record supersede adr/old --by adr/new`", False),
        ("```\nlore record supersede adr/old --bye adr/new\n```", True),
        ("`lore record supersede <old> --by <new>`", False),
        ("`lore record supersede [adr/old] --bye adr/new`", False),
        ("`lore record supersede adr/old --bye ...`", False),
        ("`lore record update`", False),
        ("`lore record update adr/old --bye`", True),
        ("`lore record update --bye`", False),
        ("`lore record update adr/x --bye`", True),
        ("`lore new`", False),
        ("`lore search x --vault y`", False),
        ("then `--bye` is wrong but a bare flag span is not an invocation", False),
    ],
)
def test_check_a_parses_inline_spans_as_well_as_fenced_lines(snippet, unparseable):
    docs = {"skills/injected/SKILL.md": snippet}
    assert bool(_unparseable(docs)) is unparseable


def _supersede_sites() -> list[tuple[str, str]]:
    return [
        (name, span)
        for name, text in _documents().items()
        for span in _INLINE.findall(text)
        if span.startswith("lore record supersede ") and len(span.split()) > 3
    ]


@pytest.mark.parametrize("name,span", _supersede_sites(), ids=lambda v: v)
def test_corrupting_a_documented_supersede_flag_is_reported_by_check_a(name, span):
    text = _documents()[name]
    corrupted = text.replace(f"`{span}`", f"`{span.replace('--by', '--bye', 1)}`", 1)
    assert corrupted != text
    assert (name, span.replace("--by", "--bye", 1)) in _unparseable({name: corrupted})


def test_the_raw_edge_allowlist_exempts_only_consolidate():
    recipe = "`lore record update lesson/x --supersedes lesson/y`"
    docs = {name: recipe for name in ("skills/consolidate/SKILL.md", "skills/distill/SKILL.md")}
    assert [name for name, _ in _raw_edge_recipes(docs)] == ["skills/distill/SKILL.md"]


# ---------------------------------------------------------------------------
# The documented routes, in their documented order, against tmp vaults.
# ---------------------------------------------------------------------------

STRANDED_QUERY = "kind:spec status:ready has:label.craft.supersedes"
BODY = "# T\n\nbody\n"


class Vault:
    def __init__(self, tmp_path: Path):
        self.path = tmp_path / "vault"
        self.path.mkdir()
        state = tmp_path / "state"
        state.mkdir()
        config = state / "_xdg_config" / "lore"
        config.mkdir(parents=True)
        (config / "config.json").write_text(
            json.dumps({"vaults": [{"name": "default", "scope": "default", "path": str(self.path)}]}),
            encoding="utf-8",
        )
        self.env = {
            **os.environ,
            "XDG_STATE_HOME": str(state),
            "XDG_CONFIG_HOME": str(state / "_xdg_config"),
            "LORE_EMAIL": "tester@example.com",
        }

    def run(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(_LORE_CLI), *args],
            capture_output=True, text=True, env=self.env, input=stdin,
        )

    def create(self, kind: str, status: str, title: str, *extra: str) -> str:
        r = self.run("record", "create", "--kind", kind, "--title", title, "--keyword", "k",
                     "--status", status, *extra, stdin=BODY)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip().splitlines()[-1]

    def sidecar(self, rid: str) -> dict:
        kind, name = rid.split("/", 1)
        return json.loads((self.path / kind / f"{name}.json").read_text(encoding="utf-8"))

    def status(self, rid: str) -> str:
        return self.sidecar(rid)["status"]

    def stranded(self) -> str:
        r = self.run("search", STRANDED_QUERY)
        assert r.returncode == 0, r.stderr
        return r.stdout


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path)


def test_distill_adr_route_retires_decision_then_adr_and_a_rerun_is_a_noop(vault):
    """distill steps 1, 3, 4: ADR created `active`; the verb retires an absorbed decision and the old
    ADR; re-running changes nothing.
    """
    decision = vault.create("decision", "active", "Absorbed decision")
    old = vault.create("adr", "active", "Old ADR")
    new = vault.create("adr", "active", "New ADR")

    for pred in (decision, old):
        r = vault.run("record", "supersede", pred, "--by", new)
        assert r.returncode == 0, r.stderr
    assert vault.status(decision) == "superseded"
    assert vault.status(old) == "superseded"
    assert set(vault.sidecar(new)["supersedes"]) == {decision, old}

    before = (vault.sidecar(decision), vault.sidecar(old), vault.sidecar(new))
    for pred in (decision, old):
        r = vault.run("record", "supersede", pred, "--by", new)
        assert r.returncode == 0, r.stderr
    assert (vault.sidecar(decision), vault.sidecar(old), vault.sidecar(new)) == before


def test_distill_raw_status_flip_of_the_old_adr_is_refused(vault):
    """distill step 4 before the verb: the plain `--status superseded` flip is refused and leaves the ADR active."""
    old = vault.create("adr", "active", "Old ADR")
    r = vault.run("record", "update", old, "--status", "superseded")
    assert r.returncode != 0
    assert "[superseded-status]" in r.stderr
    assert vault.status(old) == "active"


def test_gauntlet_route_refuses_a_draft_successor_then_lands_after_the_ready_flip_and_clears_the_label(vault):
    """gauntlet ready step: label on the draft successor; the verb waits for `--status ready`, then
    `--unset-label craft/supersedes`.
    """
    old = vault.create("spec", "ready", "Old spec")
    new = vault.create("spec", "draft", "New spec", "--label", f"craft/supersedes={old}")

    early = vault.run("record", "supersede", old, "--by", new)
    assert early.returncode != 0
    assert vault.status(old) == "ready"

    assert vault.run("record", "update", new, "--status", "ready").returncode == 0
    landed = vault.run("record", "supersede", old, "--by", new)
    assert landed.returncode == 0, landed.stderr
    assert vault.run("record", "update", new, "--unset-label", "craft/supersedes").returncode == 0

    assert vault.status(old) == "superseded"
    assert vault.sidecar(new)["supersedes"] == [old]
    assert "craft/supersedes" not in vault.sidecar(new).get("labels", {})


def test_gauntlet_torn_route_is_listed_by_the_stranded_query_until_the_verb_and_unset_complete(vault):
    """gauntlet ready step, interrupted after the flip: the documented query lists the spec; finishing
    the route empties it.
    """
    old = vault.create("spec", "ready", "Old spec")
    new = vault.create("spec", "draft", "New spec", "--label", f"craft/supersedes={old}")
    other = vault.create("spec", "ready", "Unlabelled spec")
    assert vault.run("record", "update", new, "--status", "ready").returncode == 0

    listed = vault.stranded()
    assert new in listed
    assert other not in listed

    assert vault.run("record", "supersede", old, "--by", new).returncode == 0
    assert new in vault.stranded()
    assert vault.run("record", "update", new, "--unset-label", "craft/supersedes").returncode == 0
    assert new not in vault.stranded()


@pytest.mark.parametrize("closing_kind", ["spec", "task"])
def test_routed_task_close_supersedes_the_source_by_the_verb_then_clears_the_route_label(vault, closing_kind):
    """brainstorm (successor spec) and plan (successor parent task) routed-task close: the verb, then a
    separate `--unset-label route`.
    """
    source = vault.create("task", "open", "Routed source", "--label", "route=brainstorm")
    successor = vault.create(closing_kind, "draft" if closing_kind == "spec" else "in-progress", "Successor")

    landed = vault.run("record", "supersede", source, "--by", successor)
    assert landed.returncode == 0, landed.stderr
    assert vault.status(source) == "superseded"
    assert vault.sidecar(source).get("labels", {}).get("route") == "brainstorm"

    assert vault.run("record", "update", source, "--unset-label", "route").returncode == 0
    assert "route" not in vault.sidecar(source).get("labels", {})
    assert vault.sidecar(successor)["supersedes"] == [source]


def test_execute_split_gives_every_child_the_supersedes_edge_to_the_original(vault):
    """execute 'Splitting and appending tasks': the verb once per new child; the original is
    `superseded` and both children carry the edge.
    """
    original = vault.create("task", "ready", "Too large")
    kids = [vault.create("task", "open", f"Part {n}") for n in (1, 2)]

    for kid in kids:
        r = vault.run("record", "supersede", original, "--by", kid)
        assert r.returncode == 0, r.stderr

    assert vault.status(original) == "superseded"
    assert [vault.sidecar(kid)["supersedes"] for kid in kids] == [[original], [original]]


def test_execute_drop_lands_dropped_with_its_note_from_in_progress(vault):
    """execute INVALIDATED 'Drop the task': no successor, so `--status dropped --diff` carries the note
    in the one write.
    """
    rid = vault.create("task", "in-progress", "Invalidated child")
    note = BODY + "\nDropped: the premise was invalidated.\n"
    diff = "".join(difflib.unified_diff(BODY.splitlines(True), note.splitlines(True), "a", "b"))

    r = vault.run("record", "update", rid, "--status", "dropped", "--diff", stdin=diff)

    assert r.returncode == 0, r.stderr
    assert vault.status(rid) == "dropped"
    assert (vault.path / "task" / f"{rid.split('/', 1)[1]}.md").read_text(encoding="utf-8") == note
