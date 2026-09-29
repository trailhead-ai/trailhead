"""The shared cross-host group resolve-and-refuse step and slug guard.

Both verbs that hand a group-scoped verb to another machine (`attach`, `new`)
call these; the verb name is the only thing that differs in what they say.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _session():
    return importlib.import_module("camp.cli.session")


def _group(name: str) -> dict:
    return {"group": {"name": name}}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    return {
        "CAMP_CONFIG_DIR": str(cfg),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
        "HOME": str(tmp_path),
    }


def _groups(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    monkeypatch.setattr(
        _session(), "_parsable_groups", lambda: [_group(n) for n in names]
    )


def _refusal(capsys: pytest.CaptureFixture, call) -> str:
    with pytest.raises(SystemExit) as exc:
        call()
    assert exc.value.code == 1
    return capsys.readouterr().err.strip()


@pytest.mark.parametrize("verb", ["new", "attach"])
def test_unknown_explicit_group_names_the_verb_and_known_groups(
    verb, env, monkeypatch, capsys
) -> None:
    _groups(monkeypatch, "g")
    err = _refusal(
        capsys, lambda: _session().resolve_cross_host_group(verb, "x", env)
    )
    assert err == f"camp {verb}: group 'x' is not configured on this machine (known: g)"


def test_known_list_reads_none_when_no_groups_are_configured(
    env, monkeypatch, capsys
) -> None:
    _groups(monkeypatch)
    err = _refusal(
        capsys, lambda: _session().resolve_cross_host_group("new", "x", env)
    )
    assert err.endswith("(known: none)")


def test_no_group_and_unclaimed_cwd_prints_the_verbs_own_needs_group_message(
    env, monkeypatch, tmp_path, capsys
) -> None:
    from camp.workspace.verb_taxonomy import needs_group_message

    _groups(monkeypatch, "g")
    unclaimed = tmp_path / "unclaimed"
    unclaimed.mkdir()
    monkeypatch.chdir(unclaimed)
    new_err = _refusal(
        capsys, lambda: _session().resolve_cross_host_group("new", None, env)
    )
    attach_err = _refusal(
        capsys, lambda: _session().resolve_cross_host_group("attach", None, env)
    )
    assert new_err == needs_group_message("new")
    assert attach_err == needs_group_message("attach")
    assert new_err != attach_err


def test_cwd_inside_a_member_returns_that_groups_name(
    env, monkeypatch, tmp_path
) -> None:
    _groups(monkeypatch, "g", "h")
    cwd = tmp_path / "state" / "g" / "worktrees" / "tree"
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)
    assert _session().resolve_cross_host_group("new", None, env) == "g"


def test_explicit_group_wins_over_the_cwd(env, monkeypatch, tmp_path) -> None:
    _groups(monkeypatch, "g", "h")
    cwd = tmp_path / "state" / "g" / "worktrees" / "tree"
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)
    assert _session().resolve_cross_host_group("attach", "h", env) == "h"


@pytest.mark.parametrize("verb", ["new", "attach"])
def test_group_failing_confinement_refuses_with_the_confinement_message(
    verb, env, monkeypatch, capsys
) -> None:
    from camp.group.resolve import GroupConfinementError, validate_group_name

    try:
        validate_group_name("x;id")
    except GroupConfinementError as exc:
        expected = str(exc)
    _groups(monkeypatch, "x;id")
    err = _refusal(
        capsys, lambda: _session().resolve_cross_host_group(verb, "x;id", env)
    )
    assert err == expected


@pytest.mark.parametrize("verb", ["new", "attach"])
def test_dash_leading_group_refuses_as_flag_shaped(
    verb, env, monkeypatch, capsys
) -> None:
    _groups(monkeypatch, "-x")
    err = _refusal(
        capsys, lambda: _session().resolve_cross_host_group(verb, "-x", env)
    )
    assert err.startswith(f"camp {verb}: group '-x'")


def test_group_with_an_interior_dash_resolves(env, monkeypatch) -> None:
    _groups(monkeypatch, "x-y")
    assert _session().resolve_cross_host_group("new", "x-y", env) == "x-y"


@pytest.mark.parametrize("verb", ["new", "attach"])
@pytest.mark.parametrize(
    "slugs", [[], ["a", "b"], [""], ["   "], ["-x"]], ids=repr
)
def test_slug_guard_refuses(verb, slugs, capsys) -> None:
    err = _refusal(capsys, lambda: _session().require_one_raw_slug(verb, slugs))
    assert err.startswith(f"camp {verb}: ")


def test_slug_guard_wording_for_count_and_shape(capsys) -> None:
    assert _refusal(
        capsys, lambda: _session().require_one_raw_slug("attach", ["a", "b"])
    ) == "camp attach: --host requires exactly one workspace slug, got 2"
    assert _refusal(
        capsys, lambda: _session().require_one_raw_slug("new", ["-x"])
    ) == "camp new: '-x' is not a valid workspace slug"


@pytest.mark.parametrize("raw", [" -x", "a", " a "])
def test_slug_guard_returns_the_raw_value_unstripped(raw) -> None:
    assert _session().require_one_raw_slug("new", [raw]) == raw


_FAR_SIDE_REFUSED = [
    "a/b",
    "a\\b",
    "a$b",
    "a`b",
    "a|b",
    "a;b",
    "a&b",
    "a\nb",
    "a\x00b",
    "a..b",
]


@pytest.mark.parametrize("verb", ["new", "attach"])
@pytest.mark.parametrize("raw", _FAR_SIDE_REFUSED, ids=repr)
def test_slug_guard_refuses_what_the_far_slug_rule_refuses(verb, raw, capsys) -> None:
    err = _refusal(capsys, lambda: _session().require_one_raw_slug(verb, [raw]))
    assert err.startswith(f"camp {verb}: ")
    assert repr(raw) in err


@pytest.mark.parametrize("verb", ["new", "attach"])
@pytest.mark.parametrize("raw", ["abc", "a-b", "A B", " -x"], ids=repr)
def test_slug_guard_passes_slugs_the_far_side_normalizes_raw(verb, raw) -> None:
    assert _session().require_one_raw_slug(verb, [raw]) == raw
