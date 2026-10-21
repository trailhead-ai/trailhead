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


def _fetch(url):
    import urllib.request

    return urllib.request.urlopen(url)


def test_served_folder_sends_outposts_headers_and_the_content_type_of_each_extension(tmp_path):
    for name in ("a.html", "a.js", "a.css", "a.ttf", "a.woff2"):
        (tmp_path / name).write_bytes(b"x")
    with ch.serve_folder(tmp_path) as base:
        types = {name: _fetch(f"{base}/{name}") for name in ("a.html", "a.js", "a.css", "a.ttf", "a.woff2")}
    assert {n: r.headers["Content-Type"] for n, r in types.items()} == {
        "a.html": "text/html; charset=utf-8",
        "a.js": "text/javascript; charset=utf-8",
        "a.css": "text/css; charset=utf-8",
        "a.ttf": "font/ttf",
        "a.woff2": "font/woff2",
    }
    for r in types.values():
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert "script-src 'self' 'unsafe-inline'" in r.headers["Content-Security-Policy"]
        assert r.headers["Content-Security-Policy"].endswith("frame-ancestors 'none'")


def test_served_folder_serves_only_the_folder_it_was_given(tmp_path):
    (tmp_path / "secret.txt").write_text("s")
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("ok")
    import urllib.error

    with ch.serve_folder(site) as base:
        assert _fetch(f"{base}/").read() == b"ok"
        with pytest.raises(urllib.error.HTTPError) as err:
            _fetch(f"{base}/../secret.txt")
    assert err.value.code == 404


_ENV_REQUIRE = {"PATH": "", "HOME": "/nonexistent", ch.ENV_REQUIRE: "1"}

SERVE_MODES = pytest.mark.parametrize("serve", [False, True], ids=["file", "served"])

DENY_STORAGE = (
    "Object.defineProperty(window, 'localStorage', {configurable: true, "
    "get: function () { throw new DOMException('denied', 'SecurityError'); }});"
)
FULL_STORAGE = (
    "Storage.prototype.setItem = function () { throw new DOMException('full', 'QuotaExceededError'); };"
)


@SERVE_MODES
def test_a_probe_that_reloads_carries_on_in_the_reloaded_page(serve):
    steps = [
        "sessionStorage.setItem('first', localStorage.getItem('c')); harness.reload();",
        "return [Number(sessionStorage.getItem('first')), Number(localStorage.getItem('c'))];",
    ]
    assert ch.run_probe_steps(FIXTURES / "counter", steps, serve=serve) == [1, 2]


@SERVE_MODES
def test_a_second_run_of_the_same_page_starts_from_a_fresh_profile(serve):
    read = "return Number(localStorage.getItem('c'));"
    assert ch.run_probe_steps(FIXTURES / "counter", [read], serve=serve) == 1
    assert ch.run_probe_steps(FIXTURES / "counter", [read], serve=serve) == 1


@SERVE_MODES
def test_a_value_stored_on_page_a_is_read_on_page_b_in_another_folder(serve):
    steps = [
        "localStorage.setItem('x', 'from-a'); harness.goto('../b/index.html');",
        "return {x: localStorage.getItem('x'), page: location.pathname.split('/').slice(-2).join('/')};",
    ]
    result = ch.run_probe_steps(
        FIXTURES / "two-pads",
        steps,
        entry="a/index.html",
        inject_into=["a/index.html", "b/index.html"],
        serve=serve,
    )
    assert result == {"x": "from-a", "page": "b/index.html"}


def test_a_step_that_does_not_move_on_is_reported_when_more_steps_remain():
    with pytest.raises(ch.ProbeFailed) as err:
        ch.run_probe_steps(FIXTURES / "counter", ["return 1;", "return 2;"])
    assert "step 0 finished without" in str(err.value)


def test_the_last_step_cannot_move_on_to_nothing():
    with pytest.raises(ch.ProbeFailed) as err:
        ch.run_probe_steps(FIXTURES / "counter", ["harness.reload();"])
    assert "last step" in str(err.value)


STORAGE_LISTENER = """
  var events = [];
  addEventListener('storage', function (e) { events.push({key: e.key, value: e.newValue}); });
"""


@SERVE_MODES
def test_a_write_in_one_copy_reaches_the_other_as_storage_events_with_the_right_key(serve):
    copy_1 = STORAGE_LISTENER + """
      await harness.barrier();
      localStorage.setItem('k1', 'v1');
      localStorage.setItem('k2', 'v2');
      await harness.barrier();
      return events;
    """
    copy_2 = """
      var events = [];
      await new Promise(function (resolve) { setTimeout(resolve, 50); });
      addEventListener('storage', function (e) { events.push({key: e.key, value: e.newValue}); });
      await harness.barrier();
      await harness.waitFor('two storage events', function () { return events.length >= 2; });
      await harness.barrier();
      return events;
    """
    one, two = ch.run_probe_pair(FIXTURES / "widths-narrow", copy_1, copy_2, serve=serve)
    assert two == [{"key": "k1", "value": "v1"}, {"key": "k2", "value": "v2"}]
    assert one == []


