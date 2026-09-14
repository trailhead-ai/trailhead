"""Behavioral tests for the ``record-link-rendering`` eval's fixture generator.

``make-fixture-env.sh`` is the deterministic half of the U1 measurement: given
a run directory and a condition (``table`` or ``paragraph``), it lays down one
shared record set and generates a ``task.md`` whose requested rendering shape
depends on the condition. These tests invoke the generator as a subprocess
against a fresh ``tmp_path`` (never the directory it was authored under) and
assert on the files it produced.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

FIXTURES_DIR = (
    Path(__file__).parent.parent
    / "plugins"
    / "outpost"
    / "evals"
    / "record-link-rendering"
    / "fixtures"
)
GENERATOR = FIXTURES_DIR / "make-fixture-env.sh"

# The five records shared by both conditions, keyed the same way
# expected.md's per-record table numbers them: each pairs a file location
# with the one note sentence that record — and only that record — is
# described by. Each carries a different reason it is (or is not) expected
# to link, so a test that only checks "this path appears somewhere in the
# file" cannot tell a correctly-built fixture from a broken one that kept
# every path present but paired one with the wrong record's note (or
# reordered them) — the exact failure a path-presence-only assertion misses.
RECORD_BLOCKS = [
    ("vaults/gearshed/note/rotate-tires.md", "the front and rear tires were swapped"),
    ("other-storage/attic-archive/log/winter-inventory.md", "winter inventory count is done"),
    ("untracked/ghost-vault/memo/unfiled-thought.md", "thought still needs filing"),
    ("vaults/gearshed/note/Rotate_Tires.md", "older duplicate of the first note"),
    ("vaults/gearshed/Field Note/check-in.md", "check-in log entry is in"),
]
RECORD_RELATIVE_PATHS = [rel for rel, _note in RECORD_BLOCKS]


def _run_generator(run_dir: Path, condition: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GENERATOR), str(run_dir), condition],
        capture_output=True,
        text=True,
    )


def test_paragraph_condition_names_every_record_at_the_tmp_path_root(tmp_path):
    run_dir = tmp_path / "run"
    result = _run_generator(run_dir, "paragraph")
    assert result.returncode == 0, result.stderr

    task = (run_dir / "task.md").read_text()

    assert "paragraph" in task
    last_index = -1
    for rel, note in RECORD_BLOCKS:
        needle = f"{run_dir}/{rel}"
        assert needle in task, f"missing {rel} at the tmp_path root"
        index = task.index(needle)
        assert index > last_index, f"{rel} is out of order relative to the prior record"
        # The note immediately following this record's path must be *this*
        # record's own note, not another record's — a swap that kept every
        # path present but re-paired the notes would pass a presence-only
        # check and fail this one.
        following = task[index : index + len(needle) + 120]
        assert note in following, f"{rel} is not paired with its own note"
        last_index = index

    # The generator resolves its own placeholder root — a path baked in at
    # authoring time (this fixture's own committed directory) must not
    # survive into output generated against a different root.
    assert str(FIXTURES_DIR) not in task


def test_table_condition_names_every_record_at_the_tmp_path_root(tmp_path):
    run_dir = tmp_path / "run"
    result = _run_generator(run_dir, "table")
    assert result.returncode == 0, result.stderr

    task = (run_dir / "task.md").read_text()

    assert "table" in task.lower()
    last_index = -1
    for rel, note in RECORD_BLOCKS:
        needle = f"{run_dir}/{rel}"
        assert needle in task, f"missing {rel} at the tmp_path root"
        index = task.index(needle)
        assert index > last_index, f"{rel} is out of order relative to the prior record"
        following = task[index : index + len(needle) + 120]
        assert note in following, f"{rel} is not paired with its own note"
        last_index = index

    assert str(FIXTURES_DIR) not in task


def test_condition_selector_changes_the_requested_rendering_shape(tmp_path):
    paragraph_dir = tmp_path / "paragraph-run"
    table_dir = tmp_path / "table-run"

    paragraph_result = _run_generator(paragraph_dir, "paragraph")
    table_result = _run_generator(table_dir, "table")

    assert paragraph_result.returncode == 0, paragraph_result.stderr
    assert table_result.returncode == 0, table_result.stderr

    paragraph_task = (paragraph_dir / "task.md").read_text()
    table_task = (table_dir / "task.md").read_text()

    assert "Write one short paragraph" in paragraph_task
    assert "Write a short table" in table_task
    assert "Write a short table" not in paragraph_task
    assert "Write one short paragraph" not in table_task


def test_unrecognized_condition_is_refused_not_defaulted(tmp_path):
    run_dir = tmp_path / "run"
    result = _run_generator(run_dir, "bulleted-list")

    assert result.returncode != 0
    assert not (run_dir / "task.md").exists()


def test_shared_record_set_is_identical_across_conditions(tmp_path):
    paragraph_dir = tmp_path / "paragraph-run"
    table_dir = tmp_path / "table-run"

    _run_generator(paragraph_dir, "paragraph")
    _run_generator(table_dir, "table")

    paragraph_listing = (paragraph_dir / "vault-ls.txt").read_text()
    table_listing = (table_dir / "vault-ls.txt").read_text()

    # Both conditions resolve to their own run_dir, so the listings are not
    # byte-identical strings, but they encode the same per-record facts:
    # gearshed and attic-archive are resolvable, ghost-vault is not.
    assert f"{paragraph_dir}/vaults/gearshed" in paragraph_listing
    assert f"{table_dir}/vaults/gearshed" in table_listing
    assert f"{paragraph_dir}/other-storage/attic-archive" in paragraph_listing
    assert f"{table_dir}/other-storage/attic-archive" in table_listing
    assert "ghost-vault" not in paragraph_listing
    assert "ghost-vault" not in table_listing
