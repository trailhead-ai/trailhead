"""Tests for ``trailhead core show`` — the read pinned to trailhead's own core vault."""

import json
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from trailhead.core_vault import core_copy_dir, core_show, resolve_lore_bin
from trailhead.tests.test_core_vault import _entry, _fenced_env

LORE_BIN = Path("/fake/bin/lore")
BODY = "# core body\n"


class ShowLore:
    """Scripted lore: answers `vault ls --json` and `record show`, recording argv and kwargs."""

    def __init__(self, vaults, *, ls_rc=0, ls_out=None, ls_raise=None, show_rc=0, show_raise=None):
        self.vaults, self.ls_rc, self.ls_out, self.ls_raise = vaults, ls_rc, ls_out, ls_raise
        self.show_rc, self.show_raise = show_rc, show_raise
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, args, **kw):
        sub = [str(a) for a in args[1:]]
        self.calls.append(sub)
        self.kwargs.append(kw)

        def done(rc, out="", err=""):
            return subprocess.CompletedProcess(args, rc, stdout=out, stderr=err)

        if sub == ["vault", "ls", "--json"]:
            if self.ls_raise:
                raise self.ls_raise
            out = self.ls_out if self.ls_out is not None else json.dumps(self.vaults)
            return done(self.ls_rc, out, "lore: LS-ERR" if self.ls_rc else "")
        if sub[:2] == ["record", "show"]:
            if self.show_raise:
                raise self.show_raise
            if self.show_rc:
                return done(self.show_rc, "", "lore: SHOW-ERR\n")
            return done(0, BODY)
        raise AssertionError(f"unexpected lore call {sub}")

    @property
    def shows(self):
        return [c for c in self.calls if c[:2] == ["record", "show"]]


@pytest.fixture
def cenv(tmp_path):
    return _fenced_env(tmp_path)


@pytest.fixture
def copy(cenv):
    return core_copy_dir(cenv).resolve()


def _show(lore, cenv, record_id="adr/x", lore_bin=LORE_BIN):
    return core_show(record_id, env=cenv, runner=lore, lore_bin=lore_bin)


def test_owned_core_relays_the_record_and_pins_the_vault(cenv, copy):
    lore = ShowLore([_entry("core", str(copy))])
    r = _show(lore, cenv)
    assert (r.code, r.stdout, r.stderr) == (0, BODY, "")
    assert lore.shows == [["record", "show", "adr/x", "--vault", "core"]]


def test_the_record_id_chooses_what_is_read(cenv, copy):
    lore = ShowLore([_entry("core", str(copy))])
    _show(lore, cenv, "decision/other-one")
    assert lore.shows == [["record", "show", "decision/other-one", "--vault", "core"]]


def _unavailable_cases(copy: Path, tmp_path: Path):
    c = str(copy)
    return {
        "core-writable": ([_entry("core", c, scope="team")], {}),
        "core-shared": ([_entry("core", c, shared=True)], {}),
        "core-elsewhere": ([_entry("core", str(tmp_path / "elsewhere"))], {}),
        "no-core": ([_entry("mine", str(tmp_path / "m"))], {}),
        "two-core": ([_entry("core", c), _entry("core", c)], {}),
        "alias-at-copy": ([_entry("core", c), _entry("mine", c)], {}),
        "alias-contains-copy": ([_entry("core", c), _entry("mine", str(copy.parent))], {}),
        "alias-inside-copy": ([_entry("core", c), _entry("mine", str(copy / "in"))], {}),
        "ls-nonzero": ([], {"ls_rc": 4}),
        "ls-malformed": ([], {"ls_out": "not json"}),
        "ls-timeout": ([], {"ls_raise": subprocess.TimeoutExpired(["lore"], 5)}),
        "ls-oserror": ([], {"ls_raise": PermissionError("nope")}),
        "lore-absent": ([], {"absent": True}),
    }


_CASES = list(_unavailable_cases(Path("/c"), Path("/t")))


@pytest.mark.parametrize("case", _CASES)
def test_not_owned_exits_3_with_one_plain_line_and_never_reads(case, cenv, copy, tmp_path):
    vaults, opts = _unavailable_cases(copy, tmp_path)[case]
    absent = opts.pop("absent", False)
    lore = ShowLore(vaults, **opts)
    r = _show(lore, cenv, lore_bin=None if absent else LORE_BIN)
    assert r.code == 3
    assert r.stdout == ""
    assert r.stderr.startswith("trailhead: core vault unavailable: ")
    assert r.stderr.endswith("\n") and r.stderr.count("\n") == 1 and "\x1b" not in r.stderr
    assert lore.shows == []


