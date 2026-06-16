# Trailhead Vision & Design Axioms

These are the durable principles that govern how trailhead is built. They exist so
that the seams a new feature needs — harness customization points, vanilla-usage
fallbacks, basedir-correct paths — don't have to be re-explained every time. When
you design or implement anything in this repo, treat the axioms below as
constraints, not suggestions. If a change appears to violate one, stop and
reconcile it (or flag it) before proceeding.

> This document is harness-neutral on purpose (see Axiom 1). Each harness's
> agent-entrypoint file imports it — e.g. `CLAUDE.md` for Claude Code — so the
> axioms load into context regardless of which harness an agent is driving.

---

## 1. Trailhead is harness-agnostic

This is the guiding axiom, encoded into trailhead's DNA: **you should never need a
specific harness (Claude Code, Codex, Cursor, …) to take advantage of trailhead.**

- **Never implement a feature that is specific to one harness.** Harness-specific
  behavior lives behind a seam, never in the shared core.
- The seam today is the `Harness` interface in
  [`trailhead/harness/base.py`](../trailhead/harness/base.py). `install` /
  `uninstall` are harness-agnostic: they compose generic plugin trees
  ([`compose.py`](../trailhead/compose.py) / [`wire.py`](../trailhead/wire.py)) and
  delegate only the registration tail to a concrete `Harness`.
- Adding a new harness = implementing that interface and registering it in
  [`trailhead/harness/__init__.py`](../trailhead/harness/__init__.py). It must
  require **zero** changes to the shared install/compose/wire path.

**Litmus test:** if your change names a harness anywhere outside
`trailhead/harness/<that_harness>.py`, it's probably in the wrong place.

## 2. Trailhead takes full advantage of the harness

Harness-agnostic is not harness-blind. When a harness offers a real capability
(hooks, slash commands, a memory file like `CLAUDE.md` vs `.cursorrules`), **use
it fully** — by widening the seam, not by special-casing the core.

- If a harness can do something valuable that the interface doesn't yet express,
  the right move is to **add a capability/method to the `Harness` seam** (with a
  sensible default for harnesses that lack it), then implement it per-harness.
- Capability differences are modeled as data
  ([`capabilities.py`](../trailhead/capabilities.py),
  [`capability-manifest.md`](./capability-manifest.md)), so the core can ask "does
  this harness support X?" rather than branching on a harness name.

## 3. Design for full adoption, but support vanilla usage

Assume a trailhead user runs **all** the plugins (e.g. they create workspaces with
`camp` for their camp groups) — design the happy path for that. **But** where a
plugin can reasonably stand alone, support "vanilla" usage too.

- `camp` and `lore` ship standalone CLIs precisely so they work outside a full
  trailhead install.
- When you add a feature, ask both questions: *what does it look like with the full
  suite present?* and *what's the graceful fallback when the sibling plugin is
  absent?* The answer varies by plugin and feature — make it a deliberate design
  step, not an afterthought.

## 4. Trailhead fully adopts the basedir specification

We honor the [freedesktop basedir spec](https://specifications.freedesktop.org/basedir/latest/)
on **every** platform, **including macOS** — config under `~/.config`, state under
`~/.local/state`, cache under `~/.cache` (XDG vars override when set). This is a
deliberate departure from the macOS-native `~/Library/Application Support` layout.

- All path resolution goes through [`trailhead/paths.py`](../trailhead/paths.py).
  Never hand-roll a `~/Library/...` or `~/.config/...` path elsewhere — call
  `config_dir` / `state_dir` / `cache_dir`.
- Existing macOS installs are not orphaned: the resolver falls back to the legacy
  `~/Library` location read-only, *iff the XDG path doesn't exist yet and the
  legacy one does*. New installs always land in the XDG location. See
  [`docs/paths.md`](./paths.md) for the full contract.

## 5. Agents are the primary developers of trailhead

This codebase is acted on almost exclusively by agents, so **optimize the code for
agent comprehension** — while keeping it navigable for humans.

- Favor explicit names, small focused modules, and docstrings/comments that state
  intent and contracts (not just mechanics). The existing `trailhead/docs/*.md`
  seam docs are the model: each major seam has a prose contract next to the code.
- When you add a seam or a non-obvious invariant, document it where an agent will
  encounter it: a module docstring, a comment at the decision point, and — for
  cross-cutting concerns — a `docs/` note linked from here.

## 6. Trailhead is built using trailhead

We dogfood: trailhead develops trailhead, typically inside a `camp` workspace.

- **Never corrupt the live installation.** Tests and experiments must not write to
  the real lore vault, camp groups, or any real config/state/cache dir. Use
  injected `env` / `platform` (see `paths.py`'s testable resolver signatures) and
  `tmp_path`; route everything through per-app override env vars
  (`<APP>_CONFIG_DIR`, etc.) when a test needs a real-looking path.
- Before running anything destructive, confirm it targets a throwaway location,
  not the developer's own `~/.config/{camp,lore}` (or legacy `~/Library`) data.
