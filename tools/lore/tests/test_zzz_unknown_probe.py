"""EPHEMERAL assumption probe — delete after the plan's unknown is dispositioned.

Proves/disproves: "a non-record conflicted path outside the vault's top-level
`sites/` tree is reachable in practice" (task/a-non-record-file-takes-the-published-side).

`_Fixture._init_vault` in test_resolve_core.py already commits a root
`README.md` alongside `.gitignore` at vault init. That file is: (1) not a
record (no `<kind>/<name>.{md,json}` shape), (2) not under `sites/`, and
(3) tracked + committed. If two devices edit it differently, git reports it
as an unmerged path and `_resolve_step` must classify it. This test drives
that scenario through the real fixture harness and CLI.
"""
from __future__ import annotations

import json


from test_resolve_core import _Fixture, _commit


def test_root_non_record_non_sites_file_is_reachable_as_a_conflict(tmp_path):
    fx = _Fixture(tmp_path)
    fx.create("task", "A Task")
    fx.publish()
    fx.clone_device_b()

    (fx.other / "README.md").write_text("remote readme\n")
    fx.push_device_b()

    (fx.vault / "README.md").write_text("local readme\n")
    _commit(fx.vault, "device A edit")

    r = fx.cli(["resolve", "default", "--json"])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)

    # THE ASSUMPTION: a root-level, non-record, non-sites tracked file reaches
    # git's unmerged-path list and is classified into the `files` section —
    # i.e. it is reachable in practice, not merely in theory.
    assert report["conflicts"] == [], "README.md must not be mistaken for a record"
    assert [f["path"] for f in report["files"]] == ["README.md"]
    assert "take-file" in " ".join(f["reason"] for f in report["files"])


def test_free_write_zone_fence_currently_refuses_that_reachable_path(tmp_path):
    """Companion check: `_assert_free_write_zone` — as it stands TODAY, fenced
    to `sites/` only — refuses this reachable non-sites path. This pins that
    the current fence would hold README.md for a person rather than silently
    auto-taking it; it does not modify the fence, only observes it."""
    fx = _Fixture(tmp_path)
    fx.create("task", "A Task")
    fx.publish()
    fx.clone_device_b()

    (fx.other / "README.md").write_text("remote readme\n")
    fx.push_device_b()

    (fx.vault / "README.md").write_text("local readme\n")
    _commit(fx.vault, "device A edit")

    r = fx.cli(["resolve", "default", "--json"])
    assert r.returncode == 0, r.stderr

    # take-file itself must refuse this path under today's sites/-only fence.
    r2 = fx.cli(["resolve", "take-file", "README.md", "--remote"])
    assert r2.returncode != 0
    assert "sites" in r2.stderr.lower() or "sites" in r2.stdout.lower()