def test_each_unavailable_case_has_its_own_actionable_reason(cenv, copy, tmp_path):
    reasons = {}
    for case, (vaults, opts) in _unavailable_cases(copy, tmp_path).items():
        absent = opts.pop("absent", False)
        r = _show(ShowLore(vaults, **opts), cenv, lore_bin=None if absent else LORE_BIN)
        reasons[case] = r.stderr
    assert reasons["core-shared"] == reasons["core-writable"]  # install's one message covers both
    assert len(set(reasons.values())) == len(reasons) - 1
    assert "trailhead install" in reasons["no-core"]
    assert str(tmp_path / "elsewhere") in reasons["core-elsewhere"]
    assert "rename or remove" in reasons["core-elsewhere"].lower()
    assert "more than one vault named core" in reasons["two-core"]
    assert "read-only" in reasons["core-writable"] and "read-only" in reasons["core-shared"]
    assert "mine" in reasons["alias-at-copy"]


def test_a_control_character_in_a_path_cannot_split_the_unavailable_line(cenv, copy, tmp_path):
    lore = ShowLore([_entry("core", str(tmp_path / "bad\nname"))])
    r = _show(lore, cenv)
    assert r.code == 3 and r.stderr.count("\n") == 1 and "bad" in r.stderr


@pytest.mark.parametrize("failure", ["rc1", "rc2", "rc3", "timeout"])
def test_owned_but_lore_fails_exits_1_with_lore_stderr_and_one_read(failure, cenv, copy):
    opts = {
        "rc1": {"show_rc": 1},
        "rc2": {"show_rc": 2},
        "rc3": {"show_rc": 3},
        "timeout": {"show_raise": subprocess.TimeoutExpired(["lore"], 5)},
    }[failure]
    lore = ShowLore([_entry("core", str(copy))], **opts)
    r = _show(lore, cenv)
    assert r.code == 1 and r.stdout == ""
    if failure == "timeout":
        assert "timed out" in r.stderr
    else:
        assert r.stderr == "lore: SHOW-ERR\n"
    assert lore.shows == [["record", "show", "adr/x", "--vault", "core"]]


def test_record_show_oserror_exits_1(cenv, copy):
    lore = ShowLore([_entry("core", str(copy))], show_raise=PermissionError("no"))
    r = _show(lore, cenv)
    assert r.code == 1 and r.stdout == "" and r.stderr.startswith("trailhead: ")


def test_both_lore_calls_carry_a_timeout(cenv, copy):
    lore = ShowLore([_entry("core", str(copy))])
    _show(lore, cenv)
    assert len(lore.kwargs) == 2
    assert all(isinstance(k.get("timeout"), (int, float)) and k["timeout"] > 0 for k in lore.kwargs)


@pytest.mark.parametrize(
    "bad", ["../x", "adr/x;y", "-v/x", "adr/x\n", "", "adr", "Adr/x", "adr/-x", "adr/x/y", "adr/ x"]
)
def test_refused_ids_exit_2_without_any_lore_call(bad, cenv, copy):
    lore = ShowLore([_entry("core", str(copy))])
    r = _show(lore, cenv, bad)
    assert r.code == 2 and r.stdout == ""
    assert r.stderr.startswith("trailhead: ")
    assert lore.calls == []


@pytest.mark.parametrize("good", ["adr/x", "lesson/a-b-1", "a1/0abc"])
def test_valid_ids_are_accepted(good, cenv, copy):
    assert _show(ShowLore([_entry("core", str(copy))]), cenv, good).code == 0


# -- the CLI entry --------------------------------------------------------


def _main(args, monkeypatch):
    from trailhead.cli import main

    out, err = StringIO(), StringIO()
    monkeypatch.setattr(sys, "argv", ["trailhead", *args])
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    try:
        code = main()
    except SystemExit as e:
        code = e.code
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("bad", ["../x", "adr/x;y", "-v/x", "adr/x\n", ""])
def test_cli_refuses_bad_ids_with_exit_2_before_any_lore_call(bad, monkeypatch, tmp_path):
    marker = tmp_path / "lore-was-run"
    fake = tmp_path / "lore"
    fake.write_text(f"#!/bin/sh\ntouch {marker}\n")
    fake.chmod(0o755)
    monkeypatch.setattr("trailhead.cli.resolve_lore_bin", lambda: fake)
    code, out, _ = _main(["core", "show", bad], monkeypatch)
    assert code == 2 and out == "" and not marker.exists()


def test_cli_relays_the_result_streams_and_code(monkeypatch, tmp_path):
    import trailhead.cli as cli

    monkeypatch.setattr(
        cli,
        "core_show",
        lambda rid, **k: type("R", (), {"code": 3, "stdout": "", "stderr": "trailhead: core vault unavailable: x\n"})(),
    )
    code, out, err = _main(["core", "show", "adr/x"], monkeypatch)
    assert (code, out, err) == (3, "", "trailhead: core vault unavailable: x\n")


