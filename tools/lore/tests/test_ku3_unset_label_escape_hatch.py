"""EPHEMERAL assumption probe (not a permanent regression test — delete after use).

Proves: with the reserved-label guard live in ``record/model.py``, an update whose
ONLY change is ``--unset-label <reserved-key>`` on a record that ALREADY carries
that reserved key in its sidecar exits 0 and removes the key. Every refusal
message for a reserved labels key names ``--unset-label <key>`` as the way to
unblock a write, so this is the documented escape hatch and must actually work.

Since the guard blocks CREATING a record with a reserved label key through the
CLI, the pre-existing-key state is manufactured by hand-editing the sidecar JSON
on disk after a normal CLI create — simulating data written before the guard
existed. This mirrors ``test_search_cli.py``'s precedent of hand-writing sidecars
into a TEST vault (never a live one).
"""

import json
from pathlib import Path

from conftest import make_vault as _make_vault, run_cli as _run


def _find_sidecar(vault: Path, record_id: str) -> dict:
    kind, name = record_id.split("/", 1)
    return json.loads((vault / kind / f"{name}.json").read_text(encoding="utf-8"))


def _find_body(vault: Path, record_id: str) -> str:
    kind, name = record_id.split("/", 1)
    return (vault / kind / f"{name}.md").read_text(encoding="utf-8")


def _create(vault, state, body="original body\n"):
    r = _run(
        ["record", "create", "--kind", "spec", "--title", "Escape Hatch Probe", "--keyword", "foo"],
        vault=vault,
        state_dir=state,
        stdin_text=body,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _inject_reserved_label(vault: Path, record_id: str, key: str, value: str) -> None:
    """Hand-write a reserved labels key into the sidecar (bypasses the CLI guard,
    simulating a record that predates the guard's existence)."""
    kind, name = record_id.split("/", 1)
    path = vault / kind / f"{name}.json"
    sidecar = json.loads(path.read_text(encoding="utf-8"))
    sidecar.setdefault("labels", {})[key] = value
    path.write_text(json.dumps(sidecar), encoding="utf-8")


def test_unset_label_on_reserved_key_exits_zero_and_removes_it(tmp_path):
    """The documented escape hatch: --unset-label <reserved-key> as the ONLY
    change exits 0 and drops the key, on a record that already carries it."""
    vault, state = _make_vault(tmp_path)
    body = "stable body — must not change\n"
    record_id = _create(vault, state, body=body)

    # "area" is a reserved key: it's both a KINDS member and a KQL field name.
    _inject_reserved_label(vault, record_id, "area", "home-manager")
    before = _find_sidecar(vault, record_id)
    assert before["labels"] == {"area": "home-manager"}

    r = _run(
        ["record", "update", record_id, "--unset-label", "area"],
        vault=vault,
        state_dir=state,
        # no stdin_text → metadata-only path, matches how an agent would run the
        # documented escape hatch verbatim.
    )
    assert r.returncode == 0, r.stderr

    after = _find_sidecar(vault, record_id)
    assert "labels" not in after or "area" not in after.get("labels", {}), (
        f"expected 'area' removed from labels, got {after.get('labels')!r}"
    )
    assert _find_body(vault, record_id) == body  # body byte-identical


def test_re_adding_the_reserved_key_still_refused(tmp_path):
    """Negative control: the escape is specific to REMOVAL. Re-adding the same
    reserved key (even to a different value) on a record that already carries it
    is still refused."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)
    _inject_reserved_label(vault, record_id, "area", "home-manager")

    r = _run(
        ["record", "update", record_id, "--label", "area=elsewhere"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0, (
        f"expected --label area=... to be refused (reserved key), got exit 0; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "reserved" in r.stderr.lower()
    # Sidecar must be unchanged by the refused write.
    after = _find_sidecar(vault, record_id)
    assert after["labels"] == {"area": "home-manager"}
