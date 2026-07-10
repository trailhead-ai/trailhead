"""Shared `repo:pr_number[:extra]` pair parsing for portage thin scripts.

wait_for_actionable.py and merge_prs.py both take positional `repo:pr_number`
tokens on argv (merge_prs.py additionally accepts an optional third
`:member_name` field). Both validate the same rule -- at least a repo and a
pr_number field, pr_number all-digits -- and both exit 2 with a clean stderr
message on malformity rather than raising a raw exception. This module is the
single place that shared rule lives; the field count differs per caller via
`max_parts`, and any extra fields beyond repo/pr_number are the caller's to
interpret. The digit check itself delegates to
`trailhead.vcs.github.validate_pr_number` (imported lazily inside `split_pair`,
since callers import this module before `trailhead` is guaranteed importable)
so there is one place that defines "what a valid pr_number looks like".
"""

from __future__ import annotations


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
    from trailhead.vcs.github import InvalidInputError, validate_pr_number

    parts = token.split(":", max_parts - 1)
    if len(parts) < 2:
        raise PairFormatError(f"bad pair format {token!r} (expected repo:pr_number)")
    pr_number = parts[1]
    try:
        validate_pr_number(pr_number)
    except InvalidInputError as exc:
        raise PairFormatError(str(exc)) from exc
    return tuple(parts)
