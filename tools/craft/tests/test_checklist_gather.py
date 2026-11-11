"""checklist_gather.py: the read-only check list across every configured vault.

Failure modes and rendering drive `main` with an injected `lore` runner. The one
seam test runs the script as a subprocess against the real `lore` CLI reading a
throwaway vault; its child environment is built from scratch so it cannot reach
the live install.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from _lore_runner import LoreRunner, seam_env

_LORE_TESTS = Path(__file__).resolve().parents[2] / "lore" / "tests"
_SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts"
_SCRIPT = _SCRIPTS / "checklist_gather.py"

_conftest_spec = importlib.util.spec_from_file_location(
    "lore_tests_conftest_for_checklist_gather", _LORE_TESTS / "conftest.py"
)
_lore_conftest = importlib.util.module_from_spec(_conftest_spec)
sys.modules["lore_tests_conftest_for_checklist_gather"] = _lore_conftest
_conftest_spec.loader.exec_module(_lore_conftest)

from lore_tests_conftest_for_checklist_gather import (  # noqa: E402,F401
    _allow_real_publish_spawn,
    _isolate_ambient_env,
    _publish_spawn_guard,
)

_script_spec = importlib.util.spec_from_file_location("checklist_gather", _SCRIPT)
cg = importlib.util.module_from_spec(_script_spec)
sys.modules["checklist_gather"] = cg
_script_spec.loader.exec_module(cg)

SECRET = "AKIAIOSFODNN7EXAMPLE"


def pending(cid="AC32", slice_="build-it", check="the banner shows", how="open the app",
            changed="no", history=(("2026-11-02", "waiting", "set by slice"),)):
    lines = [
        f"- **{cid}.** pending check — task/{slice_}",
        f"  - check: {check}",
        f"  - how: {how}",
        f"  - changed behaviour: {changed}",
    ]
    lines += [f"  - {d} {s}: {w}" for d, s, w in history]
    return "\n".join(lines) + "\n"


def set_aside(cid="AC17", waits="legal sign-off", history=(("2026-11-03", "waiting", "parked"),)):
    lines = [f"- **{cid}.** set aside", f"  - waits on: {waits}"]
    lines += [f"  - {d} {s}: {w}" for d, s, w in history]
    return "\n".join(lines) + "\n"


def spec_body(*entries, criteria=None):
    criteria = {"AC32": "Checkout collects billing details.", "AC17": "Invoices carry the tax number."} \
        if criteria is None else criteria
    out = "# Spec\n\n## Acceptance Criteria\n\n"
    out += "".join(f"- **{cid}.** {text}\n" for cid, text in criteria.items())
    if entries:
        out += "\n## Waiting\n\n" + "\n".join(entries)
    return out


def escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@pytest.fixture
def shelf(tmp_path):
    """`shelf("alpha", "beta")` -> an in-process lore (`LoreRunner`) over those empty
    vaults, each call under its own root."""
    made = []

    def make(*vaults):
        made.append(None)
        return LoreRunner(tmp_path / f"lore{len(made)}", vaults=vaults or ("alpha",))

    return make


def add(lore, vault, name, body, status="ready"):
    """Create a real spec `name` in `vault` holding `body`."""
    r = lore(["record", "create", "--kind", "spec", "--title", name, "--status", status,
              "--vault", vault], body)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == f"spec/{name}", r.stdout
    lore.calls.pop()
    return name


def vault_dir(lore, vault):
    return str(lore.vault_path(vault).resolve())


def hit(lore, vault, name, status="ready"):
    """The search hit real lore returns for spec `name` in `vault`."""
    path = vault_dir(lore, vault)
    return {"id": f"{path}/spec/{name}", "title": name, "kind": "spec", "status": status,
            "shared": 0, "vault": path, "snippet": ""}


def real_hits(lore):
    """What real lore's search returns for the script's query, uncounted among `.calls`."""
    r = lore(["search", "--json", "--limit", "1000", "--offset", "0", cg.SEARCH_QUERY])
    lore.calls.pop()
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hits"]


def _is_search_at(offset):
    def match(argv, _stdin):
        return argv[0] == "search" and argv[argv.index("--offset") + 1] == str(offset)

    return match


def search_pages(lore, pages, total=None, truncated=None, stale=False, config_stale=False):
    """Answer the script's paged `search` with `pages` (lists of hits; a str is served raw).
    The page read at offset N is the one that starts after N hits were served."""
    served = [h for page in pages if not isinstance(page, str) for h in page]
    total = len(served) if total is None else total
    offset = 0
    for page in pages:
        if isinstance(page, str):
            body = page
        else:
            cut = offset + len(page) < total if truncated is None else truncated
            body = json.dumps({"hits": page, "showing": len(page), "offset": offset,
                               "total": total, "stale": stale, "config_stale": config_stale,
                               "truncated": cut})
        lore.inject(_is_search_at(offset), returncode=0, stdout=body)
        offset += len(page) if not isinstance(page, str) else 0


def search_with(lore, *extra, **flags):
    """Real lore's hits, plus `extra` (hits real lore would not return), as one page."""
    search_pages(lore, [[*real_hits(lore), *extra]], **flags)


