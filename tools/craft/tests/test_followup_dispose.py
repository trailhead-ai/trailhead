"""followup_dispose.py `dispose` (drops the gathered follow-ups a dispositions file names
under one reason).

End-to-end cases run the module's `main` against the real `lore` CLI reading a throwaway
vault (the pattern of test_followup_parent.py); failure cases inject faults through the
runner's hook.
"""

from __future__ import annotations

import importlib.util
import json
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
    EnvRunner,
    body_of,
    dropped_section,
    gather_json,
    is_drop,
    make_followup,
    paste,
    reason_of,
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


def run_dispose(runner, spec_name, tmp_path, capsys, doc=None, preview=False, raw=None, confirm="auto"):
    """Run `dispose` on `doc`. A write passes the code its own file yields unless `confirm`
    names one (or None for no flag)."""
    path = tmp_path / "dispositions.json"
    path.write_text(raw if raw is not None else json.dumps(doc))
    argv = ["dispose", spec_name, "--dispositions", str(path)]
    if confirm == "auto":
        try:
            confirm = dispose.confirmation(spec_name, dispose.load_dispositions(str(path)))
        except dispose.DisposeError:
            confirm = None
    if confirm is not None and not preview:
        argv += ["--confirm", confirm]
    code = dispose.main(argv + (["--preview"] if preview else []), runner=runner)
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


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


def test_a_failed_second_drop_has_no_command_and_stays_gathered_for_a_rerun_with_its_reason(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    first, second, third = pile

    def hook(argv, stdin, runner):
        if is_drop(argv) and argv[2] == f"task/{second}":
            return refused(argv)

    code, out, err = run_incomplete(env, vault, spec, pile, hook, tmp_path, capsys)
    assert code == 2
    assert reason_of(err) == "dispose-incomplete"
    assert subject(err) == [second]
    assert sorted(d["name"] for d in out["dropped"]) == [first, third]
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(second, "drop")]
    assert sidecar_of(env, vault, second)["status"] != "dropped"
    assert out["failed"][0]["command"] is None
    assert second in [f["name"] for f in gather_json(env, spec)["followups"]]
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, file_for(env, vault, [second]))
    assert code == 0, err
    assert sidecar_of(env, vault, second)["status"] == "dropped"
    assert dropped_section(body_of(env, vault, second)).strip() == f"Reason: {REASON}"


def test_an_exception_on_the_second_write_is_a_failed_write_with_the_full_report_and_later_members_still_run(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    first, second, third = pile

    def hook(argv, stdin, runner):
        if is_drop(argv) and argv[2] == f"task/{second}":
            raise RuntimeError("boom")

    code, out, err = run_incomplete(env, vault, spec, pile, hook, tmp_path, capsys)
    assert code == 2
    assert reason_of(err) == "dispose-incomplete"
    assert subject(err) == [second]
    assert sorted(d["name"] for d in out["dropped"]) == [first, third]
    assert out["failed"] == [{"name": second, "write": "drop", "command": None}]
    assert sidecar_of(env, vault, second)["status"] != "dropped"
    assert sidecar_of(env, vault, third)["status"] == "dropped"


def test_an_exception_on_a_merge_note_write_fails_the_note_and_a_later_group_still_runs(
    lore_env, spec, two_groups, tmp_path, capsys
):
    env, vault = lore_env
    n = two_groups
    first, second = sorted([(n["Sa"], n["Aa"]), (n["Sb"], n["Bb"])])

    def hook(argv, stdin, runner):
        if is_note_write(argv, first[0]):
            raise RuntimeError("boom")

    doc = merge_doc(env, vault, [(s, [a]) for s, a in (first, second)])
    code, out, err = run_dispose(EnvRunner(env, hook=hook), spec, tmp_path, capsys, doc)
    assert code == 2
    assert reason_of(err) == "dispose-incomplete"
    assert out["failed"] == [{"name": first[0], "write": "merge-note", "command": None}]
    assert out["incomplete-merges"] == [{"survivor": first[0], "absorbed": [first[1]]}]
    assert sidecar_of(env, vault, first[1])["status"] != "superseded"
    assert [m["survivor"] for m in out["merged"]] == [second[0]]


def test_an_exception_on_a_supersede_is_a_failed_supersede_with_its_command_and_the_next_still_runs(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    first, second = sorted([one, two])

    def hook(argv, stdin, runner):
        if is_supersede(argv, first):
            raise RuntimeError("boom")

    doc = merge_doc(env, vault, [(survivor, [one, two])])
    code, out, err = run_dispose(EnvRunner(env, hook=hook), spec, tmp_path, capsys, doc)
    assert code == 2
    assert reason_of(err) == "dispose-incomplete"
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(first, "supersede")]
    assert out["failed"][0]["command"].startswith(f"lore record supersede task/{first} --by task/{survivor}")
    assert sidecar_of(env, vault, second)["status"] == "superseded"
    assert out["incomplete-merges"] == [{"survivor": survivor, "absorbed": sorted([one, two])}]


def test_an_exception_before_the_first_write_stays_internal_error(lore_env, spec, pile, tmp_path, capsys):
    env, vault = lore_env

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "show"]:
            raise RuntimeError("boom")

    runner = EnvRunner(env, hook=hook)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, file_for(env, vault, pile))
    assert code == 2 and out is None
    assert reason_of(err) == "internal-error"
    assert runner.writes() == []


def test_a_drop_whose_reread_before_the_write_fails_has_no_command_and_writes_nothing(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    target = pile[0]

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "show"] and argv[-1] == f"task/{target}":
            shown = [i for i, a in enumerate(runner.calls[:-1]) if a == argv]
            listed = [i for i, a in enumerate(runner.calls[:-1]) if a[:2] == ["task", "list"]]
            if shown and listed and listed[-1] > shown[-1]:
                return refused(argv)

    runner = EnvRunner(env, hook=hook)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, file_for(env, vault, [target]))
    assert (code, reason_of(err)) == (2, "dispose-incomplete")
    assert [(f["name"], f["write"], f["command"]) for f in out["failed"]] == [(target, "drop", None)]
    assert runner.writes() == []
    assert target in [f["name"] for f in gather_json(env, spec)["followups"]]


