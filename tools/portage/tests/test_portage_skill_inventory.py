"""Portage's selectable skill inventory is exactly the unified `pull_request`.

The four legacy PR-lifecycle skills (open/update/monitor/merge) collapsed into
one verb-dispatched skill. What the harness offers an operator is whatever
`trailhead.capabilities` discovers on disk, so that discovery is the subject:
run the loader and assert the inventory it produced.

Runtime verb-routing itself is model-interpreted prose, not something a static
test can execute — that gap is covered by the assumption-prover pass on the
harness's argument passthrough, not by this file.
"""

from __future__ import annotations

from pathlib import Path

from trailhead.capabilities import load_manifest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PORTAGE_MANIFEST = _REPO_ROOT / "tools" / "portage" / "capabilities.toml"


def test_portage_skill_inventory_is_pull_request_only():
    manifest = load_manifest(_PORTAGE_MANIFEST)
    assert set(manifest.skills) == {"pull_request"}, (
        f"expected portage's selectable skills to be exactly {{'pull_request'}}, "
        f"got {sorted(manifest.skills)}"
    )
    assert manifest.skills["pull_request"] == "skills/pull_request"