def show_fails(lore, name, returncode=1, stdout=""):
    lore.inject(lambda argv, _s: argv[:3] == ["record", "show", "--json"] and argv[-1] == f"spec/{name}",
                returncode=returncode, stdout=stdout, stderr="lore: SECRET-STDERR")


def words(lore):
    return [w for argv, _stdin in lore.calls for w in argv]


def make_shared(lore, vault):
    config = lore.config_home / "lore" / "config.json"
    doc = json.loads(config.read_text(encoding="utf-8"))
    for entry_ in doc["vaults"]:
        entry_["shared"] = entry_["name"] == vault
    config.write_text(json.dumps(doc), encoding="utf-8")


def go(capsys, lore):
    code = cg.main([], lore)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def could_not_read(label, key):
    return f"Could not read: {label} — {cg.REASONS[key]}"


# --- nothing waiting ----------------------------------------------------------


def test_nothing_waiting_is_one_line_and_one_entry_turns_it_into_the_count_line(capsys, shelf):
    lore = shelf("alpha", "beta")
    add(lore, "alpha", "one", spec_body())
    add(lore, "beta", "two", spec_body())
    code, out, _ = go(capsys, lore)
    assert (code, out) == (0, "Nothing is waiting on you.\n")

    add(lore, "beta", "three", spec_body(pending()))
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert out.splitlines()[0] == "1 check waiting, in 1 spec."
    assert "Nothing is waiting" not in out


# --- one entry ----------------------------------------------------------------


def test_one_pending_check_renders_the_design_doc_block(capsys, shelf):
    lore = shelf("trailhead")
    add(lore, "trailhead", "billing", spec_body(
        pending("AC32", "an-owner-subscribes", "Checkout asks for a billing address",
                "run a test-mode checkout", "no", (("2026-11-02", "waiting", "w"),))))
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert out == (
        "1 check waiting, in 1 spec.\n"
        "\n"
        "[1/1] Checkout collects billing details. (AC32)\n"
        "  spec: trailhead/billing\n"
        "  built by: task/an-owner-subscribes\n"
        "  set: 2026-11-02\n"
        "  check: Checkout asks for a billing address\n"
        "  how: run a test-mode checkout\n"
        "  changed existing behaviour: no\n"
    )


def test_one_set_aside_criterion_shows_what_it_waits_on_in_place_of_check(capsys, shelf):
    lore = shelf("trailhead")
    add(lore, "trailhead", "billing", spec_body(set_aside("AC17", "the Stripe Tax registration")))
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert out == (
        "1 set-aside criterion waiting, in 1 spec.\n"
        "\n"
        "[1/1] Invoices carry the tax number. (AC17)\n"
        "  spec: trailhead/billing\n"
        "  set: 2026-11-03\n"
        "  waits on: the Stripe Tax registration\n"
    )


def test_changed_behaviour_yes_is_shown(capsys, shelf):
    lore = shelf("a")
    add(lore, "a", "s", spec_body(pending(changed="yes")))
    _, out, _ = go(capsys, lore)
    assert "  changed existing behaviour: yes\n" in out


def test_a_multi_line_criterion_is_shown_on_one_line(capsys, shelf):
    lore = shelf("a")
    add(lore, "a", "s", spec_body(pending(), criteria={"AC32": "First part\n  and the second part."}))
    _, out, _ = go(capsys, lore)
    assert "[1/1] First part and the second part. (AC32)\n" in out


# --- many ---------------------------------------------------------------------