def test_a_drop_that_exits_zero_but_leaves_the_record_unchanged_is_failed(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    target = pile[0]

    def hook(argv, stdin, runner):
        if is_drop(argv) and argv[2] == f"task/{target}":
            return stub_ok(argv)

    code, out, err = run_incomplete(env, vault, spec, [target], hook, tmp_path, capsys)
    assert code == 2
    assert reason_of(err) == "dispose-incomplete"
    assert [f["name"] for f in out["failed"]] == [target]
    assert target not in [d["name"] for d in out["dropped"]]


def test_a_drop_that_sets_the_status_but_writes_no_reason_section_is_failed(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    target = pile[0]

    def hook(argv, stdin, runner):
        if is_drop(argv) and argv[2] == f"task/{target}":
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
        if is_drop(argv) and argv[2] == f"task/{target}":
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
    assert reason_of(err) == "dispose-incomplete"
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
    assert (code, reason_of(err), subject(err)) == (1, "child-changed", [pile[0]])
    assert out is None
    assert runner.writes() == []
    assert vault_snapshot(env, vault) == before


def test_a_status_moved_between_the_gather_and_the_baseline_read_refuses_child_changed(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    victim = next(n for n in pile if sidecar_of(env, vault, n)["status"] == "open")
    doc = file_for(env, vault, [victim])
    shows = []

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "show"] and argv[-1] == f"task/{victim}":
            shows.append(argv)
            if len(shows) == 2:  # the gather read the member once; this second read is the baseline
                set_status(env, vault, victim, "in-progress")

    runner = EnvRunner(env, hook=hook)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_of(err), subject(err)) == (1, "child-changed", [victim])
    assert out is None
    assert runner.writes() == []
    assert sidecar_of(env, vault, victim)["status"] == "in-progress"
    assert "## Dropped" not in body_of(env, vault, victim)


def test_a_blocked_drop_that_became_open_refuses_child_changed(lore_env, spec, pile, tmp_path, capsys):
    env, vault = lore_env
    blocked = next(n for n in pile if sidecar_of(env, vault, n)["status"] == "blocked")
    doc = file_for(env, vault, pile)
    set_status(env, vault, blocked, "open")
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_of(err), subject(err)) == (1, "child-changed", [blocked])
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
    assert (code, reason_of(err), subject(err)) == (1, "not-a-gathered-followup", [stranger])
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
    assert reason_of(err) == "invalid-dispositions-file"
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
    assert (code, reason_of(err)) == (2, "invalid-followup-name")
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
    assert reason_of(err) == ("lore-failed" if broken == "fails" else "lore-output-not-json")
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


# --- the merge path ------------------------------------------------------------------

INTENT = "also covers the retry path"


def merge_entry(env, vault, survivor, absorbed):
    """`absorbed` is a list of names, or of (name, intent) pairs."""
    return {
        "survivor": {"name": survivor, "revision": shown_revision(env, vault, survivor)},
        "absorbed": [
            {
                "name": n,
                "revision": shown_revision(env, vault, n),
                "intent": i,
            }
            for n, i in ((a, INTENT) if isinstance(a, str) else a for a in absorbed)
        ],
    }


def merge_doc(env, vault, merges, drops=(), reason=REASON):
    doc = file_for(env, vault, drops, reason=reason)
    doc["merges"] = [merge_entry(env, vault, s, a) for s, a in merges]
    return doc


def merged_line(body, name):
    return body.partition(f"\n## Merged from task/{name}\n\n")[2].split("\n")[0]


def is_note_write(argv, survivor=None):
    return (
        argv[:2] == ["record", "update"]
        and "--diff" in argv
        and "dropped" not in argv
        and (survivor is None or argv[2] == f"task/{survivor}")
    )


def is_supersede(argv, absorbed=None):
    return argv[:2] == ["record", "supersede"] and (absorbed is None or argv[2] == f"task/{absorbed}")


def stub_ok(argv):
    return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def trio(lore_env, spec):
    """(survivor, first absorbed, second absorbed): three overlapping open follow-ups."""
    env, vault = lore_env
    return (
        make_followup(env, vault, "Survivor", status="open", spec=spec),
        make_followup(env, vault, "Absorbed one", status="open", spec=spec),
        make_followup(env, vault, "Absorbed two", status="open", spec=spec),
    )


