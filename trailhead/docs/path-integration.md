# PATH Integration

`trailhead/pathint.py` manages a trailhead-controlled **shim directory** and a
brew-style **`shellenv`** endpoint. trailhead does NOT edit the user's shell rc.

**camp is the forcing case** — it's the front door run outside any Claude Code
session, so its shim is the load-bearing one when camp is installed.

---

## Shim directory

Location: `state_dir("trailhead")/bin/`

- Created via `ensure_dir(..., mode=0o700)` by `create_shims()`.
- One bash shim per **selected** CLI (`camp`, `lore`). The shim dir's *contents*
  encode the selection: `--no-lore` simply omits the `lore` shim, so a single
  PATH entry always reflects what the user asked for.
- Each shim hardcodes `TRAILHEAD_ROOT` as an absolute literal (S-5) and `exec`s
  the tool's real `bin/<tool>` wrapper:

  ```bash
  #!/usr/bin/env bash
  # trailhead-managed shim for camp
  export TRAILHEAD_ROOT="/abs/path/to/trailhead"
  exec "/abs/path/to/trailhead/tools/camp/plugins/camp/bin/camp" "$@"
  ```

- S-6: shim names are checked against a denylist of system binaries
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
