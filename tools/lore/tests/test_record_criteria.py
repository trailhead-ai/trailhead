"""criteria: the one reader of a spec's criteria and their change-history state."""

import pytest

from lore.record.criteria import (
    CriteriaError,
    check_criteria_amend,
    check_criteria_marks,
    criteria_state,
    declared_criteria,
    ledger_entries,
)


def spec(*bullets, heading="## Acceptance Criteria", tail=""):
    return "# Spec\n\nintro\n\n" + heading + "\n\n" + "\n".join(bullets) + "\n" + tail


def ac(n, text=None):
    return f"- **AC{n}.** {text or f'criterion number {n} text'}"


def five():
    return spec(*[ac(n) for n in range(1, 6)])


def hist(*removed_groups):
    return [
        {"at": "t", "by": "b", "initiated-by": "i", "reason": "r", "diff": "d",
         "criteria": {"removed": list(g)}}
        for g in removed_groups
    ]


# ---------------------------------------------------------------- declared_criteria


def test_declared_plain_list():
    body = spec("- **AC1.** first", "- **AC2.** second")
    assert declared_criteria(body) == [
        ("AC1", "- **AC1.** first"),
        ("AC2", "- **AC2.** second"),
    ]


def test_declared_skips_unidentified_bullets_and_sub_headings():
    body = spec("### Group A", "- **AC1.** one", "- plain bullet", "### Group B", "- **AC2.** two")
    assert [i for i, _ in declared_criteria(body)] == ["AC1", "AC2"]


def test_declared_heading_is_case_insensitive():
    body = spec("- **AC1.** one", heading="## acceptance CRITERIA")
    assert [i for i, _ in declared_criteria(body)] == ["AC1"]


def test_declared_ignores_fenced_and_commented_fakes():
    body = spec(
        "- **AC1.** real",
        "```",
        "- **AC2.** fenced fake",
        "```",
        "<!--",
        "- **AC3.** commented fake",
        "-->",
        "- **AC4.** real again",
    )
    assert [i for i, _ in declared_criteria(body)] == ["AC1", "AC4"]


def test_declared_folds_sub_bullet_into_parent():
    body = spec("- **AC1.** parent", "  - **AC9.** nested qualifier", "- **AC2.** next")
    got = declared_criteria(body)
    assert [i for i, _ in got] == ["AC1", "AC2"]
    assert "nested qualifier" in got[0][1]


def test_declared_folds_wrapped_continuation():
    body = spec("- **AC1.** starts here", "  and wraps", "- **AC2.** next")
    assert declared_criteria(body)[0][1] == "- **AC1.** starts here\n  and wraps"


def test_declared_no_heading_raises():
    with pytest.raises(CriteriaError):
        declared_criteria("# Spec\n\n- **AC1.** nothing under a heading\n")


def test_declared_duplicate_heading_raises():
    body = spec("- **AC1.** a") + "\n## Acceptance Criteria\n- **AC2.** b\n"
    with pytest.raises(CriteriaError):
        declared_criteria(body)


def test_declared_second_heading_inside_fence_is_not_a_duplicate():
    body = spec("- **AC1.** a", tail="```\n## Acceptance Criteria\n```\n")
    assert [i for i, _ in declared_criteria(body)] == ["AC1"]


def test_criteria_error_is_a_value_error_and_does_not_echo_body():
    secret = "SECRET-BODY-TEXT"
    with pytest.raises(CriteriaError) as exc:
        declared_criteria(secret)
    assert isinstance(exc.value, ValueError)
    assert secret not in str(exc.value)
    body = f"## Acceptance Criteria\n## Acceptance Criteria\n{secret}\n"
    with pytest.raises(CriteriaError) as exc:
        declared_criteria(body)
    assert secret not in str(exc.value)


# ---------------------------------------------------------------- criteria_state


def test_state_recorded_removal_still_absent():
    body = spec(ac(1), ac(2), ac(4))
    assert criteria_state(body, hist(["AC3"])) == {
        "declared": ["AC1", "AC2", "AC4"], "removed": ["AC3"], "reopened": {}, "closed": [],
    }


def test_state_restored_criterion_is_no_longer_removed():
    body = spec(ac(1), ac(3))
    assert criteria_state(body, hist(["AC3"]))["removed"] == []


