"""Plain aligned-column table rendering for camp's human `list`/`ls` surfaces.

No I/O, no ANSI/color: the operator's terminal may not support it, and camp
already plumbs raw peer-supplied strings through this seam (a relayed row's
cells), where color codes would be one more thing to sanitize for no
readability gain. Callers escape each cell (``printable_path``) before
handing it to :func:`render_table` — this module trusts its input completely
and only computes column widths and joins.

The single place column alignment is computed for `camp list`/`ls`, so the
local, relayed, and merged human surfaces cannot drift on how a table looks.
"""

from __future__ import annotations

from typing import Sequence

#: The literal separator between two adjacent columns.
COLUMN_SEPARATOR = "  "


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """Render *headers* plus *rows* as a left-aligned, space-padded table:
    one header line, then one line per row. A column's width is the max of
    its header and every cell beneath it in that column. The last column of
    each line is never padded, so no line carries trailing whitespace.

    Returns ``[]`` for empty *rows* — there is nothing worth a bare header
    for, matching every `camp list`/`ls` surface's existing "empty listing ->
    no stdout" contract.
    """
    if not rows:
        return []

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _line(cells: Sequence[str]) -> str:
        padded = [
            cell if i == len(cells) - 1 else cell.ljust(widths[i])
            for i, cell in enumerate(cells)
        ]
        return COLUMN_SEPARATOR.join(padded)

    return [_line(headers)] + [_line(row) for row in rows]
