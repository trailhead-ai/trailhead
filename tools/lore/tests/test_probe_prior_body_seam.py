"""EPHEMERAL assumption probe -- delete after use.

Resolves: does ``evaluate_graph_guards`` receive the record's PRIOR (on-disk)
body at the ``record update`` seam, and does a supersession back-edge / status
flip leave the on-disk body byte-for-byte unchanged? Also: does the rename
seam (``store.validate_and_write`` via ``record/rename.py``) reach
``evaluate_graph_guards`` at all?

See task/structural-active-adr-immutability-at-the-record-write-path.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from conftest import make_vault as _make_vault, run_cli as _run


def _find_body(vault: Path, record_id: str) -> str:
    kind, name = record_id.split("/", 1)
    return (vault / kind / f"{name}.md").read_text(encoding="utf-8")


def test_evaluate_graph_guards_signature_has_no_prior_body_param():
    """Observation 1: the guard dispatcher's signature carries no prior-body param.

    ``cli/record.py``'s ``read_apply_and_guard()`` closure reads
    ``existing_body`` off disk (record.py:1250-1254) but the call to
    ``evaluate_graph_guards`` at record.py:1319-1327 passes only ``body=new_body``
    -- there is no ``prior_body`` / ``existing_body`` keyword in the signature.
    If this assertion ever fails, the signature has grown a prior-body
    parameter and the planning claim needs re-checking.
    """
    from lore.record import guards as guards_mod

    sig = inspect.signature(guards_mod.evaluate_graph_guards)
    params = set(sig.parameters)
    assert "body" in params
    assert not ({"prior_body", "existing_body", "old_body"} & params), (
        f"evaluate_graph_guards unexpectedly has a prior-body param already: {params}"
    )


def test_related_only_update_leaves_body_byte_for_byte_unchanged(tmp_path):
    """Sidecar-only ``--related adr=<successor>`` must not touch the body bytes."""
    vault, state = _make_vault(tmp_path)

    body = "# Decision\n\nThis is the original ADR body.\n"
    r = _run(
        ["record", "create", "--kind", "adr", "--title", "Original Decision",
         "--keyword", "foo", "--status", "active"],
        vault=vault, state_dir=state, stdin_text=body,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()

    r2 = _run(
        ["record", "create", "--kind", "adr", "--title", "Successor Decision",
         "--keyword", "foo", "--status", "draft"],
        vault=vault, state_dir=state, stdin_text="# Successor\n",
    )
    assert r2.returncode == 0, r2.stderr
    successor_id = r2.stdout.strip()
    successor_name = successor_id.split("/", 1)[1]

    before = _find_body(vault, record_id)
    assert before == body

    r3 = _run(
        ["record", "update", record_id, "--related", f"adr={successor_name}"],
        vault=vault, state_dir=state,
    )
    assert r3.returncode == 0, r3.stderr

    after = _find_body(vault, record_id)
    assert after == before, (
        "sidecar-only --related update mutated the body bytes: "
        f"before={before!r} after={after!r}"
    )


def test_status_superseded_flip_leaves_body_byte_for_byte_unchanged(tmp_path):
    """A ``--status superseded`` flip must not touch the body bytes."""
    vault, state = _make_vault(tmp_path)

    body = "# Decision\n\nThis is the original ADR body.\n"
    r = _run(
        ["record", "create", "--kind", "adr", "--title", "Original Decision Two",
         "--keyword", "foo", "--status", "active"],
        vault=vault, state_dir=state, stdin_text=body,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()

    before = _find_body(vault, record_id)
    assert before == body

    r2 = _run(
        ["record", "update", record_id, "--status", "superseded"],
        vault=vault, state_dir=state,
    )
    assert r2.returncode == 0, r2.stderr

    after = _find_body(vault, record_id)
    assert after == before, (
        "status=superseded flip mutated the body bytes: "
        f"before={before!r} after={after!r}"
    )


def test_status_superseded_flip_with_backedge_in_same_call_leaves_body_unchanged(tmp_path):
    """A combined --status superseded + --related adr= call must not touch the body."""
    vault, state = _make_vault(tmp_path)

    body = "# Decision\n\nThis is the original ADR body.\n"
    r = _run(
        ["record", "create", "--kind", "adr", "--title", "Original Decision Three",
         "--keyword", "foo", "--status", "active"],
        vault=vault, state_dir=state, stdin_text=body,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()

    r2 = _run(
        ["record", "create", "--kind", "adr", "--title", "Successor Decision Three",
         "--keyword", "foo", "--status", "draft"],
        vault=vault, state_dir=state, stdin_text="# Successor\n",
    )
    assert r2.returncode == 0, r2.stderr
    successor_id = r2.stdout.strip()
    successor_name = successor_id.split("/", 1)[1]

    before = _find_body(vault, record_id)

    r3 = _run(
        ["record", "update", record_id, "--status", "superseded",
         "--related", f"adr={successor_name}"],
        vault=vault, state_dir=state,
    )
    assert r3.returncode == 0, r3.stderr

    after = _find_body(vault, record_id)
    assert after == before, (
        "combined status+related update mutated the body bytes: "
        f"before={before!r} after={after!r}"
    )


def test_rename_seam_never_calls_evaluate_graph_guards():
    """Observation 3: record/rename.py never imports or calls guards_mod / evaluate_graph_guards."""
    import lore.record.rename as rename_mod

    src = inspect.getsource(rename_mod)
    assert "evaluate_graph_guards" not in src
    assert "guards_mod" not in src
    assert "record.guards" not in src
    assert "from lore.record import guards" not in src
