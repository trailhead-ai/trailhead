"""Pins the vault mechanics the transcript convention's prose will document.

A transcript is a plain ``blob`` record carrying ``--label transcript=true``;
a derived record carries a forward-only ``related: blob=<name>`` edge back to
it. Nothing in the lore package changes for this convention — it rests
entirely on mechanisms the CLI and index already provide. This module pins
those mechanisms end-to-end against the real CLI in a fenced vault (see
``conftest.run_cli``) so a future code change cannot silently invalidate a
documented command:

  - a labeled blob create validates clean and writes
  - ``has:label.transcript`` / ``-has:label.transcript`` filter it in/out
  - a record created with ``--related blob=<name>`` is returned by
    ``related-blob:"<name>"`` after ``lore reindex`` — parametrized over
    decision/task/spec
  - the descendant query, and that the transcript itself does *not* show up
    in the *bare* ``related-blob:"<name>"`` facet
  - duplicate-title create forks a ``-2``-suffixed name; a date-scoped
    search then returns both, and a transcript on a different date is left
    out — so the date term is pinned as doing real narrowing
  - ``lore record delete`` removes the labeled blob from disk and from
    ``has:label.transcript`` results
  - the search-before-create command documented in ``record/SKILL.md`` is
    lifted verbatim out of that file and executed, so a form the CLI would
    reject cannot be documented
  - ``lore record update`` replaces a record's whole body, read back through
    ``lore record show``

Tests run the CLI as a subprocess via ``conftest.run_cli`` — never the real
vault. XDG_STATE_HOME / XDG_CONFIG_HOME are pinned to ``tmp_path``-scoped
dirs by the harness.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

from conftest import make_vault as _make_vault, run_cli as _run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRANSCRIPT_TITLE = "2026-08-20 — sync call"


_TRANSCRIPT_KEYWORD = "roadmap"
_TRANSCRIPT_AREA = "lore"

_TRANSCRIPT_BODY = (
    "**Date:** 2026-08-20\n"
    "**Participants:** Ada, Grace\n"
    "\n"
    "raw transcript text\n"
)

_OTHER_DATE_TITLE = "2026-09-14 — budget review"
_OTHER_DATE_BODY = (
    "**Date:** 2026-09-14\n"
    "**Participants:** Ada, Grace\n"
    "\n"
    "raw transcript text\n"
)


_RECORD_SKILL = (
    Path(__file__).parent.parent
    / "plugins"
    / "lore"
    / "skills"
    / "record"
    / "SKILL.md"
)

# The transcript import recipe as `record/SKILL.md` prints it: one fenced bash
# block piping a file into `lore record create`.
_IMPORT_BLOCK_RE = re.compile(
    r"```bash\n(cat [^\n]*\|\s*lore record create.*?)\n```", re.DOTALL
)


def _documented_import_argv(*, title, keyword, area):
    """The import command an agent copies out of the skill, as argv.

    The document is the INPUT: the fenced block is lifted, its shell
    line-continuations joined, split with `shlex`, and its placeholders filled
    in. Every mechanism below is then pinned against the command the skill
    actually prints — so a recipe that drops `--label transcript=true`, loses
    the topic keyword, or unquotes the em-dash title fails here and in every
    test that builds on it, rather than reading fine forever.
    """
    raw = _RECORD_SKILL.read_text(encoding="utf-8")
    blocks = _IMPORT_BLOCK_RE.findall(raw)
    assert len(blocks) == 1, f"expected one documented import recipe, got {blocks}"
    _, _, create = blocks[0].replace("\\\n", " ").partition("| ")
    argv = shlex.split(create)
    assert argv[0] == "lore", argv
    filled = []
    for token in argv[1:]:
        for placeholder, value in (
            ("<YYYY-MM-DD> \u2014 <topic>", title),
            ("<topic>", keyword),
            ("<name>", area),
        ):
            token = token.replace(placeholder, value)
        filled.append(token)
    return filled


def _create_blob(vault, state, *, title=_TRANSCRIPT_TITLE, body=_TRANSCRIPT_BODY):
    """Create a transcript-labeled blob by running the documented recipe.

    Returns its RECORD_ID.
    """
    r = _run(
        _documented_import_argv(
            title=title, keyword=_TRANSCRIPT_KEYWORD, area=_TRANSCRIPT_AREA
        ),
        vault=vault,
        state_dir=state,
        stdin_text=body,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _create_other_date_blob(vault, state):
    """A transcript for a *different* meeting on a different date.

    Present in the count pins so the free-text date term in the documented
    query is doing real narrowing work — without it, every record in the vault
    shares one date and a query that ignored the date would count the same.
    """
    return _create_blob(
        vault, state, title=_OTHER_DATE_TITLE, body=_OTHER_DATE_BODY
    )


def _create_derived(vault, state, blob_name, *, kind, title):
    """Create a record carrying the forward ``related: blob=<name>`` edge back
    to ``blob_name``; return the new record's bare name."""
    r = _run(
        [
            "record",
            "create",
            "--kind",
            kind,
            "--title",
            title,
            "--related",
            f"blob={blob_name}",
        ],
        vault=vault,
        state_dir=state,
        stdin_text="derived record body\n",
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().split("/", 1)[1]


def _search(vault, state, query):
    r = _run(["search", query], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _reindex(vault, state):
    r = _run(["reindex"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------
# Blob create with the transcript label validates clean and writes
# ---------------------------------------------------------------------------


def test_labeled_blob_create_validates_clean_and_writes(tmp_path):
    vault, state = _make_vault(tmp_path)
    record_id = _create_blob(vault, state)
    assert record_id.startswith("blob/")
    kind, name = record_id.split("/", 1)
    assert (vault / kind / f"{name}.md").exists()
    assert (vault / kind / f"{name}.json").exists()


# ---------------------------------------------------------------------------
# has:label.transcript / -has:label.transcript filtering
# ---------------------------------------------------------------------------


def test_has_label_transcript_returns_labeled_blob(tmp_path):
    vault, state = _make_vault(tmp_path)
    record_id = _create_blob(vault, state)
    out = _search(vault, state, "kind:blob has:label.transcript")
    assert record_id.split("/", 1)[1] in out


def test_negated_has_label_transcript_excludes_labeled_blob(tmp_path):
    vault, state = _make_vault(tmp_path)
    record_id = _create_blob(vault, state)
    out = _search(vault, state, "kind:blob -has:label.transcript")
    assert record_id.split("/", 1)[1] not in out


# ---------------------------------------------------------------------------
# --related blob=<name> validates clean and round-trips through related-blob:
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("derived_kind", ["decision", "task", "spec"])
def test_related_blob_validates_and_is_returned_by_facet(tmp_path, derived_kind):
    vault, state = _make_vault(tmp_path)
    blob_id = _create_blob(vault, state)
    blob_name = blob_id.split("/", 1)[1]

    derived_name = _create_derived(
        vault,
        state,
        blob_name,
        kind=derived_kind,
        title=f"Derived from transcript ({derived_kind})",
    )

    _reindex(vault, state)

    out = _search(vault, state, f'related-blob:"{blob_name}"')
    assert derived_name in out


# ---------------------------------------------------------------------------
# Descendant query, plus whether the transcript itself is a member of the
# bare related-blob facet.
#
# A `related-blob` facet is populated by the REVERSE of a forward
# `related: blob=<name>` edge that a derived record declares pointing at the
# blob. The transcript blob declares no such edge (it has no `related.blob`
# entry pointing at itself), so it is NOT a member of the bare
# `related-blob:"<name>"` facet — only records that declare
# `--related blob=<name>` appear.
# ---------------------------------------------------------------------------


def test_descendant_query_filters_to_derived_record_only(tmp_path):
    vault, state = _make_vault(tmp_path)
    blob_id = _create_blob(vault, state)
    blob_name = blob_id.split("/", 1)[1]

    derived_name = _create_derived(
        vault, state, blob_name, kind="decision", title="Derived decision"
    )

    _reindex(vault, state)

    out = _search(vault, state, f'related-blob:"{blob_name}" -has:label.transcript')
    assert derived_name in out
    assert blob_name not in out


def test_bare_related_blob_facet_does_not_include_the_transcript_itself(tmp_path):
    """Pins that the transcript blob is not a member of its own bare facet.

    The bare ``related-blob:"<name>"`` facet returns only the derived record,
    which is what licenses the prose never to claim otherwise.
    """
    vault, state = _make_vault(tmp_path)
    blob_id = _create_blob(vault, state)
    blob_name = blob_id.split("/", 1)[1]

    derived_name = _create_derived(
        vault,
        state,
        blob_name,
        kind="decision",
        title="Derived decision for bare-facet check",
    )

    _reindex(vault, state)

    out = _search(vault, state, f'related-blob:"{blob_name}"')
    assert derived_name in out
    assert blob_name not in out


# ---------------------------------------------------------------------------
# Duplicate-title create forks a -2-suffixed name; date-scoped search
# returns both.
# ---------------------------------------------------------------------------


def test_duplicate_title_create_forks_distinct_name(tmp_path):
    vault, state = _make_vault(tmp_path)
    first_id = _create_blob(vault, state)
    second_id = _create_blob(vault, state)

    first_name = first_id.split("/", 1)[1]
    second_name = second_id.split("/", 1)[1]
    assert first_name != second_name
    assert second_name == f"{first_name}-2"


def test_duplicate_title_date_scoped_search_returns_two_records(tmp_path):
    vault, state = _make_vault(tmp_path)
    _create_blob(vault, state)
    _create_blob(vault, state)
    _create_other_date_blob(vault, state)

    r = _run(
        ["search", "kind:blob has:label.transcript 2026-08-20", "--json"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["total"] == 2


# ---------------------------------------------------------------------------
# lore record delete removes the labeled blob from disk and from
# has:label.transcript results.
# ---------------------------------------------------------------------------


def test_delete_removes_labeled_blob_from_disk(tmp_path):
    vault, state = _make_vault(tmp_path)
    record_id = _create_blob(vault, state)
    kind, name = record_id.split("/", 1)
    body_path = vault / kind / f"{name}.md"
    sidecar_path = vault / kind / f"{name}.json"
    assert body_path.exists()
    assert sidecar_path.exists()

    r = _run(["record", "delete", record_id], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert not body_path.exists()
    assert not sidecar_path.exists()


def test_delete_removes_labeled_blob_from_search_results(tmp_path):
    vault, state = _make_vault(tmp_path)
    record_id = _create_blob(vault, state)
    name = record_id.split("/", 1)[1]

    out_before = _search(vault, state, "kind:blob has:label.transcript")
    assert name in out_before

    r = _run(["record", "delete", record_id], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr

    out_after = _search(vault, state, "kind:blob has:label.transcript")
    assert name not in out_after


# ---------------------------------------------------------------------------
# lore record update is a destructive whole-body overwrite, read back through
# lore record show.
# ---------------------------------------------------------------------------


def test_update_replaces_the_whole_body_and_show_reads_it_back(tmp_path):
    """Binds the semantics the prose warns about: piping a delta to
    ``lore record update`` does not append — the prior text is gone, which is
    exactly why the documented flow reads the record back first."""
    vault, state = _make_vault(tmp_path)
    record_id = _create_blob(vault, state)

    before = _run(["record", "show", record_id], vault=vault, state_dir=state)
    assert before.returncode == 0, before.stderr
    assert "**Participants:** Ada, Grace" in before.stdout
    assert "raw transcript text" in before.stdout

    delta = "**Date:** 2026-08-20\n**Participants:** Ada, Grace\n\ncorrected tail\n"
    upd = _run(
        ["record", "update", record_id],
        vault=vault,
        state_dir=state,
        stdin_text=delta,
    )
    assert upd.returncode == 0, upd.stderr

    after = _run(["record", "show", record_id], vault=vault, state_dir=state)
    assert after.returncode == 0, after.stderr
    assert "corrected tail" in after.stdout
    assert "raw transcript text" not in after.stdout


# ---------------------------------------------------------------------------
# The documented search-before-create command is executable as written.
# ---------------------------------------------------------------------------

_DOCUMENTED_SEARCH_RE = re.compile(r"^lore search '([^']*)'$", re.MULTILINE)


def _documented_search_query() -> str:
    """The single query string of the search-before-create command, lifted
    verbatim from ``record/SKILL.md``."""
    raw = _RECORD_SKILL.read_text(encoding="utf-8")
    matches = [
        m.group(1)
        for m in _DOCUMENTED_SEARCH_RE.finditer(raw)
        if "has:label.transcript" in m.group(1)
    ]
    assert len(matches) == 1, matches
    return matches[0]


def test_documented_search_before_create_command_is_one_positional_query():
    """``lore search`` takes exactly one query positional. A second positional
    — the date passed outside the quotes — exits 2. Pinning the documented
    string keeps the date folded into the one query."""
    assert _documented_search_query() == "kind:blob has:label.transcript <YYYY-MM-DD>"


def test_documented_search_before_create_command_runs_clean(tmp_path):
    vault, state = _make_vault(tmp_path)
    record_id = _create_blob(vault, state)
    _create_other_date_blob(vault, state)

    query = _documented_search_query().replace("<YYYY-MM-DD>", "2026-08-20")
    r = _run(["search", query, "--json"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["total"] == 1
    assert payload["hits"][0]["id"].endswith(record_id)


def test_a_second_search_positional_is_rejected_by_the_cli(tmp_path):
    """The failure mode the pin above exists to prevent."""
    vault, state = _make_vault(tmp_path)
    _create_blob(vault, state)
    r = _run(
        ["search", "kind:blob has:label.transcript", "2026-08-20"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 2
    assert "unrecognized arguments" in r.stderr
