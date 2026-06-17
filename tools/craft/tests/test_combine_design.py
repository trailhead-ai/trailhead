"""Tests for combine_design.py — the per-screen HTML assembler.

Design contract (D-4/D-5/D-6/S-2/R-8/A-7):
  - D-4: ASSEMBLES (concatenates) approved per-screen markup VERBATIM — no re-render.
  - D-5: per-screen files are <surface>-<screen>.html flat in the design dir.
  - D-6: docbar toggles driven by chrome's declared variants; none declared → no toggles.
  - S-2: path-traversal guard — every globbed filename validated as relative to design_dir.
  - R-8: glob via sorted() (determinism); assembly in-memory, written once; no partial output.
  - A-7: named CLI args tested here so the documented invocation is real.

Hermeticity: whole test runs under tmp_path, no network, no real ~/.claude/, no real vault.
Import pattern: sys.path.insert(SCRIPTS_DIR) per test_handoff_capture.py:28-31 (U-1).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "craft" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import combine_design as cd  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(p: Path, name: str, body: str) -> Path:
    f = p / name
    f.write_text(body, encoding="utf-8")
    return f


def _make_screen_html(title: str, body_content: str) -> str:
    """Minimal but realistic per-screen HTML (verbatim body must survive D-4)."""
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head><title>{title}</title></head>\n"
        "<body>\n"
        "{body}\n"
        "</body>\n"
        "</html>\n"
    ).format(title=title, body=body_content)


def _make_chrome(variants: list[str]) -> str:
    """Minimal chrome catalog markdown. variants=[] → no Variants section."""
    header = (
        "---\n"
        "type: chrome-catalog\n"
        "surface: test-surface\n"
        "---\n\n"
        "# Test Surface Chrome\n\n"
        "## Brand tokens\n\n"
        "| Token | Value |\n"
        "|-------|-------|\n"
        "| --color-primary | #000 |\n\n"
    )
    if not variants:
        return header
    variant_lines = "\n".join(f"- {v}" for v in variants)
    return header + "## Variants\n\n" + variant_lines + "\n"


def _minimal_index(rows: list[dict]) -> str:
    """Minimal index.md with a Surface column."""
    lines = [
        "# Design Index\n\n"
        "| Surface | Screen | File | Notes |\n"
        "|---------|--------|------|-------|\n"
    ]
    for row in rows:
        lines.append(
            "| {surface} | {screen} | {file} | {notes} |\n".format(
                surface=row.get("surface", ""),
                screen=row.get("screen", ""),
                file=row.get("file", ""),
                notes=row.get("notes", ""),
            )
        )
    return "".join(lines)


# ---------------------------------------------------------------------------
# U-1: confirm import cleanly via sys.path.insert pattern (hermeticity)
# ---------------------------------------------------------------------------

def test_import_via_scripts_dir_harness():
    """combine_design imports cleanly via the established craft harness (U-1)."""
    assert hasattr(cd, "assemble")
    assert hasattr(cd, "main")


# ---------------------------------------------------------------------------
# A-7: CLI interface tested (named args + --help)
# ---------------------------------------------------------------------------

def test_cli_help_names_required_args():
    """--help output must name --designs-dir, --chrome-path, and --slug."""
    script = SCRIPTS_DIR / "combine_design.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--designs-dir" in result.stdout
    assert "--chrome-path" in result.stdout
    assert "--slug" in result.stdout


def test_cli_spec_link_arg_present():
    """--spec-url arg documented in --help (the link-back-to-spec feature)."""
    script = SCRIPTS_DIR / "combine_design.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--spec-url" in result.stdout


# ---------------------------------------------------------------------------
# D-4: verbatim assembly — exact reviewed markup survives (no re-render)
# ---------------------------------------------------------------------------

def test_verbatim_body_survives_in_output(tmp_path: Path):
    """Both screens' VERBATIM bodies must appear unchanged in the reference file (D-4)."""
    designs = tmp_path / "designs"
    designs.mkdir()

    body_a = '<div class="screen-a" data-id="unique-a">Hello from A</div>'
    body_b = '<p style="color:red">Screen B sentinel content XYZ123</p>'

    _write(designs, "web-dashboard.html", _make_screen_html("Dashboard", body_a))
    _write(designs, "web-settings.html", _make_screen_html("Settings", body_b))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "dashboard", "file": "web-dashboard.html"},
        {"surface": "web", "screen": "settings", "file": "web-settings.html"},
    ]))

    out_path = tmp_path / "my-design-reference.html"
    cd.assemble(
        designs_dir=designs,
        chrome_path=chrome,
        slug="my-design",
        output_path=out_path,
        spec_url=None,
    )

    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert body_a in content, "Screen A verbatim body must appear in output"
    assert body_b in content, "Screen B verbatim body must appear in output"


