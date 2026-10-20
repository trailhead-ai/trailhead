"""followup_dispose.py `dispose` (drops the gathered follow-ups a dispositions file names
under one reason).

End-to-end cases run the module's `main` against the real `lore` CLI reading a throwaway
vault (the pattern of test_followup_parent.py); failure cases inject faults through the
runner's hook.
"""

from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from test_followup_gather import (  # noqa: F401
    _allow_real_publish_spawn,
    _isolate_ambient_env,
    _lore,
    _publish_spawn_guard,
    _build_env,
    make_spec,
    make_vault,
)
from test_followup_parent import (  # noqa: F401
    GATHER,
    EnvRunner,
    body_of,
    gather_json,
    make_followup,
    refused,
    shown_revision,
    sidecar_of,
    vault_snapshot,
)

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts" / "followup_dispose.py"
)
_spec = importlib.util.spec_from_file_location("followup_dispose", _SCRIPT)
dispose = importlib.util.module_from_spec(_spec)
sys.modules["followup_dispose"] = dispose
_spec.loader.exec_module(dispose)

REASON = "superseded by the new importer"


@pytest.fixture
def lore_env(tmp_path):
    """(env, vault_name): a child environment seeing exactly one throwaway vault."""
    vault, _ = make_vault(tmp_path)
    return _build_env(tmp_path, [("default", "default", vault)]), "default"


@pytest.fixture
def two_vault_lore_env(tmp_path):
    first, _ = make_vault(tmp_path)
    second = tmp_path / "second"
    second.mkdir()
    env = _build_env(tmp_path, [("default", "default", first), ("other", "team", second)])
    return env, ("default", "other")


@pytest.fixture
def spec(lore_env):
    env, vault = lore_env
    return make_spec(env, vault, "Parent spec", status="complete")


def run_dispose(runner, spec_name, tmp_path, capsys, doc=None, preview=False, raw=None):
    path = tmp_path / "dispositions.json"
    path.write_text(raw if raw is not None else json.dumps(doc))
    argv = ["dispose", spec_name, "--dispositions", str(path)]
    code = dispose.main(argv + (["--preview"] if preview else []), runner=runner)
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def reason_code(err):
    lines = [ln for ln in err.splitlines() if ln.startswith("reason-code: ")]
    assert len(lines) == 1, err
    return lines[0].removeprefix("reason-code: ")


def subject(err):
    lines = [ln for ln in err.splitlines() if ln.startswith("subject: ")]
    assert len(lines) == 1, err
    return lines[0].removeprefix("subject: ").split()


def drop_entry(env, vault, name):
    status = sidecar_of(env, vault, name)["status"]
    if status == "blocked":
        return {"name": name}
    return {"name": name, "revision": shown_revision(env, vault, name)}


def file_for(env, vault, names, reason=REASON):
    return {"reason": reason, "drops": [drop_entry(env, vault, n) for n in names]}


def dropped_section(body):
    return body.partition("\n## Dropped\n\n")[2]


def is_drop_write(argv):
    return argv[:2] == ["record", "update"] and "dropped" in argv


@pytest.fixture
def pile(lore_env, spec):
    """Two open follow-ups and a blocked one, all linked to the spec."""
    env, vault = lore_env
    one = make_followup(env, vault, "Alpha one", status="open", spec=spec)
    two = make_followup(env, vault, "Beta two", status="open", spec=spec)
    blocked = make_followup(env, vault, "Gamma blocked", status="blocked", spec=spec)
    return sorted([one, two, blocked])


# --- the drop path ---------------------------------------------------------------


def test_three_follow_ups_dropped_under_one_reason_all_end_dropped_with_the_reason(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    prior = {n: sidecar_of(env, vault, n)["status"] for n in pile}
    doc = file_for(env, vault, pile)
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    assert sorted(out["dropped"], key=lambda d: d["name"]) == [
        {"name": n, "prior-status": prior[n]} for n in pile
    ]
    assert out["failed"] == [] and out["merged"] == []
    assert out["reset-to-open"] == [] and out["incomplete-merges"] == []
    for name in pile:
        assert sidecar_of(env, vault, name)["status"] == "dropped"
        assert dropped_section(body_of(env, vault, name)).strip() == f"Reason: {REASON}"
    assert [f["name"] for f in gather_json(env, spec)["followups"]] == []


def test_preview_lists_the_planned_drops_and_writes_nothing(lore_env, spec, pile, tmp_path, capsys):
    env, vault = lore_env
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, file_for(env, vault, pile), preview=True)
    assert code == 0, err
    assert out["drops"] == [{"name": n, "status": sidecar_of(env, vault, n)["status"]} for n in pile]
    assert out["merges"] == []
    assert runner.writes() == []
    assert vault_snapshot(env, vault) == before