def test_many_entries_count_each_kind_and_leave_out_a_kind_with_zero(capsys, shelf):
    lore = shelf("alpha", "beta")
    add(lore, "alpha", "one", spec_body(pending("AC32"), pending("AC33", slice_="s2"),
                                       criteria={"AC32": "a", "AC33": "b"}))
    add(lore, "alpha", "two", spec_body(set_aside("AC17")))
    add(lore, "beta", "one", spec_body(pending("AC32"), pending("AC34", slice_="s3"),
                                      criteria={"AC32": "a", "AC34": "d"}))
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert out.splitlines()[0] == "4 checks and 1 set-aside criterion waiting, across 3 specs."

    only_checks = shelf("a")
    add(only_checks, "a", "s", spec_body(pending("AC32"), pending("AC33", slice_="s2"),
                                        criteria={"AC32": "a", "AC33": "b"}))
    assert go(capsys, only_checks)[1].splitlines()[0] == "2 checks waiting, in 1 spec."

    only_aside = shelf("a")
    add(only_aside, "a", "s", spec_body(set_aside("AC17"), set_aside("AC18"),
                                       criteria={"AC17": "a", "AC18": "b"}))
    assert go(capsys, only_aside)[1].splitlines()[0] == "2 set-aside criteria waiting, in 1 spec."


def test_one_criterion_under_two_building_slices_renders_both_entries(capsys, shelf):
    lore = shelf("alpha")
    add(lore, "alpha", "s", spec_body(
        pending("AC32", slice_="first", check="first attempt"),
        pending("AC32", slice_="second", check="second attempt"),
    ))
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert out.splitlines()[0] == "2 checks waiting, in 1 spec."
    assert "first attempt" in out and "second attempt" in out
    assert out.count("Checkout collects billing details.") == 2


def test_blocks_are_numbered_and_ordered_by_vault_then_spec_then_set_date(capsys, shelf):
    lore = shelf("beta", "alpha")
    add(lore, "beta", "aaa", spec_body(pending("AC32", check="beta-aaa")))
    add(lore, "alpha", "zzz", spec_body(pending("AC32", check="alpha-zzz")))
    add(lore, "alpha", "mmm", spec_body(
        pending("AC32", check="alpha-mmm-late", history=(("2026-11-05", "waiting", "w"),)),
        pending("AC33", slice_="s2", check="alpha-mmm-early",
                history=(("2026-11-01", "waiting", "w"),)),
        pending("AC34", slice_="s3", check="alpha-mmm-reset",
                history=(("2026-10-01", "waiting", "w"), ("2026-10-02", "failed", "no"),
                         ("2026-11-03", "waiting", "again"))),
        criteria={"AC32": "a", "AC33": "b", "AC34": "c"}))
    _, out, _ = go(capsys, lore)
    blocks = [ln for ln in out.splitlines() if ln.startswith("[")]
    assert [b.split(" ")[0] for b in blocks] == ["[1/5]", "[2/5]", "[3/5]", "[4/5]", "[5/5]"]
    order = [ln.split("check: ")[1] for ln in out.splitlines() if ln.startswith("  check: ")]
    assert order == ["alpha-mmm-early", "alpha-mmm-reset", "alpha-mmm-late", "alpha-zzz", "beta-aaa"]


def test_two_vaults_holding_the_same_spec_name_stay_distinguishable(capsys, shelf):
    lore = shelf("alpha", "beta")
    add(lore, "alpha", "twin", spec_body(pending()))
    add(lore, "beta", "twin", spec_body(pending()))
    _, out, _ = go(capsys, lore)
    assert "  spec: alpha/twin\n" in out and "  spec: beta/twin\n" in out


# --- status filter ------------------------------------------------------------


@pytest.mark.parametrize("latest", ["passed", "failed", "released"])
def test_an_entry_whose_latest_line_is_not_waiting_is_off_the_list(capsys, latest, shelf):
    history = (("2026-11-02", "waiting", "w"), ("2026-11-04", latest, "done"))
    lore = shelf("a")
    add(lore, "a", "s", spec_body(pending(history=history)))
    assert go(capsys, lore)[1] == "Nothing is waiting on you.\n"

    again = history + (("2026-11-06", "waiting", "back on"),)
    lore = shelf("a")
    add(lore, "a", "s", spec_body(pending(history=again)))
    _, out, _ = go(capsys, lore)
    assert out.splitlines()[0] == "1 check waiting, in 1 spec."
    assert "  set: 2026-11-06\n" in out