# ---------------------------------------------------------------------------
# D-4: numbered sections + in-page TOC
# ---------------------------------------------------------------------------

def test_numbered_sections_and_toc(tmp_path: Path):
    """Output must contain a numbered section per screen and a TOC anchoring each."""
    designs = tmp_path / "designs"
    designs.mkdir()
    _write(designs, "web-home.html", _make_screen_html("Home", "<p>home</p>"))
    _write(designs, "web-about.html", _make_screen_html("About", "<p>about</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
        {"surface": "web", "screen": "about", "file": "web-about.html"},
    ]))

    out_path = tmp_path / "test-design-reference.html"
    cd.assemble(
        designs_dir=designs,
        chrome_path=chrome,
        slug="test-design",
        output_path=out_path,
        spec_url=None,
    )

    content = out_path.read_text(encoding="utf-8")

    # TOC present with anchors
    assert 'href="#screen-' in content or 'href="#' in content, "TOC must have anchor links"

    # Both screens numbered (01, 02 or 1., 2. pattern)
    assert "web-home" in content.lower() or "home" in content.lower()
    assert "web-about" in content.lower() or "about" in content.lower()


# ---------------------------------------------------------------------------
# D-6: docbar toggles driven by chrome variants (or none)
# ---------------------------------------------------------------------------

def test_docbar_no_toggles_when_chrome_declares_none(tmp_path: Path):
    """Chrome with no Variants section → no docbar toggles in output (D-6)."""
    designs = tmp_path / "designs"
    designs.mkdir()
    _write(designs, "web-home.html", _make_screen_html("Home", "<p>home</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))  # no variants

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
    ]))

    out_path = tmp_path / "test-design-reference.html"
    cd.assemble(
        designs_dir=designs,
        chrome_path=chrome,
        slug="test-design",
        output_path=out_path,
        spec_url=None,
    )

    content = out_path.read_text(encoding="utf-8")
    # No toggle/checkbox/button UI for theme/density switches when no variants declared
    assert "docbar-toggle" not in content
    assert 'class="toggle"' not in content or content.count('class="toggle"') == 0


def test_docbar_toggles_present_for_declared_variants(tmp_path: Path):
    """Chrome declaring two variants → docbar emits exactly those two toggles (D-6)."""
    designs = tmp_path / "designs"
    designs.mkdir()
    _write(designs, "web-home.html", _make_screen_html("Home", "<p>home</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome(["theme: light/dark", "density: compact/full"]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
    ]))

    out_path = tmp_path / "test-design-reference.html"
    cd.assemble(
        designs_dir=designs,
        chrome_path=chrome,
        slug="test-design",
        output_path=out_path,
        spec_url=None,
    )

    content = out_path.read_text(encoding="utf-8")
    # Both declared variants must appear as named toggles
    assert "theme" in content
    assert "density" in content
    assert "docbar-toggle" in content


# ---------------------------------------------------------------------------
# D-5: multi-surface filename prefix grouping
# ---------------------------------------------------------------------------

