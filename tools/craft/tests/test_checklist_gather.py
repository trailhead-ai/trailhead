"""checklist_gather.py: the read-only check list across every configured vault.

Failure modes and rendering drive `main` with an injected `lore` runner. The one
seam test runs the script as a subprocess against the real `lore` CLI reading a
throwaway vault; its child environment is built from scratch so it cannot reach
the live install.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
    make_vault,
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


def shown(body, status="ready", layer="personal"):
    return json.dumps({
        "body": body, "kind": "spec", "layer": layer, "name": "x", "record_id": "spec/x",
        "revision": "r", "sidecar": {"kind": "spec", "status": status},
    })


class FakeLore:
    """Answers `vault ls`, `search --json` and `record show --json --vault V spec/N`."""

    def __init__(self, vaults=("alpha",), page_cap=20, total=None, truncated=None,
                 stale=False, config_stale=False, vault_ls_rc=0, search_rc=0, search_raw=None):
        self.vaults = [(v, f"/v/{v}") if isinstance(v, str) else v for v in vaults]
        self.specs = {}
        self.hits = []
        self.page_cap = page_cap
        self.total = total
        self.truncated = truncated
        self.stale = stale
        self.config_stale = config_stale
        self.vault_ls_rc = vault_ls_rc
        self.search_rc = search_rc
        self.search_raw = search_raw
        self.calls = []

    def path(self, vault):
        return dict(self.vaults)[vault]

    def add(self, vault, name, body=None, status="ready", layer="personal", raw=None,
            hit_vault_path=None, hit=True):
        """`raw` is the exact `record show` reply: a str (stdout) or an int (a failing rc)."""
        self.specs[(vault, name)] = raw if raw is not None else shown(body, status, layer)
        if hit:
            vpath = hit_vault_path or self.path(vault)
            self.hits.append({"id": f"{vpath}/spec/{name}", "kind": "spec", "status": "ready",
                              "vault": vpath})

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:2] == ["vault", "ls"]:
            out = "".join(f"{n}\tteam\t{p}\t[(all kinds)]\n" for n, p in self.vaults)
            return subprocess.CompletedProcess(argv, self.vault_ls_rc, out, "lore: SECRET-STDERR")
        if argv[0] == "search":
            if self.search_rc:
                return subprocess.CompletedProcess(argv, self.search_rc, "", "lore: SECRET-STDERR")
            if self.search_raw is not None:
                return subprocess.CompletedProcess(argv, 0, self.search_raw, "")
            limit = int(argv[argv.index("--limit") + 1])
            offset = int(argv[argv.index("--offset") + 1])
            page = self.hits[offset : offset + min(limit, self.page_cap)]
            total = len(self.hits) if self.total is None else self.total
            truncated = offset + len(page) < total if self.truncated is None else self.truncated
            doc = {"hits": page, "showing": len(page), "offset": offset, "total": total,
                   "stale": self.stale, "config_stale": self.config_stale, "truncated": truncated}
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")
        if argv[:3] == ["record", "show", "--json"]:
            vault = argv[argv.index("--vault") + 1]
            name = argv[-1].removeprefix("spec/")
            got = self.specs.get((vault, name))
            if got is None or isinstance(got, int):
                return subprocess.CompletedProcess(argv, got or 1, "", "lore: SECRET-STDERR")
            return subprocess.CompletedProcess(argv, 0, got, "")
        raise AssertionError(f"unexpected lore call {argv}")

    def argv_words(self):
        return [w for argv in self.calls for w in argv]


def go(capsys, fake):
    code = cg.main([], fake)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def could_not_read(label, key):
    return f"Could not read: {label} — {cg.REASONS[key]}"


# --- nothing waiting ----------------------------------------------------------


def test_nothing_waiting_is_one_line_and_one_entry_turns_it_into_the_count_line(capsys):
    fake = FakeLore(["alpha", "beta"])
    fake.add("alpha", "one", spec_body())
    fake.add("beta", "two", spec_body())
    code, out, _ = go(capsys, fake)
    assert (code, out) == (0, "Nothing is waiting on you.\n")

    fake.add("beta", "three", spec_body(pending()))
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert out.splitlines()[0] == "1 check waiting, in 1 spec."
    assert "Nothing is waiting" not in out


# --- one entry ----------------------------------------------------------------


def test_one_pending_check_renders_the_design_doc_block(capsys):
    fake = FakeLore(["trailhead"])
    fake.add("trailhead", "billing", spec_body(
        pending("AC32", "an-owner-subscribes", "Checkout asks for a billing address",
                "run a test-mode checkout", "no", (("2026-11-02", "waiting", "w"),))))
    code, out, _ = go(capsys, fake)
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


def test_one_set_aside_criterion_shows_what_it_waits_on_in_place_of_check(capsys):
    fake = FakeLore(["trailhead"])
    fake.add("trailhead", "billing", spec_body(set_aside("AC17", "the Stripe Tax registration")))
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert out == (
        "1 set-aside criterion waiting, in 1 spec.\n"
        "\n"
        "[1/1] Invoices carry the tax number. (AC17)\n"
        "  spec: trailhead/billing\n"
        "  set: 2026-11-03\n"
        "  waits on: the Stripe Tax registration\n"
    )


def test_changed_behaviour_yes_is_shown(capsys):
    fake = FakeLore(["a"])
    fake.add("a", "s", spec_body(pending(changed="yes")))
    _, out, _ = go(capsys, fake)
    assert "  changed existing behaviour: yes\n" in out


def test_a_multi_line_criterion_is_shown_on_one_line(capsys):
    fake = FakeLore(["a"])
    fake.add("a", "s", spec_body(pending(), criteria={"AC32": "First part\n  and the second part."}))
    _, out, _ = go(capsys, fake)
    assert "[1/1] First part and the second part. (AC32)\n" in out


# --- many ---------------------------------------------------------------------


def test_many_entries_count_each_kind_and_leave_out_a_kind_with_zero(capsys):
    fake = FakeLore(["alpha", "beta"])
    fake.add("alpha", "one", spec_body(pending("AC32"), pending("AC33", slice_="s2"),
                                       criteria={"AC32": "a", "AC33": "b"}))
    fake.add("alpha", "two", spec_body(set_aside("AC17")))
    fake.add("beta", "one", spec_body(pending("AC32"), pending("AC34", slice_="s3"),
                                      criteria={"AC32": "a", "AC34": "d"}))
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert out.splitlines()[0] == "4 checks and 1 set-aside criterion waiting, across 3 specs."

    only_checks = FakeLore(["a"])
    only_checks.add("a", "s", spec_body(pending("AC32"), pending("AC33", slice_="s2"),
                                        criteria={"AC32": "a", "AC33": "b"}))
    assert go(capsys, only_checks)[1].splitlines()[0] == "2 checks waiting, in 1 spec."

    only_aside = FakeLore(["a"])
    only_aside.add("a", "s", spec_body(set_aside("AC17"), set_aside("AC18"),
                                       criteria={"AC17": "a", "AC18": "b"}))
    assert go(capsys, only_aside)[1].splitlines()[0] == "2 set-aside criteria waiting, in 1 spec."


def test_blocks_are_numbered_and_ordered_by_vault_then_spec_then_set_date(capsys):
    fake = FakeLore(["beta", "alpha"])
    fake.add("beta", "aaa", spec_body(pending("AC32", check="beta-aaa")))
    fake.add("alpha", "zzz", spec_body(pending("AC32", check="alpha-zzz")))
    fake.add("alpha", "mmm", spec_body(
        pending("AC32", check="alpha-mmm-late", history=(("2026-11-05", "waiting", "w"),)),
        pending("AC33", slice_="s2", check="alpha-mmm-early",
                history=(("2026-11-01", "waiting", "w"),)),
        pending("AC34", slice_="s3", check="alpha-mmm-reset",
                history=(("2026-10-01", "waiting", "w"), ("2026-10-02", "failed", "no"),
                         ("2026-11-03", "waiting", "again"))),
        criteria={"AC32": "a", "AC33": "b", "AC34": "c"}))
    _, out, _ = go(capsys, fake)
    blocks = [ln for ln in out.splitlines() if ln.startswith("[")]
    assert [b.split(" ")[0] for b in blocks] == ["[1/5]", "[2/5]", "[3/5]", "[4/5]", "[5/5]"]
    order = [ln.split("check: ")[1] for ln in out.splitlines() if ln.startswith("  check: ")]
    assert order == ["alpha-mmm-early", "alpha-mmm-reset", "alpha-mmm-late", "alpha-zzz", "beta-aaa"]


def test_two_vaults_holding_the_same_spec_name_stay_distinguishable(capsys):
    fake = FakeLore(["alpha", "beta"])
    fake.add("alpha", "twin", spec_body(pending()))
    fake.add("beta", "twin", spec_body(pending()))
    _, out, _ = go(capsys, fake)
    assert "  spec: alpha/twin\n" in out and "  spec: beta/twin\n" in out


# --- status filter ------------------------------------------------------------


@pytest.mark.parametrize("latest", ["passed", "failed", "released"])
def test_an_entry_whose_latest_line_is_not_waiting_is_off_the_list(capsys, latest):
    history = (("2026-11-02", "waiting", "w"), ("2026-11-04", latest, "done"))
    fake = FakeLore(["a"])
    fake.add("a", "s", spec_body(pending(history=history)))
    assert go(capsys, fake)[1] == "Nothing is waiting on you.\n"

    again = history + (("2026-11-06", "waiting", "back on"),)
    fake = FakeLore(["a"])
    fake.add("a", "s", spec_body(pending(history=again)))
    _, out, _ = go(capsys, fake)
    assert out.splitlines()[0] == "1 check waiting, in 1 spec."
    assert "  set: 2026-11-06\n" in out


# --- only ready specs ---------------------------------------------------------


def test_a_non_ready_spec_is_absent_whatever_the_search_hit_said(capsys):
    fake = FakeLore(["a"])
    fake.add("a", "draft-one", spec_body(pending()), status="draft")  # hit says ready
    fake.add("a", "broken-draft", "## Waiting\n\nprose\n", status="draft")
    assert go(capsys, fake)[1] == "Nothing is waiting on you.\n"


def test_the_shown_sidecar_status_wins_over_the_search_hit(capsys):
    fake = FakeLore(["a"])
    fake.add("a", "now-ready", spec_body(pending()), status="ready")
    fake.hits[0]["status"] = "draft"
    assert go(capsys, fake)[1].splitlines()[0] == "1 check waiting, in 1 spec."


# --- unreadable ---------------------------------------------------------------


def good_spec(fake, vault="alpha"):
    fake.add(vault, "good", spec_body(pending(check="still-rendered")))


def assert_listed_after_the_count_line(out, line):
    lines = out.splitlines()
    assert lines[0] == "1 check waiting, in 1 spec."
    assert lines[1] == line
    assert "  check: still-rendered" in lines


def test_an_unparseable_section_is_unreadable_while_other_specs_render(capsys):
    fake = FakeLore(["alpha"])
    good_spec(fake)
    fake.add("alpha", "bad", "## Waiting\n\nloose prose\n")
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert "unparseable" in cg.REASONS
    assert_listed_after_the_count_line(out, could_not_read("alpha/bad", "unparseable"))


@pytest.mark.parametrize("raw", [1, "not json at all", json.dumps({"kind": "spec"}),
                                 json.dumps({"body": 5, "sidecar": {"status": "ready"}}),
                                 json.dumps({"body": "x", "sidecar": {}})])
def test_record_show_failing_or_not_json_is_unreadable(capsys, raw):
    fake = FakeLore(["alpha"])
    good_spec(fake)
    fake.add("alpha", "bad", raw=raw)
    code, out, err = go(capsys, fake)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("alpha/bad", "show-failed"))
    assert "SECRET-STDERR" not in out + err


def test_a_vault_name_failing_the_shape_check_is_listed_and_never_passed_to_lore(capsys):
    fake = FakeLore(["alpha", ("scratch pad", "/v/scratch")])
    good_spec(fake)
    fake.hits.append({"id": "/v/scratch/spec/x", "kind": "spec", "status": "ready",
                      "vault": "/v/scratch"})
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("vault scratch pad", "unsafe-name"))
    assert not any("scratch" in w for w in fake.argv_words())


def test_a_spec_name_failing_the_shape_check_is_listed_and_never_passed_to_lore(capsys):
    fake = FakeLore(["alpha"])
    good_spec(fake)
    fake.hits.append({"id": "/v/alpha/spec/evil name", "kind": "spec", "status": "ready",
                      "vault": "/v/alpha"})
    fake.hits.append({"id": "/v/alpha/spec/--vault", "kind": "spec", "status": "ready",
                      "vault": "/v/alpha"})
    code, out, _ = go(capsys, fake)
    assert code == 0
    lines = out.splitlines()
    assert could_not_read("alpha/evil name", "unsafe-name") in lines
    assert could_not_read("alpha/--vault", "unsafe-name") in lines
    assert "  check: still-rendered" in lines
    assert not any("evil" in w or w in ("spec/--vault",) for w in fake.argv_words())


def test_a_search_hit_at_an_unconfigured_vault_path_is_unreadable(capsys):
    fake = FakeLore(["alpha"])
    good_spec(fake)
    fake.hits.append({"id": "/elsewhere/ghost/spec/x", "kind": "spec", "status": "ready",
                      "vault": "/elsewhere/ghost"})
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("vault ghost", "not-configured"))
    assert not any("ghost" in w or w == "spec/x" for w in fake.argv_words())


@pytest.mark.parametrize("flag", ["stale", "config_stale"])
def test_a_stale_search_adds_the_stale_index_line(capsys, flag):
    fake = FakeLore(["alpha"], **{flag: True})
    good_spec(fake)
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("lore's search", "stale"))
    assert out.count("Could not read:") == 1


def test_the_unreadable_lines_use_the_design_docs_words(capsys):
    fake = FakeLore(["alpha"], stale=True)
    good_spec(fake)
    fake.add("alpha", "some-spec", "## Waiting\n\nprose\n")
    _, out, _ = go(capsys, fake)
    assert out.splitlines()[1:3] == [
        "Could not read: alpha/some-spec — its waiting section could not be parsed",
        "Could not read: lore's search — its index is stale, so some ready specs may be missing; "
        "run lore reindex",
    ]


def test_nothing_readable_waiting_while_something_is_unreadable(capsys):
    fake = FakeLore(["alpha"])
    fake.add("alpha", "ok", spec_body())
    fake.add("alpha", "bad", raw=1)
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert out == (
        "Nothing waiting in the specs that could be read.\n"
        "Could not read: alpha/bad — lore could not show it\n"
    )
    assert "Nothing is waiting on you." not in out


# --- hostile text -------------------------------------------------------------


def test_hostile_text_is_cleaned_before_it_reaches_the_output(capsys):
    hostile = f"see \x1b[31mred\x1b[0m ‮evil {SECRET} end"
    fake = FakeLore(["alpha"])
    fake.add("alpha", "s", spec_body(
        pending(check=hostile, how=hostile),
        set_aside("AC17", waits=hostile),
        criteria={"AC32": hostile, "AC17": hostile}))
    code, out, err = go(capsys, fake)
    assert code == 0
    assert "\x1b" not in out and "‮" not in out
    assert SECRET not in out
    assert out.count("[REDACTED]") >= 5
    assert "  check: see [31mred[0m evil [REDACTED] end\n" in out


def test_overlong_text_is_cut_visibly_but_a_real_check_is_shown_whole(capsys):
    real = "check the banner shows once. " * 12
    huge = "open the app and look. " * 220
    fake = FakeLore(["a"])
    fake.add("a", "s", spec_body(pending(check=real, how=huge)))
    _, out, _ = go(capsys, fake)
    assert f"  check: {real.strip()}\n" in out
    how = next(ln for ln in out.splitlines() if ln.startswith("  how: "))
    assert how.endswith("…") and len(how) < len("  how: ") + len(huge)


# --- paging -------------------------------------------------------------------


def paged(n=25):
    fake = FakeLore(["alpha"], page_cap=20)
    for i in range(n - 1):
        fake.add("alpha", f"quiet-{i:02d}", spec_body())
    fake.add("alpha", "zz-last", spec_body(pending(check="on-page-two")))
    return fake


def test_a_waiting_entry_on_the_second_page_is_listed(capsys):
    fake = paged()
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert out.splitlines()[0] == "1 check waiting, in 1 spec."
    assert "  check: on-page-two" in out.splitlines()
    offsets = [a[a.index("--offset") + 1] for a in fake.calls if a[0] == "search"]
    assert offsets == ["0", "20"]


def test_a_search_still_truncated_after_the_last_page_names_the_cut_off(capsys):
    fake = FakeLore(["alpha"], truncated=True)
    fake.add("alpha", "quiet", spec_body())
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert out == (
        "Nothing waiting in the specs that could be read.\n"
        + could_not_read("lore's search", "cut-off") + "\n"
    )


def test_fewer_hits_than_total_names_the_cut_off(capsys):
    fake = FakeLore(["alpha"], total=30, truncated=False)
    fake.add("alpha", "quiet", spec_body(pending()))
    code, out, _ = go(capsys, fake)
    lines = out.splitlines()
    assert lines[0] == "1 check waiting, in 1 spec."
    assert lines[1] == could_not_read("lore's search", "cut-off")


@pytest.mark.parametrize("kwargs", [{"search_rc": 1}, {"search_raw": "not json"},
                                    {"search_raw": json.dumps({"hits": "no"})}])
def test_a_search_that_cannot_be_read_never_reads_as_nothing_waiting(capsys, kwargs):
    fake = FakeLore(["alpha"], **kwargs)
    code, out, err = go(capsys, fake)
    assert code == 0
    assert out == (
        "Nothing waiting in the specs that could be read.\n"
        + could_not_read("lore's search", "search-unreadable") + "\n"
    )
    assert "SECRET-STDERR" not in out + err


def test_an_unreadable_search_is_worded_as_the_design_doc_words_it(capsys):
    _, out, _ = go(capsys, FakeLore(["alpha"], search_rc=1))
    assert out.splitlines()[1] == (
        "Could not read: lore's search — it could not be run or read, "
        "so some ready specs may be missing"
    )


def test_a_page_that_cannot_be_read_after_a_good_one_is_unreadable_not_cut_off(capsys):
    fake = FakeLore(["alpha"], page_cap=1)
    fake.add("alpha", "one", spec_body(pending()))
    fake.add("alpha", "two", spec_body())

    def runner(argv):
        if argv[0] == "search" and argv[argv.index("--offset") + 1] != "0":
            return subprocess.CompletedProcess(argv, 0, "not json", "")
        return fake(argv)

    code, out, _ = go(capsys, runner)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "1 check waiting, in 1 spec."
    assert lines[1] == could_not_read("lore's search", "search-unreadable")
    assert out.count("Could not read:") == 1


def test_a_hit_repeated_on_a_later_page_is_rendered_once_and_names_the_cut_off(capsys):
    fake = FakeLore(["alpha"], page_cap=1)
    fake.add("alpha", "one", spec_body(pending()))
    fake.hits.append(dict(fake.hits[0]))
    code, out, _ = go(capsys, fake)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "1 check waiting, in 1 spec."
    assert lines[1] == could_not_read("lore's search", "cut-off")
    assert out.count("[1/1]") == 1
    assert "[2/" not in out


def test_a_repeated_hit_with_every_distinct_record_read_is_not_a_cut_off(capsys):
    fake = FakeLore(["alpha"], page_cap=2, total=3)
    fake.add("alpha", "a", spec_body(pending()))
    fake.add("alpha", "b", spec_body())
    fake.hits.append(dict(fake.hits[1]))
    fake.add("alpha", "c", spec_body())
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert out.splitlines()[0] == "1 check waiting, in 1 spec."
    assert out.count("[1/1]") == 1
    assert "Could not read" not in out


def test_a_spec_edited_between_pages_that_is_never_returned_names_the_cut_off(capsys):
    fake = FakeLore(["alpha"], page_cap=2)
    fake.add("alpha", "a", spec_body())
    fake.add("alpha", "b", spec_body())
    fake.add("alpha", "c", spec_body(pending()))

    def runner(argv):
        done = fake(argv)
        if argv[0] == "search" and argv[argv.index("--offset") + 1] == "0":
            fake.hits.insert(0, fake.hits.pop())
        return done

    code, out, _ = go(capsys, runner)
    assert code == 0
    assert "Nothing is waiting on you." not in out
    assert out.splitlines()[0] == "Nothing waiting in the specs that could be read."
    assert could_not_read("lore's search", "cut-off") in out.splitlines()


def test_the_criterion_words_do_not_carry_the_verified_by_suffix(capsys):
    fake = FakeLore(["a"])
    fake.add("a", "s", spec_body(pending(), criteria={
        "AC32": "Checkout collects billing details. *Verified by: automated assertion.*"}))
    _, out, _ = go(capsys, fake)
    assert "[1/1] Checkout collects billing details. (AC32)\n" in out
    assert "Verified" not in out


def test_a_wrapped_verified_by_suffix_is_dropped_from_the_criterion_words(capsys):
    fake = FakeLore(["a"])
    fake.add("a", "s", spec_body(pending(), criteria={
        "AC32": "Checkout collects details.\n  *Verified by: automated\n  assertion.*"}))
    _, out, _ = go(capsys, fake)
    assert "[1/1] Checkout collects details. (AC32)\n" in out


def _gather_into(queue, fake):
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cg.main([], fake)
    queue.put((code, buf.getvalue()))


def test_an_unclosed_verified_by_with_a_huge_whitespace_run_still_lists_the_entry():
    import multiprocessing

    ctx = multiprocessing.get_context("fork")
    fake = FakeLore(["a"])
    fake.add("a", "s", spec_body(pending(), criteria={
        "AC32": "Checkout collects billing details. *Verified by:" + " " * 20000}))
    queue = ctx.Queue()
    child = ctx.Process(target=_gather_into, args=(queue, fake))
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


def test_a_hit_whose_id_is_not_under_its_vaults_spec_directory_is_a_search_problem(capsys):
    fake = FakeLore(["alpha"])
    good_spec(fake)
    fake.hits.append({"id": "/v/alpha/note/x", "kind": "spec", "status": "ready",
                      "vault": "/v/alpha"})
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("lore's search", "search-unreadable"))
    assert out.count("Could not read:") == 1
    assert not any("note" in w for w in fake.argv_words())


@pytest.mark.parametrize("bad", ["junk", 7, {"id": "/v/alpha/spec/x"}, {"vault": "/v/alpha"},
                                 {"id": 3, "vault": "/v/alpha"}])
def test_a_hit_that_is_not_a_dict_or_misses_a_field_is_a_search_problem(capsys, bad):
    fake = FakeLore(["alpha"])
    good_spec(fake)
    fake.hits.append(bad)
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("lore's search", "search-unreadable"))
    assert out.count("Could not read:") == 1


# --- shared layer -------------------------------------------------------------


def test_a_shared_layer_spec_is_unescaped_exactly_and_listed(capsys):
    check = "show <b>AT&T</b> a > b, literal &amp; &lt;i&gt; and &#27; and raw \x1b[31m end"
    body = spec_body(pending(check=check))
    assert escape(body) != body
    fake = FakeLore(["team"])
    fake.add("team", "shared-one", escape(body), layer="shared")
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert out.splitlines()[0] == "1 check waiting, in 1 spec."
    assert "  check: show <b>AT&T</b> a > b, literal &amp; &lt;i&gt; and &#27; and raw [31m end\n" in out
    assert "\x1b" not in out


# --- vault alias --------------------------------------------------------------


def test_two_configured_vaults_sharing_a_path_make_a_hit_there_unreadable(capsys):
    fake = FakeLore([("alpha", "/v/shared"), ("beta", "/v/shared"), ("gamma", "/v/gamma")])
    fake.add("gamma", "good", spec_body(pending(check="still-rendered")))
    fake.specs[("alpha", "x")] = shown(spec_body(pending()))
    fake.hits.append({"id": "/v/shared/spec/x", "kind": "spec", "status": "ready",
                      "vault": "/v/shared"})
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("vault shared", "not-configured"))
    assert not any(a[:2] == ["record", "show"] and "spec/x" in a for a in fake.calls)


def test_a_path_shared_by_an_unsafe_and_a_safe_vault_name_is_not_configured(capsys):
    fake = FakeLore([("bad name", "/v/shared"), ("alpha", "/v/shared"), ("gamma", "/v/gamma")])
    fake.add("gamma", "good", spec_body(pending(check="still-rendered")))
    fake.specs[("alpha", "x")] = shown(spec_body(pending()))
    fake.hits.append({"id": "/v/shared/spec/x", "kind": "spec", "status": "ready",
                      "vault": "/v/shared"})
    code, out, _ = go(capsys, fake)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "1 check waiting, in 1 spec."
    assert could_not_read("vault shared", "not-configured") in lines
    assert "  check: still-rendered" in lines
    assert not any(a[:2] == ["record", "show"] and "spec/x" in a for a in fake.calls)


# --- criterion lookup ---------------------------------------------------------


def test_an_undeclared_criterion_shows_the_not_found_marker(capsys):
    fake = FakeLore(["a"])
    fake.add("a", "s", spec_body(pending("AC99"), criteria={"AC1": "other"}))
    _, out, _ = go(capsys, fake)
    assert "[1/1] (criterion text not found) (AC99)\n" in out


def test_a_spec_with_no_acceptance_criteria_heading_still_lists_its_entries(capsys):
    fake = FakeLore(["a"])
    fake.add("a", "s", "# Spec\n\n## Waiting\n\n" + pending())
    _, out, _ = go(capsys, fake)
    assert "[1/1] (criterion text not found) (AC32)\n" in out


def test_a_duplicate_acceptance_criteria_heading_makes_the_spec_unreadable(capsys):
    body = spec_body(pending()) + "\n## Acceptance Criteria\n\n- **AC32.** again\n"
    fake = FakeLore(["alpha"])
    good_spec(fake)
    fake.add("alpha", "dup", body)
    code, out, _ = go(capsys, fake)
    assert code == 0
    assert_listed_after_the_count_line(out, could_not_read("alpha/dup", "unparseable"))


# --- hard failure -------------------------------------------------------------


def hard(capsys, fake):
    code, out, err = go(capsys, fake)
    assert out == ""
    assert "SECRET-STDERR" not in err
    lines = [ln for ln in err.splitlines() if ln.startswith("reason-code:")]
    assert len(lines) == 1
    reason = lines[0].removeprefix("reason-code: ")
    assert reason in cg.REASON_CODES
    return code, reason


def test_no_configured_vaults_exits_2_with_its_reason_and_empty_stdout(capsys):
    assert hard(capsys, FakeLore([])) == (2, "no-vaults")


def test_lore_missing_exits_2_with_its_reason_and_empty_stdout(capsys):
    def runner(argv):
        raise FileNotFoundError("lore")

    assert hard(capsys, runner) == (2, "lore-missing")


def test_a_timeout_exits_2_with_its_reason_and_empty_stdout(capsys):
    def runner(argv):
        raise subprocess.TimeoutExpired(argv, 30)

    assert hard(capsys, runner) == (2, "timeout")


def test_vault_ls_failing_exits_2_with_its_reason_and_empty_stdout(capsys):
    assert hard(capsys, FakeLore(["a"], vault_ls_rc=1)) == (2, "lore-failed")


def test_a_timeout_after_the_vault_listing_still_exits_2_with_empty_stdout(capsys):
    fake = FakeLore(["a"])
    fake.add("a", "s", spec_body(pending()))

    def runner(argv):
        if argv[:2] == ["record", "show"]:
            raise subprocess.TimeoutExpired(argv, 30)
        return fake(argv)

    assert hard(capsys, runner) == (2, "timeout")


def test_an_unexpected_exception_exits_2_as_internal_error(capsys):
    def runner(argv):
        raise RuntimeError("boom SECRET-STDERR")

    assert hard(capsys, runner) == (2, "internal-error")


def test_an_argument_is_refused_as_invalid_arguments(capsys):
    code = cg.main(["surprise"], FakeLore(["a"]))
    cap = capsys.readouterr()
    assert code == 2 and cap.out == "" and "reason-code: invalid-arguments" in cap.err


# --- seam: the script as a subprocess against the real lore CLI ---------------


def _build_env(tmp_path, vaults):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cfg = tmp_path / "xdg_config"
    _lore_conftest.write_vault_config(cfg, vaults)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "lore"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_lore_conftest.CLI_PATH}" "$@"\n')
    shim.chmod(0o755)
    return {
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "XDG_STATE_HOME": str(state),
        "XDG_CONFIG_HOME": str(cfg),
        "LORE_GROUPS_DIR": str(tmp_path / "groups"),
        "LORE_PUBLISH_DISABLE": "1",
        "LORE_EMAIL": "tester@example.com",
    }


def test_the_script_lists_a_waiting_entry_through_the_real_lore_cli(  # inert-gate: allow seam smoke
    tmp_path,
):
    vault, _ = make_vault(tmp_path)
    env = _build_env(tmp_path, [("default", "default", vault)])
    body = spec_body(pending("AC32", "seam-slice", "see the seam", "run it"))
    made = subprocess.run(
        ["lore", "record", "create", "--kind", "spec", "--title", "Seam spec",
         "--status", "ready", "--vault", "default"],
        capture_output=True, text=True, env=env, input=body,
    )
    assert made.returncode == 0, made.stderr
    name = made.stdout.strip().split("/", 1)[1]
    r = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True, env=env)
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
