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
  - the descendant query, and whether the transcript itself shows up in
    the *bare* ``related-blob:"<name>"`` facet — resolved here by writing
    the expectation down first, running it once, and reporting the
    observed result rather than iterating the assertion.
  - duplicate-title create forks a ``-2``-suffixed name; a date-scoped
    search then returns both
  - ``lore record delete`` removes the labeled blob from disk and from
    ``has:label.transcript`` results

Tests run the CLI as a subprocess via ``conftest.run_cli`` — never the real
vault. XDG_STATE_HOME / XDG_CONFIG_HOME are pinned to ``tmp_path``-scoped
dirs by the harness.
"""

from __future__ import annotations

import pytest

from conftest import make_vault as _make_vault, run_cli as _run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRANSCRIPT_TITLE = "2026-08-20 — sync call"


def _create_blob(vault, state, *, title=_TRANSCRIPT_TITLE):
    """Create a transcript-labeled blob via the CLI; return its RECORD_ID."""
    r = _run(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            title,
            "--label",
            "transcript=true",
        ],
        vault=vault,
        state_dir=state,
        stdin_text="raw transcript text\n",
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


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

    r = _run(
        [
            "record",
            "create",
            "--kind",
            derived_kind,
            "--title",
            f"Derived from transcript ({derived_kind})",
            "--related",
            f"blob={blob_name}",
        ],
        vault=vault,
        state_dir=state,
        stdin_text="derived record body\n",
    )
    assert r.returncode == 0, r.stderr
    derived_id = r.stdout.strip()
    derived_name = derived_id.split("/", 1)[1]

    _reindex(vault, state)

    out = _search(vault, state, f'related-blob:"{blob_name}"')
    assert derived_name in out


# ---------------------------------------------------------------------------
# Descendant query, plus whether the transcript itself is a member of the
# bare related-blob facet.
#
# Stated expectation (written BEFORE running): a "related-blob" facet is
# populated by the REVERSE of a forward `related: blob=<name>` edge a
# derived record declares pointing at the blob. The transcript blob itself
# declares no such edge (it has no `related.blob` entry pointing at
# itself), so it should NOT be a member of the bare `related-blob:"<name>"`
# facet — only records that declare `--related blob=<name>` should appear.
# ---------------------------------------------------------------------------


def test_descendant_query_filters_to_derived_record_only(tmp_path):
    vault, state = _make_vault(tmp_path)
    blob_id = _create_blob(vault, state)
    blob_name = blob_id.split("/", 1)[1]

    r = _run(
        [
            "record",
            "create",
            "--kind",
            "decision",
            "--title",
            "Derived decision",
            "--related",
            f"blob={blob_name}",
        ],
        vault=vault,
        state_dir=state,
        stdin_text="derived decision body\n",
    )
    assert r.returncode == 0, r.stderr
    derived_name = r.stdout.strip().split("/", 1)[1]

    _reindex(vault, state)

    out = _search(vault, state, f'related-blob:"{blob_name}" -has:label.transcript')
    assert derived_name in out
    assert blob_name not in out


def test_bare_related_blob_facet_does_not_include_the_transcript_itself(tmp_path):
    """Pins whether the transcript blob is a member of its own bare facet.

    OBSERVED (this test run): the bare ``related-blob:"<name>"`` facet
    returns only the derived record — the transcript blob is NOT a member
    of its own bare facet. This confirms the stated expectation above.
    """
    vault, state = _make_vault(tmp_path)
    blob_id = _create_blob(vault, state)
    blob_name = blob_id.split("/", 1)[1]

    r = _run(
        [
            "record",
            "create",
            "--kind",
            "decision",
            "--title",
            "Derived decision for bare-facet check",
            "--related",
            f"blob={blob_name}",
        ],
        vault=vault,
        state_dir=state,
        stdin_text="derived decision body\n",
    )
    assert r.returncode == 0, r.stderr
    derived_name = r.stdout.strip().split("/", 1)[1]

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

    out = _search(vault, state, "kind:blob has:label.transcript 2026-08-20")
    lines = [line for line in out.splitlines() if line.strip()]
    assert len([line for line in lines if "2026-08-20" in line]) == 2


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