def test_multi_surface_sections_labeled_by_prefix(tmp_path: Path):
    """admin-*.html + mobile-*.html → sections labeled by surface prefix (D-5)."""
    designs = tmp_path / "designs"
    designs.mkdir()
    _write(designs, "admin-home.html", _make_screen_html("Admin Home", "<p>admin home</p>"))
    _write(
        designs, "admin-settings.html",
        _make_screen_html("Admin Settings", "<p>admin settings</p>"),
    )
    _write(designs, "mobile-home.html", _make_screen_html("Mobile Home", "<p>mobile home</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "admin", "screen": "home", "file": "admin-home.html"},
        {"surface": "admin", "screen": "settings", "file": "admin-settings.html"},
        {"surface": "mobile", "screen": "home", "file": "mobile-home.html"},
    ]))

    out_path = tmp_path / "multi-surface-reference.html"
    cd.assemble(
        designs_dir=designs,
        chrome_path=chrome,
        slug="multi-surface",
        output_path=out_path,
        spec_url=None,
    )

    content = out_path.read_text(encoding="utf-8")
    # Both surface labels must appear
    assert "admin" in content
    assert "mobile" in content
    # Admin body present
    assert "<p>admin home</p>" in content
    assert "<p>mobile home</p>" in content


# ---------------------------------------------------------------------------
# D-4 + self-contained: spec link + no decision log duplication
# ---------------------------------------------------------------------------

def test_spec_link_present_in_output(tmp_path: Path):
    """Output must contain a link back to the spec URL when provided."""
    designs = tmp_path / "designs"
    designs.mkdir()
    _write(designs, "web-home.html", _make_screen_html("Home", "<p>home</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
    ]))

    spec_url = "https://example.com/specs/my-feature"
    out_path = tmp_path / "test-design-reference.html"
    cd.assemble(
        designs_dir=designs,
        chrome_path=chrome,
        slug="test-design",
        output_path=out_path,
        spec_url=spec_url,
    )

    content = out_path.read_text(encoding="utf-8")
    assert spec_url in content, "Spec URL must appear as a link in the output"


def test_no_external_stylesheet_link_except_flagged_webfont(tmp_path: Path):
    (
        "Output must be self-contained — no external stylesheet links "
        "(except mockup-only web-font flagged as comment)."
    )
    designs = tmp_path / "designs"
    designs.mkdir()
    _write(designs, "web-home.html", _make_screen_html("Home", "<p>home</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
    ]))

    out_path = tmp_path / "test-design-reference.html"
    cd.assemble(
        designs_dir=designs,
        chrome_path=chrome,
        slug="test-design",
        output_path=out_path,
        spec_url=None,
    )

    content = out_path.read_text(encoding="utf-8")
    # No live <link rel="stylesheet" href="http..."> tags (external CDN sheet)
    import re
    live_stylesheet_links = re.findall(
        r'<link[^>]+rel=["\']stylesheet["\'][^>]+href=["\']https?://',
        content, re.IGNORECASE
    )
    # If any web-font link exists, it must be inside a comment (mockup-only flag)
    for link in live_stylesheet_links:
        # Check it's within an HTML comment
        idx = content.find(link)
        before = content[:idx]
        # Last <!-- before the link must not have a --> after it before the link
        last_comment_open = before.rfind("<!--")
        last_comment_close = before.rfind("-->")
        assert last_comment_open > last_comment_close, (
            f"External stylesheet link {link!r} is not inside a comment. "
            "CDN web-font links must be flagged as mockup-only via HTML comment."
        )


# ---------------------------------------------------------------------------
# S-2: path-traversal guard
# ---------------------------------------------------------------------------

def test_path_traversal_rejected_nonzero_exit_no_output(tmp_path: Path):
    """A crafted ../../outside.html filename → nonzero exit, no output file written (S-2)."""
    designs = tmp_path / "designs"
    designs.mkdir()

    # Create an outside file that a traversal would reach
    outside = tmp_path / "outside.html"
    outside.write_text(_make_screen_html("Outside", "<p>should not appear</p>"))

    # Symlink inside the designs dir pointing outside (simulates ../../outside.html)
    escape_link = designs / "escape.html"
    escape_link.symlink_to(outside)

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "escape", "file": "escape.html"},
    ]))

    out_path = tmp_path / "test-design-reference.html"
    with pytest.raises(SystemExit) as exc_info:
        cd.assemble(
            designs_dir=designs,
            chrome_path=chrome,
            slug="test-design",
            output_path=out_path,
            spec_url=None,
        )

    assert exc_info.value.code != 0, "Path traversal must cause nonzero exit"
    assert not out_path.exists(), "No output file must be written on traversal error (R-8)"


