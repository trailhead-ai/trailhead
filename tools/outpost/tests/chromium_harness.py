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
from collections.abc import Callable, Iterator, Mapping
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

PROBE_FILE = "pad-probe.js"

_WRAPPER = """(function () {
  function report(payload) {
    var pre = document.createElement("pre");
    pre.id = "probe-result";
    pre.textContent = JSON.stringify(payload);
    document.body.appendChild(pre);
  }
  function padReady() {
    return new Promise(function (resolve) {
      var root = document.documentElement;
      if (root.hasAttribute("data-pad-ready")) { resolve(); return; }
      new MutationObserver(function (_, observer) {
        if (root.hasAttribute("data-pad-ready")) { observer.disconnect(); resolve(); }
      }).observe(root, { attributes: true });
    });
  }
  var probe = async function () {
%s
  };
  padReady()
    .then(function () { return document.fonts.ready; })
    .then(probe)
    .then(
      function (value) { report({ ok: true, value: value === undefined ? null : value }); },
      function (error) { report({ ok: false, error: String((error && error.message) || error) }); }
    );
})();
"""


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
    inject_into: str | None = None,
    serve: bool = False,
    serve_port: int = 0,
):
    env = os.environ if env is None else env
    browser = (lookup or (lambda: find_browser(env)))()
    if browser is None:
        message = "no headless Chrome/Chromium found (see chromium_harness.find_browser)"
        if env.get(ENV_REQUIRE):
            pytest.fail(f"{message}; {ENV_REQUIRE} is set, so a missing browser is a failure")
        pytest.skip(f"{message}; set {ENV_REQUIRE} to fail instead of skip")

    page_dir = Path(page_dir)
    with tempfile.TemporaryDirectory(prefix="chromium-harness-") as tmp:
        page = Path(tmp) / "page"
        shutil.copytree(page_dir, page)
        target = page / (inject_into or entry)
        target.write_text(_inject(target.read_text()))
        (target.parent / PROBE_FILE).write_text(_WRAPPER % probe_js)
        index = page / entry

        argv = [str(browser.path)]
        if not browser.is_headless_shell:
            argv.append("--headless=new")
        if env.get(ENV_REQUIRE):
            argv.append("--no-sandbox")
        argv += [
            f"--user-data-dir={Path(tmp) / 'profile'}",
            f"--window-size={window_size[0]},{window_size[1]}",
            f"--virtual-time-budget={virtual_time_budget}",
            "--dump-dom",
        ]
        with contextlib.ExitStack() as stack:
            if serve:
                base = stack.enter_context(serve_folder(page, port=serve_port))
                argv.append(f"{base}/{entry}")
            else:
                argv.append(index.as_uri())
            dom = _launch(argv, browser, timeout, env)

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