def test_state_entries_without_criteria_contribute_nothing():
    h = [{"at": "t", "reason": "r"}, {"at": "u"}]
    assert criteria_state(five(), h)["removed"] == []


@pytest.mark.parametrize("h", [None, []])
def test_state_empty_history(h):
    assert criteria_state(five(), h)["removed"] == []


def test_state_removed_is_union_in_numeric_order():
    body = spec(ac(1))
    h = hist(["AC10", "AC2"], ["AC3", "AC2"])
    assert criteria_state(body, h)["removed"] == ["AC2", "AC3", "AC10"]


def test_state_declared_is_document_order_ids():
    body = spec(ac(3), ac(1))
    assert criteria_state(body, None)["declared"] == ["AC3", "AC1"]


def test_state_missing_heading_raises():
    with pytest.raises(CriteriaError):
        criteria_state("no heading", None)


MALFORMED = [
    pytest.param({}, id="dict-container"),
    pytest.param("x", id="str-container"),
    pytest.param([None], id="entry-none"),
    pytest.param(["x"], id="entry-str"),
    pytest.param([{"criteria": None}], id="criteria-none"),
    pytest.param([{"criteria": []}], id="criteria-list"),
    pytest.param([{"criteria": {}}], id="criteria-empty-object"),
    pytest.param([{"criteria": {"removed": []}}], id="removed-empty"),
    pytest.param([{"criteria": {"removed": "AC1"}}], id="removed-string"),
    pytest.param([{"criteria": {"removed": ["AC1", "AC1"]}}], id="removed-dup"),
    pytest.param([{"criteria": {"removed": ["AC0"]}}], id="removed-ac0"),
    pytest.param([{"criteria": {"removed": ["AC01"]}}], id="removed-leading-zero"),
    pytest.param([{"criteria": {"removed": ["X1"]}}], id="removed-non-canonical"),
    pytest.param([{"criteria": {"removed": [1]}}], id="removed-non-string"),
    pytest.param([{"criteria": {"removed": ["AC1"], "x": 1}}], id="extra-key"),
]


@pytest.mark.parametrize("h", MALFORMED)
def test_state_malformed_history_raises(h):
    with pytest.raises(CriteriaError):
        criteria_state(five(), h)


@pytest.mark.parametrize("h", MALFORMED)
def test_amend_malformed_history_raises(h):
    with pytest.raises(CriteriaError):
        check_criteria_amend(five(), five(), h)


def test_malformed_entry_after_valid_entry_still_raises():
    with pytest.raises(CriteriaError):
        criteria_state(five(), hist(["AC1"]) + [None])


# ---------------------------------------------------------------- check_criteria_amend: allowed


def test_remove_one_is_allowed():
    new = spec(ac(1), ac(3), ac(4), ac(5))
    assert check_criteria_amend(five(), new, None) == (["AC2"], [])


def test_add_next_number_is_allowed():
    new = spec(*[ac(n) for n in range(1, 7)])
    assert check_criteria_amend(five(), new, None) == ([], [])


def test_add_several_contiguous_is_allowed():
    new = spec(*[ac(n) for n in range(1, 8)])
    assert check_criteria_amend(five(), new, None) == ([], [])


def test_edit_text_in_place_is_allowed():
    new = spec(ac(1), ac(2, "entirely reworded"), ac(3), ac(4), ac(5))
    assert check_criteria_amend(five(), new, None) == ([], [])


def test_whitespace_only_reflow_of_same_id_is_allowed():
    old = spec("- **AC1.** one two three")
    new = spec("- **AC1.**   one\n  two   three")
    assert check_criteria_amend(old, new, None) == ([], [])


def test_legacy_body_with_zero_ids_has_no_refusals():
    old = spec("- plain one", "- plain two")
    new = spec("- plain one", "- plain two", "- plain three")
    assert check_criteria_amend(old, new, None) == ([], [])


def test_removed_and_added_in_one_amend_is_allowed():
    new = spec(ac(1), ac(3), ac(4), ac(5), ac(6))
    assert check_criteria_amend(five(), new, None) == (["AC2"], [])


def test_removed_now_is_numeric_order():
    old = spec(*[ac(n) for n in range(1, 12)])
    new = spec(ac(1))
    removed, _ = check_criteria_amend(old, new, None)
    assert removed == [f"AC{n}" for n in range(2, 12)]


