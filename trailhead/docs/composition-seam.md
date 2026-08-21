# Composition Seam

`trailhead/compose.py` — the install-composition seam for trailhead tool packages.

## What the seam does

A `trailhead install` composes a tool's selected capabilities into a harness plugin
path by **directory selection**: chosen capability directories are copied into a
destination plugin dir.  This is NOT sparse-checkout, NOT a generated manifest, NOT
code surgery.

## Architecture: pure planner + separate applier

The seam is split into two functions, mirroring the pure-resolver / `ensure_dir`
split in `paths.py`:

```
compose_plan(manifest, selected, dest) -> Plan   # pure — NO filesystem side-effects
apply_plan(plan) -> None                          # the ONLY function that writes
```

**`compose_plan`** is completely pure.  It resolves source directories and returns
a `Plan` (an ordered list of `CopyOp(src, dest)` objects).  It never stats, creates,
or writes anything.  Dry-run = call `compose_plan` and inspect the `Plan` without
ever calling `apply_plan`.

**`apply_plan`** is the single write boundary.  It executes the ops.

## Always-on set

Every composed plugin automatically includes, regardless of `selected`:

| What | Why |
|---|---|
| `.claude-plugin/` | Plugin identity.  A composed dest is only a structurally valid harness plugin when `.claude-plugin/plugin.json` is present. |
| Every `base` dir | Base capabilities are unconditionally wired on every install. |
| `hooks_json` file (if declared) | Hooks are a whole-tool unit.  The single `hooks/hooks.json` is wired; capability-gating lives inside it via matchers, NOT by selecting hook dirs. |

## Union-of-selected rule

For each name in `selected`, the capability's `skills` entries are added as
**directory** CopyOps, and `agents` entries are added as **file** CopyOps under
`dest/agents/`.  The result is the **union** of the always-on set, all selected
capability skill dirs, and all selected capability agent files.

## De-dup vs collision

Two distinct cases arise when the same destination path appears more than once:

| Case | Meaning | Result |
|---|---|---|
| Same `src` → same `dest` | Benign overlap: a base dir re-listed by a capability, or the same dir in two capabilities. | De-duplicated to a single `CopyOp`.  No error. |
| Different `src` → same `dest` | Genuine collision: two different source trees claim the same destination path. | `CollisionError(dest, src_a, src_b)` raised in the **pure planning phase**, before `apply_plan` writes anything.  Never leave a half-assembled plugin dir. |

## Dual-end path confinement (binding)

Every path is confirmed to stay inside its expected root **before** any stat or write:

- **Source confinement**: every `src` must be `is_relative_to(plugin_root.resolve())`.
  This is checked by the manifest loader at manifest-load time; `compose_plan` re-asserts it.
- **Destination confinement**: every `dest` must be `is_relative_to(dest.resolve())`.
  A manifest entry must never write outside the destination plugin dir.

Both checks use `resolve()` + `is_relative_to()` to defeat `../` traversal and
absolute-path injection.  Confinement is checked BEFORE any stat/copy.

## `symlinks=False` (binding)

`apply_plan(plan)` always copies, via `shutil.copytree(..., symlinks=False)`.
Symlinks inside a source tree are **never** preserved as escaping links —
their target contents are copied instead.  This eliminates the risk of a
symlink inside a tool's plugin dir pointing outside the plugin boundary and
landing in the destination with a live escaping reference.

## The `plugin_root` field

`compose_plan` needs to resolve source paths relative to the tool's plugin root.
The `Manifest` dataclass stores `plugin_root: Path` — the absolute path
to `<manifest_dir>/plugins/<tool_name>/`.  `compose_plan` uses `manifest.plugin_root`
directly; it does not re-parse the manifest or reimplement the confinement helper.

## Structural validity

**Structural validity is proven**:

After `apply_plan(plan)`:

- `dest/.claude-plugin/plugin.json` exists and parses as valid JSON.
- Selected skill dirs (e.g. `dest/skills/decision`) exist and contain content.

This confirms structural validity by inspection.

**What is deferred**: live harness-load validation (confirming Claude Code
actually loads the composed plugin as a working harness extension).  This
requires the installer UX to exist and is deferred until then.

## Multi-tool orchestration: `wire.py` + the harness seam

`compose_plan`/`apply_plan` compose a single tool.  Multi-tool orchestration
lives in `wire.py`, which sequences:

1. For each tool in the selection, call `compose_plan` (pure).
2. Write to a **staging dir** under `state_dir("trailhead")/composed/tmp/`.
3. **Atomic promote**: `shutil.rmtree(live_dest)` then `shutil.move(staging, live_dest)`.
   A mid-compose failure leaves the prior live dest untouched.
4. Delegate the registration tail to the injected `Harness`:
   `harness.generate_manifest`, then `harness.register` (once) and per tool
   `harness.install_tool` / `rewire_tool`.

`wire.py` is harness-agnostic — it knows nothing about `marketplace.json` or the
`claude plugin` CLI.  Everything Claude-Code-specific (the Shape-A
`marketplace.json` writer, the `claude plugin …` CLI calls, and the on-disk
registration markers) lives in `harness/claude_code.py` behind the `Harness`
interface (`harness/base.py`).  The CLI runner is injectable for test
hermeticity — tests stub it and assert on the args; the real `claude plugin` CLI
is never invoked in tests.  `doctor`/`uninstall` likewise read registration state
through the seam (`is_registered` / `installed_tools` / `manifest_name`), never by
re-deriving the marker scheme.

The composed tree is global, but a harness's install state need not be: every
seam method that reads or writes it takes an `env` mapping naming *which*
configuration of the harness is meant.  Claude Code keeps registration and
per-tool install state per config dir — the `claude plugin` CLI writes it into
whichever dir `CLAUDE_CONFIG_DIR` selects — so a machine running two Claude
accounts has two independent install states over one shared composed tree, and
every `claude plugin …` invocation names its target dir explicitly.  A harness
whose state really is global ignores `env`.

## What is NOT here (installer layer)

The following are explicitly handled by the installer layer, not this module:

- Preset → capability-name mapping (`--preset minimal` → `{}`).  See `presets.py`.
- Installer UX (`trailhead install`, `trailhead config` sub-command).  See `cli.py`.
- Multi-tool orchestration and marketplace registration.  See `wire.py` and
  `harness/claude_code.py`.
- Live harness-launch validation (structural validity proven; live load deferred to
  the dogfood checkpoint).

## Summary of named errors

| Error | Raised by | Condition |
|---|---|---|
| `UnknownSubagentError(name, tool)` / `UnknownSkillError(name, tool)` | `compose_plan` | A name in `selected` is not in the manifest. |
| `CollisionError(dest, src_a, src_b)` | `compose_plan` | Two different srcs map to the same dest. |
| `DestConfinementError(dest_root, path)` | `compose_plan` | A dest path escapes the target plugin dir. |
| `ConfineError(tool, context, entry)` | Loader + `compose_plan` | A src path escapes the plugin root. |
