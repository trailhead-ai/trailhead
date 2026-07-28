"""EPHEMERAL assumption probe for KU2 (see plan
task/reserved-label-keys-point-agents-at-related-then-guard-the-wrong-flag).

Proves: a label written via ``lore record create --label craft/subsystems=X``
is returned by ``lore search 'label.craft.subsystems:X'`` — i.e. the
dot-for-slash convention round-trips through the CLI create path, the index,
AND the search CLI, not just the KQL parser (Axiom 15) or a hand-written
sidecar fixture (test_search_cli.py::test_namespaced_label_eq_end_to_end).

Delete this file once the real regression test lands in craft-repoint's test
contract.
"""

from __future__ import annotations

from conftest import make_vault as _make_vault, run_cli as _run


def test_cli_created_namespaced_label_is_searchable_via_dot_form(tmp_path):
    vault, state = _make_vault(tmp_path)

    create = _run(
        [
            "record",
            "create",
            "--kind",
            "spec",
            "--title",
            "PR Dashboard Subsystem Label",
            "--keyword",
            "foo",
            "--label",
            "craft/subsystems=pr-dashboard",
        ],
        vault=vault,
        state_dir=state,
        stdin_text="body\n",
    )
    assert create.returncode == 0, create.stderr
    record_id = create.stdout.strip()
    assert record_id.startswith("spec/"), f"expected spec/<name>, got {record_id!r}"
    name = record_id.split("/", 1)[1]

    search = _run(
        ["search", "label.craft.subsystems:pr-dashboard"],
        vault=vault,
        state_dir=state,
    )
    assert search.returncode == 0, search.stderr
    assert name in search.stdout, (
        f"expected {name!r} in search output for label.craft.subsystems:pr-dashboard, "
        f"got: {search.stdout!r}"
    )
