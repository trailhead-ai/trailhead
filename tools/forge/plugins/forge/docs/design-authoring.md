# Design Authoring — `combine_design.py`

This document covers the combine contract, the filename-prefix convention (D-5), the
docbar-variant convention (D-6), and the `combine_design.py` CLI so the design phase
is usable directly before the brainstorm-dispatch wire lands (A-6).

---

## Overview

A design in forge consists of:

1. **A design directory** — a flat directory of per-screen `<surface>-<screen>.html` files
   (the filename-prefix convention, D-5) plus an `index.md` engineering record.
2. **A chrome catalog** — a `<surface>.md` file declaring brand tokens and (optionally)
   cross-cutting variants (D-6).
3. **`combine_design.py`** — assembles all per-screen files into one self-contained
   `<slug>-design-reference.html` reference document.

---

## Filename-prefix convention (D-5)

Per-screen files are named `<surface>-<screen>.html`, flat in the design directory.

```
designs/
  admin-home.html
  admin-settings.html
  mobile-home.html
  index.md
```

The surface is the prefix up to the first hyphen. A single-surface design omits the
prefix concern; a multi-surface design uses consistent prefixes so `combine_design.py`
can group and label sections by surface.

The `index.md` carries a markdown table with at minimum a `Surface`, `Screen`, and
`File` column. This is the ordering and labeling source for the combined reference.

Example `index.md` table:

```markdown
| Surface | Screen    | File                  | Notes           |
|---------|-----------|-----------------------|-----------------|
| admin   | home      | admin-home.html       |                 |
| admin   | settings  | admin-settings.html   |                 |
| mobile  | home      | mobile-home.html      |                 |
```

---

## Docbar-variant convention (D-6)

The combined reference's docbar exposes a toggle for each cross-cutting variant a
chrome catalog declares. A chrome that declares no variants produces a docbar with
only the title and TOC — no toggles, no baked-in dark/light/density assumption.

To declare variants, add a `## Variants` section to the chrome catalog:

```markdown
## Variants

- theme: light/dark
- density: compact/full
```

Each list item becomes one named toggle in the docbar. The toggle sets a
`data-variant-<name>` attribute on `<html>` so per-screen CSS can respond to it.

If the chrome has no `## Variants` section, the docbar renders title + TOC only.

---

## `combine_design.py` CLI

```
combine_design.py --designs-dir <DIR> --chrome-path <FILE> --slug <SLUG>
                  [--output <FILE>] [--spec-url <URL>]
```

### Required arguments

| Argument | Description |
|----------|-------------|
| `--slug` | Design slug — used as the output filename prefix and document title |

### Positional/optional arguments

| Argument | Env fallback | Description |
|----------|--------------|-------------|
| `--designs-dir` | `DESIGNS_ROOT` | Directory containing `<surface>-<screen>.html` files + `index.md`. When omitted, falls back to the `DESIGNS_ROOT` env var. Error if neither is provided. |
| `--chrome-path` | `CHROME_ROOT` | Path to the chrome catalog markdown file. When omitted, falls back to the `CHROME_ROOT` env var. Error if neither is provided. |
| `--output` | — | Output path (default: `<designs-dir>/<slug>-design-reference.html`) |
| `--spec-url` | — | URL to link back to the originating spec (does not duplicate the decision log) |

The CLI flag always takes precedence over the env var; the env var is the fallback only.

### Example invocation

```bash
python3 tools/forge/plugins/forge/scripts/combine_design.py \
  --designs-dir path/to/designs/my-feature \
  --chrome-path path/to/chrome/admin-ui.md \
  --slug my-feature \
  --spec-url https://example.com/specs/my-feature
```

This produces `path/to/designs/my-feature/my-feature-design-reference.html`.

### How to invoke the artist directly

Before the brainstorm-dispatch wire is connected, you can use the `artist` agent
directly by providing it a brief containing:

- The feature name and design goals
- The surface(s) being designed (to select the right chrome catalog)
- The chrome catalog path and designs directory path as explicit inputs
- Component mapping rows — each referencing a real `file:line` in your codebase,
  or a `"new, no counterpart — <justification>"` note for genuinely greenfield UI

The artist returns per-screen `.html` files + an `index.md` for each screen in the
brief. Run `combine_design.py` over the output directory to produce the reference.

---

## Output structure

The combined reference contains:

1. **Docbar** — slug title + toggles for each declared chrome variant (if any)
2. **Screens TOC** — in-page navigation anchoring each numbered section
3. **00 Design tokens** — swatch table from the chrome catalog's brand tokens
4. **Numbered screen sections** — per-screen markup assembled VERBATIM (D-4,
   assemble-not-re-render); each section is wrapped in a labeled `<section>` element
5. **Spec link** — a back-link to the spec URL if `--spec-url` was provided

The output is self-contained: inline styles and toggle JS are embedded in the file.

---

## Security

Every per-screen file path is validated as relative to the `--designs-dir` root before
being read. A path escaping the directory (via symlink or `../` traversal) is rejected
with a named error and no partial output is written.

Per-screen bodies are **trusted/approved input emitted verbatim**. The `<section>`
wrapper is **NOT a sanitization or containment boundary** — a body containing
`</section>...<script>` breaks out to document level. The threat model is approved
mockups from the same operator running the tool; iframe isolation is out of scope.

The `--output` path is intentionally operator-chosen and unconstrained. The input
security boundary (S-2) applies to globbed *inputs* only, not the output path.

---

## Error handling

All errors produce a named message on stderr naming the offending file and exit nonzero.
No partial output file is written on any error — the output path either contains the
complete assembled reference or does not exist.
