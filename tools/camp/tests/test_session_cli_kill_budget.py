"""`camp kill` resolves its re-poll budget from the environment.

The stop engine reads no environment of its own — it is data-to-data plus one
injected tmux seam — so the override on its re-poll budget has to be resolved
at the edge that already holds the environment, and passed in like any other
caller's choice. This is that wiring: what the verb resolves is what the engine
is asked to spend.

Why the budget is overridable at all: it is sized for a busy tmux server, so a
test driving a session that never goes waits the whole of it in real seconds to
observe the failure it asserts, and those tests drive camp as a subprocess,
where the parameter alone cannot reach.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _budget_asked_for(monkeypatch, env_value):
    """Run `camp kill` far enough to capture the budget it hands the engine."""
    from camp.cli import session as session_cli
    from camp.launch import stop as stop_module

    captured: dict[str, float] = {}

    def _capture(ref, **kwargs):
        captured["poll_timeout"] = kwargs["poll_timeout"]
        raise SystemExit(0)

    monkeypatch.setattr(stop_module, "stop_session", _capture)
    # The verb resolves a session pool before it stops anything; neither the
    # pool nor the refusal wording is the subject here.
    monkeypatch.setattr(
        session_cli, "_parsable_groups", lambda: [{"name": "g", "members": []}]
    )
    monkeypatch.setattr(
        session_cli,
        "_session_pool",
        lambda *a, **k: ([object()], [object()], [object()], [object()]),
    )

    env = {} if env_value is None else {"CAMP_TEST_STOP_POLL_TIMEOUT_SECONDS": env_value}
    with pytest.raises(SystemExit):
        session_cli._cmd_kill_cli(["some-ref"], env=env)
    return captured["poll_timeout"]


def test_an_override_is_what_the_engine_is_asked_to_spend(monkeypatch):
    from camp.launch import stop as stop_module

    overridden = _budget_asked_for(monkeypatch, "1.5")
    unset = _budget_asked_for(monkeypatch, None)

    assert overridden == pytest.approx(1.5)
    assert unset == pytest.approx(stop_module.POLL_TIMEOUT_SECONDS)


@pytest.mark.parametrize("bad", ["", "abc", "0", "-1"])
def test_a_malformed_or_non_positive_override_leaves_the_shipped_budget(monkeypatch, bad):
    """A stray setting must not shrink a real kill's window to nothing."""
    from camp.launch import stop as stop_module

    assert _budget_asked_for(monkeypatch, bad) == pytest.approx(
        stop_module.POLL_TIMEOUT_SECONDS
    )
