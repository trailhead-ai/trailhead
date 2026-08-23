"""The bookmark surface is retired; the resume path it fronted still works.

Re-entering a session is `camp launch --resume <ref>`, addressed by unambiguous
prefix of the derived name or session id. The `camp bookmark` verb family, the
`camp resume` verb, the bookmark package, its skill, and its `camp rm` guard are
all gone, and the free-text note they carried has no replacement.

Test contract:
- `camp.bookmark` and `camp.cli.groupless` no longer import, and the bookmark
  skill directory is gone — the package cannot be reintroduced by accident.
- `bookmark` and `resume` are absent from `camp help` and from every live verb
  table; each spelling lands on a legacy redirect naming `camp launch --resume`,
  the replacement an operator who typed the retired verb yesterday needs.
- The launch, sessions, and resume paths each still resolve the same harness for
  the same group — the specific regression the deletion risks.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# the bookmark surface is gone
# ---------------------------------------------------------------------------


def test_the_bookmark_package_no_longer_imports() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("camp.bookmark")


def test_the_groupless_subverb_classifier_no_longer_imports() -> None:
    """It classified `bookmark ls`/`rm`; with those gone it had no other caller."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("camp.cli.groupless")


def test_the_bookmark_skill_directory_is_gone() -> None:
    assert not (_PLUGIN_DIR / "skills" / "bookmark").exists()


def test_neither_verb_is_a_live_verb() -> None:
    """Retired, not renamed-away: neither spelling resolves to a live verb.

    Both stay RESERVED — a redirect key has to be, or bare-slug validation would
    claim the token before the redirect could name its replacement.
    """
    from camp.spine import RESERVED
    from camp.workspace.verb_taxonomy import resolve_verb

    for verb in ("bookmark", "resume"):
        assert verb in RESERVED
        assert resolve_verb(verb) == (verb, "legacy")


def test_neither_verb_appears_in_help() -> None:
    result = _run(["help"])
    assert result.returncode == 0, result.stderr
    assert "camp bookmark" not in result.stdout
    assert "camp resume" not in result.stdout


def test_camp_bookmark_points_at_the_replacement_verb(tmp_path: Path) -> None:
    """The operator typed a verb, not a slug — so the answer names the verb that
    replaced it, not the bare-slug rule, which describes something else."""
    result = _run(["bookmark"], env={"CAMP_CONFIG_DIR": str(tmp_path)})
    combined = result.stdout + result.stderr
    assert result.returncode != 0, result.stdout
    assert "camp launch --resume" in combined
    assert "bare slug dispatch is no longer supported" not in combined


def test_camp_resume_points_at_the_replacement_verb(tmp_path: Path) -> None:
    result = _run(["resume", "some-ref"], env={"CAMP_CONFIG_DIR": str(tmp_path)})
    combined = result.stdout + result.stderr
    assert result.returncode != 0, result.stdout
    assert "camp launch --resume" in combined
    assert "bare slug dispatch is no longer supported" not in combined


# ---------------------------------------------------------------------------
# the three consumers still resolve the same harness
# ---------------------------------------------------------------------------


def test_launch_path_resolves_the_group_harness(monkeypatch) -> None:
    """`camp launch`'s confirm step asks the group's harness (cli.session:launch_and_confirm)."""
    import camp.cli.session as cli_session

    harness = _FakeHarness()
    seen: list[dict] = []
    confirmed: list[object] = []

    def fake_harness_for(group):
        seen.append(group)
        return harness

    launched = type("LaunchedSession", (), {
        "session_id": "sess-1", "launch_dir": "/tmp/ws", "tmux_name": "camp-ws-abc",
        "pane_env": {},
    })()
    monkeypatch.setattr("camp.launch.profile.harness_for", fake_harness_for)
    monkeypatch.setattr("camp.launch.session.launch_session", lambda *a, **k: launched)
    monkeypatch.setattr(
        "camp.launch.session.confirm_session",
        lambda harness, _launched, env=None: confirmed.append(harness),
    )

    group = {"group": {"name": "g"}}
    assert cli_session.launch_and_confirm(group, "ws", env={}) is launched
    assert seen == [group]
    assert confirmed == [harness]


def test_sessions_path_resolves_addressable_harnesses(monkeypatch) -> None:
    """`camp sessions` builds its harness pool via cli.session:_addressable_harnesses."""
    import camp.cli.session as cli_session

    harness = _FakeHarness()
    seen: list[dict] = []

    def fake_harness_for(config):
        seen.append(config)
        return harness

    monkeypatch.setattr("camp.launch.profile.harness_for", fake_harness_for)
    monkeypatch.setattr(cli_session, "_harness_display_name", lambda h: "fake")

    group = {"group": {"name": "g"}}
    assert cli_session._addressable_harnesses([group]) == [harness]
    assert seen == [group]


def test_resume_path_resolves_the_group_harness(monkeypatch) -> None:
    """`camp launch --resume`'s enumeration asks the group's harness (cli.session:_enumerate_sessions)."""
    import camp.cli.session as cli_session

    harness = _FakeHarness()
    seen: list[dict] = []

    def fake_harness_for(group):
        seen.append(group)
        return harness

    monkeypatch.setattr("camp.launch.profile.harness_for", fake_harness_for)
    monkeypatch.setattr(
        "camp.launch.session.enumerate_records", lambda h, ws, env: ["record"]
    )

    group = {"group": {"name": "g"}}
    assert cli_session._enumerate_sessions(group, None, {}) == ["record"]
    assert seen == [group]