def test_path_traversal_via_cli_nonzero_no_output(tmp_path: Path):
    """CLI invocation with a symlink escaping designs_dir → nonzero exit, no output (S-2)."""
    designs = tmp_path / "designs"
    designs.mkdir()

    outside = tmp_path / "outside.html"
    outside.write_text(_make_screen_html("Outside", "<p>evil content</p>"))

    escape_link = designs / "evil.html"
    escape_link.symlink_to(outside)

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "evil", "file": "evil.html"},
    ]))

    out_path = tmp_path / "evil-design-reference.html"
    script = SCRIPTS_DIR / "combine_design.py"
    result = subprocess.run(
        [
            sys.executable, str(script),
            "--designs-dir", str(designs),
            "--chrome-path", str(chrome),
            "--slug", "evil-design",
            "--output", str(out_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, "Path traversal must cause nonzero CLI exit"
    assert not out_path.exists(), "No output file on traversal error"


# ---------------------------------------------------------------------------
# R-8: determinism — index.md row order is authoritative
# ---------------------------------------------------------------------------

def test_index_order_is_authoritative_over_filename_alpha(tmp_path: Path):
    """index.md row order must be preserved even when it is non-alphabetical (R-8 / D-5).

    A designer writing login→dashboard→settings must NOT get alphabetized output.
    This test sets up web-zzz before web-aaa in index.md and asserts zzz comes
    FIRST in the output — the opposite of filename-sorted order.  The test would go
    RED if the code re-sorted ordered_screens by filename after building the list.
    """
    designs = tmp_path / "designs"
    designs.mkdir()

    _write(designs, "web-zzz.html", _make_screen_html("ZZZ", "<p>zzz content</p>"))
    _write(designs, "web-aaa.html", _make_screen_html("AAA", "<p>aaa content</p>"))
    _write(designs, "web-mmm.html", _make_screen_html("MMM", "<p>mmm content</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    # index lists screens in narrative order: zzz → mmm → aaa (NOT alphabetical)
    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "zzz", "file": "web-zzz.html"},
        {"surface": "web", "screen": "mmm", "file": "web-mmm.html"},
        {"surface": "web", "screen": "aaa", "file": "web-aaa.html"},
    ]))

    out_path = tmp_path / "index-order-reference.html"
    cd.assemble(
        designs_dir=designs,
        chrome_path=chrome,
        slug="index-order",
        output_path=out_path,
        spec_url=None,
    )

    content = out_path.read_text(encoding="utf-8")

    zzz_pos = content.index("zzz content")
    mmm_pos = content.index("mmm content")
    aaa_pos = content.index("aaa content")

    assert zzz_pos < mmm_pos < aaa_pos, (
        f"Sections must appear in index.md row order (zzz→mmm→aaa), not alphabetical order. "
        f"Got: zzz({zzz_pos}) mmm({mmm_pos}) aaa({aaa_pos})"
    )


# ---------------------------------------------------------------------------
# R-8: no partial output on error
# ---------------------------------------------------------------------------

def test_no_partial_output_on_missing_screen_file(tmp_path: Path):
    """Missing per-screen file → nonzero exit, no output written (R-8)."""
    designs = tmp_path / "designs"
    designs.mkdir()
    # web-home.html exists; web-missing.html does NOT
    _write(designs, "web-home.html", _make_screen_html("Home", "<p>home</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
        {"surface": "web", "screen": "missing", "file": "web-missing.html"},
    ]))

    out_path = tmp_path / "test-design-reference.html"
    with pytest.raises(SystemExit) as exc_info:
        cd.assemble(
            designs_dir=designs,
            chrome_path=chrome,
            slug="test-design",
            output_path=out_path,
            spec_url=None,
        )

    assert exc_info.value.code != 0
    assert not out_path.exists(), "No partial output file must exist after error"