def test_restoring_a_recorded_removal_that_old_body_still_declares_is_allowed():
    assert check_criteria_amend(five(), five(), hist(["AC5"])) == ([], [])


# ---------------------------------------------------------------- check_criteria_amend: refusals


def test_skipping_a_number_is_refused_naming_next_unused():
    new = spec(*[ac(n) for n in range(1, 6)], ac(7))
    removed, refusals = check_criteria_amend(five(), new, None)
    assert removed == []
    assert len(refusals) == 1
    assert "AC7" in refusals[0] and "AC6" in refusals[0]


def test_non_contiguous_run_of_additions_is_refused():
    new = spec(*[ac(n) for n in range(1, 6)], ac(6), ac(8))
    _, refusals = check_criteria_amend(five(), new, None)
    assert len(refusals) == 1 and "AC8" in refusals[0] and "AC7" in refusals[0]


def test_next_unused_accounts_for_recorded_removals():
    old = spec(ac(1), ac(2))
    _, refusals = check_criteria_amend(old, spec(ac(1), ac(2), ac(3)), hist(["AC3"]))
    assert len(refusals) == 1 and "AC3" in refusals[0] and "AC4" in refusals[0]
    _, refusals = check_criteria_amend(old, spec(ac(1), ac(2), ac(5)), hist(["AC3"]))
    assert len(refusals) == 1 and "AC5" in refusals[0] and "AC4" in refusals[0]
    assert check_criteria_amend(old, spec(ac(1), ac(2), ac(4)), hist(["AC3"])) == ([], [])


def test_reusing_removed_highest_id_is_refused_naming_next_unused():
    old = spec(*[ac(n) for n in range(1, 5)])
    new = spec(*[ac(n) for n in range(1, 6)])
    removed, refusals = check_criteria_amend(old, new, hist(["AC5"]))
    assert removed == []
    assert len(refusals) == 1 and "AC5" in refusals[0] and "AC6" in refusals[0]
    assert "reused" in refusals[0]
    assert "--revert-to 1" in refusals[0]


def test_reuse_refusal_names_the_entry_that_recorded_the_removal():
    old = spec(ac(1), ac(3))
    new = spec(ac(1), ac(2), ac(3))
    _, refusals = check_criteria_amend(old, new, hist(["AC9"], ["AC2"]))
    assert len(refusals) == 1
    assert "--revert-to 2" in refusals[0]
    assert "--revert-to 1" not in refusals[0]


def test_reusing_removed_interior_id_is_refused():
    old = spec(ac(1), ac(3))
    new = spec(ac(1), ac(2), ac(3))
    _, refusals = check_criteria_amend(old, new, hist(["AC2"]))
    assert len(refusals) == 1 and "AC2" in refusals[0] and "AC4" in refusals[0]
    assert "reused" in refusals[0]


def test_shift_renumber_is_refused():
    new = spec(ac(1), ac(2, "criterion number 3 text"), ac(3, "criterion number 4 text"),
               ac(4, "criterion number 5 text"))
    removed, refusals = check_criteria_amend(five(), new, None)
    assert removed == ["AC5"]
    assert refusals
    assert any("AC2" in r for r in refusals)
    renumber = [r for r in refusals if "AC2" in r]
    assert all("never relabelled" in r for r in renumber)
    assert all("two amends" in r and "AC6" in r for r in renumber)


def test_swapping_text_between_ids_is_refused():
    new = spec(ac(1, "criterion number 2 text"), ac(2, "criterion number 1 text"),
               ac(3), ac(4), ac(5))
    _, refusals = check_criteria_amend(five(), new, None)
    assert refusals


def test_renumber_ignores_whitespace_differences():
    old = spec("- **AC1.** alpha beta", "- **AC2.** gamma")
    new = spec("- **AC1.** delta", "- **AC2.**   alpha\n  beta")
    _, refusals = check_criteria_amend(old, new, None)
    assert len(refusals) == 1 and "AC2" in refusals[0]


def test_duplicate_id_in_new_body_is_refused_naming_it():
    new = spec(ac(1), ac(2), ac(3), ac(3, "other"), ac(4), ac(5))
    _, refusals = check_criteria_amend(five(), new, None)
    assert any("AC3" in r for r in refusals)


# ---------------------------------------------------------------- body-shape decisions


