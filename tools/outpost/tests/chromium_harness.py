"""Run a page folder in a real headless browser and read back a probe's JSON.

``run_probe(page_dir, probe_js)`` copies the page folder to a temp dir and adds a
probe script file beside the copy's ``index.html``, loaded through a plain
``<script src>`` so a page whose policy forbids inline script still runs it. The
probe waits for ``document.fonts.ready`` and for ``data-pad-ready`` on
``<html>``, runs ``probe_js`` (the body of an async function that ``return``s a
JSON-serialisable value, and may ``await``), and dumps the DOM from a headless
browser. ``entry`` names the file the browser opens and ``inject_into`` the file
that gets the probe (default: the entry), so a test can open a page that
redirects and probe the page it lands on. The wrapper writes the outcome into
``<pre id="probe-result">``; the value is parsed back out and returned.

With ``serve=True`` the copy is served over HTTP on a loopback port (``serve_folder``)
and ``entry`` is a URL path under it rather than a file; a server that cannot start
raises ``ServerStartError`` before the browser launches.

``color_scheme="dark"`` or ``"light"`` launches the browser preferring that scheme
(``--blink-settings=preferredColorScheme``, 0 = dark, 1 = light), so a page's
``prefers-color-scheme`` rules apply; a probe should assert the media query itself so a flag
that silently does nothing fails the test.

Four more entry points share ``run_probe``'s keyword arguments (``entry``, ``inject_into``,
``serve``, ``serve_port``, ``window_size``, ``timeout``, ``virtual_time_budget``, ``env``, ``lookup``,
``color_scheme``) and its missing-browser rule. ``inject_into`` may also be a list of files: every
listed page gets the probe, so a probe can move to any of them. Every probe body, in every entry
point, receives a ``harness`` object.

- ``run_probe_steps(page_dir, steps, ...)``: ``steps`` is a list of probe bodies, one per page load.
  A step that is not the last must end by calling ``harness.reload()`` or ``harness.goto(url)``
  (``url`` resolved against the current page); the harness then loads that page and runs the next
  step there. State survives the move through ``localStorage`` / ``sessionStorage``; the value
  returned is the last step's. A step that does not move on, or a last step that does, raises
  ``ProbeFailed``. Two pages in different folders of ``page_dir`` share one ``file://`` origin, so
  ``entry="a/index.html"``, ``inject_into=["a/index.html", "b/index.html"]`` and
  ``harness.goto("../b/index.html")`` carry ``localStorage`` from a to b.
- ``run_probe_pair(page_dir, probe_one, probe_two, ...)`` returns ``(value_one, value_two)``. The page
  is opened twice at once (the second copy through ``window.open``, so it works from ``file://``
  and served; a framing wrapper does not, the sites CSP forbids framing). Each copy runs its probe
  once the page is ready; ``await harness.barrier()`` in both probes lines them up (a probe that
  registers a listener after its first ``await`` needs it before the other copy writes). Writes
  in one copy arrive in the other as ``storage`` events; the writer gets none. A throw in either
  probe raises ``ProbeFailed`` naming the copy. Defaults to ``PAIR_VIRTUAL_TIME_BUDGET``, a larger
  budget, because a wait for the other copy is real inter-process time that the virtual clock
  does not hold back.
- ``run_probe_launches(page_dir, probes, ...)`` runs one probe per browser launch, in order, sharing
  one profile and (served) one server on one OS-picked free port, so ``localStorage`` written by
  launch 1 is there for launch 2 under the same origin. It returns the list of values. Separate
  calls never share a profile or a port.
- ``before_script=<js>`` (on every entry point) writes the script beside each injected page and loads
  it as the first ``<script>`` in ``<head>``, ahead of the page's own scripts, so a test can replace
  ``window.localStorage`` or ``Storage.prototype.setItem`` before the page sees them.
- ``await harness.waitFor(description, condition, timeoutMs)`` polls ``condition`` (sync or async,
  truthy means done) every 20 ms of virtual time and returns its truthy value; on timeout it throws
  ``timed out waiting for: <description>``, which surfaces as ``ProbeFailed``. The default timeout
  is ``WAIT_FOR_BUDGET_SHARE`` of ``virtual_time_budget``: a wait that outlasts the budget would
  surface as ``ProbeNotReported`` instead of naming what was awaited.

Browser lookup (``find_browser``): ``$OUTPOST_TEST_BROWSER``, then
``chrome-headless-shell`` on PATH, then the Playwright cache's headless shell,
and only then full Chrome/Chromium (launched with ``--headless=new``). Every
launch runs under a wall-clock timeout. With no browser found the calling test
is skipped, or failed when ``$OUTPOST_REQUIRE_BROWSER`` is set.
"""