def test_malformed_missing_screen_named_error(tmp_path: Path):
    """Missing per-screen input → nonzero exit + named error naming the offending file."""
    designs = tmp_path / "designs"
    designs.mkdir()

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "ghost", "file": "web-ghost.html"},
    ]))

    out_path = tmp_path / "test-design-reference.html"
    script = SCRIPTS_DIR / "combine_design.py"
    result = subprocess.run(
        [
            sys.executable, str(script),
            "--designs-dir", str(designs),
            "--chrome-path", str(chrome),
            "--slug", "test-design",
            "--output", str(out_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, "Missing screen file must cause nonzero exit"
    # Named error must mention the offending file
    error_output = result.stderr + result.stdout
    assert "web-ghost.html" in error_output, (
        f"Error output must name the offending file. Got: {error_output!r}"
    )
    assert not out_path.exists(), "No partial output file"


# ---------------------------------------------------------------------------
# CLI end-to-end: basic invocation produces output
# ---------------------------------------------------------------------------

def test_cli_basic_invocation(tmp_path: Path):
    """CLI invocation with valid inputs produces the expected output file."""
    designs = tmp_path / "designs"
    designs.mkdir()

    _write(designs, "web-home.html", _make_screen_html("Home", "<p>cli home sentinel</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
    ]))

    out_path = tmp_path / "cli-design-reference.html"
    script = SCRIPTS_DIR / "combine_design.py"
    result = subprocess.run(
        [
            sys.executable, str(script),
            "--designs-dir", str(designs),
            "--chrome-path", str(chrome),
            "--slug", "cli-design",
            "--output", str(out_path),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert out_path.exists(), "Output file must be created"
    content = out_path.read_text(encoding="utf-8")
    assert "<p>cli home sentinel</p>" in content


def test_cli_with_spec_url(tmp_path: Path):
    """CLI --spec-url arg produces output containing the URL."""
    designs = tmp_path / "designs"
    designs.mkdir()

    _write(designs, "web-home.html", _make_screen_html("Home", "<p>home</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
    ]))

    spec_url = "https://example.com/my-spec"
    out_path = tmp_path / "spec-design-reference.html"
    script = SCRIPTS_DIR / "combine_design.py"
    result = subprocess.run(
        [
            sys.executable, str(script),
            "--designs-dir", str(designs),
            "--chrome-path", str(chrome),
            "--slug", "spec-design",
            "--output", str(out_path),
            "--spec-url", spec_url,
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    content = out_path.read_text(encoding="utf-8")
    assert spec_url in content


# ---------------------------------------------------------------------------
# Design tokens swatch section
# ---------------------------------------------------------------------------

def test_design_tokens_swatch_section_present(tmp_path: Path):
    """Output contains a '00 Design tokens' swatch section."""
    designs = tmp_path / "designs"
    designs.mkdir()
    _write(designs, "web-home.html", _make_screen_html("Home", "<p>home</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    index = designs / "index.md"
    index.write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
    ]))

    out_path = tmp_path / "tokens-design-reference.html"
    cd.assemble(
        designs_dir=designs,
        chrome_path=chrome,
        slug="tokens-design",
        output_path=out_path,
        spec_url=None,
    )

    content = out_path.read_text(encoding="utf-8")
    assert "Design tokens" in content or "design-tokens" in content.lower()


# ---------------------------------------------------------------------------
# M-1: _html_escape must also escape single quotes (defense-in-depth)
# ---------------------------------------------------------------------------

def test_html_escape_escapes_single_quotes():
    """_html_escape must replace ' with &#39; so the helper is safe in single-quoted attrs."""
    result = cd._html_escape("it's a test")
    assert "&#39;" in result, "_html_escape must escape single quotes as &#39;"
    assert "'" not in result, "Raw single quote must not remain after escaping"


def test_html_escape_escapes_all_required_chars():
    """_html_escape covers &, <, >, \", and ' as required characters."""
    result = cd._html_escape("& < > \" '")
    assert "&amp;" in result
    assert "&lt;" in result
    assert "&gt;" in result
    assert "&quot;" in result
    assert "&#39;" in result


# ---------------------------------------------------------------------------
# Leak gate: combine_design.py + docs surface are zenith-token-clean (D-7/S-3)
# ---------------------------------------------------------------------------

_STEP6_DENYLIST_TOKENS = [
    # Structurally-observable zenith tokens — safe to name in tracked test source
    r"zenithhealth",
    r"\bzenith\b",
    r"dash0",
    r"cortana(-zh)?",
    r"\basana\b",
    r"platform-admin-ui",
    r"patient-portal-web",
    r"mobile-overview",
    r"admin-preview",
    r"preview\s*(url|server|host)",
    r"\.workspace-manifest",
    r"brain/(designs|chrome|specs|plans|sessions)",
]


def _write_ephemeral_denylist(p: Path) -> Path:
    """Write an ephemeral denylist to tmp_path (S-3: never depend on machine-local)."""
    dl = p / "step6-denylist.txt"
    dl.write_text("\n".join(_STEP6_DENYLIST_TOKENS) + "\n", encoding="utf-8")
    return dl


# ---------------------------------------------------------------------------
# I-1: env-var root-resolution fallbacks (DESIGNS_ROOT / CHROME_ROOT)
# ---------------------------------------------------------------------------

def test_env_fallback_designs_root_when_flag_absent(tmp_path: Path, monkeypatch):
    """When --designs-dir is absent but DESIGNS_ROOT is set, combine_design.py uses the env var."""
    designs = tmp_path / "designs"
    designs.mkdir()
    _write(designs, "web-home.html", _make_screen_html("Home", "<p>env-root sentinel</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    (designs / "index.md").write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
    ]))

    out_path = tmp_path / "env-root-reference.html"
    script = SCRIPTS_DIR / "combine_design.py"

    env = {"DESIGNS_ROOT": str(designs), "CHROME_ROOT": str(chrome.parent)}
    # Inherit PATH so python and locale work; inject our vars
    import os
    full_env = {**os.environ, **env}

    result = subprocess.run(
        [
            sys.executable, str(script),
            # --designs-dir intentionally absent; DESIGNS_ROOT env provides it
            "--chrome-path", str(chrome),
            "--slug", "env-root",
            "--output", str(out_path),
        ],
        capture_output=True,
        text=True,
        env=full_env,
    )

    assert result.returncode == 0, (
        f"combine_design.py must fall back to DESIGNS_ROOT when --designs-dir absent. "
        f"stderr: {result.stderr}"
    )
    assert out_path.exists()
    assert "<p>env-root sentinel</p>" in out_path.read_text(encoding="utf-8")


def test_env_fallback_chrome_root_when_flag_absent(tmp_path: Path, monkeypatch):
    (
        "When --chrome-path is absent but CHROME_ROOT is set, combine_design.py "
        "uses CHROME_ROOT as the chrome path."
    )
    designs = tmp_path / "designs"
    designs.mkdir()
    _write(designs, "web-home.html", _make_screen_html("Home", "<p>chrome-root sentinel</p>"))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    (designs / "index.md").write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
    ]))

    out_path = tmp_path / "chrome-root-reference.html"
    script = SCRIPTS_DIR / "combine_design.py"

    import os
    # CHROME_ROOT points to the chrome file path (the fallback for --chrome-path)
    full_env = {**os.environ, "CHROME_ROOT": str(chrome)}

    result = subprocess.run(
        [
            sys.executable, str(script),
            "--designs-dir", str(designs),
            # --chrome-path intentionally absent; CHROME_ROOT env provides it
            "--slug", "chrome-root",
            "--output", str(out_path),
        ],
        capture_output=True,
        text=True,
        env=full_env,
    )

    assert result.returncode == 0, (
        f"combine_design.py must fall back to CHROME_ROOT when --chrome-path absent. "
        f"stderr: {result.stderr}"
    )
    assert out_path.exists()
    assert "<p>chrome-root sentinel</p>" in out_path.read_text(encoding="utf-8")


