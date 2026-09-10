"""Every TOML the README documents is run through the code that consumes it.

A documented spelling the real parser would reject, and a backticked deny entry
the launch gate does not enforce, are both cheap to introduce and expensive to
discover. Each documented block is fed to the production parser, and each named
entry checked against the production deny list — the README's examples are the
inputs, not the assertions.

What the README *says* about the deny list is not checked here: the behaviours
that prose describes — a declared account extending the list, a relative one
contributing nothing — are pinned by execution in ``test_launch_eligibility``.
"""

from __future__ import annotations

import importlib
import io
import re
import shlex
import sys
import tomllib
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.group.config import _parse_launch  # noqa: E402
from camp.host.config import load_hosts  # noqa: E402
from camp.launch.eligibility import CREDENTIAL_DENY_ENTRIES  # noqa: E402
from camp.spine import cmd_help  # noqa: E402

README = Path(__file__).resolve().parents[1] / "README.md"

_TOML_BLOCK = re.compile(r"```toml\n(.*?)```", re.DOTALL)

#: The exact invocation lines the README's "Remote hosts" section documents,
#: one call form per line, no trailing comment — chosen so each is directly
#: executable rather than needing bracket-notation interpretation.
_HOST_INVOCATION = re.compile(
    r"^camp (?:list|sessions) --host <name>(?: --json)?$", re.MULTILINE
)

#: The exact invocation lines the README's "Remote hosts" section documents
#: for the `-a`/`--all-hosts`/`-ag` widen-every-machine option — the same
#: shape as `_HOST_INVOCATION` above, one call form per line. `<name>` is
#: the group placeholder the README uses alongside `-a`/`--all-hosts` (never
#: alongside `-ag`, which needs no group).
_ALL_HOSTS_INVOCATION = re.compile(
    r"^camp (?:list|sessions) (?:(?:-a|--all-hosts) --group <name>|-ag)(?: --json)?$",
    re.MULTILINE,
)


def _toml_blocks() -> list[str]:
    return _TOML_BLOCK.findall(README.read_text())


def _host_invocation_lines() -> list[str]:
    return _HOST_INVOCATION.findall(README.read_text())


def _all_hosts_invocation_lines() -> list[str]:
    return _ALL_HOSTS_INVOCATION.findall(README.read_text())


@pytest.mark.parametrize("block", _toml_blocks())
def test_every_documented_toml_block_is_valid_toml(block: str) -> None:
    tomllib.loads(block)


def test_every_documented_launch_block_parses_under_the_real_parser() -> None:
    """A documented `[launch]` spelling the production parser rejects is a lie."""
    seen = 0
    for block in _toml_blocks():
        raw = tomllib.loads(block).get("launch")
        if raw is None:
            continue
        seen += 1
        _parse_launch(raw, README)
    assert seen, "no documented [launch] block found — the README lost its example"


def test_the_documented_account_key_is_the_spelling_the_parser_accepts() -> None:
    accounts = [
        tomllib.loads(b)["launch"]["account"]
        for b in _toml_blocks()
        if "account" in tomllib.loads(b).get("launch", {})
    ]
    assert accounts, "the README no longer documents the account key"
    for value in accounts:
        parsed = _parse_launch({"account": value}, README)
        assert parsed is not None and parsed["account"] == value


def _deny_paragraph() -> str:
    """The prose block that enumerates the fixed deny entries."""
    text = README.read_text()
    start = text.index("**A credential deny list overrides the allowlist")
    end = text.index("\n\n", text.index("fixed\nin camp's code", start))
    return text[start:end]


def test_every_deny_entry_the_readme_names_is_really_in_the_floor() -> None:
    """The prose must not advertise protection the code does not provide.

    Scoped to the deny-list paragraph and applied to EVERY home-relative path
    it backticks, so adding an unenforced entry to the prose fails here rather
    than reading as a promise camp does not keep.
    """
    named = re.findall(r"`(~[^`]*)`", _deny_paragraph())
    assert named, "deny paragraph names no entries — the extractor has drifted"
    missing = sorted({n for n in named if n not in CREDENTIAL_DENY_ENTRIES})
    assert not missing, f"README names deny entries the code does not enforce: {missing}"


# ---------------------------------------------------------------------------
# --host — the hosts.toml block, the documented option, the invocation forms.
#
# Every property here is checked by running a real consumer (the host loader,
# the dispatcher, the transport seam) over the documented artifact — never by
# reading the README's or spine.py's text for a phrase.
# ---------------------------------------------------------------------------