from __future__ import annotations

import contextlib
import html
import http.server
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

ENV_BROWSER = "OUTPOST_TEST_BROWSER"
ENV_REQUIRE = "OUTPOST_REQUIRE_BROWSER"
ENV_PLAYWRIGHT = "PLAYWRIGHT_BROWSERS_PATH"

DEFAULT_TIMEOUT = 60.0
MAC_APP_BUNDLES = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
)
_FULL_CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
_RESULT_RE = re.compile(r'<pre id="probe-result"[^>]*>(.*?)</pre>', re.DOTALL)

_COLOR_SCHEMES = {"dark": 0, "light": 1}

PROBE_FILE = "pad-probe.js"
BEFORE_FILE = "pad-before.js"
COPY_WINDOW_NAME = "harness-copy-2"
STEP_WINDOW_PREFIX = "harness-step:"
PAIR_VIRTUAL_TIME_BUDGET = 30000
WAIT_FOR_BUDGET_SHARE = 0.8

_WRAPPER = """(function () {
  var MODE = %(mode)s;
  var COPY_NAME = %(copy_name)s;
  var STEP_PREFIX = %(step_prefix)s;
  var POLL_MS = 20;
  var steps = [
%(steps)s
  ];

  function report(payload) {
    var pre = document.createElement("pre");
    pre.id = "probe-result";
    pre.textContent = JSON.stringify(payload);
    document.body.appendChild(pre);
  }
  function failure(error) { return { ok: false, error: String((error && error.message) || error) }; }
  function padReady() {
    return new Promise(function (resolve) {
      var root = document.documentElement;
      if (root.hasAttribute("data-pad-ready")) { resolve(); return; }
      new MutationObserver(function (_, observer) {
        if (root.hasAttribute("data-pad-ready")) { observer.disconnect(); resolve(); }
      }).observe(root, { attributes: true });
    });
  }
  function ready() {
    return padReady().then(function () { return document.fonts.ready; });
  }
  function pause(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  async function waitFor(description, condition, timeoutMs) {
    var ticks = Math.ceil((timeoutMs === undefined ? %(wait_ms)d : timeoutMs) / POLL_MS);
    for (var tick = 0; tick <= ticks; tick++) {
      var value = await condition();
      if (value) { return value; }
      await pause(POLL_MS);
    }
    throw new Error("timed out waiting for: " + description);
  }

  function makeHarness(peer) {
    var moveTo = null;
    var mine = 0;
    var seen = 0;
    if (peer) {
      addEventListener("message", function (event) {
        if (event.source === peer && event.data && event.data.t === "barrier") { seen += 1; }
      });
    }
    return {
      waitFor: waitFor,
      reload: function () { moveTo = { reload: true }; },
      goto: function (url) { moveTo = { url: new URL(url, location.href).href }; },
      barrier: async function () {
        if (!peer) { throw new Error("barrier needs a second copy: use run_probe_pair"); }
        mine += 1;
        peer.postMessage({ t: "barrier" }, "*");
        await waitFor("the other copy to reach barrier " + mine, function () { return seen >= mine; });
      },
      moved: function () { return moveTo; }
    };
  }

  async function runSteps() {
    var index = window.name.indexOf(STEP_PREFIX) === 0
      ? Number(window.name.slice(STEP_PREFIX.length)) : 0;
    var harness = makeHarness(null);
    await ready();
    var value = await steps[index](harness);
    var move = harness.moved();
    if (move === null) {
      if (index < steps.length - 1) {
        throw new Error("step " + index + " finished without calling harness.reload() or harness.goto()");
      }
      return value === undefined ? null : value;
    }
    if (index >= steps.length - 1) {
      throw new Error("the last step called harness.reload() or harness.goto(): there is no step to run after it");
    }
    window.name = STEP_PREFIX + (index + 1);
    if (move.reload) { location.reload(); } else { location.href = move.url; }
    return NOT_FINISHED;
  }
  var NOT_FINISHED = {};

  function runCopyTwo() {
    var opener = window.opener;
    var harness = makeHarness(opener);
    ready().then(function () {
      var started = false;
      addEventListener("message", function (event) {
        if (event.source !== opener || !event.data || event.data.t !== "go" || started) { return; }
        started = true;
        Promise.resolve().then(function () { return steps[1](harness); }).then(
          function (value) {
            opener.postMessage({ t: "result", ok: true, value: value === undefined ? null : value }, "*");
          },
          function (error) { opener.postMessage(Object.assign({ t: "result" }, failure(error)), "*"); }
        );
      });
      opener.postMessage({ t: "hello" }, "*");
    });
  }

  async function runCopyOne() {
    await ready();
    var popup = window.open(location.href, COPY_NAME);
    if (!popup) { throw new Error("the browser would not open a second copy of the page"); }
    var hello = false;
    var second = null;
    var harness = makeHarness(popup);
    addEventListener("message", function (event) {
      if (event.source !== popup || !event.data) { return; }
      if (event.data.t === "hello") { hello = true; }
      if (event.data.t === "result") { second = event.data; }
    });
    await waitFor("the second copy of the page to signal ready", function () { return hello; });
    popup.postMessage({ t: "go" }, "*");
    var first;
    try { first = await steps[0](harness); } catch (error) {
      popup.close();
      throw new Error("first copy: " + ((error && error.message) || error));
    }
    await waitFor("the second copy's probe to report", function () { return second; });
    popup.close();
    if (!second.ok) { throw new Error("second copy: " + second.error); }
    return [first === undefined ? null : first, second.value];
  }

  var role;
  if (MODE === "pair" && window.name === COPY_NAME) { runCopyTwo(); return; }
  role = MODE === "pair" ? runCopyOne : runSteps;
  role().then(
    function (value) { if (value !== NOT_FINISHED) { report({ ok: true, value: value }); } },
    function (error) { report(failure(error)); }
  );
})();
"""


