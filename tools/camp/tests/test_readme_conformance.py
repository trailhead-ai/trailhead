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
import json
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

from camp.cli.dispatch import _ALL_GROUPS_VERBS, _ALL_HOSTS_VERBS, _HOST_VERBS  # noqa: E402
from camp.group.config import _parse_launch  # noqa: E402
from camp.host.config import load_hosts  # noqa: E402
from camp.launch.eligibility import CREDENTIAL_DENY_ENTRIES  # noqa: E402
from camp.spine import cmd_help  # noqa: E402
from camp.workspace.verb_taxonomy import LEGACY_REDIRECTS  # noqa: E402

README = Path(__file__).resolve().parents[1] / "README.md"

_TOML_BLOCK = re.compile(r"```toml\n(.*?)```", re.DOTALL)


def _live_host_verbs(table: frozenset[str]) -> list[str]:
    """The verbs from *table* (`_HOST_VERBS` or `_ALL_HOSTS_VERBS`) that still
    document a `camp <verb> --host <name> [--json]`-shaped invocation — the
    group-listing family `_ALL_GROUPS_VERBS` names — with every retired verb
    (`LEGACY_REDIRECTS`) excluded.

    Structural, not a hardcoded name list: `attach`/`kill`/`doctor` are
    excluded because none of them is in `_ALL_GROUPS_VERBS` (`attach`'s
    `--host` form carries a `<slug>` and answers a different shape
    entirely, each covered by its own conformance check), not because this
    function names them.
    """
    return sorted((table & _ALL_GROUPS_VERBS) - LEGACY_REDIRECTS.keys())


def _verb_alternation(table: frozenset[str]) -> str:
    """A regex alternation over every verb in *table*, sorted for a
    deterministic pattern. Deliberately the WHOLE table, not the narrower
    `_live_host_verbs` answer — so a help text or README line that (wrongly)
    still documents a retired verb's `--host`/`--all-hosts` form is still
    matched, and the mismatch against `_live_host_verbs` is what catches it."""
    return "|".join(re.escape(verb) for verb in sorted(table))


#: The exact invocation lines the README's "Remote hosts" section documents,
#: one call form per line, no trailing comment — chosen so each is directly
#: executable rather than needing bracket-notation interpretation.
_HOST_INVOCATION = re.compile(
    rf"^camp (?:{_verb_alternation(_HOST_VERBS)}) --host <name>(?: --json)?$", re.MULTILINE
)

#: The exact invocation lines the README's "Remote hosts" section documents
#: for the `-a`/`--all-hosts`/`-ag` widen-every-machine option — the same
#: shape as `_HOST_INVOCATION` above, one call form per line. `<name>` is
#: the group placeholder the README uses alongside `-a`/`--all-hosts` (never
#: alongside `-ag`, which needs no group).
_ALL_HOSTS_INVOCATION = re.compile(
    rf"^camp (?:{_verb_alternation(_ALL_HOSTS_VERBS)}) "
    r"(?:(?:-a|--all-hosts) --group <name>|-ag)(?: --json)?$",
    re.MULTILINE,
)


def _documented_host_verbs(text: str, table: frozenset[str]) -> list[str]:
    """Every verb in *table* documented with `--host <name>` in *text* — the
    exact extraction `test_help_documents_host_option_that_the_dispatcher_
    actually_treats_as_recognized` runs against the real `camp --help`
    output. Shared rather than re-built per test, so a pattern that stops
    matching what the real help text prints breaks every caller, not just
    the one against real output."""
    pattern = re.compile(rf"camp ({_verb_alternation(table)}) --host <name>")
    return sorted(set(pattern.findall(text)))


def test_documented_host_verbs_tracks_the_dispatch_tables_not_a_hardcoded_pair() -> None:
    """`_live_host_verbs` answers from the dispatch tables, not a fixed
    `["list", "sessions"]` literal — proven by feeding two different help
    texts through the real `_documented_host_verbs` extraction. A text
    that (wrongly) still documents the retired `sessions --host` form
    disagrees with the derived answer; one documenting only the live `list
    --host` form agrees."""
    stale = _documented_host_verbs("camp sessions --host <name> [--json]\n", _HOST_VERBS)
    assert stale != _live_host_verbs(_HOST_VERBS)

    live = _documented_host_verbs("camp list --host <name> [--json]\n", _HOST_VERBS)
    assert live == _live_host_verbs(_HOST_VERBS)


#: The exact invocation lines the README's "Attach" section documents — the
#: bare picker, a slug, and a slug forwarded to a declared host — one call
#: form per line.
_ATTACH_INVOCATION = re.compile(
    r"^camp attach(?: <slug>(?: --host <name>)?)?$",
    re.MULTILINE,
)


def _toml_blocks() -> list[str]:
    return _TOML_BLOCK.findall(README.read_text())


def _host_invocation_lines() -> list[str]:
    return _HOST_INVOCATION.findall(README.read_text())


def _all_hosts_invocation_lines() -> list[str]:
    return _ALL_HOSTS_INVOCATION.findall(README.read_text())