def test_preview_dependents_name_a_gathered_follow_up_and_a_task_in_another_vault(
    two_vault_lore_env, tmp_path, capsys
):
    env, (vault, other) = two_vault_lore_env
    spec = make_spec(env, vault, "Parent spec", status="complete")
    target = make_followup(env, vault, "Target", status="open", spec=spec)
    lone = make_followup(env, vault, "Lone", status="open", spec=spec)
    sibling = make_followup(env, vault, "Sibling", status="open", spec=spec, depends=[target])
    abroad = make_followup(env, other, "Abroad", status="ready", depends=[target])
    doc = file_for(env, vault, [target, lone])
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc, preview=True)
    assert code == 0, err
    assert out["dependents"][target] == sorted(
        [
            {"name": sibling, "vault": vault, "status": "open"},
            {"name": abroad, "vault": other, "status": "ready"},
        ],
        key=lambda d: (d["vault"], d["name"]),
    )
    assert out["dependents"][lone] == []


def test_a_real_drop_of_a_follow_up_with_a_dependent_leaves_the_dependent_untouched(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    target = make_followup(env, vault, "Target", status="open", spec=spec)
    dependent = make_followup(env, vault, "Dependent", status="blocked", spec=spec, depends=[target])
    before = _lore(env, "record", "show", "--json", "--vault", vault, f"task/{dependent}")
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, file_for(env, vault, [target]))
    assert code == 0, err
    assert [d["name"] for d in out["dropped"]] == [target]
    assert _lore(env, "record", "show", "--json", "--vault", vault, f"task/{dependent}") == before


# --- failures after the first write -----------------------------------------------


def run_incomplete(env, vault, spec, names, hook, tmp_path, capsys):
    runner = EnvRunner(env, hook=hook)
    return run_dispose(runner, spec, tmp_path, capsys, file_for(env, vault, names))


def test_a_failed_second_drop_is_reported_with_a_command_that_completes_it(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    first, second, third = pile

    def hook(argv, stdin, runner):
        if is_drop_write(argv) and argv[2] == f"task/{second}":
            return refused(argv)

    code, out, err = run_incomplete(env, vault, spec, pile, hook, tmp_path, capsys)
    assert code == 2
    assert reason_code(err) == "dispose-incomplete"
    assert subject(err) == [second]
    assert sorted(d["name"] for d in out["dropped"]) == [first, third]
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(second, "drop")]
    assert sidecar_of(env, vault, second)["status"] != "dropped"
    command = out["failed"][0]["command"]
    assert REASON not in command
    done = subprocess.run(
        shlex.split(command), capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL
    )
    assert done.returncode == 0, done.stderr
    assert sidecar_of(env, vault, second)["status"] == "dropped"