def _step_source(body: str) -> str:
    return "    async function (harness) {\n" + body + "\n    }"


def _render_wrapper(mode: str, bodies: Sequence[str], virtual_time_budget: int) -> str:
    return _WRAPPER % {
        "mode": json.dumps(mode),
        "copy_name": json.dumps(COPY_WINDOW_NAME),
        "step_prefix": json.dumps(STEP_WINDOW_PREFIX),
        "steps": ",\n".join(_step_source(body) for body in bodies),
        "wait_ms": int(virtual_time_budget * WAIT_FOR_BUDGET_SHARE),
    }


class HarnessError(Exception):
    """Base for every error the harness raises itself."""


class BrowserLaunchError(HarnessError):
    """The browser binary could not be run to completion."""


class ServerStartError(HarnessError):
    """The loopback server for an HTTP-mode probe could not be started."""


class ProbeNotReported(HarnessError):
    """The page was dumped but its probe never wrote a result."""


class ProbeFailed(HarnessError):
    """The probe ran and threw."""


# Copied from Outpost's sites handler (outpost: server/sites/static.ts: SITES_CSP,
# MIME_BY_EXT and the nosniff header). This is a snapshot, not a link: a later change
# to Outpost's headers will not turn a test that serves pages here red.
SITES_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; media-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
_MIME_BY_EXT = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".ttf": "font/ttf",
    ".woff2": "font/woff2",
}


