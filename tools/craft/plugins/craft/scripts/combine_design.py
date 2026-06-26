#!/usr/bin/env python3
"""Deterministic, stdlib-only HTML assembler for design references.

Given a design dir of per-screen <surface>-<screen>.html files (the filename-prefix
convention) + an index.md + a chrome catalog's declared variants, emits ONE
self-contained <slug>-design-reference.html containing:
  - a docbar with global toggles driven by the chrome's declared variants
    (none declared → title + TOC only)
  - a '00 Design tokens' swatch section (from chrome brand tokens table)
  - an in-page 'Screens' TOC anchoring each section
  - numbered screen+state sections assembled by concatenating approved per-screen
    markup VERBATIM (assemble, NOT re-render)
  - a link back to the spec (does NOT duplicate the decision log)

Self-contained: inline styles + toggle JS in one file. Any CDN web-font link is
flagged mockup-only as an in-file comment. Roots are explicit CLI args, never a
hardcoded path.

Security: every globbed per-screen filename is validated as relative to the
design_dir root — any path escaping the root is rejected with a named nonzero error
and NO partial output written.

Determinism: assembly order follows the deterministic index.md row sequence;
output is built in-memory, written once at the end.

Usage:
    combine_design.py --designs-dir <path> --chrome-path <path> --slug <slug>
                      [--output <path>] [--spec-url <url>]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Chrome parsing — extract declared variants + brand token rows
# ---------------------------------------------------------------------------


def _parse_chrome_variants(chrome_text: str) -> list[str]:
    """Extract variant names from a chrome catalog's ## Variants section.

    Returns a list of variant names (e.g. ["theme: light/dark", "density: compact/full"])
    or [] if no ## Variants section exists.
    """
    # Find the ## Variants section
    match = re.search(r"^## Variants\s*\n(.*?)(?=^##|\Z)", chrome_text, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    block = match.group(1)
    variants = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            variants.append(stripped[2:].strip())
        elif stripped and not stripped.startswith("#"):
            variants.append(stripped)
    return [v for v in variants if v]


def _parse_chrome_tokens(chrome_text: str) -> list[tuple[str, str]]:
    """Extract brand token rows from the ## Brand tokens table in a chrome catalog.

    Returns list of (token_name, value) tuples.
    """
    match = re.search(
        r"^## Brand tokens?\s*\n(.*?)(?=^##|\Z)", chrome_text, re.MULTILINE | re.DOTALL
    )
    if not match:
        return []
    block = match.group(1)
    rows = []
    for line in block.splitlines():
        # Parse markdown table rows: | token | value |
        if line.strip().startswith("|") and "|" in line[1:]:
            parts = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(parts) >= 2 and parts[0] and parts[1] and not set(parts[0]) <= set("-"):
                rows.append((parts[0], parts[1]))
    return rows


# ---------------------------------------------------------------------------
# Index.md parsing — extract screen file list
# ---------------------------------------------------------------------------


def _parse_index(index_text: str) -> list[dict[str, str]]:
    """Parse index.md for the screen file table.

    Expects a markdown table with columns including Surface, Screen, File.
    Returns list of dicts with keys: surface, screen, file (lowercase).
    """
    rows = []
    header_found = False
    col_map: dict[str, int] = {}

    for line in index_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        parts = [c.strip() for c in stripped.strip("|").split("|")]
        if not header_found:
            # Look for a header row with Surface, Screen, File columns
            lower_parts = [p.lower() for p in parts]
            if "surface" in lower_parts and "file" in lower_parts:
                col_map = {
                    "surface": lower_parts.index("surface"),
                    "screen": lower_parts.index("screen") if "screen" in lower_parts else -1,
                    "file": lower_parts.index("file"),
                }
                header_found = True
            continue
        # Skip separator rows
        if all(set(p) <= set("- ") for p in parts if p):
            continue
        if not header_found:
            continue
        if len(parts) > col_map["file"]:
            surface = parts[col_map["surface"]] if col_map["surface"] < len(parts) else ""
            screen = (
                parts[col_map["screen"]]
                if col_map.get("screen", -1) >= 0 and col_map["screen"] < len(parts)
                else ""
            )
            file_val = parts[col_map["file"]]
            if file_val:
                rows.append({"surface": surface, "screen": screen, "file": file_val})
    return rows


# ---------------------------------------------------------------------------
# Path traversal guard
# ---------------------------------------------------------------------------


def _validate_screen_path(resolved: Path, design_dir: Path, filename: str) -> None:
    """Assert resolved path is within design_dir. Raises SystemExit(1) on escape."""
    design_dir_resolved = design_dir.resolve()
    try:
        resolved.relative_to(design_dir_resolved)
    except ValueError:
        print(
            f"combine_design: ERROR path traversal detected — '{filename}' resolves to "
            f"'{resolved}' which is outside the design directory '{design_dir_resolved}'.",
            file=sys.stderr,
        )
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# HTML generation helpers
# ---------------------------------------------------------------------------


def _slug_anchor(text: str) -> str:
    """Convert display text to a valid HTML anchor id."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _docbar_html(variants: list[str], title: str) -> str:
    """Emit the docbar header. Includes toggles only if variants are declared."""
    lines = [
        '<div id="docbar" style="'
        "font-family:sans-serif;padding:12px 20px;background:#f5f5f5;"
        'border-bottom:1px solid #ddd;display:flex;align-items:center;gap:16px;">\n',
        f'  <span style="font-weight:bold;font-size:1.1em;">{_html_escape(title)}</span>\n',
    ]
    if variants:
        lines.append('  <span style="margin-left:auto;display:flex;gap:12px;">\n')
        for variant in variants:
            # variant may be "theme: light/dark" — use the name before ":"
            label = variant.split(":")[0].strip()
            anchor = _slug_anchor(label)
            lines.append(
                f'    <label class="docbar-toggle" style="cursor:pointer;">'
                f'<input type="checkbox" id="toggle-{anchor}" '
                f"onchange=\"applyVariant('{anchor}',this.checked)\"> "
                f"{_html_escape(label)}</label>\n"
            )
        lines.append("  </span>\n")
    lines.append("</div>\n")
    return "".join(lines)