def test_cli_core_without_a_verb_exits_2(monkeypatch):
    assert _main(["core"], monkeypatch)[0] == 2


# -- the manifest-based lore binary ---------------------------------------


def _manifest(root: Path, with_cli: bool, make_bin: bool) -> dict[str, Path]:
    tool = root / "tools" / "lore"
    (tool / "plugins" / "lore" / "bin").mkdir(parents=True)
    (tool / "capabilities.toml").write_text(
        '[tool]\nname = "lore"\nbase = []\n' + ('cli_bin = "bin/lore"\n' if with_cli else "")
    )
    if make_bin:
        (tool / "plugins" / "lore" / "bin" / "lore").write_text("#!/bin/sh\n")
    return {"lore": tool / "capabilities.toml"}


def test_resolve_lore_bin_follows_the_manifest(tmp_path):
    paths = _manifest(tmp_path, True, True)
    assert resolve_lore_bin(paths) == tmp_path / "tools" / "lore" / "plugins" / "lore" / "bin" / "lore"


@pytest.mark.parametrize("with_cli,make_bin", [(False, True), (True, False)])
def test_resolve_lore_bin_is_none_when_disabled_or_missing(with_cli, make_bin, tmp_path):
    assert resolve_lore_bin(_manifest(tmp_path, with_cli, make_bin)) is None


def test_resolve_lore_bin_is_none_without_a_lore_manifest():
    assert resolve_lore_bin({}) is None


def test_resolve_lore_bin_default_points_at_the_shipped_lore():
    found = resolve_lore_bin()
    assert found is not None and found.name == "lore" and found.exists()


def _decode_error():
    return UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


@pytest.mark.parametrize("raised", [_decode_error(), ValueError("bad text")])
def test_vault_ls_decode_failure_exits_3_not_1(raised, cenv, copy):
    lore = ShowLore([_entry("core", str(copy))], ls_raise=raised)
    r = _show(lore, cenv)
    assert r.code == 3 and r.stdout == ""
    assert r.stderr.startswith("trailhead: core vault unavailable: ")
    assert lore.shows == []


def test_record_show_decode_failure_exits_1_since_ownership_was_confirmed(cenv, copy):
    lore = ShowLore([_entry("core", str(copy))], show_raise=_decode_error())
    r = _show(lore, cenv)
    assert r.code == 1 and r.stdout == ""
    assert r.stderr.startswith("trailhead: ") and "lore" in r.stderr and r.stderr.count("\n") == 1


class _SilentFailLore(ShowLore):
    def __init__(self, vaults, err):
        super().__init__(vaults, show_rc=7)
        self.err = err

    def __call__(self, args, **kw):
        out = super().__call__(args, **kw)
        if list(args[1:3]) == ["record", "show"]:
            out.stderr = self.err
        return out


@pytest.mark.parametrize("err", ["", None])
def test_record_show_failure_with_empty_stderr_still_names_the_failure(err, cenv, copy):
    r = _show(_SilentFailLore([_entry("core", str(copy))], err), cenv)
    assert r.code == 1 and r.stdout == ""
    assert r.stderr == "trailhead: lore record show failed (exit 7)\n"


def test_record_show_failure_with_stderr_relays_lore_stderr_unchanged(cenv, copy):
    r = _show(_SilentFailLore([_entry("core", str(copy))], "lore: boom\n"), cenv)
    assert r.stderr == "lore: boom\n"


def test_duplicate_core_remedy_names_the_extra_registration_not_a_rename(cenv, copy):
    r = _show(ShowLore([_entry("core", str(copy)), _entry("core", str(copy))]), cenv)
    assert "more than one vault named core" in r.stderr
    assert "lore vault delete core" in r.stderr
    assert "rename or remove" not in r.stderr.lower()


def test_record_show_stderr_control_and_bidi_characters_are_replaced(cenv, copy):
    err = "lore: \x1b[31mred\x1b[0m \x1b]0;pwned\x07 ‮evil\n"
    r = _show(_SilentFailLore([_entry("core", str(copy))], err), cenv)
    assert r.code == 1 and r.stdout == ""
    assert not any(c in r.stderr for c in ("\x1b", "\x07", "‮"))
    assert r.stderr == "lore: ?[31mred?[0m ?]0;pwned? ?evil\n"


def test_record_show_multiline_stderr_keeps_its_newlines(cenv, copy):
    r = _show(_SilentFailLore([_entry("core", str(copy))], "lore: first\nlore: second\n"), cenv)
    assert r.stderr == "lore: first\nlore: second\n"