def test_the_documented_hosts_toml_block_parses_under_the_real_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The README's `hosts.toml` example must be a block the production
    loader accepts, yielding the host and the resolved `ssh`/`camp_bin`
    values the surrounding prose states — not merely valid TOML."""
    blocks = [b for b in _toml_blocks() if "[hosts." in b]
    assert blocks, "README no longer documents a [hosts.<name>] block"

    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))

    for block in blocks:
        (cfg / "hosts.toml").write_text(block, encoding="utf-8")
        hosts = load_hosts()
        assert hosts["andromeda"].ssh == "andromeda.lan"
        assert (
            hosts["andromeda"].camp_bin
            == "/home/tom/.local/state/trailhead/bin/camp"
        )


def test_help_documents_host_option_that_the_dispatcher_actually_treats_as_recognized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`camp --help` must document `--host` for `list` and `sessions` only
    where the dispatcher truly treats it as a recognized option — proven by
    contrast against a genuinely unrecognized flag, which this dispatcher
    silently ignores rather than refusing."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_help([])
    help_text = buf.getvalue()

    documented = sorted(set(re.findall(r"camp (list|sessions) --host <name>", help_text)))
    assert documented == ["list", "sessions"], (
        "camp --help no longer documents --host for both list and sessions"
    )

    dispatch = importlib.import_module("camp.cli.dispatch")
    cfg = tmp_path / "empty-config"
    cfg.mkdir()
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    # The typo-flag contrast for `list` falls through to the standalone
    # no-group listing, which reads $WORKSPACE_ROOT/code — set it so that
    # fallback never reaches the developer's real home.
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    def _invoke(argv: list[str]) -> tuple[int, str]:
        monkeypatch.setattr(sys, "argv", argv)
        try:
            dispatch.main()
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, capsys.readouterr().err

    for verb in documented:
        host_code, host_err = _invoke(["camp", verb, "--host", "ghost-host"])
        typo_code, typo_err = _invoke(["camp", verb, "--host-typo", "ghost-host"])

        # --host must reach ITS OWN refusal — proving the dispatcher really
        # reads it as the documented option — never the outcome a genuinely
        # unrecognized flag (--host-typo) produces.
        assert "ghost-host" in host_err and "hosts.toml" in host_err
        assert "ghost-host" not in typo_err
        assert (host_code, host_err) != (typo_code, typo_err)


def test_documented_host_invocation_forms_produce_an_answer_against_a_stub_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Every `camp list --host <name>` / `camp sessions --host <name>` form
    the README documents must actually run and answer — not refuse — when
    the named host resolves and the transport answers."""
    lines = _host_invocation_lines()
    assert lines, "README no longer documents a --host invocation form"

    dispatch = importlib.import_module("camp.cli.dispatch")
    transport = importlib.import_module("camp.host.transport")

    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "hosts.toml").write_text("[hosts.andromeda]\n", encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))

    outcome = transport.Answered(stdout="[]", stderr="", exit_code=0)
    monkeypatch.setattr(transport, "run_camp", lambda host, remote_argv, **kw: outcome)

    for line in lines:
        argv = shlex.split(line.replace("<name>", "andromeda"))
        assert argv[0] == "camp"
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exc:
            dispatch.main()
        assert exc.value.code == 0, f"{line!r} did not answer: exit {exc.value.code}"
        captured = capsys.readouterr()
        if "--json" in argv:
            assert captured.out.strip() == "[]"
        else:
            assert captured.out == ""



# ---------------------------------------------------------------------------
# --all-hosts / -a — same contract as --host above, run through the real
# dispatcher and help emitter rather than checked as prose.
# ---------------------------------------------------------------------------


def test_help_documents_all_hosts_option_that_the_dispatcher_actually_treats_as_recognized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`camp --help` must document `--all-hosts` for `list` and `sessions`
    only where the dispatcher truly treats it as recognized — proven by
    contrast against a genuinely unrecognized flag."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_help([])
    help_text = buf.getvalue()

    documented = sorted(
        set(re.findall(r"camp (list|sessions) --all-hosts\|-a", help_text))
    )
    assert documented == ["list", "sessions"], (
        "camp --help no longer documents --all-hosts for both list and sessions"
    )

    dispatch = importlib.import_module("camp.cli.dispatch")
    cfg = tmp_path / "empty-config"
    cfg.mkdir()
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))

    def _invoke(argv: list[str]) -> tuple[int, str]:
        monkeypatch.setattr(sys, "argv", argv)
        try:
            dispatch.main()
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, capsys.readouterr().err

    for verb in documented:
        # No group configured at all: --all-hosts must reach ITS OWN
        # "needs a group" refusal, naming -ag and --group — never the
        # unrelated "no group resolved from cwd" message a plain --group
        # miss produces, and never an unrecognized-flag no-op.
        all_hosts_code, all_hosts_err = _invoke(["camp", verb, "-a"])
        typo_code, typo_err = _invoke(["camp", verb, "-a-typo"])

        assert "-ag" in all_hosts_err and "--group" in all_hosts_err, all_hosts_err
        assert "-ag" not in typo_err
        assert (all_hosts_code, all_hosts_err) != (typo_code, typo_err)


def test_documented_all_hosts_invocation_forms_produce_an_answer_against_a_stub_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Every `-a`/`--all-hosts`/`-ag` form the README documents must actually
    run and answer — not refuse — when a group resolves and the transport
    answers."""
    lines = _all_hosts_invocation_lines()
    assert lines, "README no longer documents an --all-hosts invocation form"

    dispatch = importlib.import_module("camp.cli.dispatch")
    transport = importlib.import_module("camp.host.transport")

    cfg = tmp_path / "config"
    groups_dir = cfg / "groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "andromeda.toml").write_text(
        '[group]\nname = "andromeda"\n\n'
        '[[members]]\nname = "member-a"\nrepo_root = "/tmp/fake-member-a"\n'
    )
    (cfg / "hosts.toml").write_text("[hosts.andromeda]\n", encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))

    outcome = transport.Answered(stdout="[]", stderr="", exit_code=0)
    monkeypatch.setattr(transport, "run_camp", lambda host, remote_argv, **kw: outcome)

    import camp.launch.session as launch_session

    monkeypatch.setattr(launch_session, "enumerate_records", lambda *a, **k: [])

    for line in lines:
        argv = shlex.split(line.replace("<name>", "andromeda"))
        assert argv[0] == "camp"
        monkeypatch.setattr(sys, "argv", argv)
        try:
            dispatch.main()
            code = 0
        except SystemExit as exc:
            code = exc.code
        assert code == 0, f"{line!r} did not answer: exit {code}, stderr={capsys.readouterr().err!r}"
        captured = capsys.readouterr()
        if "--json" in argv:
            assert captured.out.strip() == "[]"