# --- only ready specs ---------------------------------------------------------


def test_a_non_ready_spec_is_absent_whatever_the_search_hit_said(capsys, shelf):
    lore = shelf("a")
    add(lore, "a", "draft-one", spec_body(pending()), status="draft")
    add(lore, "a", "broken-draft", "## Waiting\n\nprose\n", status="draft")
    search_pages(lore, [[hit(lore, "a", "draft-one"), hit(lore, "a", "broken-draft")]])  # hits say ready
    assert go(capsys, lore)[1] == "Nothing is waiting on you.\n"


def test_the_shown_sidecar_status_wins_over_the_search_hit(capsys, shelf):
    lore = shelf("a")
    add(lore, "a", "now-ready", spec_body(pending()), status="ready")
    search_pages(lore, [[hit(lore, "a", "now-ready", status="draft")]])
    assert go(capsys, lore)[1].splitlines()[0] == "1 check waiting, in 1 spec."


# --- unreadable ---------------------------------------------------------------


def good_spec(lore, vault="alpha"):
    add(lore, vault, "good", spec_body(pending(check="still-rendered")))


def assert_listed_after_the_count_line(out, line):
    lines = out.splitlines()
    assert lines[0] == "1 check waiting, in 1 spec."
    assert lines[1] == line
    assert "  check: still-rendered" in lines


def test_an_unparseable_section_is_unreadable_while_other_specs_render(capsys, shelf):
    lore = shelf("alpha")
    good_spec(lore)
    add(lore, "alpha", "bad", "## Waiting\n\nloose prose\n")
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert "unparseable" in cg.REASONS
    assert_listed_after_the_count_line(out, could_not_read("alpha/bad", "unparseable"))


@pytest.mark.parametrize("raw", [1, "not json at all", json.dumps({"kind": "spec"}),
                                 json.dumps({"body": 5, "sidecar": {"status": "ready"}}),
                                 json.dumps({"body": "x", "sidecar": {}})])
def test_record_show_failing_or_not_json_is_unreadable(capsys, shelf, raw):
    lore = shelf("alpha")
    good_spec(lore)
    add(lore, "alpha", "bad", spec_body())
    if isinstance(raw, int):
        show_fails(lore, "bad", returncode=raw)
    else:
        show_fails(lore, "bad", returncode=0, stdout=raw)
    code, out, err = go(capsys, lore)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("alpha/bad", "show-failed"))
    assert "SECRET-STDERR" not in out + err


def test_a_vault_name_failing_the_shape_check_is_listed_and_never_passed_to_lore(capsys, shelf):
    lore = shelf("alpha", "scratch pad")
    good_spec(lore)
    add(lore, "scratch pad", "x", spec_body(pending()))
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("vault scratch pad", "unsafe-name"))
    assert not any("scratch" in w for w in words(lore))


def test_a_spec_name_failing_the_shape_check_is_listed_and_never_passed_to_lore(capsys, shelf):
    lore = shelf("alpha")
    good_spec(lore)
    path = vault_dir(lore, "alpha")
    search_with(
        lore,
        {"id": f"{path}/spec/evil name", "kind": "spec", "status": "ready", "vault": path},
        {"id": f"{path}/spec/--vault", "kind": "spec", "status": "ready", "vault": path},
    )
    code, out, _ = go(capsys, lore)
    assert code == 0
    lines = out.splitlines()
    assert could_not_read("alpha/evil name", "unsafe-name") in lines
    assert could_not_read("alpha/--vault", "unsafe-name") in lines
    assert "  check: still-rendered" in lines
    assert not any("evil" in w or w in ("spec/--vault",) for w in words(lore))


def test_a_search_hit_at_an_unconfigured_vault_path_is_unreadable(capsys, shelf):
    lore = shelf("alpha")
    good_spec(lore)
    search_with(lore, {"id": "/elsewhere/ghost/spec/x", "kind": "spec", "status": "ready",
                       "vault": "/elsewhere/ghost"})
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("vault ghost", "not-configured"))
    assert not any("ghost" in w or w == "spec/x" for w in words(lore))


