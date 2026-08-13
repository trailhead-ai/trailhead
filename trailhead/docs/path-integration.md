# PATH Integration

`trailhead/pathint.py` manages a trailhead-controlled **shim directory** and a
brew-style **`shellenv`** endpoint. trailhead does NOT edit the user's shell rc.

**camp is the forcing case** — it's the front door run outside any Claude Code
session, so its shim is the load-bearing one when camp is installed.

---

## Shim directory

Location: `state_dir("trailhead")/bin/`

- Created via `ensure_dir(..., mode=0o700)` by `create_shims()`.
- One bash shim per **selected** plugin CLI (`camp`, `lore`). The shim dir's
  *contents* encode the selection: `--no-lore` simply omits the `lore` shim, so
  a single PATH entry always reflects which plugin CLIs the user asked for.
  This mechanism covers only the plugin CLIs — the management CLI itself
  (`trailhead`) never gets a shim; see "The `trailhead` function" below.
- Each shim hardcodes `TRAILHEAD_ROOT` as an absolute literal and `exec`s
  the tool's real `bin/<tool>` wrapper:

  ```bash
  #!/usr/bin/env bash
  # trailhead-managed shim for camp
  export TRAILHEAD_ROOT="/abs/path/to/trailhead"
  exec "/abs/path/to/trailhead/tools/camp/plugins/camp/bin/camp" "$@"
  ```

- Shim names are checked against a denylist of system binaries
  (`python`, `git`, `sh`, …) before anything is written.

`install` calls `create_shims()` and then tells the user how to put the shim dir
on PATH (below). `uninstall` simply `rmtree`s the shim dir.

---

## `shellenv` (brew-style)

Instead of writing the user's shell rc, trailhead exposes
`trailhead shellenv [--shell fish|zsh|bash]`, which prints export statements —
exactly like `brew shellenv`. The user adds one line to their profile:

```sh
eval "$(/abs/path/to/trailhead/bin/trailhead shellenv)"
```

`shellenv_lines()` emits, for zsh/bash:

```sh
export TRAILHEAD_ROOT="/abs/path/to/trailhead";
export PATH="/…/state/trailhead/bin:$PATH";
```

and for fish:

```fish
set -gx TRAILHEAD_ROOT "/abs/path/to/trailhead";
fish_add_path "/…/state/trailhead/bin";
```

Shell is detected from `$SHELL`'s basename (`--shell` overrides). Because the
profile only references the shim dir (not individual tool paths), re-running
`install` with a different selection updates PATH in place with no profile edit.

---

## The `trailhead` function

`shellenv_lines()` also emits a `trailhead` shell function (fish and POSIX
forms, alongside the `camp()` wrapper) so the management CLI itself is
invokable by bare name — there is no `trailhead` shim in the shim dir, and
`trailhead` never enters the CLI-selection machinery. The function is
**self-refreshing**: every invocation resolves `<repo-root>/bin/trailhead`
fresh from the profile line's own root, at shell-startup time — never from
install-time state. Move or re-clone the checkout, update the one profile
line, and bare-name `trailhead` follows in any new shell with no re-install.

It's emitted unconditionally of which plugin CLIs were selected (including
when every plugin CLI is disabled), and only when `<repo-root>/bin/trailhead`
exists and is executable — a non-editable pip install has no checkout
alongside it, so nothing is emitted for `trailhead` and the rest of the
`shellenv` output stays eval-valid.

**Pip shadowing:** in a shell that has eval'd the shellenv line, the emitted
`trailhead` function shadows any pip-installed `trailhead` console script
earlier on PATH. In a shell that hasn't, the pip console script wins. This
overlap is intentionally left unresolved — see doctor's PATH-shadowing note
below.

### Recovery: deleted or moved checkout

Because the function re-resolves the checkout on every shell startup, deleting
or moving the checkout referenced by the profile's `eval` line breaks shell
startup itself — the `eval "$(<checkout>/bin/trailhead shellenv)"` call fails
loudly (the binary it tries to run no longer exists at that path) rather than
silently running stale code. To recover: remove or update the `eval "$(...)"`
line in the shell profile to point at a checkout that exists (or a fresh
clone), then open a new shell.

---

## `trailhead doctor`

`doctor` reports the bare-name path with a `trailhead` field: a
`which("trailhead")`-style PATH resolution, plus — only when the resolved
path has the `<repo>/bin/trailhead` shape — a checkout-present verdict for
the repo it resolves into. A Python subprocess cannot see shell functions, so
a null-resolved path is reported as such rather than as a failure; the human
copy directs the user to `command -v trailhead` in a live shell to check.
Because a pip-installed `trailhead` can shadow, or be shadowed by, the
shellenv function depending on PATH order, doctor's `trailhead:` line always
carries that shadowing caveat.
