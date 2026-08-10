# Capability Manifest Format

Each tool package ships a `capabilities.toml` file at its root
(`tools/<name>/capabilities.toml`). The manifest declares the tool's name,
what always ships regardless of selection (`base` dirs, an optional
`hooks_json`), and — via `capabilities.py`'s `load_manifest` — the *selectable
inventory* an install config can pick by name: subagents and skills.

There is no capability-GROUP concept. An earlier design grouped skills/agents
under named `[capabilities.<name>]` tables that a config selected as a unit;
that model is gone. Selection today is per-subagent / per-skill **by name**
(see `trailhead/install_config.py`), and the selectable inventory itself is
**discovered on disk by convention**, never hand-listed in the manifest.

---

## `[tool]` block

```toml
[tool]
name = "lore"                       # required — MUST equal the plugins/<name>/ dir
base = ["skills/_shared"]           # always-on, non-selectable dirs
hooks_json = "hooks/hooks.json"     # optional — see "Hooks are a whole-tool unit"
validate = true                     # optional, default true — see validate=false escape hatch
```

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `name` | string | yes | Tool identifier. Must match the plugin directory name under `plugins/`. |
| `base` | list of strings | no (default `[]`) | Dirs that ship on every install of this tool, regardless of which subagents/skills are selected. Typically shared includes with no `SKILL.md` of their own (e.g. `skills/_shared`), so they're never independently selectable. |
| `hooks_json` | string | no | Path to the hooks registration file, relative to the plugin root. See [Hooks are a whole-tool unit](#hooks-are-a-whole-tool-unit). |
| `validate` | bool | no (default `true`) | When `false`, the loader parses and structures the manifest but skips all directory/file existence checks. See [validate=false escape hatch](#validatefalse-escape-hatch). |

A manifest with no capabilities to speak of is a valid, complete manifest —
several tools ship nothing beyond `[tool] name = "..."` (see
[Real examples](#real-examples) below).

---

## Selectable inventory — discovered by convention

`load_manifest` never reads a hand-authored list of skills/agents from the
TOML. Instead it globs the tool's plugin root:

- **subagents** — every `agents/<name>.md` file → `{name: "agents/<name>.md"}`.
- **skills** — every `skills/<name>/` directory that contains a `SKILL.md` →
  `{name: "skills/<name>"}`, **minus** any dir named in `base`.

A skill dir without a `SKILL.md` (e.g. `skills/_shared` holding a shared
include) is therefore never selectable on its own — list it in `base` so it
still ships. To make a new subagent or skill selectable, just add the
`agents/<name>.md` file or `skills/<name>/SKILL.md` dir; no manifest edit
needed. `Manifest.subagents` and `Manifest.skills` are the discovered
inventory — the "ALL" set an install config can request by passing a bare
plugin string (see `install_config.py`).

---

## Path resolution

Every path in `base` and `hooks_json` is **relative to the tool's plugin
root**:

```
<manifest_dir>/plugins/<tool.name>/
```

For example, in `tools/craft/capabilities.toml` with `name = "craft"`, the
entry `"skills/_shared"` resolves to:

```
tools/craft/plugins/craft/skills/_shared
```

### Type conventions

- `base` entries must resolve to **directories**.
- `hooks_json` must resolve to a **file**.
- Discovered `agents/<name>.md` entries are always files (globbed with `*.md`);
  discovered `skills/<name>/` entries are always directories containing a
  `SKILL.md` file — both are inherently well-typed by how they're found, so
  the loader does not separately validate their type.

---

## Hooks are a whole-tool unit

Hooks are registered for the tool as a whole, not per-capability. A tool with
hooks ships a single `plugins/<name>/hooks/hooks.json` that registers all of
its hooks. The manifest names this file once at `[tool]` level via
`hooks_json`; the whole containing dir is wired by the composer so sibling
scripts ship too.

Capability-gating of hooks (e.g. "only fire hook H when subagent/skill S is
selected") is handled *inside* `hooks.json` via matchers, not by splitting the
hooks file. Tools without hooks (e.g. craft) omit `hooks_json` entirely.

---

## Confinement guarantee

Every referenced path (`base` entries and `hooks_json`) is verified to stay
**inside the plugin root** before any filesystem stat is performed:

```python
candidate = (plugin_root.resolve() / entry).resolve()
assert candidate.is_relative_to(plugin_root.resolve())
```

This defeats two classes of attack:

- **`../` traversal** — `resolve()` collapses `..` components before the
  `is_relative_to` check.
- **Absolute-path injection** — Python's `Path("/a") / "/b"` silently drops
  `/a`, producing `/b`. The `resolve()`-then-`is_relative_to` check exposes
  this because `/b` is not inside the plugin root.

A path that fails confinement raises `ConfineError(tool, context, entry)`
immediately, before any disk access. Discovered subagents/skills need no
separate confinement check — they're globbed from inside the plugin root, so
they can never escape it.

---

## `validate=false` escape hatch

Set `validate = false` in `[tool]` to skip all existence and type checks on
`base` and `hooks_json`. The manifest is still parsed and structurally
validated (required fields, confinement); only the filesystem checks are
skipped.

Use this for tools whose plugin directory tree does not yet exist on disk
(e.g. a placeholder manifest committed before the actual files land).

```toml
[tool]
name = "example-tool"
validate = false
base = ["skills/placeholder"]
```

Once the on-disk tree exists, remove `validate = false` (or set it to `true`)
so the loader confirms every referenced path.

---

## Malformed TOML

`tomllib.TOMLDecodeError` (e.g. a duplicated table in the same file) is caught
by the loader and re-raised as `ManifestError` citing the manifest path —
callers never see a raw `tomllib` exception.

---

## Real examples

The minimal manifest — no `base`, no hooks, everything selectable is
discovered from `agents/*.md` and `skills/*/`:

```toml
# tools/portage/capabilities.toml
[tool]
name = "portage"
```

A manifest with always-on includes — a shared skills dir (never independently
selectable, because it has no `SKILL.md`) and a plain data dir the shipped
skills read at runtime:

```toml
# tools/craft/capabilities.toml
[tool]
name = "craft"
base = ["skills/_shared", "templates", "scripts"]
```
