"""The gateway test support waits for a freshly started fake harness to become identifiable."""

from __future__ import annotations

import pytest

from trailhead.tests._gateway_support import await_harness


def _resolver(answers):
    calls = iter(answers)
    return lambda **kwargs: next(calls) if kwargs == {"start_pid": 7} else pytest.fail(f"asked for {kwargs}")


def test_the_harness_that_resolves_after_a_few_misses_is_returned():
    resolve = _resolver([None, None, (7, 9)])
    assert await_harness(7, resolve=resolve, timeout=5, interval=0) == (7, 9)


def test_the_harness_that_resolves_at_once_is_returned():
    assert await_harness(7, resolve=_resolver([(7, 9)]), timeout=5, interval=0) == (7, 9)


def test_an_outer_session_found_before_the_child_is_identifiable_is_not_accepted():
    resolve = _resolver([(3, 1), None, (7, 9)])
    assert await_harness(7, resolve=resolve, timeout=5, interval=0) == (7, 9)


def test_a_harness_that_never_resolves_fails_with_the_stated_reason():
    with pytest.raises(AssertionError, match="fake harness did not resolve to a session key"):
        await_harness(7, resolve=lambda **kwargs: None, timeout=0.05, interval=0.01)
