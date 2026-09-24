"""`camp bookmark` / `camp resume` redirect to `camp attach`.

`launch`'s own redirect is covered by `test_verb_aliases.py` and
`test_cli_dispatch_split.py`; this file covers `bookmark` and `resume`
redirecting to `attach`, plus `_addressable_harnesses`'s own harness
resolution.

Test contract:
- Each retired spelling, run through the CLI, answers with `camp attach` —
  the replacement an operator who typed the retired verb yesterday needs —
  rather than the bare-slug refusal, which answers a question about slugs.
  The answer varies by which retired verb was typed.
- `_addressable_harnesses` resolves a declared group's harness, plus the
  always-probed default store, deduping when both resolve the same harness —
  the pool `camp remove`'s teardown guard, `camp transfer` and `camp doctor`
  all read.

That the retired verbs are absent from the live verb table is not tested here:
removal is not a behaviour, and such a test passes vacuously on any tree where
the verb never existed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _run(args: list[str], *, env: dict | None = None) -> subprocess.CompletedProcess:
    base_env = {**os.environ}
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, str(_PLUGIN_DIR / "cli" / "camp"), *args],
        capture_output=True,
        text=True,
        env=base_env,
    )


class _FakeHarness:
    name = "fake"

    def session_launch_env_unset(self):
        return []

    def session_launch_env_set(self, account, *, env=None):
        return {}

    def session_transcripts(self, workspace=None, *, env=None):
        return []


# ---------------------------------------------------------------------------
# the bookmark surface is gone
# ---------------------------------------------------------------------------


def test_camp_bookmark_points_at_the_replacement_verb(tmp_path: Path) -> None:
    """The operator typed a verb, not a slug — so the answer names the verb that
    replaced it, not the bare-slug rule, which describes something else."""
    result = _run(["bookmark"], env={"CAMP_CONFIG_DIR": str(tmp_path)})
    combined = result.stdout + result.stderr
    assert result.returncode != 0, result.stdout
    assert "camp attach" in combined
    assert "bare slug dispatch is no longer supported" not in combined


def test_camp_resume_points_at_the_replacement_verb(tmp_path: Path) -> None:
    result = _run(["resume", "some-ref"], env={"CAMP_CONFIG_DIR": str(tmp_path)})
    combined = result.stdout + result.stderr
    assert result.returncode != 0, result.stdout
    assert "camp attach" in combined
    assert "bare slug dispatch is no longer supported" not in combined


# ---------------------------------------------------------------------------
# the harness-pool helper still resolves a group's harness
# ---------------------------------------------------------------------------


def test_addressable_harnesses_resolves_a_groups_harness(monkeypatch) -> None:
    """`cli.session._addressable_harnesses` builds the harness pool `camp
    remove`, `camp transfer` and `camp doctor` all read."""
    import camp.cli.session as cli_session

    harness = _FakeHarness()
    seen: list[dict] = []

    def fake_harness_for(config):
        seen.append(config)
        return harness

    monkeypatch.setattr("camp.launch.profile.harness_for", fake_harness_for)
    monkeypatch.setattr(cli_session, "_harness_display_name", lambda h: "fake")

    group = {"group": {"name": "g"}}
    stores = cli_session._addressable_harnesses([group])
    assert [s.harness for s in stores] == [harness]
    # The default (no-account) store is always probed too, alongside
    # every declared group's store — it dedupes against `group`'s own
    # store here because `fake_harness_for` answers the SAME harness
    # for both calls, so `stores` still holds exactly one entry.
    assert seen == [group, {}]