def test_cli_flag_wins_over_env_designs_root(tmp_path: Path):
    """Explicit --designs-dir flag must take precedence over DESIGNS_ROOT env var."""
    # Two design directories — flag points to flag_designs, env points to env_designs.
    flag_designs = tmp_path / "flag_designs"
    flag_designs.mkdir()
    _write(flag_designs, "web-home.html", _make_screen_html("Home", "<p>FLAG sentinel</p>"))
    (flag_designs / "index.md").write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
    ]))

    env_designs = tmp_path / "env_designs"
    env_designs.mkdir()
    _write(env_designs, "web-home.html", _make_screen_html("Home", "<p>ENV sentinel</p>"))
    (env_designs / "index.md").write_text(_minimal_index([
        {"surface": "web", "screen": "home", "file": "web-home.html"},
    ]))

    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    out_path = tmp_path / "flag-wins-reference.html"
    script = SCRIPTS_DIR / "combine_design.py"

    import os
    full_env = {**os.environ, "DESIGNS_ROOT": str(env_designs)}

    result = subprocess.run(
        [
            sys.executable, str(script),
            "--designs-dir", str(flag_designs),  # flag points here
            "--chrome-path", str(chrome),
            "--slug", "flag-wins",
            "--output", str(out_path),
        ],
        capture_output=True,
        text=True,
        env=full_env,
    )

    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    content = out_path.read_text(encoding="utf-8")
    assert "<p>FLAG sentinel</p>" in content, "Flag must win over DESIGNS_ROOT env var"
    assert "<p>ENV sentinel</p>" not in content