@pytest.mark.parametrize("flag", ["stale", "config_stale"])
def test_a_stale_search_adds_the_stale_index_line(capsys, flag, shelf):
    lore = shelf("alpha")
    good_spec(lore)
    search_with(lore, **{flag: True})
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("lore's search", "stale"))
    assert out.count("Could not read:") == 1


def test_the_unreadable_lines_use_the_design_docs_words(capsys, shelf):
    lore = shelf("alpha")
    good_spec(lore)
    add(lore, "alpha", "some-spec", "## Waiting\n\nprose\n")
    search_with(lore, stale=True)
    _, out, _ = go(capsys, lore)
    assert out.splitlines()[1:3] == [
        "Could not read: alpha/some-spec — its waiting section could not be parsed",
        "Could not read: lore's search — its index is stale, so some ready specs may be missing; "
        "run lore reindex",
    ]


def test_nothing_readable_waiting_while_something_is_unreadable(capsys, shelf):
    lore = shelf("alpha")
    add(lore, "alpha", "ok", spec_body())
    add(lore, "alpha", "bad", spec_body())
    show_fails(lore, "bad")
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert out == (
        "Nothing waiting in the specs that could be read.\n"
        "Could not read: alpha/bad — lore could not show it\n"
    )
    assert "Nothing is waiting on you." not in out


# --- hostile text -------------------------------------------------------------


def test_hostile_text_is_cleaned_before_it_reaches_the_output(capsys, shelf):
    hostile = f"see \x1b[31mred\x1b[0m ‮evil {SECRET} end"
    lore = shelf("alpha")
    add(lore, "alpha", "s", spec_body(
        pending(check=hostile, how=hostile),
        set_aside("AC17", waits=hostile),
        criteria={"AC32": hostile, "AC17": hostile}))
    code, out, err = go(capsys, lore)
    assert code == 0
    assert "\x1b" not in out and "‮" not in out
    assert SECRET not in out
    assert out.count("[REDACTED]") >= 5
    assert "  check: see [31mred[0m evil [REDACTED] end\n" in out


def test_overlong_text_is_cut_visibly_but_a_real_check_is_shown_whole(capsys, shelf):
    real = "check the banner shows once. " * 12
    huge = "open the app and look. " * 220
    lore = shelf("a")
    add(lore, "a", "s", spec_body(pending(check=real, how=huge)))
    _, out, _ = go(capsys, lore)
    assert f"  check: {real.strip()}\n" in out
    how = next(ln for ln in out.splitlines() if ln.startswith("  how: "))
    assert how.endswith("…") and len(how) < len("  how: ") + len(huge)


# --- paging -------------------------------------------------------------------


def paged(shelf):
    """One real quiet spec and one real waiting spec, served as two pages of one."""
    lore = shelf("alpha")
    add(lore, "alpha", "quiet-00", spec_body())
    add(lore, "alpha", "zz-last", spec_body(pending(check="on-page-two")))
    search_pages(lore, [[hit(lore, "alpha", "quiet-00")], [hit(lore, "alpha", "zz-last")]])
    return lore


def test_a_waiting_entry_on_the_second_page_is_listed(capsys, shelf):
    lore = paged(shelf)
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert out.splitlines()[0] == "1 check waiting, in 1 spec."
    assert "  check: on-page-two" in out.splitlines()
    offsets = [a[a.index("--offset") + 1] for a, _ in lore.calls if a[0] == "search"]
    assert offsets == ["0", "1"]


def test_a_search_still_truncated_after_the_last_page_names_the_cut_off(capsys, shelf):
    lore = shelf("alpha")
    add(lore, "alpha", "quiet", spec_body())
    search_with(lore, truncated=True)
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert out == (
        "Nothing waiting in the specs that could be read.\n"
        + could_not_read("lore's search", "cut-off") + "\n"
    )


def test_fewer_hits_than_total_names_the_cut_off(capsys, shelf):
    lore = shelf("alpha")
    add(lore, "alpha", "quiet", spec_body(pending()))
    search_pages(lore, [real_hits(lore), []], total=30, truncated=False)
    code, out, _ = go(capsys, lore)
    lines = out.splitlines()
    assert lines[0] == "1 check waiting, in 1 spec."
    assert lines[1] == could_not_read("lore's search", "cut-off")