def test_two_overlapping_follow_ups_merged_into_a_third_carry_their_intent_and_end_superseded(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    bystander = make_followup(env, vault, "Bystander", status="open", spec=spec)
    before = _lore(env, "record", "show", "--json", "--vault", vault, f"task/{bystander}")
    doc = merge_doc(env, vault, [(survivor, [(one, "intent of one"), (two, "intent of two")])])
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    body = body_of(env, vault, survivor)
    assert merged_line(body, one) == "Intent: intent of one"
    assert merged_line(body, two) == "Intent: intent of two"
    assert [sidecar_of(env, vault, n)["status"] for n in (one, two)] == ["superseded"] * 2
    assert sorted(sidecar_of(env, vault, survivor)["supersedes"]) == sorted(
        [f"task/{one}", f"task/{two}"]
    )
    assert out["merged"] == [
        {"survivor": survivor, "absorbed": sorted([one, two]), "survivor-prior-status": "open"}
    ]
    assert out["failed"] == [] and out["incomplete-merges"] == [] and out["dropped"] == []
    assert [f["name"] for f in gather_json(env, spec)["followups"]] == sorted([survivor, bystander])
    assert _lore(env, "record", "show", "--json", "--vault", vault, f"task/{bystander}") == before


def test_a_confirmed_write_with_a_drop_returns_the_included_list_the_file_named(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    doc = {**file_for(env, vault, pile[:1]), "included": [pile[2]]}
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    assert out["dropped"] == [{"name": pile[0], "prior-status": "open"}]
    assert out["included"] == [pile[2]]


def test_a_confirmed_write_with_a_merge_returns_a_different_included_list_for_a_different_file(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    other = make_followup(env, vault, "Other", status="open", spec=spec)
    doc = {
        **merge_doc(env, vault, [(survivor, [(one, "intent of one"), (two, "intent of two")])]),
        "included": [survivor, other],
    }
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    assert len(out["merged"]) == 1
    assert out["included"] == sorted([survivor, other])


def test_an_incomplete_write_still_returns_the_included_list_the_file_named(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    first, second, third = pile

    def hook(argv, stdin, runner):
        if is_drop(argv) and argv[2] == f"task/{second}":
            return refused(argv)

    doc = {**file_for(env, vault, [first, second]), "included": [third]}
    code, out, err = run_dispose(EnvRunner(env, hook=hook), spec, tmp_path, capsys, doc)
    assert code == 2
    assert reason_of(err) == "dispose-incomplete"
    assert out["included"] == [third]


def test_a_ready_survivor_ends_open_and_is_reported_reset_while_an_open_one_is_not(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    ready = make_followup(env, vault, "Ready survivor", status="ready", spec=spec)
    open_one = make_followup(env, vault, "Open survivor", status="open", spec=spec)
    a = make_followup(env, vault, "Feeds ready", status="open", spec=spec)
    b = make_followup(env, vault, "Feeds open", status="ready", spec=spec)
    doc = merge_doc(env, vault, [(ready, [a]), (open_one, [b])])
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    assert sidecar_of(env, vault, ready)["status"] == "open"
    assert sidecar_of(env, vault, open_one)["status"] == "open"
    assert out["reset-to-open"] == [{"name": ready, "prior-status": "ready"}]
    assert sorted((m["survivor"], m["survivor-prior-status"]) for m in out["merged"]) == sorted(
        [(ready, "ready"), (open_one, "open")]
    )


def test_a_ready_survivor_whose_notes_already_landed_is_still_reset_to_open(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    absorbed = make_followup(env, vault, "Absorbed", status="open", spec=spec)
    body = f"Survivor body.\n\n## Merged from task/{absorbed}\n\nIntent: {INTENT}\n"
    survivor = make_followup(env, vault, "Ready survivor", status="ready", spec=spec, body=body)
    doc = merge_doc(env, vault, [(survivor, [absorbed])])
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    assert sidecar_of(env, vault, survivor)["status"] == "open"
    assert body_of(env, vault, survivor).count(f"## Merged from task/{absorbed}") == 1
    assert out["reset-to-open"] == [{"name": survivor, "prior-status": "ready"}]
    assert sidecar_of(env, vault, absorbed)["status"] == "superseded"


def test_an_absorbed_follow_up_with_a_dependent_in_another_vault_is_not_mergeable_but_can_survive(
    two_vault_lore_env, tmp_path, capsys
):
    env, (vault, other) = two_vault_lore_env
    spec = make_spec(env, vault, "Parent spec", status="complete")
    depended = make_followup(env, vault, "Depended on", status="open", spec=spec)
    plain = make_followup(env, vault, "Plain", status="open", spec=spec)
    dependent = make_followup(env, other, "Dependent", status="open", depends=[depended])

    def both():
        return (vault_snapshot(env, vault), vault_snapshot(env, other))

    before = both()
    runner = EnvRunner(env)
    doc = merge_doc(env, vault, [(plain, [depended])])
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_of(err), subject(err)) == (1, "not-mergeable", [depended])
    assert f"{other}/{dependent}" in err
    assert out is None
    assert runner.writes() == [] and both() == before

    dependent_before = _lore(env, "record", "show", "--json", "--vault", other, f"task/{dependent}")
    flipped = merge_doc(env, vault, [(depended, [plain])])
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, flipped)
    assert code == 0, err
    assert sidecar_of(env, vault, plain)["status"] == "superseded"
    assert _lore(env, "record", "show", "--json", "--vault", other, f"task/{dependent}") == dependent_before


@pytest.mark.parametrize("who", ["survivor", "absorbed"])
def test_a_blocked_merge_member_is_not_mergeable_and_the_vault_unchanged(
    lore_env, spec, trio, tmp_path, capsys, who
):
    env, vault = lore_env
    survivor, one, two = trio
    victim = survivor if who == "survivor" else one
    set_status(env, vault, victim, "blocked")
    doc = merge_doc(env, vault, [(survivor, [one, two])])
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_of(err), subject(err)) == (1, "not-mergeable", [victim])
    assert out is None
    assert runner.writes() == [] and vault_snapshot(env, vault) == before


def bad_merge_docs(env, vault, survivor, one, two):
    def entry(s, absorbed):
        return merge_entry(env, vault, s, absorbed)

    def base(merges, drops=()):
        doc = file_for(env, vault, drops)
        doc["merges"] = merges
        return doc

    empty_intent = entry(survivor, [one])
    empty_intent["absorbed"][0]["intent"] = " \n\t "
    markup_only = entry(survivor, [one])
    markup_only["absorbed"][0]["intent"] = "`<[]>`!"
    no_intent = entry(survivor, [one])
    del no_intent["absorbed"][0]["intent"]
    no_revision = entry(survivor, [one])
    del no_revision["survivor"]["revision"]
    return {
        "survivor-in-own-absorbed": base([entry(survivor, [survivor, one])]),
        "name-in-two-merges": base([entry(survivor, [one]), entry(two, [one])]),
        "name-in-a-merge-and-a-drop": base([entry(survivor, [one])], drops=[two, one]),
        "survivor-also-absorbed-elsewhere": base([entry(survivor, [one]), entry(one, [two])]),
        "empty-absorbed-list": base([{"survivor": entry(survivor, [one])["survivor"], "absorbed": []}]),
        "intent-empty-once-sanitized": base([empty_intent]),
        "intent-only-markup": base([markup_only]),
        "intent-missing": base([no_intent]),
        "survivor-revision-missing": base([no_revision]),
        "merge-not-an-object": base(["x"]),
    }


@pytest.mark.parametrize(
    "case",
    ["survivor-in-own-absorbed", "name-in-two-merges", "name-in-a-merge-and-a-drop",
     "survivor-also-absorbed-elsewhere", "empty-absorbed-list", "intent-empty-once-sanitized",
     "intent-only-markup", "intent-missing", "survivor-revision-missing", "merge-not-an-object"],
)
def test_a_malformed_merge_is_invalid_dispositions_file_and_the_vault_unchanged(
    lore_env, spec, trio, tmp_path, capsys, case
):
    env, vault = lore_env
    doc = bad_merge_docs(env, vault, *trio)[case]
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_of(err)) == (2, "invalid-dispositions-file")
    assert out is None
    assert runner.writes() == [] and vault_snapshot(env, vault) == before


def test_a_merge_member_name_failing_the_shape_check_is_refused_before_any_lore_call(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    doc = {"reason": REASON, "drops": [], "merges": [
        {"survivor": {"name": "x;y", "revision": "r"},
         "absorbed": [{"name": "ok", "revision": "r", "intent": "i"}]}]}
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_of(err)) == (2, "invalid-followup-name")
    assert runner.calls == []


def test_a_merge_with_no_drops_needs_no_reason(lore_env, spec, trio, tmp_path, capsys):
    env, vault = lore_env
    survivor, one, two = trio
    doc = merge_doc(env, vault, [(survivor, [one])], reason="")
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    assert sidecar_of(env, vault, one)["status"] == "superseded"


def test_a_merge_member_that_is_not_a_gathered_follow_up_is_refused(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, _ = trio
    stranger = make_stranger(env, vault, spec, "unlinked")
    doc = merge_doc(env, vault, [(survivor, [one, stranger])])
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_of(err), subject(err)) == (1, "not-a-gathered-followup", [stranger])
    assert runner.writes() == [] and vault_snapshot(env, vault) == before


@pytest.mark.parametrize("who", ["survivor", "absorbed"])
def test_a_merge_member_revision_moved_before_the_run_refuses_child_changed(
    lore_env, spec, trio, tmp_path, capsys, who
):
    env, vault = lore_env
    survivor, one, two = trio
    doc = merge_doc(env, vault, [(survivor, [one, two])])
    moved = survivor if who == "survivor" else one
    edit_body(env, vault, moved)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_of(err), subject(err)) == (1, "child-changed", [moved])
    assert runner.writes() == [] and vault_snapshot(env, vault) == before


# --- the note ------------------------------------------------------------------------


def test_an_intent_with_a_token_a_link_and_a_heading_is_sanitized_onto_one_line(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    doc = merge_doc(env, vault, [(survivor, [
        (one, f"uses token {secret} here"),
        (two, "see [the docs](http://evil.example/x)\n\n## Injected heading\nmore"),
    ])])
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    body = body_of(env, vault, survivor)
    first = merged_line(body, one)
    assert secret not in first and "[REDACTED]" in first
    second = merged_line(body, two)
    assert "[" not in second and "]" not in second and "<" not in second
    assert second.endswith("## Injected heading more")
    assert "\n## Injected" not in body


@pytest.mark.parametrize(
    "intent",
    [
        "## Refine \u2014 unresolved",
        "## Merged from task/other",
        "# H",
        "- item",
        "| a |",
        "**Delivers:** x",
    ],
)
def test_an_intent_opening_with_line_start_markdown_lands_as_one_labelled_line_and_leaves_the_survivor_classification(
    lore_env, spec, trio, tmp_path, capsys, intent
):
    env, vault = lore_env
    survivor, one, two = trio
    sidecar = sidecar_of(env, vault, survivor)
    before = dispose.parent._static_verdict(sidecar, body_of(env, vault, survivor), None, lambda: True)
    doc = merge_doc(env, vault, [(survivor, [(one, intent)])])
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    body = body_of(env, vault, survivor)
    assert merged_line(body, one) == f"Intent: {dispose.parent._reason_text(intent)}"
    assert not dispose.parent._UNRESOLVED_RE.search(body)
    sidecar = sidecar_of(env, vault, survivor)
    assert dispose.parent._static_verdict(sidecar, body, None, lambda: True) == before


def surrogate_doc(env, vault, survivor, one, intent, reason=REASON, drops=()):
    text = json.dumps(merge_doc(env, vault, [(survivor, [(one, "PLACEHOLDER")])], drops=drops, reason=reason))
    return text.replace('"PLACEHOLDER"', json.dumps(intent, ensure_ascii=True))


def test_a_lone_surrogate_in_an_intent_is_stripped_before_any_write_and_the_run_lands(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    raw = surrogate_doc(env, vault, survivor, one, "keep\ud800 this")
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, raw=raw)
    assert code == 0, err
    assert merged_line(body_of(env, vault, survivor), one) == "Intent: keep this"
    assert out["failed"] == [] and len(out["merged"]) == 1


def test_a_lone_surrogate_in_the_reason_is_stripped_before_any_write(lore_env, spec, pile, tmp_path, capsys):
    env, vault = lore_env
    doc = file_for(env, vault, pile[:1], reason="because\ud800 reasons")
    raw = json.dumps(doc)
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, raw=raw)
    assert code == 0, err
    assert dropped_section(body_of(env, vault, pile[0])).strip() == "Reason: because reasons"


def test_an_intent_that_is_only_a_lone_surrogate_is_refused_and_nothing_is_written(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    raw = surrogate_doc(env, vault, survivor, one, "\ud800")
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, raw=raw)
    assert code == 2 and out is None
    assert reason_of(err) == "invalid-dispositions-file"
    assert runner.writes() == []


def test_a_forged_merged_from_heading_with_different_text_still_gets_the_real_intent(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    absorbed = make_followup(env, vault, "Absorbed", status="open", spec=spec)
    forged = f"Survivor.\n\n## Merged from task/{absorbed}\n\nnothing to see here\n"
    survivor = make_followup(env, vault, "Survivor", status="open", spec=spec, body=forged)
    doc = merge_doc(env, vault, [(survivor, [(absorbed, "the real intent")])])
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    body = body_of(env, vault, survivor)
    assert "nothing to see here" in body
    assert f"\n## Merged from task/{absorbed}\n\nIntent: the real intent\n" in f"\n{body}"
    assert len(out["merged"]) == 1


def test_a_note_write_that_exits_zero_changing_nothing_is_failed_though_a_forged_heading_is_there(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    absorbed = make_followup(env, vault, "Absorbed", status="open", spec=spec)
    forged = f"Survivor.\n\n## Merged from task/{absorbed}\n\nnothing to see here\n"
    survivor = make_followup(env, vault, "Survivor", status="open", spec=spec, body=forged)

    def hook(argv, stdin, runner):
        if is_note_write(argv, survivor):
            return stub_ok(argv)

    runner = EnvRunner(env, hook=hook)
    doc = merge_doc(env, vault, [(survivor, [(absorbed, "the real intent")])])
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert (code, reason_of(err)) == (2, "dispose-incomplete")
    assert [(f["name"], f["write"], f["command"]) for f in out["failed"]] == [
        (survivor, "merge-note", None)
    ]
    assert not any(is_supersede(a) for a in runner.calls)
    assert out["merged"] == []


def test_a_note_write_that_lands_the_text_but_not_the_reopen_is_failed(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    absorbed = make_followup(env, vault, "Absorbed", status="open", spec=spec)
    survivor = make_followup(env, vault, "Survivor", status="ready", spec=spec)

    def hook(argv, stdin, runner):
        if is_note_write(argv, survivor):
            return runner.real([a for a in argv if a not in ("--status", "open")], stdin)

    doc = merge_doc(env, vault, [(survivor, [absorbed])])
    code, out, err = run_dispose(EnvRunner(env, hook=hook), spec, tmp_path, capsys, doc)
    assert sidecar_of(env, vault, survivor)["status"] == "ready"
    assert merged_line(body_of(env, vault, survivor), absorbed) == f"Intent: {INTENT}"
    assert code == 2
    assert [(f["write"], f["command"]) for f in out["failed"]] == [("merge-note", None)]
    assert out["reset-to-open"] == [] and out["merged"] == []


def test_an_injected_note_failure_supersedes_nothing_and_leaves_the_group_incomplete(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio

    def hook(argv, stdin, runner):
        if is_note_write(argv, survivor):
            return refused(argv)

    runner = EnvRunner(env, hook=hook)
    doc = merge_doc(env, vault, [(survivor, [one, two])])
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert code == 2
    assert (reason_of(err), subject(err)) == ("dispose-incomplete", [survivor])
    assert [(f["name"], f["write"], f["command"]) for f in out["failed"]] == [
        (survivor, "merge-note", None)
    ]
    assert out["incomplete-merges"] == [{"survivor": survivor, "absorbed": sorted([one, two])}]
    assert out["merged"] == []
    assert not any(is_supersede(a) for a in runner.calls)
    assert [sidecar_of(env, vault, n)["status"] for n in (one, two)] == ["open", "open"]


# --- the supersedes -------------------------------------------------------------------


def run_with_failing_supersede(env, vault, spec, trio, tmp_path, capsys, failing, how="refuse"):
    survivor, one, two = trio

    def hook(argv, stdin, runner):
        if is_supersede(argv, failing):
            return refused(argv) if how == "refuse" else stub_ok(argv)

    runner = EnvRunner(env, hook=hook)
    doc = merge_doc(env, vault, [(survivor, [one, two])])
    return runner, run_dispose(runner, spec, tmp_path, capsys, doc)


def test_a_failed_supersede_on_the_second_absorbed_still_lands_the_first_and_names_a_command_that_completes_it(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    second = sorted([one, two])[1]
    first = sorted([one, two])[0]
    runner, (code, out, err) = run_with_failing_supersede(env, vault, spec, trio, tmp_path, capsys, second)
    assert code == 2
    assert (reason_of(err), subject(err)) == ("dispose-incomplete", [second])
    assert sidecar_of(env, vault, first)["status"] == "superseded"
    assert sidecar_of(env, vault, second)["status"] == "open"
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(second, "supersede")]
    assert out["incomplete-merges"] == [{"survivor": survivor, "absorbed": sorted([one, two])}]
    assert out["merged"] == []
    command = out["failed"][0]["command"]
    done = paste(env, command)
    assert done.returncode == 0, done.stderr
    assert sidecar_of(env, vault, second)["status"] == "superseded"
    assert f"task/{second}" in sidecar_of(env, vault, survivor)["supersedes"]


def test_a_failed_supersede_on_the_first_absorbed_still_attempts_the_second(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    first, second = sorted([one, two])
    runner, (code, out, err) = run_with_failing_supersede(env, vault, spec, trio, tmp_path, capsys, first)
    assert code == 2
    assert [f["name"] for f in out["failed"]] == [first]
    assert [a[2] for a in runner.calls if is_supersede(a)] == [f"task/{first}", f"task/{second}"]
    assert sidecar_of(env, vault, second)["status"] == "superseded"


def test_a_supersede_that_exits_zero_leaving_the_record_unchanged_is_failed(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    second = sorted([one, two])[1]
    runner, (code, out, err) = run_with_failing_supersede(
        env, vault, spec, trio, tmp_path, capsys, second, how="stub"
    )
    assert code == 2
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(second, "supersede")]
    assert out["merged"] == []


def test_a_supersede_that_marks_the_status_but_leaves_no_link_on_the_survivor_is_failed(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    target = sorted([one, two])[1]

    def hook(argv, stdin, runner):
        if is_supersede(argv, target):
            runner.real(argv)
            return runner.real(
                ["record", "update", f"task/{survivor}", "--vault", vault, "--unset-supersedes", f"task/{target}"]
            )

    doc = merge_doc(env, vault, [(survivor, [one, two])])
    code, out, err = run_dispose(EnvRunner(env, hook=hook), spec, tmp_path, capsys, doc)
    assert sidecar_of(env, vault, target)["status"] == "superseded"
    assert f"task/{target}" not in sidecar_of(env, vault, survivor).get("supersedes", [])
    assert code == 2
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(target, "supersede")]


# --- a write that lands but exits non-zero ---------------------------------------------


def lands_then_exits_nonzero(argv, stdin, runner):
    runner.real(argv, stdin)
    return refused(argv)


def test_a_drop_that_lands_but_exits_nonzero_is_failed_and_not_listed_dropped(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    target = pile[0]

    def hook(argv, stdin, runner):
        if is_drop(argv) and argv[2] == f"task/{target}":
            return lands_then_exits_nonzero(argv, stdin, runner)

    code, out, err = run_incomplete(env, vault, spec, [target], hook, tmp_path, capsys)
    assert sidecar_of(env, vault, target)["status"] == "dropped"
    assert (code, reason_of(err)) == (2, "dispose-incomplete")
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(target, "drop")]
    assert out["failed"][0]["command"] is None
    assert out["dropped"] == []


def test_a_note_write_that_lands_but_exits_nonzero_is_failed_and_the_group_incomplete(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio

    def hook(argv, stdin, runner):
        if is_note_write(argv, survivor):
            return lands_then_exits_nonzero(argv, stdin, runner)

    runner = EnvRunner(env, hook=hook)
    doc = merge_doc(env, vault, [(survivor, [one, two])])
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert merged_line(body_of(env, vault, survivor), one) == f"Intent: {INTENT}"
    assert (code, reason_of(err)) == (2, "dispose-incomplete")
    assert [(f["name"], f["write"], f["command"]) for f in out["failed"]] == [
        (survivor, "merge-note", None)
    ]
    assert out["merged"] == []
    assert out["incomplete-merges"] == [{"survivor": survivor, "absorbed": sorted([one, two])}]


def test_a_supersede_that_lands_but_exits_nonzero_is_failed_and_the_group_incomplete(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    target = sorted([one, two])[1]

    def hook(argv, stdin, runner):
        if is_supersede(argv, target):
            return lands_then_exits_nonzero(argv, stdin, runner)

    doc = merge_doc(env, vault, [(survivor, [one, two])])
    code, out, err = run_dispose(EnvRunner(env, hook=hook), spec, tmp_path, capsys, doc)
    assert sidecar_of(env, vault, target)["status"] == "superseded"
    assert (code, reason_of(err)) == (2, "dispose-incomplete")
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(target, "supersede")]
    assert out["failed"][0]["command"] is not None
    assert out["merged"] == []
    assert out["incomplete-merges"] == [{"survivor": survivor, "absorbed": sorted([one, two])}]


# --- recovery -------------------------------------------------------------------------


def test_after_a_supersede_failure_a_rerun_with_the_gathered_absorbed_keeps_one_note_and_lands_it(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    first, second = sorted([one, two])
    runner, (code, out, err) = run_with_failing_supersede(env, vault, spec, trio, tmp_path, capsys, second)
    assert code == 2
    assert [f["name"] for f in gather_json(env, spec)["followups"] if f["name"] == first] == []
    again = merge_doc(env, vault, [(survivor, [second])])
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, again)
    assert code == 0, err
    body = body_of(env, vault, survivor)
    assert body.count(f"## Merged from task/{second}") == 1
    assert body.count(f"## Merged from task/{first}") == 1
    assert sidecar_of(env, vault, second)["status"] == "superseded"
    assert sorted(sidecar_of(env, vault, survivor)["supersedes"]) == sorted([f"task/{first}", f"task/{second}"])
    assert out["merged"] == [{"survivor": survivor, "absorbed": [second], "survivor-prior-status": "open"}]
    assert out["failed"] == []


def test_a_rerun_that_writes_no_new_note_makes_no_note_write(lore_env, spec, trio, tmp_path, capsys):
    env, vault = lore_env
    survivor, one, two = trio
    second = sorted([one, two])[1]
    run_with_failing_supersede(env, vault, spec, trio, tmp_path, capsys, second)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, merge_doc(env, vault, [(survivor, [second])]))
    assert code == 0, err
    assert not any(is_note_write(a) for a in runner.calls)
    assert [a for a in runner.calls if is_supersede(a)] == [
        ["record", "supersede", f"task/{second}", "--by", f"task/{survivor}", "--vault", vault]
    ]


# --- the ordering matrix -----------------------------------------------------------------


def kinds(calls, survivor_names, dropped_names):
    """The write calls in order, each as ("drop"|"note"|"supersede", name)."""
    seq = []
    for argv in calls:
        if is_drop(argv):
            seq.append(("drop", argv[2].removeprefix("task/")))
        elif is_supersede(argv):
            seq.append(("supersede", argv[2].removeprefix("task/")))
        elif is_note_write(argv):
            seq.append(("note", argv[2].removeprefix("task/")))
    return seq


@pytest.fixture
def two_groups(lore_env, spec):
    env, vault = lore_env
    names = {
        k: make_followup(env, vault, k, status="open", spec=spec)
        for k in ("Sa", "Aa", "Sb", "Bb", "Dd")
    }
    return names


def test_every_drop_lands_before_the_first_merge_write(lore_env, spec, two_groups, tmp_path, capsys):
    env, vault = lore_env
    n = two_groups
    runner = EnvRunner(env)
    doc = merge_doc(env, vault, [(n["Sa"], [n["Aa"]]), (n["Sb"], [n["Bb"]])], drops=[n["Dd"]])
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert code == 0, err
    seq = kinds(runner.calls, None, None)
    assert seq[0] == ("drop", n["Dd"])
    assert all(k != "drop" for k, _ in seq[1:])


def test_a_failed_drop_does_not_stop_the_merges(lore_env, spec, two_groups, tmp_path, capsys):
    env, vault = lore_env
    n = two_groups

    def hook(argv, stdin, runner):
        if is_drop(argv):
            return refused(argv)

    doc = merge_doc(env, vault, [(n["Sa"], [n["Aa"]])], drops=[n["Dd"]])
    code, out, err = run_dispose(EnvRunner(env, hook=hook), spec, tmp_path, capsys, doc)
    assert code == 2
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(n["Dd"], "drop")]
    assert sidecar_of(env, vault, n["Aa"])["status"] == "superseded"
    assert len(out["merged"]) == 1


def test_within_a_group_the_note_comes_before_every_supersede(lore_env, spec, trio, tmp_path, capsys):
    env, vault = lore_env
    survivor, one, two = trio
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, merge_doc(env, vault, [(survivor, [one, two])]))
    assert code == 0, err
    assert kinds(runner.calls, None, None) == [
        ("note", survivor), ("supersede", sorted([one, two])[0]), ("supersede", sorted([one, two])[1])
    ]


def test_a_failure_in_one_group_does_not_stop_later_groups(lore_env, spec, two_groups, tmp_path, capsys):
    env, vault = lore_env
    n = two_groups
    first, second = sorted([(n["Sa"], n["Aa"]), (n["Sb"], n["Bb"])])

    def hook(argv, stdin, runner):
        if is_note_write(argv, first[0]):
            return refused(argv)

    runner = EnvRunner(env, hook=hook)
    doc = merge_doc(env, vault, [(first[0], [first[1]]), (second[0], [second[1]])])
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert code == 2
    assert out["incomplete-merges"] == [{"survivor": first[0], "absorbed": [first[1]]}]
    assert [m["survivor"] for m in out["merged"]] == [second[0]]
    assert sidecar_of(env, vault, first[1])["status"] == "open"
    assert sidecar_of(env, vault, second[1])["status"] == "superseded"


def test_a_later_group_failure_leaves_an_earlier_group_merged(lore_env, spec, two_groups, tmp_path, capsys):
    env, vault = lore_env
    n = two_groups
    first, second = sorted([(n["Sa"], n["Aa"]), (n["Sb"], n["Bb"])])

    def hook(argv, stdin, runner):
        if is_supersede(argv, second[1]):
            return refused(argv)

    doc = merge_doc(env, vault, [(first[0], [first[1]]), (second[0], [second[1]])])
    code, out, err = run_dispose(EnvRunner(env, hook=hook), spec, tmp_path, capsys, doc)
    assert code == 2
    assert [m["survivor"] for m in out["merged"]] == [first[0]]
    assert [g["survivor"] for g in out["incomplete-merges"]] == [second[0]]


# --- re-read before each merge write ----------------------------------------------------


@pytest.mark.parametrize("change", [edit_body, move_status], ids=["body", "status"])
def test_a_survivor_that_moved_before_its_note_write_is_child_changed_and_its_group_skipped(
    lore_env, spec, trio, tmp_path, capsys, change
):
    env, vault = lore_env
    survivor, one, two = trio
    hook = edit_between_check_and_write(env, vault, survivor, change)
    runner = EnvRunner(env, hook=hook)
    doc = merge_doc(env, vault, [(survivor, [one, two])])
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert code == 2
    assert [(f["name"], f["write"], f["command"], f["reason"]) for f in out["failed"]] == [
        (survivor, "merge-note", None, "child-changed")
    ]
    assert not any(is_note_write(a) or is_supersede(a) for a in runner.calls)
    assert out["incomplete-merges"] == [{"survivor": survivor, "absorbed": sorted([one, two])}]


def test_an_absorbed_follow_up_whose_status_moved_before_its_supersede_is_child_changed(
    lore_env, spec, trio, tmp_path, capsys
):
    env, vault = lore_env
    survivor, one, two = trio
    first, second = sorted([one, two])
    before = shown_revision(env, vault, second)

    def hook(argv, stdin, runner):
        if is_note_write(argv, survivor):
            move_status(env, vault, second)

    runner = EnvRunner(env, hook=hook)
    doc = merge_doc(env, vault, [(survivor, [one, two])])
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc)
    assert shown_revision(env, vault, second) == before
    assert code == 2
    assert [(f["name"], f["write"], f["command"], f["reason"]) for f in out["failed"]] == [
        (second, "supersede", None, "child-changed")
    ]
    assert not any(is_supersede(a, second) for a in runner.calls)
    assert sidecar_of(env, vault, first)["status"] == "superseded"
    assert sidecar_of(env, vault, second)["status"] == "ready"


# --- preview --------------------------------------------------------------------------


def test_preview_shows_the_exact_note_the_reset_and_the_supersedes_and_writes_nothing(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    survivor = make_followup(env, vault, "Ready survivor", status="ready", spec=spec)
    open_survivor = make_followup(env, vault, "Open survivor", status="open", spec=spec)
    a = make_followup(env, vault, "Aa", status="open", spec=spec)
    b = make_followup(env, vault, "Bb", status="ready", spec=spec)
    doc = merge_doc(env, vault, [
        (survivor, [(a, "see [docs](http://x.example/y)\n## Heading")]),
        (open_survivor, [(b, "plain intent")]),
    ])
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc, preview=True)
    assert code == 0, err
    groups = {g["survivor"]: g for g in out["merges"]}
    ready_group, open_group = groups[survivor], groups[open_survivor]
    assert (ready_group["status"], ready_group["reset-to-open"]) == ("ready", True)
    assert (open_group["status"], open_group["reset-to-open"]) == ("open", False)
    note = ready_group["absorbed"][0]["note"]
    assert note.startswith(f"## Merged from task/{a}\n\nIntent: see ")
    assert note.endswith(" ## Heading\n") and note.count("\n") == 3
    assert "[" not in note and "]" not in note
    assert open_group["absorbed"][0]["note"] == f"## Merged from task/{b}\n\nIntent: plain intent\n"
    assert open_group["absorbed"][0]["status"] == "ready"
    assert open_group["supersedes"] == [{"name": b, "by": open_survivor}]
    assert out["drops"] == []
    assert runner.writes() == [] and vault_snapshot(env, vault) == before


def test_preview_refuses_a_not_mergeable_member_like_the_real_run(lore_env, spec, trio, tmp_path, capsys):
    env, vault = lore_env
    survivor, one, two = trio
    set_status(env, vault, one, "blocked")
    runner = EnvRunner(env)
    doc = merge_doc(env, vault, [(survivor, [one, two])])
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc, preview=True)
    assert (code, reason_of(err), subject(err)) == (1, "not-mergeable", [one])
    assert runner.writes() == []


# --- bodies stored without a final newline ---------------------------------------------

UNTERMINATED = "**Delivers:** A widget.\n\n**Files:** `src/a.py`"


def unterminated(env, vault, title, spec, status="open", body=UNTERMINATED):
    name = make_followup(env, vault, title, status=status, spec=spec, body=body)
    assert not body_of(env, vault, name).endswith("\n")
    return name


def test_a_follow_up_whose_body_has_no_final_newline_is_dropped_with_its_reason(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    target = unterminated(env, vault, "No newline", spec)
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, file_for(env, vault, [target]))
    assert code == 0, err
    assert out["failed"] == [] and [d["name"] for d in out["dropped"]] == [target]
    assert sidecar_of(env, vault, target)["status"] == "dropped"
    body = body_of(env, vault, target)
    assert body.startswith(UNTERMINATED + "\n\n## Dropped\n\n")
    assert dropped_section(body).strip() == f"Reason: {REASON}"


def test_a_merge_into_a_survivor_whose_body_has_no_final_newline_lands_its_note_and_supersedes(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    survivor = unterminated(env, vault, "Survivor", spec)
    absorbed = unterminated(env, vault, "Absorbed", spec)
    doc = merge_doc(env, vault, [(survivor, [absorbed])])
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc)
    assert code == 0, err
    assert out["failed"] == []
    body = body_of(env, vault, survivor)
    assert body.startswith(UNTERMINATED + "\n\n")
    assert merged_line(body, absorbed) == f"Intent: {INTENT}"
    assert sidecar_of(env, vault, absorbed)["status"] == "superseded"
    assert sidecar_of(env, vault, survivor)["supersedes"] == [f"task/{absorbed}"]


def test_a_survivor_whose_unterminated_last_line_is_the_note_gets_no_second_note(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    absorbed = unterminated(env, vault, "Absorbed", spec)
    body = f"Survivor body.\n\n## Merged from task/{absorbed}\n\nIntent: {INTENT}"
    survivor = unterminated(env, vault, "Survivor", spec, body=body)
    runner = EnvRunner(env)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, merge_doc(env, vault, [(survivor, [absorbed])]))
    assert code == 0, err
    assert not any(is_note_write(a) for a in runner.calls)
    assert body_of(env, vault, survivor).count(f"## Merged from task/{absorbed}") == 1
    assert sidecar_of(env, vault, absorbed)["status"] == "superseded"


def test_a_note_rerun_after_a_supersede_failure_on_an_unterminated_survivor_keeps_one_note(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    survivor = unterminated(env, vault, "Survivor", spec)
    absorbed = unterminated(env, vault, "Absorbed", spec)

    def hook(argv, stdin, runner):
        if is_supersede(argv, absorbed):
            return refused(argv)

    doc = merge_doc(env, vault, [(survivor, [absorbed])])
    code, out, err = run_dispose(EnvRunner(env, hook=hook), spec, tmp_path, capsys, doc)
    assert code == 2
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(absorbed, "supersede")]
    again = merge_doc(env, vault, [(survivor, [absorbed])])
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, again)
    assert code == 0, err
    assert body_of(env, vault, survivor).count(f"## Merged from task/{absorbed}") == 1
    assert sidecar_of(env, vault, absorbed)["status"] == "superseded"


# --- a dependent whose own name fails the shape check ------------------------------------


def listing_with(other, entries):
    def hook(argv, stdin, runner):
        if listing_of(other, argv):
            return subprocess.CompletedProcess(argv, 0, json.dumps(entries), "")

    return hook


UNSHAPED = "bad name;rm"


@pytest.mark.parametrize("preview", [True, False])
def test_an_absorbed_follow_up_with_a_dependent_whose_name_fails_the_shape_check_is_not_mergeable(
    two_vault_lore_env, tmp_path, capsys, preview
):
    env, (vault, other) = two_vault_lore_env
    spec = make_spec(env, vault, "Parent spec", status="complete")
    survivor = make_followup(env, vault, "Survivor", status="open", spec=spec)
    absorbed = make_followup(env, vault, "Absorbed", status="open", spec=spec)
    hook = listing_with(other, [{"name": UNSHAPED, "status": "open", "depends-on": [absorbed]}])
    runner = EnvRunner(env, hook=hook)
    before = vault_snapshot(env, vault)
    doc = merge_doc(env, vault, [(survivor, [absorbed])])
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc, preview)
    assert (code, reason_of(err), subject(err)) == (1, "not-mergeable", [absorbed])
    assert "bad name" not in err
    assert out is None
    assert runner.writes() == [] and vault_snapshot(env, vault) == before