def test_cli_neither_flag_nor_env_exits_nonzero(tmp_path: Path):
    (
        "When neither --designs-dir nor DESIGNS_ROOT is provided, "
        "combine_design.py exits nonzero with a named error."
    )
    chrome = tmp_path / "chrome.md"
    chrome.write_text(_make_chrome([]))

    out_path = tmp_path / "no-root-reference.html"
    script = SCRIPTS_DIR / "combine_design.py"

    import os
    # Strip DESIGNS_ROOT from env if set
    env = {k: v for k, v in os.environ.items() if k != "DESIGNS_ROOT"}

    result = subprocess.run(
        [
            sys.executable, str(script),
            # --designs-dir absent, DESIGNS_ROOT absent
            "--chrome-path", str(chrome),
            "--slug", "no-root",
            "--output", str(out_path),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0, (
        "combine_design.py must exit nonzero when neither --designs-dir "
        "nor DESIGNS_ROOT is provided"
    )
    error_output = result.stderr + result.stdout
    assert "DESIGNS_ROOT" in error_output or "designs" in error_output.lower(), (
        f"Error message must name DESIGNS_ROOT or designs dir. Got: {error_output!r}"
    )


def test_leak_gate_combine_design_script_is_clean(tmp_path: Path):
    """combine_design.py must have no Step-6 zenith tokens (D-7/S-3)."""
    script_path = SCRIPTS_DIR / "combine_design.py"
    if not script_path.exists():
        pytest.skip("combine_design.py not yet implemented")

    denylist = _write_ephemeral_denylist(tmp_path)
    gate = SCRIPTS_DIR / "leak_gate.py"

    result = subprocess.run(
        [sys.executable, str(gate), str(script_path.parent), "--denylist", str(denylist)],
        capture_output=True,
        text=True,
    )
    # Filter hits to only combine_design.py
    hits = [line for line in result.stdout.splitlines() if "combine_design" in line]
    assert not hits, (
        "combine_design.py contains forbidden tokens:\n" + "\n".join(hits)
    )
