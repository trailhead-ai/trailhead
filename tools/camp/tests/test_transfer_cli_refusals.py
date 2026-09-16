"""Test contract: the refusals `camp transfer-probe` and `camp transfer-receive`
emit for a missing required argument.

These two verbs are the far side of a transfer — invoked over SSH by the sending
host, never typed by an operator — so their refusals are read by a machine and
by whoever is debugging a transfer that stopped. Each one has to name the flag
that is missing, and for a phased argument, the phase it is missing FOR.

Nothing is declared ``required=True``: the checks run after the parse precisely
so the wording can say "for begin" rather than argparse's generic form. That
choice is what these tests pin — the flag named, the phase named, the stream,
and the exit code — because it is the part a later refactor of these call sites
could quietly change.

Each case varies which flag is withheld and asserts the refusal changes with it,
so a handler that named a constant flag, or refused unconditionally, goes red.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


@pytest.fixture()
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty camp config dir, so group loading succeeds with no groups and
    the phase checks below are reached without any real group on disk."""
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "config" / "groups").mkdir(parents=True)
    return tmp_path


def _refuse(fn, args: list[str], capsys) -> tuple[int, str, str]:
    with pytest.raises(SystemExit) as exc:
        fn(args)
    captured = capsys.readouterr()
    return exc.value.code, captured.err, captured.out


# ---------------------------------------------------------------------------
# transfer-probe — two required flags, refused in a fixed order.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "flag"),
    [
        ([], "--group"),
        (["--slug", "myslug"], "--group"),
        (["--group", "mygroup"], "--slug"),
    ],
    ids=["neither", "slug-only", "group-only"],
)
def test_transfer_probe_names_the_flag_that_is_missing(
    args: list[str], flag: str, isolated_config, capsys
) -> None:
    """Withholding a different flag changes which one the refusal names.

    The ``neither`` case also pins the ORDER: with both absent the refusal
    reports ``--group``, which is the one a caller fixes first.
    """
    from camp.cli.transfer import _cmd_transfer_probe_cli

    code, err, out = _refuse(_cmd_transfer_probe_cli, args, capsys)
    assert code == 1
    assert err == f"camp transfer-probe: {flag} is required\n"
    assert out == ""


# ---------------------------------------------------------------------------
# transfer-receive — the phase itself, then the flags that phase requires.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "got"),
    [([], "None"), (["nonsense"], "'nonsense'"), (["--group", "g"], "'--group'")],
    ids=["no-phase", "unknown-phase", "flag-where-phase-goes"],
)
def test_transfer_receive_requires_a_known_phase_and_quotes_what_it_got(
    args: list[str], got: str, isolated_config, capsys
) -> None:
    """The phase is positional and closed-set, so the refusal lists the whole
    set and echoes what arrived — including when a flag turns up in the slot."""
    from camp.cli.transfer import _cmd_transfer_receive_cli

    code, err, _out = _refuse(_cmd_transfer_receive_cli, args, capsys)
    assert code == 1
    assert err.startswith("camp transfer-receive: a phase of '")
    assert f"got {got}" in err
    for phase in ("begin", "claim", "conversations", "finish", "history", "worktree"):
        assert phase in err


@pytest.mark.parametrize("phase", ["begin", "claim", "conversations", "finish", "history", "worktree"])
@pytest.mark.parametrize(
    ("args", "flag"),
    [([], "--group"), (["--group", "mygroup"], "--slug")],
    ids=["no-group", "group-without-slug"],
)
def test_transfer_receive_requires_group_and_slug_in_every_phase(
    phase: str, args: list[str], flag: str, isolated_config, capsys
) -> None:
    """``--group`` and ``--slug`` are common to every phase, so the same two
    refusals must hold across all six — a phase that skipped either would
    address a workspace nobody named."""
    from camp.cli.transfer import _cmd_transfer_receive_cli

    code, err, _out = _refuse(_cmd_transfer_receive_cli, [phase, *args], capsys)
    assert code == 1
    assert err == f"camp transfer-receive: {flag} is required\n"


@pytest.mark.parametrize(
    ("phase", "flag"),
    [
        ("begin", "--owner"),
        ("claim", "--owner"),
        ("conversations", "--session-id"),
        ("history", "--member"),
        ("worktree", "--member"),
    ],
)
def test_a_phase_names_its_own_missing_flag_and_the_phase_it_is_for(
    phase: str, flag: str, isolated_config, capsys
) -> None:
    """The reason nothing is declared ``required=True``: the refusal says which
    PHASE the flag is required for.

    Varying the phase varies both halves of the message, so a handler that
    hardcoded either one goes red.
    """
    from camp.cli.transfer import _cmd_transfer_receive_cli

    code, err, _out = _refuse(
        _cmd_transfer_receive_cli,
        [phase, "--group", "mygroup", "--slug", "myslug"],
        capsys,
    )
    assert code == 1
    assert err == f"camp transfer-receive: {flag} is required for {phase}\n"


def test_conversations_requires_subpath_once_session_id_is_supplied(
    isolated_config, capsys
) -> None:
    """`conversations` requires two of its own; supplying the first must not
    satisfy the second. This is the case a single-check handler would pass."""
    from camp.cli.transfer import _cmd_transfer_receive_cli

    code, err, _out = _refuse(
        _cmd_transfer_receive_cli,
        ["conversations", "--group", "g", "--slug", "s", "--session-id", "sid"],
        capsys,
    )
    assert code == 1
    assert err == "camp transfer-receive: --subpath is required for conversations\n"


def test_a_flag_belonging_to_another_phase_is_refused_not_ignored(
    isolated_config, capsys
) -> None:
    """The parser is built PER PHASE so another phase's flag is refused rather
    than accepted and dropped — `--member` means nothing to `begin`."""
    from camp.cli.transfer import _cmd_transfer_receive_cli

    code, err, _out = _refuse(
        _cmd_transfer_receive_cli,
        ["begin", "--group", "g", "--slug", "s", "--member", "repo_a"],
        capsys,
    )
    assert code == 1
    assert err == "camp transfer-receive: unknown flag '--member'\n"
