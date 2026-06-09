# Capability Manifest Format

Each tool package ships a `capabilities.toml` file at its root
(`tools/<name>/capabilities.toml`).  The manifest declares what the tool
can do, which skills and agents implement each capability, and which
baseline skills are always active when the tool is installed.

---

## `[tool]` block

```toml
[tool]
name = "lore"                           # required
base = ["skills/_shared", "skills/sync", "skills/ping"]   # always-on dirs
hooks_json = "hooks/hooks.json"         # optional — see D-E
validate = true                         # optional, default true — see validate=false
```

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `name` | string | yes | Tool identifier. Must match the plugin directory name under `plugins/`. |
| `base` | list of strings | no (default `[]`) | Skill directories that are always loaded when the tool is installed, regardless of which capabilities are active. |
| `hooks_json` | string | no | Path to the hooks registration file, relative to the plugin root. See [D-E: hooks are a whole-tool unit](#de-hooks-are-a-whole-tool-unit). |
| `validate` | bool | no (default `true`) | When `false`, the loader parses and structures the manifest but skips all directory/file existence checks. See [validate=false escape hatch](#validatefalse-escape-hatch). |

---

## `[capabilities.<name>]` blocks

```toml
[capabilities.capture]
description = "Capture decisions, dead-ends, deferrals, radar items, area notes, and intake seeds into the vault."
skills = ["skills/decision", "skills/dead-end", "skills/defer"]
agents = []
```

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `description` | string | yes | User-facing prose describing what this capability enables. Rendered by `trailhead config capabilities`. |
| `skills` | list of strings | yes | Skill directories that belong to this capability. Use `[]` for a capability that is planned but not yet built. |
| `agents` | list of strings | yes | Agent files (`.md`) that belong to this capability. Use `[]` if the capability has no agents. |

Both `skills` and `agents` **must be present** as keys, even if empty.  A
capability whose `skills` or `agents` key is **absent entirely** (as
opposed to set to `[]`) is rejected with a `ManifestError` — omitting a
key is an authoring mistake, not an intentional empty capability.

---

## Path resolution

Every path in `base`, `skills`, and `agents` is **relative to the tool's
plugin root**:

```
<manifest_dir>/plugins/<tool.name>/
```

For example, in `tools/lore/capabilities.toml` with `name = "lore"`, the
entry `"skills/decision"` resolves to:

```
tools/lore/plugins/lore/skills/decision
```

### Type conventions

- `skills/<x>` entries must resolve to **directories**.
- `agents/<x>.md` entries must resolve to **files** (the `.md` extension
  is enforced by convention; the loader validates that the entry resolves
  to a file).
- `hooks_json` must resolve to a **file**.

---

## D-E: Hooks are a whole-tool unit

Hooks are registered for the tool as a whole, not per-capability.  A tool
with hooks ships a single `plugins/<name>/hooks/hooks.json` that registers
all of its hooks.  The manifest names this file once at `[tool]` level via
`hooks_json`.

Capability-gating of hooks (e.g. "only fire hook H when capability C is
active") is handled *inside* `hooks.json` via matchers, not by splitting
the hooks file across capabilities.  You cannot meaningfully compose two
capabilities' `hooks_json` paths.

Forge has no hooks — `hooks_json` is omitted entirely.

---

## D-F: Confinement guarantee

Every referenced path (all `base` entries, all capability `skills` /
`agents` entries, and `hooks_json`) is verified to stay **inside the
plugin root** before any filesystem stat is performed:

```python
candidate = (plugin_root.resolve() / entry).resolve()
assert candidate.is_relative_to(plugin_root.resolve())
```

This defeats two classes of attack:

- **`../` traversal** — `resolve()` collapses `..` components before the
  `is_relative_to` check.
- **Absolute-path injection** — Python's `Path("/a") / "/b"` silently
  drops `/a`, producing `/b`.  The `resolve()`-then-`is_relative_to`
  check exposes this because `/b` is not inside the plugin root.

A path that fails confinement raises `ConfineError(tool, capability, entry)`
immediately, before any disk access.

---

## `validate=false` escape hatch

Set `validate = false` in `[tool]` to skip all existence and type checks.
The manifest is still parsed and structurally validated (required fields,
key presence, confinement); only the filesystem checks are skipped.

Use this for tools whose plugin directory tree does not yet exist on disk
(e.g. a placeholder manifest committed before Step 2 lands the actual
files).

```toml
[tool]
name = "camp"
validate = false
base = ["skills/worktree"]
```

Once the on-disk tree exists, remove `validate = false` (or set it to
`true`) so the loader confirms every referenced path.

---

## Duplicate capability tables

`tomllib` raises `TOMLDecodeError` on a duplicated `[capabilities.<name>]`
table in the same file.  The loader catches this and re-raises as
`ManifestError` citing the manifest path.  Authors must not rely on
"last-wins" semantics — TOML does not provide them; it is an error.

---

## Full example — `tools/lore/capabilities.toml`

```toml
[tool]
name = "lore"
base = ["skills/_shared", "skills/sync", "skills/ping"]
hooks_json = "hooks/hooks.json"

[capabilities.capture]
description = "Capture decisions, dead-ends, deferrals, radar items, area notes, and intake seeds into the vault."
skills = [
  "skills/decision",
  "skills/dead-end",
  "skills/defer",
  "skills/radar",
  "skills/check-radar",
  "skills/area",
  "skills/seed",
  "skills/brainstorm",
]
agents = []

[capabilities.recall]
description = "Surface, re-justify, and synthesize accumulated vault memory."
skills = ["skills/review", "skills/reflect"]
agents = ["agents/lore-librarian.md"]

[capabilities.sessions]
description = "Session lifecycle — checkpoint mid-session state and finalize on close."
skills = ["skills/checkpoint", "skills/finished"]
agents = []

[capabilities.shared-vaults]
description = "Layered personal + shared vaults (WS-9, not yet built)."
skills = []
agents = []
```