def test_old_body_without_heading_declares_nothing():
    old = "# Spec\nno criteria section\n"
    new = spec(ac(1), ac(2))
    assert check_criteria_amend(old, new, None) == ([], [])
    _, refusals = check_criteria_amend(old, spec(ac(2)), None)
    assert refusals and "AC2" in refusals[0] and "AC1" in refusals[0]


def test_old_body_with_duplicate_heading_declares_nothing():
    old = spec(ac(1)) + "\n## Acceptance Criteria\n" + ac(2) + "\n"
    assert check_criteria_amend(old, spec(ac(1)), None) == ([], [])


def test_new_body_heading_deleted_is_refused_and_removals_still_reported():
    removed, refusals = check_criteria_amend(five(), "# Spec\nno section\n", None)
    assert removed == [f"AC{n}" for n in range(1, 6)]
    assert len(refusals) == 1


def test_neither_body_has_heading_is_not_refused():
    assert check_criteria_amend("a\n", "b\n", None) == ([], [])


def test_new_body_duplicate_heading_is_refused():
    new = five() + "\n## Acceptance Criteria\n" + ac(6) + "\n"
    removed, refusals = check_criteria_amend(five(), new, None)
    assert len(refusals) == 1
    assert isinstance(removed, list)


def test_removed_now_is_computed_when_other_refusals_exist():
    new = spec(ac(1), ac(3), ac(4), ac(5), ac(9))
    removed, refusals = check_criteria_amend(five(), new, None)
    assert removed == ["AC2"]
    assert refusals


# ---------------------------------------------------------------- non-canonical ids


def test_non_canonical_id_added_is_refused_without_echo():
    new = spec(*[ac(n) for n in range(1, 6)], "- **AC007.** sneaky")
    _, refusals = check_criteria_amend(five(), new, None)
    assert refusals
    assert all("AC007" not in r and "sneaky" not in r for r in refusals)


def test_ac0_added_is_refused_without_echo():
    new = spec(ac(0), *[ac(n) for n in range(1, 6)])
    _, refusals = check_criteria_amend(five(), new, None)
    assert refusals
    assert all("AC0" not in r for r in refusals)


def test_non_canonical_id_carried_over_unchanged_is_left_alone():
    old = spec(ac(1), "- **AC007.** legacy")
    new = spec(ac(1, "edited"), "- **AC007.** legacy", ac(2))
    assert check_criteria_amend(old, new, None) == ([], [])


def test_non_canonical_id_removed_is_refused_and_not_in_removed_now():
    old = spec(ac(1), "- **AC007.** legacy")
    new = spec(ac(1))
    removed, refusals = check_criteria_amend(old, new, None)
    assert removed == []
    assert refusals and all("AC007" not in r for r in refusals)


def test_non_canonical_ids_do_not_affect_numbering_arithmetic():
    old = spec(ac(1), "- **AC007.** legacy")
    assert check_criteria_amend(old, spec(ac(1), "- **AC007.** legacy", ac(2)), None) == ([], [])
    _, refusals = check_criteria_amend(old, spec(ac(1), "- **AC007.** legacy", ac(3)), None)
    assert len(refusals) == 1 and "AC2" in refusals[0]


# ---------------------------------------------------------------- echo hygiene


def test_refusals_never_contain_criterion_text():
    secret_a, secret_b = "ZZTOPSECRETONE", "ZZTOPSECRETTWO"
    old = spec(ac(1, secret_a), ac(2, secret_b))
    new = spec(ac(1, secret_b), ac(2, secret_a), ac(2, "dupe " + secret_a), ac(9, secret_a + " added"))
    _, refusals = check_criteria_amend(old, new, hist(["AC3"]))
    assert len(refusals) >= 3
    for r in refusals:
        assert "ZZTOP" not in r


# ------------------------------------------------ reverting to a duplicated heading


def test_duplicate_heading_in_new_body_records_no_removals():
    new = five() + "\n## Acceptance Criteria\n" + ac(6) + "\n"
    removed, refusals = check_criteria_amend(five(), new, None)
    assert removed == []
    assert refusals


def test_deleted_heading_in_new_body_still_records_every_old_id():
    removed, refusals = check_criteria_amend(five(), "# Spec\nno section\n", None)
    assert removed == [f"AC{n}" for n in range(1, 6)]
    assert refusals


# ------------------------------------------------ identifiers too long to be canonical


def big(digits, lead="1"):
    return "AC" + lead + "0" * (digits - 1)


