"""Unit tests for the shared `repo:pr_number[:extra]` pair parser used by
wait_for_actionable.py and merge_prs.py.

Loaded the same way test_portage_thin_scripts.py loads the scripts themselves
(by file path, via the shared `_script_loader.load_script` helper), since the
module lives alongside the thin scripts rather than in an importable package.
"""

from __future__ import annotations

import pytest

from _script_loader import load_script


@pytest.fixture()
def pr_pair():
    return load_script("_pr_pair")


class TestSplitPair:
    def test_valid_two_field_pair(self, pr_pair):
        assert pr_pair.split_pair("api:42") == ("api", "42")

    def test_missing_colon_raises(self, pr_pair):
        with pytest.raises(pr_pair.PairFormatError, match="bad pair format"):
            pr_pair.split_pair("no-colon-here")

    def test_non_digit_pr_number_raises(self, pr_pair):
        with pytest.raises(pr_pair.PairFormatError, match="must be all digits"):
            pr_pair.split_pair("api:abc")

    def test_three_field_pair_with_max_parts_3(self, pr_pair):
        assert pr_pair.split_pair("api:42:member", max_parts=3) == ("api", "42", "member")

    def test_two_field_pair_still_valid_with_max_parts_3(self, pr_pair):
        assert pr_pair.split_pair("api:42", max_parts=3) == ("api", "42")

    def test_extra_colon_beyond_max_parts_2_is_rejected_as_bad_pr_number(self, pr_pair):
        # split(":", max_parts - 1) with max_parts=2 folds everything after
        # the first colon into the pr_number field, so it fails digit validation.
        with pytest.raises(pr_pair.PairFormatError, match="must be all digits"):
            pr_pair.split_pair("api:42:extra", max_parts=2)
