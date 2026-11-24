"""The gateway test support waits for a freshly started fake harness to become identifiable."""

from __future__ import annotations

import pytest

from trailhead.tests._gateway_support import await_session_key


def _resolver(answers):
    calls = iter(answers)
    return lambda **kwargs: next(calls)


def test_a_key_that_resolves_after_a_few_misses_is_returned():
    resolve = _resolver([None, None, "ws-7-9"])
    assert await_session_key(7, resolve=resolve, timeout=5, interval=0) == "ws-7-9"


def test_a_key_that_resolves_at_once_is_returned():
    assert await_session_key(7, resolve=_resolver(["ws-7-9"]), timeout=5, interval=0) == "ws-7-9"


def test_a_harness_that_never_resolves_fails_with_the_stated_reason():
    with pytest.raises(AssertionError, match="fake harness did not resolve to a session key"):
        await_session_key(7, resolve=lambda **kwargs: None, timeout=0.05, interval=0.01)