def test_nine_digit_id_is_canonical_and_ten_digit_is_not():
    nine, ten = big(9), big(10)
    old = spec(ac(1))
    _, ok = check_criteria_amend(old, spec(ac(1), f"- **{nine}.** t"), None)
    assert not any("non-canonical" in r for r in ok)
    _, bad = check_criteria_amend(old, spec(ac(1), f"- **{ten}.** t"), None)
    assert any("non-canonical" in r and "added" in r for r in bad)
    assert not any(ten in r for r in bad)


@pytest.mark.parametrize("digits", [10, 5000])
def test_over_long_id_added_removed_or_repeated_is_refused_without_raising_or_echo(digits):
    long_id = big(digits)
    with_long = spec(ac(1), f"- **{long_id}.** t")
    added = check_criteria_amend(spec(ac(1)), with_long, None)[1]
    removed_ = check_criteria_amend(with_long, spec(ac(1)), None)[1]
    repeated = check_criteria_amend(
        with_long, spec(ac(1), f"- **{long_id}.** t", f"- **{long_id}.** u"), None
    )[1]
    for refusals in (added, removed_, repeated):
        assert refusals
        assert not any(long_id in r for r in refusals)
    assert any("added" in r for r in added)
    assert any("removed" in r for r in removed_)


@pytest.mark.parametrize("digits", [10, 5000])
def test_over_long_id_carried_over_unchanged_is_left_alone(digits):
    body = spec(ac(1), f"- **{big(digits)}.** t")
    assert check_criteria_amend(body, body, None) == ([], [])


def test_a_nine_digit_id_carried_over_sets_the_next_unused_number_without_raising():
    old = spec(ac(1), f"- **{big(9, '9')}.** t")
    _, refusals = check_criteria_amend(old, old + ac(2) + "\n", None)
    assert any("AC2 is not the next unused number; the next unused is AC900000001" in r for r in refusals)


@pytest.mark.parametrize("digits", [10, 5000])
def test_stored_history_with_an_over_long_removed_id_is_a_criteria_error(digits):
    with pytest.raises(CriteriaError):
        criteria_state(spec(ac(1)), hist([big(digits)]))
    with pytest.raises(CriteriaError):
        check_criteria_amend(spec(ac(1)), spec(ac(1)), hist([big(digits)]))


# ------------------------------------------------ renumber and reuse messages


def test_a_remove_and_re_add_in_one_amend_is_told_to_split_into_two_amends():
    old = spec(ac(1), ac(2), ac(3))
    new = spec(ac(1), ac(3), ac(4, "criterion number 2 text"))
    _, refusals = check_criteria_amend(old, new, None)
    moved = [r for r in refusals if "AC4" in r]
    assert len(moved) == 1
    assert "two amends" in moved[0]
    assert "remove" in moved[0] and "criterion number" not in moved[0]


def test_reuse_refusal_names_the_most_recent_entry_that_recorded_the_removal():
    old = spec(ac(1), ac(3))
    new = spec(ac(1), ac(2), ac(3))
    _, refusals = check_criteria_amend(old, new, hist(["AC2"], ["AC9"], ["AC2"]))
    assert "--revert-to 3" in refusals[0]
    assert "--revert-to 1" not in refusals[0]


# ------------------------------------------------------------- marks and ledger


def mark_entry(**crit):
    return {"at": "t", "by": "b", "initiated-by": "i", "reason": "r", "diff": "", "criteria": crit}


def reopen(ids, ledger=()):
    return mark_entry(reopened=list(ids), ledger=list(ledger))


def close(*ids):
    return mark_entry(closed=list(ids))


def slices(*entries, heading="## Slices"):
    return heading + "\n\n" + "\n".join(entries) + "\n"


def slice_line(task, covers=None, partial=None):
    fields = f"`task/{task}`, closed 2026-01-01"
    if covers:
        fields += ", covers " + ", ".join(covers)
    if partial:
        fields += ", partially covers " + ", ".join(partial)
    return f"- value claim ({fields})"


def with_ledger(*lines, base=None):
    return (base or five()) + "\n" + slices(*lines)


# fold


def test_reopen_then_close_gives_closed():
    state = criteria_state(five(), [reopen(["AC2"], ["a"]), close("AC2")])
    assert state["closed"] == ["AC2"]
    assert state["reopened"] == {}


