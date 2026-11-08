#!/usr/bin/env python3
"""Follow-ups gate — certifies a proposed write to a spec's `## Follow-ups`
section against the body it was composed from.

Usage:
    followups_gate.py --prior <path> < proposed-body

The proposed spec body arrives on stdin. `--prior` names the JSON document
`lore record show spec/<n> --json` printed when the session read the spec: the
gate takes the prior body from its `body` field and the revision from its
`revision` field (a 64-character lowercase hex sha256 digest; anything else is
`malformed-prior`). On success the gate exits 0 and prints exactly one line,
`base: <revision>` — the revision the writer passes to
`lore record update --base`, so the body this gate checked and the revision
lore compares come from one read. The revision is never computed by hashing
text: `show` folds CRLF to LF, so a hash of the body text misses lore's
revision for a CRLF body.

Requires a lore that supports `lore record update --base`. An older lore fails
the write closed with an argparse error on the unknown flag; the remedy is
`trailhead update`.

The section's entry grammar lives in `followups.py`, the single parser. This
gate refuses a proposed body that:
    - loses an entry the prior had, including by replacing the whole section
    - repeats an entry id
    - drops an entry without a reason
    - changes an entry already `done by` or `dropped`
    - changes the `found:` or `from:` of an existing entry
    - carries a malformed entry, a task target that is not `task/<id>`, or a
      `where:` path that is not repository-relative
    - differs from the prior anywhere outside `## Follow-ups`
    - creates the section anywhere but the end of the body (a section the prior
      already had may stay where it is)

Entry ids are `F` followed by a positive integer without leading zeros.

The outside-the-section comparison excises the section from both bodies (an
absent section excises nothing), joins the lines with LF and drops trailing
blank lines, never trailing whitespace within an authored line.

Every `reason:` line is vault-sourced text, so it passes once, at the output
boundary, through `criterion_gate._neutralize_control_chars` and then
`_scrub_credential_shaped`.

Exit codes:
    0  certified — `base: <revision>` on stdout.
    1  violation — `reason:` names the entry id or section and the fix. Stable
       `reason-code:` tokens: removed-entry, duplicate-entry-id,
       drop-without-reason, settled-entry-changed, entry-origin-changed,
       malformed-entry, malformed-task-id, path-outside-repository,
       duplicate-followups-heading, change-outside-followups, section-not-at-end.
    2  could not certify — empty-stdin, non-utf8-stdin, stdin-too-large,
       unterminated-masked-region (the proposed body ends inside an open fence
       or comment), unreadable-prior, malformed-prior (not a JSON object
       carrying string `body`, `revision` and `layer`), shared-layer-prior
       (`layer` is not `personal`: in a shared vault `show` XML-escapes the
       body, so a body composed from it would write escaped text),
       malformed-prior-section (the prior's own section does not parse).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from covers_gate import _COMMONMARK_LINE_RE, DuplicateHeadingError  # noqa: E402
from gate_plumbing import (  # noqa: E402
    Refusal as _Refusal,
    emit_refusal,
    load_prior,
    read_stdin,
)
from followups import (  # noqa: E402
    FollowupsError,
    excise_section,
    has_section,
    parse_followups,
    section_is_last,
)
from observation_gate import _ends_inside_masked_region  # noqa: E402

def _certify(prior_body: str, proposed: str) -> None:
    if _ends_inside_masked_region(_COMMONMARK_LINE_RE.split(proposed)):
        raise _Refusal(
            "the proposed body ends inside an open fenced code block or HTML comment — "
            "close it so the '## Follow-ups' section is fully visible",
            "unterminated-masked-region",
            2,
        )
    try:
        prior_entries = parse_followups(prior_body)
    except (FollowupsError, DuplicateHeadingError) as e:
        raise _Refusal(
            f"the prior body's own '## Follow-ups' section does not parse: {e} — "
            "repair it with a direct edit before writing again",
            "malformed-prior-section",
            2,
        )
    try:
        entries = parse_followups(proposed)
    except FollowupsError as e:
        raise _Refusal(str(e), e.reason_code, 1)
    except DuplicateHeadingError as e:
        raise _Refusal(f"{e} — keep a single '## Follow-ups' heading", e.reason_code, 1)

    seen: set[str] = set()
    for e in entries:
        if e.id in seen:
            raise _Refusal(
                f"entry {e.id} appears twice — ids are never reused; give the new entry "
                "the next unused id",
                "duplicate-entry-id",
                1,
            )
        seen.add(e.id)

    by_id = {e.id: e for e in entries}
    for old in prior_entries:
        new = by_id.get(old.id)
        if new is None:
            raise _Refusal(
                f"entry {old.id} is missing from the proposed body — entries are never "
                "removed; restore it (set 'status: dropped: <reason>' to retire it)",
                "removed-entry",
                1,
            )
        settled = old.status_kind in ("done", "dropped")
        if settled and (old.status_kind, old.status_detail) != (new.status_kind, new.status_detail):
            raise _Refusal(
                f"entry {old.id} is already '{old.status_kind}' and its status cannot "
                "change — restore the prior status line",
                "settled-entry-changed",
                1,
            )
        if (old.found, old.from_) != (new.found, new.from_):
            raise _Refusal(
                f"entry {old.id} has a changed 'found:' or 'from:' — an entry's origin is "
                "fixed; restore the prior lines",
                "entry-origin-changed",
                1,
            )

    if excise_section(prior_body) != excise_section(proposed):
        raise _Refusal(
            "the proposed body differs from the prior outside '## Follow-ups' — change only "
            "that section (a first write appends it at the end of the body)",
            "change-outside-followups",
            1,
        )
    if not has_section(prior_body) and not section_is_last(proposed):
        raise _Refusal(
            "a new '## Follow-ups' section goes at the end of the body — move it below "
            "everything else",
            "section-not-at-end",
            1,
        )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Certify a proposed '## Follow-ups' write against the body it was composed "
        "from. Exit 0 certified, 1 violation, 2 could not certify."
    )
    ap.add_argument(
        "--prior",
        required=True,
        help="path to the JSON 'lore record show spec/<n> --json' printed when the spec was read",
    )
    args = ap.parse_args(argv)
    try:
        proposed = read_stdin()
        prior_body, revision = load_prior(args.prior)
        _certify(prior_body, proposed)
    except _Refusal as r:
        return emit_refusal("followups-gate", r)
    print(f"base: {revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