def test_a_dependent_with_an_unshaped_name_on_a_dropped_follow_up_is_counted_not_printed(
    two_vault_lore_env, tmp_path, capsys
):
    env, (vault, other) = two_vault_lore_env
    spec = make_spec(env, vault, "Parent spec", status="complete")
    target = make_followup(env, vault, "Target", status="open", spec=spec)
    hook = listing_with(other, [{"name": UNSHAPED, "status": "open", "depends-on": [target]}])
    code, out, err = run_dispose(
        EnvRunner(env, hook=hook), spec, tmp_path, capsys, file_for(env, vault, [target]), preview=True
    )
    assert code == 0, err
    assert out["dependents"][target] == []
    assert "bad name" not in json.dumps(out) + err


def test_a_follow_up_nobody_depends_on_stays_mergeable_beside_an_unshaped_unrelated_task(
    two_vault_lore_env, tmp_path, capsys
):
    env, (vault, other) = two_vault_lore_env
    spec = make_spec(env, vault, "Parent spec", status="complete")
    survivor = make_followup(env, vault, "Survivor", status="open", spec=spec)
    absorbed = make_followup(env, vault, "Absorbed", status="open", spec=spec)
    hook = listing_with(other, [{"name": UNSHAPED, "status": "open", "depends-on": ["somebody-else"]}])
    doc = merge_doc(env, vault, [(survivor, [absorbed])])
    code, out, err = run_dispose(EnvRunner(env, hook=hook), spec, tmp_path, capsys, doc)
    assert code == 0, err
    assert sidecar_of(env, vault, absorbed)["status"] == "superseded"