def test_close_then_reopen_gives_reopened_with_the_later_snapshot():
    state = criteria_state(five(), [close("AC2"), reopen(["AC2"], ["a", "b"])])
    assert state["reopened"] == {"AC2": ["a", "b"]}
    assert state["closed"] == []


def test_reopen_reopen_gives_the_later_snapshot():
    state = criteria_state(five(), [reopen(["AC2"], ["a"]), reopen(["AC2"], ["a", "b"])])
    assert state["reopened"] == {"AC2": ["a", "b"]}


def test_a_snapshot_is_per_entry_and_shared_by_the_ids_it_reopens():
    state = criteria_state(five(), [reopen(["AC1", "AC3"], ["x"])])
    assert state["reopened"] == {"AC1": ["x"], "AC3": ["x"]}


def test_reopen_then_remove_gives_neither():
    body = spec(*[ac(n) for n in (1, 3, 4, 5)])
    state = criteria_state(body, [reopen(["AC2"], ["a"]), *hist(["AC2"])])
    assert state["reopened"] == {} and state["closed"] == []
    assert state["removed"] == ["AC2"]


def test_a_mark_made_before_a_removal_does_not_return_when_the_criterion_is_declared_again():
    state = criteria_state(five(), [reopen(["AC2"], ["a"]), close("AC3"), *hist(["AC2", "AC3"])])
    assert state["reopened"] == {} and state["closed"] == []


def test_close_then_remove_gives_neither():
    body = spec(*[ac(n) for n in (1, 3, 4, 5)])
    state = criteria_state(body, [close("AC2"), *hist(["AC2"])])
    assert state["closed"] == [] and state["reopened"] == {}


def test_a_mark_on_an_undeclared_id_is_dropped():
    state = criteria_state(five(), [reopen(["AC9"], ["a"]), close("AC8")])
    assert state["reopened"] == {} and state["closed"] == []


def test_no_marks_gives_empty_marks():
    state = criteria_state(five(), hist(["AC9"]))
    assert state["reopened"] == {} and state["closed"] == []
    assert state["declared"] == ["AC1", "AC2", "AC3", "AC4", "AC5"]


def test_marked_ids_come_back_in_numeric_order():
    body = spec(ac(2), ac(4), ac(10))
    state = criteria_state(body, [close("AC10", "AC2"), reopen(["AC4", "AC10"], ["a"])])
    assert state["closed"] == ["AC2"]
    assert list(state["reopened"]) == ["AC4", "AC10"]


# criteria object shape (read side)

GOOD_SHAPES = [
    {"removed": ["AC1"]},
    {"reopened": ["AC1"], "ledger": []},
    {"closed": ["AC1"]},
    {"removed": ["AC1"], "closed": ["AC2"]},
    {"reopened": ["AC1"], "closed": ["AC2"], "ledger": ["a.b_c-1"]},
]


@pytest.mark.parametrize("crit", GOOD_SHAPES)
def test_a_well_formed_criteria_object_is_read(crit):
    assert criteria_state(five(), [mark_entry(**crit)])["declared"]


BAD_SHAPES = {
    "not-object": ["AC1"],
    "empty-object": {},
    "unknown-key": {"closed": ["AC1"], "extra": 1},
    "ledger-alone": {"ledger": []},
    "reopened-empty": {"reopened": [], "ledger": []},
    "closed-empty": {"closed": []},
    "closed-not-list": {"closed": "AC1"},
    "reopened-not-list": {"reopened": "AC1", "ledger": []},
    "non-canonical": {"closed": ["AC01"]},
    "non-string-id": {"closed": [1]},
    "repeated": {"closed": ["AC1", "AC1"]},
    "ledger-missing": {"reopened": ["AC1"]},
    "ledger-without-reopened": {"closed": ["AC1"], "ledger": []},
    "ledger-not-list": {"reopened": ["AC1"], "ledger": "a"},
    "ledger-non-string": {"reopened": ["AC1"], "ledger": [1]},
    "ledger-off-shape": {"reopened": ["AC1"], "ledger": ["Bad Id"]},
    "ledger-leading-dash": {"reopened": ["AC1"], "ledger": ["-a"]},
    "ledger-repeated": {"reopened": ["AC1"], "ledger": ["a", "a"]},
    "both-lists": {"reopened": ["AC1"], "closed": ["AC1"], "ledger": []},
}