def test_each_copy_reads_its_own_result_and_a_throw_names_the_copy():
    one, two = ch.run_probe_pair(
        FIXTURES / "widths-narrow",
        "return 'one:' + (window.opener === null);",
        "return 'two:' + (window.opener !== null);",
    )
    assert (one, two) == ("one:true", "two:true")
    with pytest.raises(ch.ProbeFailed) as err:
        ch.run_probe_pair(FIXTURES / "widths-narrow", "return 1;", "throw new Error('copy two broke');")
    assert "second copy" in str(err.value) and "copy two broke" in str(err.value)


@SERVE_MODES
def test_a_before_script_that_denies_local_storage_is_in_force_for_the_pages_first_script(serve):
    read = "return window.__first;"
    assert ch.run_probe(FIXTURES / "first-script", read, serve=serve) == {"threw": False}
    denied = ch.run_probe(FIXTURES / "first-script", read, serve=serve, before_script=DENY_STORAGE)
    assert denied == {"threw": True, "name": "SecurityError"}


@SERVE_MODES
def test_a_before_script_can_make_only_set_item_throw(serve):
    result = ch.run_probe(
        FIXTURES / "first-script", "return window.__first;", serve=serve, before_script=FULL_STORAGE
    )
    assert result == {"threw": True, "name": "QuotaExceededError"}


def test_a_before_script_applies_to_the_page_a_probe_moves_to():
    steps = ["harness.goto('../b/index.html');", "return typeof window.__beforeRan;"]
    result = ch.run_probe_steps(
        FIXTURES / "two-pads",
        steps,
        entry="a/index.html",
        inject_into=["a/index.html", "b/index.html"],
        before_script="window.__beforeRan = true;",
    )
    assert result == "boolean"


@pytest.mark.parametrize("worker", [1, 2])
def test_two_launches_share_one_server_port_and_one_profile(worker):
    probe = "return {port: location.port, c: Number(localStorage.getItem('c'))};"
    first, second = ch.run_probe_launches(FIXTURES / "counter", [probe, probe], serve=True)
    assert first["port"] and first["port"] == second["port"]
    assert (first["c"], second["c"]) == (1, 2)


def test_two_launches_from_disk_share_one_profile():
    probe = "return Number(localStorage.getItem('c'));"
    assert ch.run_probe_launches(FIXTURES / "counter", [probe, probe]) == [1, 2]


def test_separate_launch_runs_do_not_share_state():
    probe = "return Number(localStorage.getItem('c'));"
    assert ch.run_probe_launches(FIXTURES / "counter", [probe]) == [1]
    assert ch.run_probe_launches(FIXTURES / "counter", [probe]) == [1]


def test_wait_for_returns_the_value_once_its_condition_holds():
    probe = """
      var calls = 0;
      var got = await harness.waitFor('third call', function () { calls += 1; return calls >= 3 ? calls : false; });
      return {got: got, calls: calls};
    """
    assert ch.run_probe(FIXTURES / "widths-narrow", probe) == {"got": 3, "calls": 3}


def test_wait_for_that_never_holds_fails_naming_the_awaited_state():
    probe = "await harness.waitFor('the other tab to write k9', function () { return false; }, 300);"
    with pytest.raises(ch.ProbeFailed) as err:
        ch.run_probe(FIXTURES / "widths-narrow", probe)
    assert "the other tab to write k9" in str(err.value)


@pytest.mark.parametrize(
    "call",
    [
        lambda: ch.run_probe_steps(FIXTURES / "counter", ["return 1;"], env=_ENV_REQUIRE, lookup=lambda: None),
        lambda: ch.run_probe_pair(
            FIXTURES / "counter", "return 1;", "return 2;", env=_ENV_REQUIRE, lookup=lambda: None
        ),
        lambda: ch.run_probe_launches(FIXTURES / "counter", ["return 1;"], env=_ENV_REQUIRE, lookup=lambda: None),
    ],
    ids=["steps", "pair", "launches"],
)
def test_a_missing_browser_fails_rather_than_skips_for_every_entry_point(call):
    with pytest.raises((pytest.skip.Exception, pytest.fail.Exception)) as outcome:
        call()
    assert outcome.type is pytest.fail.Exception


def test_wait_for_without_a_timeout_fails_named_before_the_virtual_time_budget_runs_out():
    probe = "await harness.waitFor('a state that never arrives', function () { return false; });"
    with pytest.raises(ch.ProbeFailed) as err:
        ch.run_probe(FIXTURES / "widths-narrow", probe, virtual_time_budget=1500)
    assert "a state that never arrives" in str(err.value)
