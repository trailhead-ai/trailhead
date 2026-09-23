"""Tests for launch/table.py — the shared plain-text table formatter every
`camp list`/`ls` human surface renders through.

Test contract:
- A column's width is the max of its header and every cell beneath it — a
  short cell pads to the widest one in its column, proven by varying which
  row holds the widest value.
- Columns are left-aligned and separated by two spaces.
- Empty `rows` renders no lines at all (matches the existing "empty listing
  -> no stdout" contract).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _import():
    from camp.launch import table

    return table


def test_column_width_is_max_of_header_and_widest_cell_in_that_column():
    table = _import()
    lines = table.render_table(
        ["WORKSPACE", "SESSIONS"],
        [["camp-cli", "0"], ["a-very-long-workspace-slug", "3"]],
    )
    # The WORKSPACE column pads to the widest cell ("a-very-long-workspace-slug"),
    # so the shorter "camp-cli" row's SESSIONS cell still lines up in the same
    # column position as the longer row's.
    assert lines[1].index("0") == lines[2].index("3")


def test_widest_value_can_be_the_header_itself():
    table = _import()
    lines = table.render_table(["LONGHEADER", "X"], [["a", "0"]])
    # "LONGHEADER" (10 chars) is wider than "a" (1 char) plus padding, so the
    # header's own width sets the column, not the (shorter) cell beneath it.
    second_column_start = len("LONGHEADER") + len(table.COLUMN_SEPARATOR)
    assert lines[0][second_column_start:] == "X"
    assert lines[1][second_column_start:] == "0"


def test_columns_are_left_aligned_and_two_space_separated():
    table = _import()
    lines = table.render_table(["A", "B"], [["x", "y"]])
    assert lines[1] == "x  y"


def test_empty_rows_renders_no_lines():
    table = _import()
    assert table.render_table(["A", "B"], []) == []