def _attach_invocation_lines() -> list[str]:
    return _ATTACH_INVOCATION.findall(README.read_text())


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
    start = text.index("**A credential deny list applies unconditionally")
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
    """`camp --help` must document `--host` for exactly the verbs
    `_HOST_VERBS` still lives for — today just `list`, since `sessions` is
    retired — proven by contrast against a genuinely unrecognized flag, which
    this dispatcher silently ignores rather than refusing."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_help([])
    help_text = buf.getvalue()

    documented = _documented_host_verbs(help_text, _HOST_VERBS)
    assert documented == _live_host_verbs(_HOST_VERBS), (
        "camp --help documents --host for a verb the dispatch tables no "
        "longer name as live"
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
    """`camp --help` must document `--all-hosts` for exactly the verbs
    `_ALL_HOSTS_VERBS` still lives for in the group-listing shape — today
    just `list`, since `sessions` is retired — proven by contrast against a
    genuinely unrecognized flag."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_help([])
    help_text = buf.getvalue()

    pattern = re.compile(rf"camp ({_verb_alternation(_ALL_HOSTS_VERBS)}) --all-hosts\|-a")
    documented = sorted(set(pattern.findall(help_text)))
    assert documented == _live_host_verbs(_ALL_HOSTS_VERBS), (
        "camp --help documents --all-hosts for a verb the dispatch tables no "
        "longer name as live"
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


# ---------------------------------------------------------------------------
# camp attach — the README's seven documented forms, run through the real
# dispatcher (`test_attach_cli.py` covers the behavior in depth; this test's
# only job is proving each documented LINE parses and dispatches).
# ---------------------------------------------------------------------------


def test_every_documented_attach_form_dispatches_through_the_real_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Every `camp attach …` line the README documents must reach camp's own
    attach code — never the bare-slug or unknown-verb error a typo in the
    README would otherwise produce silently."""
    lines = _attach_invocation_lines()
    assert lines, "README no longer documents a camp attach invocation form"
    assert len(lines) == 3, f"expected all three documented forms — {lines!r}"

    dispatch = importlib.import_module("camp.cli.dispatch")
    transport = importlib.import_module("camp.host.transport")
    handoff = importlib.import_module("camp.host.handoff")

    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "hosts.toml").write_text("[hosts.andromeda]\n", encoding="utf-8")
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))

    monkeypatch.setattr(
        transport,
        "run_camp",
        lambda host, remote_argv, **kw: transport.Answered(
            stdout=json.dumps({"ok": False, "rows": []}), stderr="", exit_code=0
        ),
    )
    monkeypatch.setattr(handoff, "handoff", lambda argv: None)

    for line in lines:
        argv = shlex.split(
            line.replace("<slug>", "no-such-slug").replace("<name>", "andromeda")
        )
        assert argv[0] == "camp"
        monkeypatch.setattr(sys, "argv", argv)
        try:
            dispatch.main()
        except SystemExit:
            pass
        err = capsys.readouterr().err
        assert "bare slug dispatch is no longer supported" not in err, (line, err)
        assert "camp: bare slug" not in err, (line, err)


# ---------------------------------------------------------------------------
# The retired-verb table — the README's one place a retired verb is named,
# checked against LEGACY_REDIRECTS itself rather than a copy of it.
# ---------------------------------------------------------------------------

_RETIRED_ROW = re.compile(r"^\| `camp (\S+)` \| `camp (\S+)` \|$", re.MULTILINE)


def _retired_rows(text: str) -> list[tuple[str, str]]:
    return _RETIRED_ROW.findall(text)


def _check_retired_verb_table(text: str) -> None:
    """Every row *text*'s retired-verb table lists must name a verb
    `LEGACY_REDIRECTS` actually retires, mapped to the exact target the
    module holds for it — read from the module, not retyped here. Raises
    `AssertionError` on the first row that disagrees, so a caller can run
    this against both the real README and a deliberately wrong fixture."""
    rows = _retired_rows(text)
    assert rows, "no retired-verb table found"
    for old, new in rows:
        assert old in LEGACY_REDIRECTS, f"{old!r} is not a retired verb"
        assert new == LEGACY_REDIRECTS[old], (
            f"table maps {old!r} to {new!r}, but LEGACY_REDIRECTS holds "
            f"{LEGACY_REDIRECTS[old]!r}"
        )


def test_readme_retired_verb_table_matches_legacy_redirects() -> None:
    """The README's own retired-verb table passes the real check."""
    _check_retired_verb_table(README.read_text())


def test_readme_retired_verb_table_check_catches_a_wrong_target() -> None:
    """The real check above must fail against a table whose `kill` row names
    `attach` instead of `stop` — run through `_check_retired_verb_table`
    itself, not a re-derivation of its logic, so a break in that function
    (not just a bad README) turns this red too."""
    fixture = "| Retired | Replacement |\n|---|---|\n| `camp kill` | `camp attach` |\n"
    assert LEGACY_REDIRECTS.get("kill") != "attach", (
        "fixture's wrong target must disagree with LEGACY_REDIRECTS for the "
        "real check to catch it"
    )
    with pytest.raises(AssertionError):
        _check_retired_verb_table(fixture)