@pytest.mark.parametrize(
    "kwargs",
    [{"returncode": 1, "stderr": "lore: SECRET-STDERR"},
     {"returncode": 0, "stdout": "not json"},
     {"returncode": 0, "stdout": json.dumps({"hits": "no"})}],
)
def test_a_search_that_cannot_be_read_never_reads_as_nothing_waiting(capsys, kwargs, shelf):
    lore = shelf("alpha")
    lore.inject(["search"], **kwargs)
    code, out, err = go(capsys, lore)
    assert code == 0
    assert out == (
        "Nothing waiting in the specs that could be read.\n"
        + could_not_read("lore's search", "search-unreadable") + "\n"
    )
    assert "SECRET-STDERR" not in out + err


def test_an_unreadable_search_is_worded_as_the_design_doc_words_it(capsys, shelf):
    lore = shelf("alpha")
    lore.inject(["search"], returncode=1, stderr="lore: SECRET-STDERR")
    _, out, _ = go(capsys, lore)
    assert out.splitlines()[1] == (
        "Could not read: lore's search — it could not be run or read, "
        "so some ready specs may be missing"
    )


def test_a_page_that_cannot_be_read_after_a_good_one_is_unreadable_not_cut_off(capsys, shelf):
    lore = shelf("alpha")
    add(lore, "alpha", "one", spec_body(pending()))
    add(lore, "alpha", "two", spec_body())
    search_pages(lore, [[hit(lore, "alpha", "one")], "not json"], total=2)
    code, out, _ = go(capsys, lore)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "1 check waiting, in 1 spec."
    assert lines[1] == could_not_read("lore's search", "search-unreadable")
    assert out.count("Could not read:") == 1


def test_a_hit_repeated_on_a_later_page_is_rendered_once_and_names_the_cut_off(capsys, shelf):
    lore = shelf("alpha")
    add(lore, "alpha", "one", spec_body(pending()))
    one = hit(lore, "alpha", "one")
    search_pages(lore, [[one], [dict(one)]])
    code, out, _ = go(capsys, lore)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "1 check waiting, in 1 spec."
    assert lines[1] == could_not_read("lore's search", "cut-off")
    assert out.count("[1/1]") == 1
    assert "[2/" not in out


def test_a_repeated_hit_with_every_distinct_record_read_is_not_a_cut_off(capsys, shelf):
    lore = shelf("alpha")
    add(lore, "alpha", "a", spec_body(pending()))
    add(lore, "alpha", "b", spec_body())
    add(lore, "alpha", "c", spec_body())
    a, b, c = (hit(lore, "alpha", n) for n in "abc")
    search_pages(lore, [[a, b], [dict(b), c]], total=3)
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert out.splitlines()[0] == "1 check waiting, in 1 spec."
    assert out.count("[1/1]") == 1
    assert "Could not read" not in out


def test_a_spec_edited_between_pages_that_is_never_returned_names_the_cut_off(capsys, shelf):
    lore = shelf("alpha")
    add(lore, "alpha", "a", spec_body())
    add(lore, "alpha", "b", spec_body())
    add(lore, "alpha", "c", spec_body(pending()))
    a, b = hit(lore, "alpha", "a"), hit(lore, "alpha", "b")
    search_pages(lore, [[a, b], [b]], total=3)  # `c` moved to the front between the pages
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert "Nothing is waiting on you." not in out
    assert out.splitlines()[0] == "Nothing waiting in the specs that could be read."
    assert could_not_read("lore's search", "cut-off") in out.splitlines()


def test_the_criterion_words_do_not_carry_the_verified_by_suffix(capsys, shelf):
    lore = shelf("a")
    add(lore, "a", "s", spec_body(pending(), criteria={
        "AC32": "Checkout collects billing details. *Verified by: automated assertion.*"}))
    _, out, _ = go(capsys, lore)
    assert "[1/1] Checkout collects billing details. (AC32)\n" in out
    assert "Verified" not in out


def test_a_wrapped_verified_by_suffix_is_dropped_from_the_criterion_words(capsys, shelf):
    lore = shelf("a")
    add(lore, "a", "s", spec_body(pending(), criteria={
        "AC32": "Checkout collects details.\n  *Verified by: automated\n  assertion.*"}))
    _, out, _ = go(capsys, lore)
    assert "[1/1] Checkout collects details. (AC32)\n" in out


