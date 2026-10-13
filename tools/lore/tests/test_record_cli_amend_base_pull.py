"""``record amend --base`` is compared AFTER the implicit pull.

A revision read before another device pushed a body edit is stale against what
the pull brings in. Compared before the pull, the check would pass on the old
local body and the amend would then run over the pulled one.
"""

from __future__ import annotations

import json
from hashlib import sha256

from test_pull_only import (
    _fetch_stamp, _git, _one_vault, _push_from_device_b, _run_cli,
)

BODY = "# Decision\n\nline one\nline two\nline three\n"
THEIRS = "# Decision\n\nline one\nline two by device B\nline three\n"
MINE = "# Decision\n\nline one\nline TWO\nline three\n"


def _cli(env, *args, stdin_text=None):
    config_home, state_dir = env
    return _run_cli(list(args), config_home=config_home, state_dir=state_dir,
                    stdin_text=stdin_text)


def _revision(env, rid):
    r = _cli(env, "record", "show", rid, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["revision"]


def _stale_scenario(tmp_path):
    """Device A holds a record it read; device B then pushes a body edit.

    Returns ``(env, vault, rid, stale_revision, device_b_state)`` with the fetch
    stamp cleared so the next write path pulls.
    """
    config_home, state_dir, vault, _remote, other = _one_vault(tmp_path)
    env = (config_home, state_dir)
    created = _cli(env, "record", "create", "--kind", "adr", "--title", "A Decision",
                   "--keyword", "k", "--status", "draft", stdin_text=BODY)
    assert created.returncode == 0, created.stderr
    rid = created.stdout.strip()
    kind, name = rid.split("/", 1)
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "the created record")
    _git(vault, "push", "origin")
    _git(other, "pull")
    stale = _revision(env, rid)

    _push_from_device_b(other, f"{kind}/{name}.md", THEIRS)
    _fetch_stamp(state_dir, vault).unlink(missing_ok=True)
    return env, vault, rid, stale, (vault / kind / f"{name}.md",
                                    vault / kind / f"{name}.json")


def test_stale_base_is_refused_against_the_body_the_pull_brought_in(tmp_path):
    env, vault, rid, stale, (md, side) = _stale_scenario(tmp_path)
    sidecar_before = side.read_text(encoding="utf-8")

    r = _cli(env, "record", "amend", rid, "--reason", "fix wording",
             "--initiated-by", "operator", "--base", stale, stdin_text=MINE)

    assert r.returncode != 0, "stale amend must be refused"
    assert "Pulled 1 commit(s)" in r.stderr, r.stderr
    assert md.read_text(encoding="utf-8") == THEIRS
    assert stale in r.stderr
    assert sha256(THEIRS.encode("utf-8")).hexdigest() in r.stderr
    assert json.loads(side.read_text(encoding="utf-8")).get("change-history") in (None, [])
    assert side.read_text(encoding="utf-8") == sidecar_before


def test_base_read_after_the_pull_amends_the_pulled_body(tmp_path):
    env, vault, rid, stale, (md, side) = _stale_scenario(tmp_path)
    fresh = sha256(THEIRS.encode("utf-8")).hexdigest()
    assert fresh != stale

    r = _cli(env, "record", "amend", rid, "--reason", "fix wording",
             "--initiated-by", "operator", "--base", fresh, stdin_text=MINE)

    assert r.returncode == 0, r.stderr
    assert md.read_text(encoding="utf-8") == MINE
    assert len(json.loads(side.read_text(encoding="utf-8"))["change-history"]) == 1