@pytest.mark.parametrize("crit", BAD_SHAPES.values(), ids=BAD_SHAPES)
def test_a_malformed_criteria_object_is_refused_never_repaired(crit):
    with pytest.raises(CriteriaError):
        criteria_state(five(), [{"criteria": crit}])


def test_an_entry_without_criteria_is_skipped_whatever_its_diff():
    assert criteria_state(five(), [{"diff": ""}])["closed"] == []


# ledger_entries


def test_no_slices_section_gives_empty():
    assert ledger_entries(five()) == []


def test_a_slices_section_with_no_entries_gives_empty():
    assert ledger_entries("## Slices\n\nnothing yet\n") == []


def test_one_entry():
    body = slices(slice_line("alpha", covers=["AC1", "AC2"]))
    assert ledger_entries(body) == [("alpha", ["AC1", "AC2"], [])]


def test_many_entries_with_both_fields_and_a_legacy_entry():
    body = slices(
        slice_line("a", covers=["AC1"], partial=["AC2"]),
        "- legacy four field entry (`task/b`, closed 2026-01-01)",
        slice_line("c", partial=["AC3"]),
    )
    assert ledger_entries(body) == [("a", ["AC1"], ["AC2"]), ("b", [], []), ("c", [], ["AC3"])]


def test_a_wrapped_entry_is_scored_whole():
    body = slices("- a long value claim that wraps\n  across lines (`task/w`, closed 2026-01-01,\n  covers AC4)")
    assert ledger_entries(body) == [("w", ["AC4"], [])]


def test_a_fenced_or_commented_token_is_ignored():
    body = (
        "## Slices\n\n```\n- fake (`task/f`, closed 2026-01-01, covers AC1)\n```\n"
        "<!--\n- hid (`task/h`, closed 2026-01-01, covers AC2)\n-->\n"
        + slice_line("real", covers=["AC3"])
        + "\n"
    )
    assert ledger_entries(body) == [("real", ["AC3"], [])]


def test_a_task_mention_in_the_prose_is_not_the_entry_id():
    body = slices("- see `task/other` for context (`task/own`, closed 2026-01-01, covers AC1)")
    assert ledger_entries(body) == [("own", ["AC1"], [])]


def test_an_entry_without_a_trailing_task_parenthetical_is_skipped():
    assert ledger_entries(slices("- prose mentions (`task/x`, closed 2026-01-01) then more")) == []


def test_a_masked_slices_heading_does_not_open_the_section():
    body = "```\n## Slices\n```\n- stray (`task/x`, closed 2026-01-01, covers AC1)\n"
    assert ledger_entries(body) == []


def test_a_second_slices_heading_is_an_error():
    body = slices(slice_line("a")) + "\n## Slices\n" + slice_line("b")
    with pytest.raises(CriteriaError):
        ledger_entries(body)


@pytest.mark.parametrize("covers", ["AC1,AC1", "ac1", "AC1 AC2", "AC1,,AC2"])
def test_a_malformed_coverage_token_is_an_error_not_echoed(covers):
    body = slices(f"- c (`task/a`, closed 2026-01-01, covers {covers})")
    with pytest.raises(CriteriaError) as exc:
        ledger_entries(body)
    assert covers not in str(exc.value)


def test_a_malformed_partial_token_is_an_error():
    body = slices("- c (`task/a`, closed 2026-01-01, partially covers nope)")
    with pytest.raises(CriteriaError):
        ledger_entries(body)


# check_criteria_marks

SENTINEL = "ZZSENTINELZZ"


def marks(stored, new=None, history=None, reopen=(), close=()):
    return check_criteria_marks(stored, new if new is not None else stored, history, list(reopen), list(close))


def test_a_reopen_names_the_stored_entries_that_cover_or_partly_cover_it():
    stored = with_ledger(
        slice_line("a", covers=["AC1"]),
        slice_line("b", partial=["AC1"]),
        slice_line("c", covers=["AC2"]),
    )
    voided, refusals = marks(stored, reopen=["AC1"])
    assert refusals == []
    assert voided == {"AC1": ["a", "b"]}


def test_voided_is_empty_for_a_reopen_with_an_empty_ledger():
    assert marks(five(), reopen=["AC3"]) == ({}, [])


