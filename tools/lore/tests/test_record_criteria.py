"""criteria: the one reader of a spec's criteria and their change-history state."""

import pytest

from lore.record.criteria import (
    CriteriaError,
    check_criteria_amend,
    criteria_state,
    declared_criteria,
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
    assert criteria_state(body, hist(["AC3"])) == {"declared": ["AC1", "AC2", "AC4"], "removed": ["AC3"]}


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
