"""ASSUMPTION PROBE (ephemeral — delete after use).

Resolves: does lore's sidecar writer round-trip an unrecognised top-level
sidecar key without dropping it, when an ordinary ``lore record update``
reads/mutates/rewrites the sidecar?

Blocks: task/lore-records-a-supersession-as-a-typed-edge (adding a new
top-level ``supersedes`` key) — see
task/connections-render-as-three-named-classes-on-a-record-page.

Two surfaces probed:
  1. The pure ``record.sidecar`` dumps/loads round trip (bytes-level).
  2. The real CLI ``lore record update <id> --status <x>`` path, which reads
     the on-disk sidecar into a dict, applies field mutations, then calls
     ``record_model.validate`` + ``record_store.validate_and_write``.
"""
from __future__ import annotations

import json

from conftest import load_script, make_vault as _make_vault, run_cli as _run

_CREATE_ARGS = [
    "record",
    "create",
    "--kind",
    "spec",
    "--title",
    "My Record",
    "--keyword",
    "foo",
]


def _create(vault, state, body="original body\n"):
    r = _run(_CREATE_ARGS, vault=vault, state_dir=state, stdin_text=body)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _find_sidecar(vault, record_id):
    kind, name = record_id.split("/", 1)
    return json.loads((vault / kind / f"{name}.json").read_text(encoding="utf-8"))


def _sidecar_path(vault, record_id):
    kind, name = record_id.split("/", 1)
    return vault / kind / f"{name}.json"


# ---------------------------------------------------------------------------
# 1 — pure sidecar.dumps/json.loads round trip
# ---------------------------------------------------------------------------


def test_sidecar_dumps_preserves_unknown_top_level_key():
    """``record.sidecar.dumps`` is a passthrough dict serializer: an unknown
    top-level key survives a dumps→loads round trip verbatim."""
    sidecar_mod = load_script("lore.record.sidecar")
    original = {
        "title": "x",
        "kind": "spec",
        "status": "draft",
        "supersedes": ["adr/foo"],
    }
    text = sidecar_mod.dumps(original)
    reloaded = json.loads(text)
    assert reloaded["supersedes"] == ["adr/foo"]


# ---------------------------------------------------------------------------
# 2 — the real CLI update path
# ---------------------------------------------------------------------------


def test_cli_update_with_unknown_top_level_key_present(tmp_path):
    """A record whose on-disk sidecar carries an unrecognised top-level key
    (simulating a newer lore build's ``supersedes`` field) goes through an
    unrelated ``lore record update --status`` call.

    This pins WHICH of the two possible behaviors the current build has:
    either the write is rejected outright (validate() flags "unsupported
    key" and the CLI aborts with the key still on disk, untouched), or the
    write proceeds and the key survives/drops in the rewritten sidecar.
    """
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    # Inject an unknown top-level key directly onto disk, as if a newer lore
    # build had written it.
    path = _sidecar_path(vault, record_id)
    sidecar = json.loads(path.read_text(encoding="utf-8"))
    sidecar["supersedes"] = ["adr/some-other-record"]
    path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    before = _find_sidecar(vault, record_id)
    assert before["supersedes"] == ["adr/some-other-record"]

    # Ordinary, unrelated metadata update through the real CLI path.
    r = _run(
        ["record", "update", record_id, "--status", "ready"],
        vault=vault,
        state_dir=state,
    )

    if r.returncode != 0:
        # The write was rejected. Confirm it's the validation error we expect
        # (not something else), and that the on-disk key is untouched (the
        # rejected write must not have partially mutated the file).
        assert "supersedes" in r.stderr or "unsupported key" in r.stderr, r.stderr
        after = _find_sidecar(vault, record_id)
        assert after["supersedes"] == ["adr/some-other-record"]
        assert after["status"] != "ready"  # the mutation did NOT apply
    else:
        # The write succeeded. Assert on whether the key survived.
        after = _find_sidecar(vault, record_id)
        assert after.get("status") == "ready"
        assert after.get("supersedes") == ["adr/some-other-record"], (
            "unknown top-level key was DROPPED on rewrite: " + json.dumps(after)
        )