# --- the confirmation code ---------------------------------------------------------


def test_the_code_a_preview_prints_is_accepted_by_a_write_of_the_same_file_and_the_drops_land(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    doc = file_for(env, vault, pile[:2])
    code, preview, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc, preview=True)
    assert code == 0, err
    assert sidecar_of(env, vault, pile[0])["status"] == "open"
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc, confirm=preview["confirm"])
    assert code == 0, err
    assert sorted(d["name"] for d in out["dropped"]) == pile[:2]
    for name in pile[:2]:
        assert sidecar_of(env, vault, name)["status"] == "dropped"


def test_a_code_typed_in_upper_case_with_spaces_is_accepted(lore_env, spec, pile, tmp_path, capsys):
    env, vault = lore_env
    doc = file_for(env, vault, pile[:1])
    _, preview, _ = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc, preview=True)
    code, _, err = run_dispose(
        EnvRunner(env), spec, tmp_path, capsys, doc, confirm=f" {preview['confirm'].upper()} "
    )
    assert code == 0, err
    assert sidecar_of(env, vault, pile[0])["status"] == "dropped"


def test_a_write_with_no_code_is_refused_confirmation_missing_and_no_write_is_attempted(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    runner = EnvRunner(env)
    before = vault_snapshot(env, vault)
    code, _, err = run_dispose(runner, spec, tmp_path, capsys, file_for(env, vault, pile), confirm=None)
    assert (code, reason_of(err)) == (1, "confirmation-missing")
    assert runner.writes() == []
    assert vault_snapshot(env, vault) == before


def test_a_blank_code_is_confirmation_missing(lore_env, spec, pile, tmp_path, capsys):
    env, vault = lore_env
    runner = EnvRunner(env)
    code, _, err = run_dispose(runner, spec, tmp_path, capsys, file_for(env, vault, pile), confirm="  ")
    assert (code, reason_of(err)) == (1, "confirmation-missing")
    assert runner.writes() == []


def test_a_refused_write_makes_no_lore_call_at_all(lore_env, spec, pile, tmp_path, capsys):
    env, vault = lore_env
    runner = EnvRunner(env)
    doc = file_for(env, vault, pile)
    run_dispose(runner, spec, tmp_path, capsys, doc, confirm=None)
    run_dispose(runner, spec, tmp_path, capsys, doc, confirm="zzzz-not-it")
    assert runner.calls == []


@pytest.mark.parametrize("change", ["one-more-drop", "revision-moved", "reason", "included"])
def test_a_code_from_a_different_file_is_confirmation_mismatch_and_nothing_is_written(
    lore_env, spec, pile, tmp_path, capsys, change
):
    env, vault = lore_env
    shown = file_for(env, vault, pile[:1])
    _, preview, _ = run_dispose(EnvRunner(env), spec, tmp_path, capsys, shown, preview=True)
    sent = file_for(env, vault, pile[:1])
    if change == "one-more-drop":
        sent = file_for(env, vault, pile[:2])
    elif change == "revision-moved":
        sent["drops"][0]["revision"] = "not-the-shown-revision"
    elif change == "reason":
        sent["reason"] = "a different reason"
    else:
        sent["included"] = [pile[1]]
    runner = EnvRunner(env)
    before = vault_snapshot(env, vault)
    code, _, err = run_dispose(runner, spec, tmp_path, capsys, sent, confirm=preview["confirm"])
    assert (code, reason_of(err)) == (1, "confirmation-mismatch")
    assert runner.writes() == []
    assert vault_snapshot(env, vault) == before


def test_a_code_for_one_spec_does_not_confirm_the_same_file_under_another_spec(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    other = make_spec(env, vault, "Other spec", status="complete")
    doc = file_for(env, vault, pile[:1])
    _, preview, _ = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc, preview=True)
    code, _, err = run_dispose(EnvRunner(env), other, tmp_path, capsys, doc, confirm=preview["confirm"])
    assert (code, reason_of(err)) == (1, "confirmation-mismatch")


def test_the_preview_code_covers_the_included_list(lore_env, spec, pile, tmp_path, capsys):
    env, vault = lore_env
    doc = file_for(env, vault, pile[:1])
    with_included = {**doc, "included": [pile[1]]}
    _, plain, _ = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc, preview=True)
    _, shown, _ = run_dispose(EnvRunner(env), spec, tmp_path, capsys, with_included, preview=True)
    assert shown["included"] == [pile[1]]
    assert plain["included"] == []
    assert shown["confirm"] != plain["confirm"]