def test_a_drop_that_exits_zero_but_leaves_the_record_unchanged_is_failed(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    target = pile[0]

    def hook(argv, stdin, runner):
        if is_drop_write(argv) and argv[2] == f"task/{target}":
            return subprocess.CompletedProcess(argv, 0, "", "")

    code, out, err = run_incomplete(env, vault, spec, [target], hook, tmp_path, capsys)
    assert code == 2
    assert reason_code(err) == "dispose-incomplete"
    assert [f["name"] for f in out["failed"]] == [target]
    assert target not in [d["name"] for d in out["dropped"]]


def test_a_drop_that_sets_the_status_but_writes_no_reason_section_is_failed(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    target = pile[0]

    def hook(argv, stdin, runner):
        if is_drop_write(argv) and argv[2] == f"task/{target}":
            return runner.real([a for a in argv if a != "--diff"])

    code, out, err = run_incomplete(env, vault, spec, [target], hook, tmp_path, capsys)
    assert sidecar_of(env, vault, target)["status"] == "dropped"
    assert code == 2
    assert [f["name"] for f in out["failed"]] == [target]
    assert out["dropped"] == []


def test_a_drop_that_writes_the_reason_but_leaves_the_status_is_failed(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    target = pile[0]

    def hook(argv, stdin, runner):
        if is_drop_write(argv) and argv[2] == f"task/{target}":
            return runner.real([a for a in argv if a not in ("--status", "dropped")], stdin)

    code, out, err = run_incomplete(env, vault, spec, [target], hook, tmp_path, capsys)
    assert "Reason:" in dropped_section(body_of(env, vault, target))
    assert sidecar_of(env, vault, target)["status"] != "dropped"
    assert code == 2
    assert [f["name"] for f in out["failed"]] == [target]
    assert out["dropped"] == []


def edit_between_check_and_write(env, vault, victim, change):
    """A hook that applies `change` to `victim` when the dependents read starts (the second
    task listing, after the gather's): after the run has read every drop, before its first write."""
    state = {"listings": 0}

    def hook(argv, stdin, runner):
        if argv[:2] == ["task", "list"]:
            state["listings"] += 1
            if state["listings"] == 2:
                change(env, vault, victim)

    return hook


def edit_body(env, vault, name):
    body = body_of(env, vault, name)
    _lore(env, "record", "update", f"task/{name}", "--vault", vault, stdin=body + "\nNew line.\n")


def set_status(env, vault, name, status):
    done = subprocess.run(
        ["lore", "record", "update", f"task/{name}", "--vault", vault, "--status", status],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
    )
    assert done.returncode == 0, done.stderr


def move_status(env, vault, name):
    set_status(env, vault, name, "ready")


def test_a_body_edited_before_the_write_is_child_changed_in_failed_and_the_rest_land(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    victim = pile[0]
    hook = edit_between_check_and_write(env, vault, victim, edit_body)
    code, out, err = run_incomplete(env, vault, spec, pile, hook, tmp_path, capsys)
    assert code == 2
    assert reason_code(err) == "dispose-incomplete"
    assert [(f["name"], f["write"], f["reason"]) for f in out["failed"]] == [
        (victim, "drop", "child-changed")
    ]
    assert sorted(d["name"] for d in out["dropped"]) == pile[1:]
    assert sidecar_of(env, vault, victim)["status"] == "open"
    assert "Dropped" not in body_of(env, vault, victim)


def test_a_status_moved_before_the_write_is_child_changed_though_the_revision_holds(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    victim = pile[0]
    before = shown_revision(env, vault, victim)
    hook = edit_between_check_and_write(env, vault, victim, move_status)
    code, out, err = run_incomplete(env, vault, spec, pile, hook, tmp_path, capsys)
    assert shown_revision(env, vault, victim) == before
    assert code == 2
    assert [(f["name"], f["reason"]) for f in out["failed"]] == [(victim, "child-changed")]
    assert sorted(d["name"] for d in out["dropped"]) == pile[1:]
    assert sidecar_of(env, vault, victim)["status"] == "ready"


# --- refusals before any write ----------------------------------------------------


def test_a_revision_moved_before_the_run_refuses_child_changed_and_writes_nothing(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    doc = file_for(env, vault, pile)
    edit_body(env, vault, pile[0])
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_code(err), subject(err)) == (1, "child-changed", [pile[0]])
    assert out is None
    assert runner.writes() == []
    assert vault_snapshot(env, vault) == before


def test_a_blocked_drop_that_became_open_refuses_child_changed(lore_env, spec, pile, tmp_path, capsys):
    env, vault = lore_env
    blocked = next(n for n in pile if sidecar_of(env, vault, n)["status"] == "blocked")
    doc = file_for(env, vault, pile)
    set_status(env, vault, blocked, "open")
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_code(err), subject(err)) == (1, "child-changed", [blocked])
    assert runner.writes() == []
    assert vault_snapshot(env, vault) == before


def make_stranger(env, vault, spec, how):
    if how == "nested":
        holder = make_followup(env, vault, "Holder", status="open", spec=spec)
        return make_followup(env, vault, "Nested", status="open", spec=spec, parent=holder)
    if how == "dropped":
        return make_followup(env, vault, "Gone", status="dropped", spec=spec)
    return make_followup(env, vault, "Unlinked", status="open")


@pytest.mark.parametrize("how", ["nested", "dropped", "unlinked"])
def test_a_name_that_is_not_a_gathered_follow_up_is_refused_and_the_vault_unchanged(
    lore_env, spec, pile, tmp_path, capsys, how
):
    env, vault = lore_env
    stranger = make_stranger(env, vault, spec, how)
    doc = file_for(env, vault, [pile[0]])
    doc["drops"].append(drop_entry(env, vault, stranger) if how != "dropped" else {"name": stranger, "revision": "x"})
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_code(err), subject(err)) == (1, "not-a-gathered-followup", [stranger])
    assert out is None
    assert runner.writes() == []
    assert vault_snapshot(env, vault) == before


# --- the reason ---------------------------------------------------------------------


def test_a_credential_shaped_token_in_the_reason_is_redacted_in_every_dropped_section(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    doc = file_for(env, vault, pile, reason=f"not needed, token {secret} pasted")
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    for name in pile:
        section = dropped_section(body_of(env, vault, name))
        assert secret not in section
        assert "[REDACTED]" in section


def test_a_reason_with_a_newline_and_a_heading_stays_on_one_line(lore_env, spec, pile, tmp_path, capsys):
    env, vault = lore_env
    doc = file_for(env, vault, [pile[0]], reason="first line\n\n## Injected heading\nmore")
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    section = dropped_section(body_of(env, vault, pile[0]))
    assert section.strip() == "Reason: first line ## Injected heading more"
    assert "\n## Injected" not in body_of(env, vault, pile[0])


# --- the dispositions file ---------------------------------------------------------


def invalid_files(env, vault, names, tmp_path):
    entries = [drop_entry(env, vault, n) for n in names]
    target = tmp_path / "real.json"
    target.write_text(json.dumps({"reason": REASON, "drops": entries}))
    (tmp_path / "link.json").symlink_to(target)
    return {
        "symlink": ("SYMLINK", None),
        "over-cap": (json.dumps({"reason": "x" * 70000, "drops": entries}), None),
        "not-an-object": (json.dumps([entries[0]]), None),
        "not-json": ("{nope", None),
        "repeats-a-name": (json.dumps({"reason": REASON, "drops": [entries[0], entries[0]]}), None),
        "empty-reason": (json.dumps({"reason": " \n\t ", "drops": entries}), None),
        "drops-not-a-list": (json.dumps({"reason": REASON, "drops": entries[0]}), None),
        "revision-not-a-string": (
            json.dumps({"reason": REASON, "drops": [{"name": names[0], "revision": 7}]}),
            None,
        ),
        "non-empty-merges": (json.dumps({"reason": REASON, "drops": entries, "merges": [{}]}), None),
    }


@pytest.mark.parametrize(
    "case",
    ["symlink", "over-cap", "not-an-object", "not-json", "repeats-a-name", "empty-reason",
     "drops-not-a-list", "revision-not-a-string", "non-empty-merges"],
)
def test_a_malformed_dispositions_file_is_refused_and_the_vault_unchanged(
    lore_env, spec, pile, tmp_path, capsys, case
):
    env, vault = lore_env
    raw, _ = invalid_files(env, vault, pile, tmp_path)[case]
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    if raw == "SYMLINK":
        code = dispose.main(
            ["dispose", spec, "--dispositions", str(tmp_path / "link.json")], runner=runner
        )
        err = capsys.readouterr().err
    else:
        code, _, err = run_dispose(runner, spec, tmp_path, capsys, raw=raw)
    assert code == 2
    assert reason_code(err) == "invalid-dispositions-file"
    assert runner.writes() == []
    assert vault_snapshot(env, vault) == before


@pytest.mark.parametrize("name", ["bad name", "-rf", "a/../b", "x;y"])
def test_a_follow_up_name_failing_the_shape_check_is_refused_before_any_lore_call(
    lore_env, spec, tmp_path, capsys, name
):
    env, vault = lore_env
    runner = EnvRunner(env)
    doc = {"reason": REASON, "drops": [{"name": name, "revision": "r"}]}
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_code(err)) == (2, "invalid-followup-name")
    assert runner.calls == []


# --- reading dependents across vaults -----------------------------------------------


def listing_of(vault, argv):
    return argv[:2] == ["task", "list"] and argv[argv.index("--vault") + 1] == vault


@pytest.mark.parametrize("preview", [True, False])
@pytest.mark.parametrize("broken", ["fails", "not-a-list"])
def test_a_failed_dependents_read_in_another_vault_stops_the_run_and_writes_nothing(
    two_vault_lore_env, tmp_path, capsys, preview, broken
):
    env, (vault, other) = two_vault_lore_env
    spec = make_spec(env, vault, "Parent spec", status="complete")
    target = make_followup(env, vault, "Target", status="open", spec=spec)

    def hook(argv, stdin, runner):
        if listing_of(other, argv):
            if broken == "fails":
                return refused(argv)
            return subprocess.CompletedProcess(argv, 0, '{"not": "a list"}', "")

    runner = EnvRunner(env, hook=hook)
    before = vault_snapshot(env, vault)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, file_for(env, vault, [target]), preview)
    assert code == 2
    assert reason_code(err) == ("lore-failed" if broken == "fails" else "lore-output-not-json")
    assert out is None
    assert runner.writes() == []
    assert vault_snapshot(env, vault) == before


def test_a_successful_dependents_read_with_no_match_is_not_an_error(
    two_vault_lore_env, tmp_path, capsys
):
    env, (vault, other) = two_vault_lore_env
    spec = make_spec(env, vault, "Parent spec", status="complete")
    target = make_followup(env, vault, "Target", status="open", spec=spec)
    make_followup(env, other, "Unrelated", status="open")
    code, out, err = run_dispose(
        EnvRunner(env), spec, tmp_path, capsys, file_for(env, vault, [target]), preview=True
    )
    assert code == 0, err
    assert out["dependents"] == {target: []}