class _SitesHandler(http.server.SimpleHTTPRequestHandler):
    def guess_type(self, path):
        return _MIME_BY_EXT.get(Path(path).suffix.lower(), "application/octet-stream")

    def end_headers(self):
        self.send_header("Content-Security-Policy", SITES_CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, format, *args):
        pass


@contextlib.contextmanager
def serve_folder(root: Path, *, port: int = 0) -> Iterator[str]:
    """Serve ``root`` on a loopback port under Outpost's sites headers; yield the base URL."""
    handler = lambda *a, **kw: _SitesHandler(*a, directory=str(root), **kw)  # noqa: E731
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as err:
        raise ServerStartError(f"could not start the loopback server on 127.0.0.1:{port}: {err}") from err
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@dataclass(frozen=True)
class Browser:
    path: Path

    @property
    def is_headless_shell(self) -> bool:
        return "headless-shell" in self.path.name


def _is_exe(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_browser(
    env: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
    app_bundles: tuple[Path, ...] = MAC_APP_BUNDLES,
) -> Browser | None:
    env = os.environ if env is None else env
    if home is None:
        home = Path(env["HOME"]) if "HOME" in env else Path.home()
    search_path = env.get("PATH", "")

    override = env.get(ENV_BROWSER)
    if override:
        if not _is_exe(Path(override)):
            raise HarnessError(f"{ENV_BROWSER}={override} is not an executable file")
        return Browser(Path(override))

    on_path = shutil.which("chrome-headless-shell", path=search_path)
    if on_path:
        return Browser(Path(on_path))

    roots = (
        [Path(env[ENV_PLAYWRIGHT])]
        if env.get(ENV_PLAYWRIGHT)
        else [home / ".cache" / "ms-playwright", home / "Library" / "Caches" / "ms-playwright"]
    )
    for root in roots:
        pattern = "chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"
        for candidate in sorted(root.glob(pattern), reverse=True):
            if _is_exe(candidate):
                return Browser(candidate)

    for name in _FULL_CHROME_NAMES:
        found = shutil.which(name, path=search_path)
        if found:
            return Browser(Path(found))
    for bundle in app_bundles:
        if _is_exe(bundle):
            return Browser(bundle)
    return None


def _launch(argv: list[str], browser: Browser, timeout: float, env: Mapping[str, str]) -> str:
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(env),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise BrowserLaunchError(
            f"{browser.path} did not finish within {timeout:g}s (killed); "
            "the binary hung or will not run headless"
        ) from None
    if proc.returncode != 0:
        raise BrowserLaunchError(
            f"{browser.path} exited {proc.returncode}: {stderr.strip()[-500:]}"
        )
    return stdout


def _inject(index_html: str) -> str:
    wrapper = f'<script src="{PROBE_FILE}"></script>\n'
    pos = index_html.lower().rfind("</body>")
    if pos == -1:
        return index_html + wrapper
    return index_html[:pos] + wrapper + index_html[pos:]


_HEAD_OPEN_RE = re.compile(r"<head(?:\s[^>]*)?>", re.IGNORECASE)
_DOCTYPE_RE = re.compile(r"<!doctype[^>]*>", re.IGNORECASE)


def _inject_before(index_html: str) -> str:
    tag = f'<script src="{BEFORE_FILE}"></script>\n'
    head = _HEAD_OPEN_RE.search(index_html)
    if head:
        return index_html[: head.end()] + tag + index_html[head.end() :]
    doctype = _DOCTYPE_RE.search(index_html)
    at = doctype.end() if doctype else 0
    return index_html[:at] + tag + index_html[at:]


def _require_browser(env: Mapping[str, str], lookup: Callable[[], Browser | None] | None) -> Browser:
    browser = (lookup or (lambda: find_browser(env)))()
    if browser is None:
        message = "no headless Chrome/Chromium found (see chromium_harness.find_browser)"
        if env.get(ENV_REQUIRE):
            pytest.fail(f"{message}; {ENV_REQUIRE} is set, so a missing browser is a failure")
        pytest.skip(f"{message}; set {ENV_REQUIRE} to fail instead of skipping")
    return browser


def _prepare_copy(
    page_dir: Path,
    tmp: str,
    entry: str,
    inject_into: str | Sequence[str] | None,
    before_script: str | None,
) -> tuple[Path, list[Path]]:
    page = Path(tmp) / "page"
    shutil.copytree(page_dir, page)
    names = [inject_into or entry] if isinstance(inject_into, (str, type(None))) else list(inject_into)
    targets = [page / name for name in names]
    for target in targets:
        text = target.read_text()
        if before_script is not None:
            text = _inject_before(text)
            (target.parent / BEFORE_FILE).write_text(before_script)
        target.write_text(_inject(text))
    return page, targets


def _argv(
    browser: Browser,
    env: Mapping[str, str],
    tmp: str,
    window_size: tuple[int, int],
    virtual_time_budget: int,
    color_scheme: str | None,
) -> list[str]:
    argv = [str(browser.path)]
    if not browser.is_headless_shell:
        argv.append("--headless=new")
    if env.get(ENV_REQUIRE):
        argv.append("--no-sandbox")
    if color_scheme is not None:
        argv.append(f"--blink-settings=preferredColorScheme={_COLOR_SCHEMES[color_scheme]}")
    argv += [
        f"--user-data-dir={Path(tmp) / 'profile'}",
        f"--window-size={window_size[0]},{window_size[1]}",
        f"--virtual-time-budget={virtual_time_budget}",
        "--dump-dom",
    ]
    return argv


def _read_result(dom: str, page_dir: Path):
    match = _RESULT_RE.search(dom)
    if match is None:
        raise ProbeNotReported(
            f"probe never reported for page {page_dir.name!r} ({page_dir}): "
            "no <pre id=\"probe-result\"> in the dumped DOM; "
            "check that the page sets data-pad-ready and the virtual-time budget is enough"
        )
    result = json.loads(html.unescape(match.group(1)))
    if not result["ok"]:
        raise ProbeFailed(f"probe threw on page {page_dir.name!r}: {result['error']}")
    return result["value"]


def _run_launches(
    page_dir: Path,
    wrappers: Sequence[str],
    *,
    window_size: tuple[int, int],
    timeout: float,
    virtual_time_budget: int,
    env: Mapping[str, str] | None,
    lookup: Callable[[], Browser | None] | None,
    entry: str,
    inject_into: str | Sequence[str] | None,
    serve: bool,
    serve_port: int,
    color_scheme: str | None,
    before_script: str | None,
) -> list:
    env = os.environ if env is None else env
    browser = _require_browser(env, lookup)
    page_dir = Path(page_dir)
    values = []
    with tempfile.TemporaryDirectory(prefix="chromium-harness-") as tmp:
        page, targets = _prepare_copy(page_dir, tmp, entry, inject_into, before_script)
        argv = _argv(browser, env, tmp, window_size, virtual_time_budget, color_scheme)
        with contextlib.ExitStack() as stack:
            if serve:
                base = stack.enter_context(serve_folder(page, port=serve_port))
                argv.append(f"{base}/{entry}")
            else:
                argv.append((page / entry).as_uri())
            for wrapper in wrappers:
                for probe_dir in {target.parent for target in targets}:
                    (probe_dir / PROBE_FILE).write_text(wrapper)
                values.append(_read_result(_launch(argv, browser, timeout, env), page_dir))
    return values


def run_probe(
    page_dir: Path,
    probe_js: str,
    *,
    window_size: tuple[int, int] = (1280, 900),
    timeout: float = DEFAULT_TIMEOUT,
    virtual_time_budget: int = 5000,
    env: Mapping[str, str] | None = None,
    lookup: Callable[[], Browser | None] | None = None,
    entry: str = "index.html",
    inject_into: str | Sequence[str] | None = None,
    serve: bool = False,
    serve_port: int = 0,
    color_scheme: str | None = None,
    before_script: str | None = None,
):
    return run_probe_steps(
        page_dir,
        [probe_js],
        window_size=window_size,
        timeout=timeout,
        virtual_time_budget=virtual_time_budget,
        env=env,
        lookup=lookup,
        entry=entry,
        inject_into=inject_into,
        serve=serve,
        serve_port=serve_port,
        color_scheme=color_scheme,
        before_script=before_script,
    )


def run_probe_steps(
    page_dir: Path,
    steps: Sequence[str],
    *,
    window_size: tuple[int, int] = (1280, 900),
    timeout: float = DEFAULT_TIMEOUT,
    virtual_time_budget: int = 5000,
    env: Mapping[str, str] | None = None,
    lookup: Callable[[], Browser | None] | None = None,
    entry: str = "index.html",
    inject_into: str | Sequence[str] | None = None,
    serve: bool = False,
    serve_port: int = 0,
    color_scheme: str | None = None,
    before_script: str | None = None,
):
    wrapper = _render_wrapper("steps", steps, virtual_time_budget)
    (value,) = _run_launches(
        page_dir,
        [wrapper],
        window_size=window_size,
        timeout=timeout,
        virtual_time_budget=virtual_time_budget,
        env=env,
        lookup=lookup,
        entry=entry,
        inject_into=inject_into,
        serve=serve,
        serve_port=serve_port,
        color_scheme=color_scheme,
        before_script=before_script,
    )
    return value


def run_probe_pair(
    page_dir: Path,
    probe_one: str,
    probe_two: str,
    *,
    window_size: tuple[int, int] = (1280, 900),
    timeout: float = DEFAULT_TIMEOUT,
    virtual_time_budget: int = PAIR_VIRTUAL_TIME_BUDGET,
    env: Mapping[str, str] | None = None,
    lookup: Callable[[], Browser | None] | None = None,
    entry: str = "index.html",
    inject_into: str | Sequence[str] | None = None,
    serve: bool = False,
    serve_port: int = 0,
    color_scheme: str | None = None,
    before_script: str | None = None,
) -> tuple:
    wrapper = _render_wrapper("pair", [probe_one, probe_two], virtual_time_budget)
    (value,) = _run_launches(
        page_dir,
        [wrapper],
        window_size=window_size,
        timeout=timeout,
        virtual_time_budget=virtual_time_budget,
        env=env,
        lookup=lookup,
        entry=entry,
        inject_into=inject_into,
        serve=serve,
        serve_port=serve_port,
        color_scheme=color_scheme,
        before_script=before_script,
    )
    return tuple(value)


def run_probe_launches(
    page_dir: Path,
    probes: Sequence[str],
    *,
    window_size: tuple[int, int] = (1280, 900),
    timeout: float = DEFAULT_TIMEOUT,
    virtual_time_budget: int = 5000,
    env: Mapping[str, str] | None = None,
    lookup: Callable[[], Browser | None] | None = None,
    entry: str = "index.html",
    inject_into: str | Sequence[str] | None = None,
    serve: bool = False,
    serve_port: int = 0,
    color_scheme: str | None = None,
    before_script: str | None = None,
) -> list:
    wrappers = [_render_wrapper("steps", [probe], virtual_time_budget) for probe in probes]
    return _run_launches(
        page_dir,
        wrappers,
        window_size=window_size,
        timeout=timeout,
        virtual_time_budget=virtual_time_budget,
        env=env,
        lookup=lookup,
        entry=entry,
        inject_into=inject_into,
        serve=serve,
        serve_port=serve_port,
        color_scheme=color_scheme,
        before_script=before_script,
    )