def _gather_into(queue, lore):
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cg.main([], lore)
    queue.put((code, buf.getvalue()))


def test_an_unclosed_verified_by_with_a_huge_whitespace_run_still_lists_the_entry(shelf):
    import multiprocessing

    ctx = multiprocessing.get_context("fork")
    lore = shelf("a")
    add(lore, "a", "s", spec_body(pending(), criteria={
        "AC32": "Checkout collects billing details. *Verified by:" + " " * 20000}))
    queue = ctx.Queue()
    child = ctx.Process(target=_gather_into, args=(queue, lore))
    child.start()
    child.join(10)
    hung = child.is_alive()
    if hung:
        child.terminate()
        child.join()
    assert not hung, "gathering did not finish within 10s: verified-by stripping is not linear"
    code, out = queue.get(timeout=5)
    assert code == 0
    assert "(AC32)" in out
    assert "Checkout collects billing details." in out


def test_a_hit_whose_id_is_not_under_its_vaults_spec_directory_is_a_search_problem(capsys, shelf):
    lore = shelf("alpha")
    good_spec(lore)
    path = vault_dir(lore, "alpha")
    search_with(lore, {"id": f"{path}/note/x", "kind": "spec", "status": "ready", "vault": path})
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("lore's search", "search-unreadable"))
    assert out.count("Could not read:") == 1
    assert not any("note" in w for w in words(lore))


@pytest.mark.parametrize(
    "bad",
    [lambda p: "junk", lambda p: 7, lambda p: {"id": f"{p}/spec/x"}, lambda p: {"vault": p},
     lambda p: {"id": 3, "vault": p}],
)
def test_a_hit_that_is_not_a_dict_or_misses_a_field_is_a_search_problem(capsys, bad, shelf):
    lore = shelf("alpha")
    good_spec(lore)
    search_with(lore, bad(vault_dir(lore, "alpha")))
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("lore's search", "search-unreadable"))
    assert out.count("Could not read:") == 1


# --- shared layer -------------------------------------------------------------


def test_a_shared_layer_spec_is_unescaped_exactly_and_listed(capsys, shelf):
    check = "show <b>AT&T</b> a > b, literal &amp; &lt;i&gt; and &#27; and raw \x1b[31m end"
    body = spec_body(pending(check=check))
    assert escape(body) != body
    lore = shelf("home", "team")
    make_shared(lore, "team")
    add(lore, "team", "shared-one", body)
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert out.splitlines()[0] == "1 check waiting, in 1 spec."
    assert "  check: show <b>AT&T</b> a > b, literal &amp; &lt;i&gt; and &#27; and raw [31m end\n" in out
    assert "\x1b" not in out


# --- vault alias --------------------------------------------------------------


def test_two_configured_vaults_sharing_a_path_make_a_hit_there_unreadable(capsys, shelf):
    lore = shelf("gamma")
    shared = lore.root / "shared"
    shared.mkdir()
    lore.write_config([("alpha", "default", shared), ("beta", "team", shared),
                       ("gamma", "team", lore.vault_path("gamma"))])
    add(lore, "gamma", "good", spec_body(pending(check="still-rendered")))
    add(lore, "alpha", "x", spec_body(pending()))
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("vault shared", "not-configured"))
    assert not any(a[:2] == ["record", "show"] and "spec/x" in a for a, _ in lore.calls)


def test_a_path_shared_by_an_unsafe_and_a_safe_vault_name_is_not_configured(capsys, shelf):
    lore = shelf("gamma")
    shared = lore.root / "shared"
    shared.mkdir()
    lore.write_config([("bad name", "default", shared), ("alpha", "team", shared),
                       ("gamma", "team", lore.vault_path("gamma"))])
    add(lore, "gamma", "good", spec_body(pending(check="still-rendered")))
    add(lore, "alpha", "x", spec_body(pending()))
    code, out, _ = go(capsys, lore)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "1 check waiting, in 1 spec."
    assert could_not_read("vault shared", "not-configured") in lines
    assert "  check: still-rendered" in lines
    assert not any(a[:2] == ["record", "show"] and "spec/x" in a for a, _ in lore.calls)


# --- criterion lookup ---------------------------------------------------------