def test_an_empty_drops_and_merges_object_previews_with_a_code_and_a_write_with_it_writes_nothing_and_echoes_included(
    lore_env, spec, pile, tmp_path, capsys
):
    env, vault = lore_env
    doc = {"reason": "", "drops": [], "included": [pile[1], pile[0]]}
    runner = EnvRunner(env)
    code, preview, err = run_dispose(runner, spec, tmp_path, capsys, doc, preview=True)
    assert code == 0, err
    assert preview["drops"] == [] and preview["merges"] == []
    assert preview["confirm"]
    before = vault_snapshot(env, vault)
    code, out, err = run_dispose(runner, spec, tmp_path, capsys, doc, confirm=preview["confirm"])
    assert code == 0, err
    assert out["included"] == sorted(pile[:2])
    assert runner.writes() == []
    assert vault_snapshot(env, vault) == before


def test_a_nothing_to_do_write_without_the_code_is_still_refused(lore_env, spec, pile, tmp_path, capsys):
    env, vault = lore_env
    doc = {"reason": "", "drops": [], "included": [pile[0]]}
    code, _, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc, confirm=None)
    assert (code, reason_of(err)) == (1, "confirmation-missing")


@pytest.mark.parametrize(
    "doc",
    [
        {"reason": "", "drops": [], "included": "alpha"},
        {"reason": "", "drops": [], "included": [3]},
        {"reason": "", "drops": [], "included": ["a", "a"]},
        {"reason": "", "drops": [], "merges": [{}]},
        {"reason": "", "drops": {}},
        {"drops": []},
    ],
    ids=[
        "included-not-a-list",
        "included-not-strings",
        "included-repeats",
        "empty-merge-group",
        "drops-not-a-list",
        "no-reason",
    ],
)
def test_a_malformed_object_is_still_invalid_now_that_an_empty_one_is_accepted(
    lore_env, spec, tmp_path, capsys, doc
):
    env, vault = lore_env
    code, _, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc, preview=True)
    assert (code, reason_of(err)) == (2, "invalid-dispositions-file")


