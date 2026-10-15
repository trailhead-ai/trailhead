"""Behavioural tests for the real-browser probe harness (``chromium_harness``).

Most tests drive a real headless browser against the fixture pages under
``fixtures/chromium-harness``; they skip when no browser can be found (and fail
instead when ``OUTPOST_REQUIRE_BROWSER`` is set, as CI does). The lookup-order,
launch-flag, timeout and skip/fail tests use stub executables so they run the
same everywhere.
"""

from __future__ import annotations

import functools
import shutil
import stat
import time
from pathlib import Path

import pytest

import chromium_harness as ch

FIXTURES = Path(__file__).parent / "fixtures" / "chromium-harness"

STUB_DOM = '<html><body><pre id="probe-result">{"ok":true,"value":{"stub":true}}</pre></body></html>'


def _stub(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _logging_stub(path: Path, log: Path) -> Path:
    return _stub(path, f"printf '%s\\n' \"$@\" > '{log}'\necho '{STUB_DOM}'")


def _lookup(env: dict[str, str], home: Path):
    return functools.partial(ch.find_browser, env=env, home=home, app_bundles=())


@pytest.fixture()
def bare(tmp_path: Path):
    """An isolated environment: empty PATH dir, empty HOME, no mac bundles."""
    path_dir = tmp_path / "bin"
    path_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": str(path_dir), "HOME": str(home)}
    return path_dir, home, env


def test_pages_with_different_css_widths_measure_differently():
    probe = "return {width: document.getElementById('box').getBoundingClientRect().width};"
    narrow = ch.run_probe(FIXTURES / "widths-narrow", probe)
    wide = ch.run_probe(FIXTURES / "widths-wide", probe)
    assert narrow == {"width": 100}
    assert wide == {"width": 250}


def test_page_script_and_font_from_its_own_folder_are_loaded():
    probe = """
      return {
        fonts: Array.from(document.fonts).map(function (f) { return f.status; }),
        made: !!document.getElementById('made-by-script'),
        sample: document.getElementById('sample').getBoundingClientRect().width,
      };
    """
    result = ch.run_probe(FIXTURES / "assets", probe)
    assert result == {"fonts": ["loaded"], "made": True, "sample": 64}


def test_probe_that_never_reports_raises_named_error_naming_the_page():
    with pytest.raises(ch.ProbeNotReported) as err:
        ch.run_probe(FIXTURES / "never-ready", "return {};")
    assert "never-ready" in str(err.value)


def test_probe_that_throws_raises_named_error_carrying_its_message():
    with pytest.raises(ch.ProbeFailed) as err:
        ch.run_probe(FIXTURES / "widths-narrow", "throw new Error('boom from probe');")
    assert "boom from probe" in str(err.value)
    assert "widths-narrow" in str(err.value)


def test_binary_that_never_exits_raises_launch_error_naming_it_within_timeout(bare):
    path_dir, home, env = bare
    hang = _stub(path_dir / "chrome-headless-shell", f"{shutil.which('sleep')} 60")
    started = time.monotonic()
    with pytest.raises(ch.BrowserLaunchError) as err:
        ch.run_probe(
            FIXTURES / "widths-narrow", "return {};", timeout=1, env=env, lookup=_lookup(env, home)
        )
    assert str(hang) in str(err.value)
    assert time.monotonic() - started < 15


def test_headless_shell_is_preferred_over_full_chrome(bare, tmp_path):
    path_dir, home, env = bare
    shell_log, chrome_log = tmp_path / "shell.log", tmp_path / "chrome.log"
    _logging_stub(path_dir / "chrome-headless-shell", shell_log)
    _logging_stub(path_dir / "google-chrome", chrome_log)
    result = ch.run_probe(
        FIXTURES / "widths-narrow", "return {};", env=env, lookup=_lookup(env, home)
    )
    assert result == {"stub": True}
    assert shell_log.exists() and not chrome_log.exists()
    assert "--headless=new" not in shell_log.read_text().splitlines()


def test_full_chrome_alone_is_chosen_and_launched_with_headless_new(bare, tmp_path):
    path_dir, home, env = bare
    chrome_log = tmp_path / "chrome.log"
    _logging_stub(path_dir / "google-chrome", chrome_log)
    result = ch.run_probe(
        FIXTURES / "widths-narrow", "return {};", env=env, lookup=_lookup(env, home)
    )
    assert result == {"stub": True}
    args = chrome_log.read_text().splitlines()
    assert "--headless=new" in args
    assert "--dump-dom" in args
    assert any(a.startswith("--user-data-dir=") for a in args)
    assert any(a.startswith("--virtual-time-budget=") for a in args)
    assert "--window-size=1280,900" in args


def _build_all_sources(tmp_path: Path):
    path_dir = tmp_path / "bin"
    cache = tmp_path / "pw"
    home = tmp_path / "home"
    home.mkdir()
    override = _stub(tmp_path / "override" / "my-browser", "true")
    path_shell = _stub(path_dir / "chrome-headless-shell", "true")
    cached = _stub(
        cache / "chromium_headless_shell-1200" / "chrome-headless-shell-linux64" / "chrome-headless-shell",
        "true",
    )
    path_chrome = _stub(path_dir / "google-chrome", "true")
    env = {"PATH": str(path_dir), "HOME": str(home), "PLAYWRIGHT_BROWSERS_PATH": str(cache)}
    return env, home, override, path_shell, cached, path_chrome


def test_lookup_order_is_override_then_path_shell_then_cache_then_full_chrome(tmp_path):
    env, home, override, path_shell, cached, path_chrome = _build_all_sources(tmp_path)

    def chosen(e):
        return ch.find_browser(env=e, home=home, app_bundles=()).path

    assert chosen({**env, ch.ENV_BROWSER: str(override)}) == override
    assert chosen(env) == path_shell
    path_shell.unlink()
    assert chosen(env) == cached
    cached.unlink()
    assert chosen(env) == path_chrome


@pytest.mark.parametrize(
    "cache_rel",
    [".cache/ms-playwright", "Library/Caches/ms-playwright"],
)
def test_default_playwright_cache_locations_are_searched(tmp_path, cache_rel):
    home = tmp_path / "home"
    shell = _stub(
        home / cache_rel / "chromium_headless_shell-9" / "chrome-headless-shell-x" / "chrome-headless-shell",
        "true",
    )
    empty = tmp_path / "bin"
    empty.mkdir()
    found = ch.find_browser(env={"PATH": str(empty)}, home=home, app_bundles=())
    assert found.path == shell


def test_no_browser_skips_when_require_flag_unset(bare):
    _, home, env = bare
    with pytest.raises((pytest.skip.Exception, pytest.fail.Exception)) as outcome:
        ch.run_probe(FIXTURES / "widths-narrow", "return {};", env=env, lookup=_lookup(env, home))
    assert outcome.type is pytest.skip.Exception


def test_no_browser_fails_naming_the_flag_when_require_flag_set(bare):
    _, home, env = bare
    env = {**env, ch.ENV_REQUIRE: "1"}
    with pytest.raises((pytest.skip.Exception, pytest.fail.Exception)) as outcome:
        ch.run_probe(FIXTURES / "widths-narrow", "return {};", env=env, lookup=_lookup(env, home))
    assert outcome.type is pytest.fail.Exception
    assert ch.ENV_REQUIRE in str(outcome.value)