def _toc_html(screens: list[tuple[str, str, str]]) -> str:
    """Emit in-page TOC. screens: list of (number_label, display_name, anchor)."""
    lines = [
        '<nav id="screens-toc" style="padding:16px 20px;border-bottom:1px solid #eee;">\n',
        '  <h2 style="margin:0 0 8px;font-size:1em;font-family:sans-serif;">Screens</h2>\n',
        '  <ol style="font-family:sans-serif;margin:0;padding-left:20px;">\n',
    ]
    for num, name, anchor in screens:
        lines.append(
            f'    <li><a href="#{anchor}" style="text-decoration:none;">'
            f"{_html_escape(num)} — {_html_escape(name)}</a></li>\n"
        )
    lines.append("  </ol>\n</nav>\n")
    return "".join(lines)


def _tokens_swatch_html(tokens: list[tuple[str, str]]) -> str:
    """Emit the '00 Design tokens' swatch section."""
    lines = [
        '<section id="screen-00-design-tokens" style="padding:20px;">\n',
        '  <h2 style="font-family:sans-serif;font-size:1em;">00 Design tokens</h2>\n',
    ]
    if tokens:
        lines.append(
            '  <table style="border-collapse:collapse;font-family:monospace;font-size:0.9em;">\n'
        )
        for name, value in tokens:
            swatch = ""
            if value.startswith("#") or value.startswith("rgb"):
                swatch = (
                    f'<span style="display:inline-block;width:14px;height:14px;'
                    f"background:{_html_escape(value)};border:1px solid #999;"
                    f'vertical-align:middle;margin-right:6px;"></span>'
                )
            lines.append(
                f'    <tr><td style="padding:2px 12px 2px 0;">{_html_escape(name)}</td>'
                f"<td>{swatch}{_html_escape(value)}</td></tr>\n"
            )
        lines.append("  </table>\n")
    else:
        lines.append(
            '  <p style="font-family:sans-serif;color:#666;">'
            "No brand tokens found in chrome catalog.</p>\n"
        )
    lines.append("</section>\n")
    return "".join(lines)