def test_an_included_name_failing_the_shape_check_is_refused_before_any_lore_call(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    runner = EnvRunner(env)
    doc = {"reason": "", "drops": [], "included": ["bad name"]}
    code, _, err = run_dispose(runner, spec, tmp_path, capsys, doc, preview=True)
    assert (code, reason_of(err)) == (2, "invalid-followup-name")
    assert runner.calls == []


def test_a_merge_write_needs_the_code_too(lore_env, spec, trio, tmp_path, capsys):
    env, vault = lore_env
    doc = merge_doc(env, vault, [(trio[0], [trio[1]])])
    runner = EnvRunner(env)
    code, _, err = run_dispose(runner, spec, tmp_path, capsys, doc, confirm=None)
    assert (code, reason_of(err)) == (1, "confirmation-missing")
    assert runner.writes() == []
    _, preview, _ = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc, preview=True)
    code, out, err = run_dispose(EnvRunner(env), spec, tmp_path, capsys, doc, confirm=preview["confirm"])
    assert code == 0, err
    assert out["merged"][0]["survivor"] == trio[0]


@pytest.mark.parametrize("change", ["intent", "absorbed-revision", "another-absorbed"])
def test_a_code_from_a_merge_that_differs_in_what_it_would_write_is_confirmation_mismatch(
    lore_env, spec, trio, tmp_path, capsys, change
):
    env, vault = lore_env
    shown = merge_doc(env, vault, [(trio[0], [trio[1]])])
    _, preview, _ = run_dispose(EnvRunner(env), spec, tmp_path, capsys, shown, preview=True)
    sent = merge_doc(env, vault, [(trio[0], [trio[1]])])
    absorbed = sent["merges"][0]["absorbed"]
    if change == "intent":
        absorbed[0]["intent"] = "a different intent"
    elif change == "absorbed-revision":
        absorbed[0]["revision"] = "not-the-shown-revision"
    else:
        sent = merge_doc(env, vault, [(trio[0], [trio[1], trio[2]])])
    runner = EnvRunner(env)
    code, _, err = run_dispose(runner, spec, tmp_path, capsys, sent, confirm=preview["confirm"])
    assert (code, reason_of(err)) == (1, "confirmation-mismatch")
    assert runner.writes() == []
