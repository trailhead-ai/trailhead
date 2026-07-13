"""Unit tests for the shared `repo:pr_number[:extra]` pair parser.

Ported from the retired ``test_pr_pair.py`` (which loaded ``_pr_pair.py`` by
file path) onto the now-importable ``portage.pairs`` module — the parser moved
into the package with the CLI refactor, its contract unchanged.
"""

from __future__ import annotations

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage.pairs import PairFormatError, split_pair


class TestSplitPair:
    def test_valid_two_field_pair(self):
        assert split_pair("api:42") == ("api", "42")

    def test_missing_colon_raises(self):
        with pytest.raises(PairFormatError, match="bad pair format"):
            split_pair("no-colon-here")

    def test_non_digit_pr_number_raises(self):
        with pytest.raises(PairFormatError, match="must be all digits"):
            split_pair("api:abc")

    def test_three_field_pair_with_max_parts_3(self):
        assert split_pair("api:42:member", max_parts=3) == ("api", "42", "member")

    def test_two_field_pair_still_valid_with_max_parts_3(self):
        assert split_pair("api:42", max_parts=3) == ("api", "42")

    def test_extra_colon_beyond_max_parts_2_is_rejected_as_bad_pr_number(self):
        # split(":", max_parts - 1) with max_parts=2 folds everything after
        # the first colon into the pr_number field, so it fails digit validation.
        with pytest.raises(PairFormatError, match="must be all digits"):
            split_pair("api:42:extra", max_parts=2)