def test_an_undeclared_criterion_shows_the_not_found_marker(capsys, shelf):
    lore = shelf("a")
    add(lore, "a", "s", spec_body(pending("AC99"), criteria={"AC1": "other"}))
    _, out, _ = go(capsys, lore)
    assert "[1/1] (criterion text not found) (AC99)\n" in out


def test_a_spec_with_no_acceptance_criteria_heading_still_lists_its_entries(capsys, shelf):
    lore = shelf("a")
    add(lore, "a", "s", "# Spec\n\n## Waiting\n\n" + pending())
    _, out, _ = go(capsys, lore)
    assert "[1/1] (criterion text not found) (AC32)\n" in out


def test_a_duplicate_acceptance_criteria_heading_makes_the_spec_unreadable(capsys, shelf):
    body = spec_body(pending()) + "\n## Acceptance Criteria\n\n- **AC32.** again\n"
    lore = shelf("alpha")
    good_spec(lore)
    add(lore, "alpha", "dup", body)
    code, out, _ = go(capsys, lore)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("alpha/dup", "unparseable"))


# --- hard failure -------------------------------------------------------------


def hard(capsys, lore):
    code, out, err = go(capsys, lore)
    assert out == ""
    assert "SECRET-STDERR" not in err
    lines = [ln for ln in err.splitlines() if ln.startswith("reason-code:")]
    assert len(lines) == 1
    reason = lines[0].removeprefix("reason-code: ")
    assert reason in cg.REASON_CODES
    return code, reason


def test_no_configured_vaults_exits_2_with_its_reason_and_empty_stdout(capsys, shelf):
    """Hostile input: real lore never answers `vault ls` with exit 0 and no lines, so the reply is injected."""
    lore = shelf("a")
    lore.inject(["vault", "ls"], returncode=0, stdout="")
    assert hard(capsys, lore) == (2, "no-vaults")


def test_lore_missing_exits_2_with_its_reason_and_empty_stdout(capsys, shelf):
    lore = shelf("a")
    lore.inject([], raises=FileNotFoundError("lore"))
    assert hard(capsys, lore) == (2, "lore-missing")


def test_a_timeout_exits_2_with_its_reason_and_empty_stdout(capsys, shelf):
    lore = shelf("a")
    lore.inject([], raises=subprocess.TimeoutExpired(["lore"], 30))
    assert hard(capsys, lore) == (2, "timeout")


def test_vault_ls_failing_exits_2_with_its_reason_and_empty_stdout(capsys, shelf):
    lore = shelf("a")
    lore.inject(["vault", "ls"], returncode=1, stderr="lore: SECRET-STDERR")
    assert hard(capsys, lore) == (2, "lore-failed")


def test_a_timeout_after_the_vault_listing_still_exits_2_with_empty_stdout(capsys, shelf):
    lore = shelf("a")
    add(lore, "a", "s", spec_body(pending()))
    lore.inject(["record", "show"], raises=subprocess.TimeoutExpired(["lore"], 30))
    assert hard(capsys, lore) == (2, "timeout")


def test_an_unexpected_exception_exits_2_as_internal_error(capsys, shelf):
    lore = shelf("a")
    lore.inject([], raises=RuntimeError("boom SECRET-STDERR"))
    assert hard(capsys, lore) == (2, "internal-error")


def test_an_argument_is_refused_as_invalid_arguments(capsys, shelf):
    code = cg.main(["surprise"], shelf("a"))
    cap = capsys.readouterr()
    assert code == 2 and cap.out == "" and "reason-code: invalid-arguments" in cap.err


# --- seam: the script as a subprocess against the real lore CLI ---------------


def test_the_script_lists_a_waiting_entry_through_the_real_lore_cli(  # inert-gate: allow seam smoke
    tmp_path,
):
    lore = LoreRunner(tmp_path)
    body = spec_body(pending("AC32", "seam-slice", "see the seam", "run it"))
    name = add(lore, "default", "seam-spec", body)
    r = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True,
                       env=seam_env(lore, tmp_path))
    assert r.returncode == 0, r.stderr
    assert r.stdout == (
        "1 check waiting, in 1 spec.\n"
        "\n"
        "[1/1] Checkout collects billing details. (AC32)\n"
        f"  spec: default/{name}\n"
        "  built by: task/seam-slice\n"
        "  set: 2026-11-02\n"
        "  check: see the seam\n"
        "  how: run it\n"
        "  changed existing behaviour: no\n"
    )