def test_voided_leaves_out_an_id_no_entry_names():
    stored = with_ledger(slice_line("a", covers=["AC1"]))
    voided, refusals = marks(stored, reopen=["AC1", "AC2"])
    assert refusals == [] and voided == {"AC1": ["a"]}


def test_closing_a_partially_covered_id_passes():
    stored = with_ledger(slice_line("a", partial=["AC4"]))
    assert marks(stored, close=["AC4"]) == ({}, [])


def test_closing_an_id_named_only_by_coverage_an_earlier_reopen_voided_passes():
    stored = with_ledger(slice_line("a", covers=["AC2"]))
    voided, refusals = marks(stored, history=[reopen(["AC2"], ["a"])], close=["AC2"])
    assert refusals == [] and voided == {}


def test_close_of_an_id_no_entry_names_is_refused_with_the_fix():
    stored = with_ledger(slice_line("a", covers=["AC1"]))
    _, refusals = marks(stored, close=["AC5"])
    assert refusals == [
        "AC5 cannot be closed: no slice has covered it, so the close would not count. "
        "Cover it in a slice, or remove the criterion by amend"
    ]


def test_close_of_an_already_closed_id_is_refused():
    stored = with_ledger(slice_line("a", covers=["AC3"]))
    _, refusals = marks(stored, history=[close("AC3")], close=["AC3"])
    assert refusals == ["AC3 is already closed; nothing to do"]


def test_a_close_after_a_reopen_of_a_closed_id_is_allowed():
    stored = with_ledger(slice_line("a", covers=["AC3"]))
    assert marks(stored, history=[close("AC3"), reopen(["AC3"], ["a"])], close=["AC3"]) == ({}, [])


def test_a_non_canonical_id_is_refused_with_the_canonical_form():
    _, refusals = marks(five(), reopen=["AC02"])
    assert refusals == ["AC02 is not a criterion identifier; use AC2"]


@pytest.mark.parametrize("bad", [SENTINEL, "AC0", "AC" + "9" * 40, "", "AC1\nAC2"])
def test_a_hostile_id_is_refused_without_being_echoed(bad):
    _, refusals = marks(five(), close=[bad])
    assert len(refusals) == 1
    assert "not a criterion identifier" in refusals[0]
    assert bad not in refusals[0] or bad == ""


def test_an_undeclared_id_is_refused_against_the_post_amend_body():
    new = spec(*[ac(n) for n in (1, 2, 3, 4)])
    _, refusals = marks(five(), new, reopen=["AC5"])
    assert refusals == ["AC5 is not declared in the amended body; name a criterion the spec declares"]


def test_an_id_declared_only_by_the_post_amend_body_is_accepted():
    new = spec(*[ac(n) for n in range(1, 7)])
    stored = with_ledger(slice_line("a", covers=["AC1"]))
    _, refusals = marks(stored, new, reopen=["AC6"])
    assert refusals == []


def test_an_id_in_both_lists_is_refused_once():
    _, refusals = marks(five(), reopen=["AC2"], close=["AC2"])
    assert refusals == ["AC2 is named to reopen and to close; choose one"]


def test_a_repeated_id_is_refused():
    _, refusals = marks(five(), reopen=["AC2", "AC2"])
    assert refusals == ["AC2 is named more than once; name it once"]


def test_a_reopen_is_refused_when_a_stored_entry_has_an_off_shape_task_id():
    stored = with_ledger(slice_line("Bad Id", covers=["AC1"]))
    _, refusals = marks(stored, reopen=["AC1"])
    assert len(refusals) == 1
    assert "task id" in refusals[0] and "Bad Id" not in refusals[0]


def test_an_off_shape_task_id_does_not_stop_a_close():
    stored = with_ledger(slice_line("Bad Id", covers=["AC1"]))
    assert marks(stored, close=["AC1"]) == ({}, [])


def test_an_unreadable_ledger_is_a_refusal_not_an_exception():
    stored = with_ledger(slice_line("a", covers=["AC1"])) + "\n## Slices\n"
    _, refusals = marks(stored, reopen=["AC1"])
    assert len(refusals) == 1 and "Slices" in refusals[0]


def test_no_marks_requested_checks_nothing():
    assert marks(five() + "\n## Slices\n## Slices\n") == ({}, [])


def test_a_malformed_history_raises():
    with pytest.raises(CriteriaError):
        marks(five(), history=[{"criteria": {}}], close=["AC1"])
