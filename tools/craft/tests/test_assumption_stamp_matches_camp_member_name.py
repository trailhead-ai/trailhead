"""Assumption probe (ephemeral — delete after task 1 lands real coverage).

Unknown under test: does the member name a real, currently-stamped spec's
`## Maturity` section carries match, byte for byte, the camp member name a
planning session running inside this workspace would have in hand (from
manifest.json) to pass as --target-repo?

Evidence sources:
- Real stamped spec: vault record
  spec/camp-answers-for-every-host-group-and-account, body line
  "- trailhead: production"
  (/home/tomduffield/.local/state/lore/vaults/trailhead/spec/camp-answers-for-every-host-group-and-account.md:53)
- Real camp manifest for this workspace: manifest.json members[0].name == "trailhead"
  (/home/tomduffield/.local/state/camp/trailhead/worktrees/project-maturity/manifest.json:8)

This test runs maturity_stamp.py's CLI (the same parse_entries the plan says
migration_bar.py will import) against the real spec's stamp text and asserts
the resolved member-name key equals the real manifest member name, exactly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
STAMP = REPO_ROOT / "plugins" / "craft" / "scripts" / "maturity_stamp.py"

REAL_STAMPED_SPEC_BODY = """\
# camp answers for every host, group, and account

## Maturity

<!-- keep this line in the written spec: allowed levels are prototype | early | production. -->
- trailhead: production

## Acceptance Criteria
"""

MANIFEST_PATH = Path(
    "/home/tomduffield/.local/state/camp/trailhead/worktrees/project-maturity/manifest.json"
)


def _run_stamp(stdin_text: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(STAMP)],
        input=stdin_text,
        capture_output=True,
        text=True,
    )


def test_real_stamp_member_name_matches_real_camp_member_name():
    # Execute the real parser CLI against the real, currently-committed stamp text.
    result = _run_stamp(REAL_STAMPED_SPEC_BODY)
    assert result.returncode == 0, result.stderr
    # stdout: "maturity: <member>=<level>[, ...]" sorted by member name
    assert result.stdout.startswith("maturity: ")
    entries_str = result.stdout[len("maturity: "):].strip()
    entries = dict(
        pair.split("=", 1) for pair in entries_str.split(", ") if pair
    )
    stamp_member_names = set(entries.keys())

    # Execute the real manifest as camp tooling actually writes it, and read
    # the real member names a planning session in this workspace would have.
    manifest = json.loads(MANIFEST_PATH.read_text())
    camp_member_names = {m["name"] for m in manifest["members"]}

    assert stamp_member_names.issubset(camp_member_names), (
        f"stamp member names {stamp_member_names} not found in camp manifest "
        f"member names {camp_member_names}; --target-repo would need a "
        f"defined absent-target behaviour"
    )
    assert "trailhead" in stamp_member_names
    assert "trailhead" in camp_member_names