def _screen_section_html(number: str, name: str, anchor: str, body_html: str) -> str:
    """Wrap a screen's body in a numbered section."""
    return (
        f'<section id="{anchor}" style="padding:20px;border-top:2px solid #ddd;">\n'
        f'  <h2 style="font-family:sans-serif;">'
        f"{_html_escape(number)} — {_html_escape(name)}</h2>\n"
        f"  <!-- begin verbatim screen markup (assembled verbatim, not re-rendered) -->\n"
        f"  {body_html}\n"
        f"  <!-- end verbatim screen markup -->\n"
        f"</section>\n"
    )


def _html_escape(s: str) -> str:
    """Minimal HTML entity escaping for text embedded in HTML attributes/text."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _toggle_js() -> str:
    """Inline JS for the docbar variant toggles."""
    return (
        "<script>\n"
        "function applyVariant(name, active) {\n"
        "  document.documentElement.setAttribute(\n"
        "    'data-variant-' + name, active ? 'on' : 'off'\n"
        "  );\n"
        "}\n"
        "</script>\n"
    )


def _shell_html(
    slug: str,
    docbar: str,
    toc: str,
    tokens_section: str,
    screen_sections: str,
    spec_link: str,
    has_variants: bool,
) -> str:
    """Assemble the final self-contained HTML document."""
    toggle_script = _toggle_js() if has_variants else ""
    # NOTE: any CDN web-font <link> would go here, flagged as mockup-only:
    # <!-- mockup-only web-font: <link rel="stylesheet" href="https://fonts.googleapis.com/..."> -->
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        f'  <meta charset="utf-8">\n'
        f'  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{_html_escape(slug)} — Design Reference</title>\n"
        "  <style>\n"
        "    * { box-sizing: border-box; }\n"
        "    body { margin: 0; background: #fff; color: #111; }\n"
        "    a { color: #0066cc; }\n"
        "  </style>\n"
        f"  {toggle_script}"
        "</head>\n"
        "<body>\n"
        f"{docbar}"
        f"{toc}"
        f"{tokens_section}"
        f"{screen_sections}"
        f"{spec_link}"
        "</body>\n"
        "</html>\n"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def assemble(
    designs_dir: Path,
    chrome_path: Path,
    slug: str,
    output_path: Path,
    spec_url: str | None,
) -> None:
    """Assemble a self-contained design reference HTML.

    Raises SystemExit(1) on any error; no partial output is written.
    """
    designs_dir = designs_dir.resolve()

    # Read chrome catalog
    if not chrome_path.exists():
        print(f"combine_design: ERROR chrome catalog not found: {chrome_path}", file=sys.stderr)
        raise SystemExit(1)
    chrome_text = chrome_path.read_text(encoding="utf-8")
    variants = _parse_chrome_variants(chrome_text)
    tokens = _parse_chrome_tokens(chrome_text)

    # Read index.md
    index_path = designs_dir / "index.md"
    if not index_path.exists():
        print(f"combine_design: ERROR index.md not found in {designs_dir}", file=sys.stderr)
        raise SystemExit(1)
    index_text = index_path.read_text(encoding="utf-8")
    screen_rows = _parse_index(index_text)

    if not screen_rows:
        print(
            "combine_design: ERROR no screen rows found in index.md — "
            "expected a markdown table with Surface and File columns.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Build ordered screen list from index (index.md row order is authoritative)
    ordered_screens: list[tuple[str, str, Path]] = []  # (surface, screen_label, path)
    for row in screen_rows:
        fname = row["file"]
        screen_path = (designs_dir / fname).resolve()

        # path traversal guard
        _validate_screen_path(screen_path, designs_dir, fname)

        if not screen_path.exists():
            print(
                f"combine_design: ERROR per-screen file not found: '{fname}' "
                f"(looked in {designs_dir})",
                file=sys.stderr,
            )
            raise SystemExit(1)

        surface = row.get("surface") or fname.split("-")[0]
        screen_label = row.get("screen") or fname.rsplit(".", 1)[0]
        ordered_screens.append((surface, screen_label, screen_path))

    # Build TOC entries
    toc_entries: list[tuple[str, str, str]] = []
    for i, (surface, screen_label, _) in enumerate(ordered_screens, start=1):
        number = f"{i:02d}"
        display = f"{surface}-{screen_label}"
        anchor = f"screen-{_slug_anchor(display)}"
        toc_entries.append((number, display, anchor))

    # Read screen bodies (verbatim) — all in memory before any write
    screen_bodies: list[str] = []
    for _, _, screen_path in ordered_screens:
        screen_bodies.append(screen_path.read_text(encoding="utf-8"))

    # Assemble sections in memory
    screen_sections_parts: list[str] = []
    for (num, name, anchor), body in zip(toc_entries, screen_bodies):
        screen_sections_parts.append(_screen_section_html(num, name, anchor, body))

    # Spec link (does NOT duplicate decision log — only a backlink)
    if spec_url:
        spec_link_html = (
            '<footer style="padding:20px;border-top:2px solid #ddd;font-family:sans-serif;">\n'
            f'  <a href="{_html_escape(spec_url)}">&#8592; Back to spec</a>\n'
            "</footer>\n"
        )
    else:
        spec_link_html = ""

    # Build full document in memory
    docbar = _docbar_html(variants, f"{slug} — Design Reference")
    toc = _toc_html(toc_entries)
    tokens_section = _tokens_swatch_html(tokens)
    screen_sections = "".join(screen_sections_parts)

    html = _shell_html(
        slug=slug,
        docbar=docbar,
        toc=toc,
        tokens_section=tokens_section,
        screen_sections=screen_sections,
        spec_link=spec_link_html,
        has_variants=bool(variants),
    )

    # Write ONCE at the end (no partial output)
    output_path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Assemble per-screen HTML files into a single self-contained design reference. "
            "Reads <surface>-<screen>.html files from --designs-dir, "
            "the chrome catalog from --chrome-path for variant declarations, "
            "and emits <slug>-design-reference.html (or --output path)."
        )
    )
    ap.add_argument(
        "--designs-dir",
        default=None,
        metavar="DIR",
        help=(
            "directory containing per-screen <surface>-<screen>.html files + index.md "
            "(falls back to DESIGNS_ROOT env var when absent)"
        ),
    )
    ap.add_argument(
        "--chrome-path",
        default=None,
        metavar="FILE",
        help=(
            "path to the chrome catalog markdown file (declares variants for docbar) "
            "(falls back to CHROME_ROOT env var when absent)"
        ),
    )
    ap.add_argument(
        "--slug",
        required=True,
        metavar="SLUG",
        help="design slug, used as the output filename prefix and document title",
    )
    ap.add_argument(
        "--output",
        metavar="FILE",
        help="output path (default: <designs-dir>/<slug>-design-reference.html)",
    )
    ap.add_argument(
        "--spec-url",
        metavar="URL",
        help="URL to link back to the originating spec (does not duplicate the decision log)",
    )
    args = ap.parse_args(argv)

    # Resolve --designs-dir: flag takes precedence, then DESIGNS_ROOT env var
    designs_dir_raw = args.designs_dir or os.environ.get("DESIGNS_ROOT")
    if not designs_dir_raw:
        print(
            "combine_design: ERROR --designs-dir not provided and DESIGNS_ROOT env var is not set.",
            file=sys.stderr,
        )
        return 1
    designs_dir = Path(designs_dir_raw).expanduser()

    # Resolve --chrome-path: flag takes precedence, then CHROME_ROOT env var
    chrome_path_raw = args.chrome_path or os.environ.get("CHROME_ROOT")
    if not chrome_path_raw:
        print(
            "combine_design: ERROR --chrome-path not provided and CHROME_ROOT env var is not set.",
            file=sys.stderr,
        )
        return 1
    chrome_path = Path(chrome_path_raw).expanduser()

    if args.output:
        output_path = Path(args.output).expanduser()
    else:
        output_path = designs_dir / f"{args.slug}-design-reference.html"

    assemble(
        designs_dir=designs_dir,
        chrome_path=chrome_path,
        slug=args.slug,
        output_path=output_path,
        spec_url=args.spec_url,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
