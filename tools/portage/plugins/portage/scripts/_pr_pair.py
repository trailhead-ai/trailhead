"""Shared `repo:pr_number[:extra]` pair parsing for portage thin scripts.

wait_for_actionable.py and merge_prs.py both take positional `repo:pr_number`
tokens on argv (merge_prs.py additionally accepts an optional third
`:member_name` field). Both validate the same rule -- at least a repo and a
pr_number field, pr_number all-digits -- and both exit 2 with a clean stderr
message on malformity rather than raising a raw exception. This module is the
single place that shared rule lives; the field count differs per caller via
`max_parts`, and any extra fields beyond repo/pr_number are the caller's to
interpret.
"""

from __future__ import annotations

import re

_PR_NUMBER_RE = re.compile(r"\d+")


class PairFormatError(Exception):
    """Raised when a `repo:pr_number[:extra]` token fails to parse or validate."""


def split_pair(token: str, *, max_parts: int = 2) -> tuple[str, ...]:
    """Split and validate a `repo:pr_number[:extra...]` token.

    Splits on ':' into at most `max_parts` fields, requires at least a repo
    and a pr_number field, and validates pr_number is all-digits. Returns the
    split fields as a tuple (its length is 2 or, when `max_parts` > 2 and the
    input has more fields, up to `max_parts`). Raises PairFormatError on any
    violation; callers print the message to stderr and exit 2.
    """
    parts = token.split(":", max_parts - 1)
    if len(parts) < 2:
        raise PairFormatError(f"bad pair format {token!r} (expected repo:pr_number)")
    pr_number = parts[1]
    if not _PR_NUMBER_RE.fullmatch(pr_number):
        raise PairFormatError(f"pr_number must be all digits, got: {pr_number!r}")
    return tuple(parts)
