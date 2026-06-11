# PATH Integration

`trailhead/pathint.py` manages a trailhead-controlled shim directory and an
idempotent, marker-delimited block in the user's shell rc file so wired tools'
CLIs work from a plain shell.

**camp is the forcing case** — it's the front door run outside any Claude Code
session, so its shim is the load-bearing one when camp is wired.

---

## Shim directory

Location: `state_dir("trailhead")/bin/`

  - Created via `ensure_dir(..., mode=0o700)`.
  - One bash shim per wired tool that has a CLI (`camp`, `lore`).
  - The shim directory is created **before** writing the shell rc block
    (fish_add_path silently ignores non-existent directories).

### Shim shape

```bash
#!/usr/bin/env bash
# trailhead-managed shim for <tool>
export TRAILHEAD_ROOT="<absolute trailhead repo root — hardcoded at install time>"
exec "<absolute path to tools/<tool>/plugins/<tool>/bin/<tool>>" "$@"
```

**S-5 security invariant:** `TRAILHEAD_ROOT` is an **absolute literal
hardcoded at write time** — it is never propagated from the caller's
environment.  This prevents an attacker who sets `TRAILHEAD_ROOT` before
invoking the shim from redirecting `camp`/`lore` to a hostile `cli/`.

**S-6 denylist:** shim names that match system binaries are refused:
`python`, `python3`, `git`, `ssh`, `curl`, `install`, `update`, `sh`,
`bash`, `fish`, `zsh`.  The real tool names (`camp`, `lore`, `trailhead`)
are safe.

---

## Shell rc block

### Markers

```
# >>> trailhead managed PATH >>>
<shell-specific PATH line>
# <<< trailhead managed PATH <<<
```

The marker text is fixed.  Never edit the contents between the markers
manually — edits will be silently replaced on the next `trailhead install`
or `trailhead config path_integration on`.

### Per-shell rc file and idiom

| Shell | rc file                        | PATH idiom                         |
|-------|--------------------------------|------------------------------------|
| fish  | `~/.config/fish/config.fish`   | `fish_add_path --path "<shim_dir>"` |
| zsh   | `~/.zshrc`                     | `export PATH="<shim_dir>:$PATH"`   |
| bash  | `~/.bashrc`                    | `export PATH="<shim_dir>:$PATH"`   |

**fish note (A-11):** `fish_add_path --path` is used (not `set -gx PATH`).
`fish_add_path` is idempotent — it deduplicates on multi-source.

### Shell detection

Shell is detected via `$SHELL` basename.  When `$SHELL` disagrees with the
interactive shell (e.g. login shell is zsh but `$SHELL=/bin/bash`), use the
`--shell` override:

```
trailhead install --shell fish
trailhead config path_integration on --shell zsh
```

---

## Inject algorithm

1. Read the rc file (empty string if absent).
2. **R-4 corrupt-marker repair:** if the open marker is present but the
   close marker is absent (interrupted prior run), strip from the open marker
   to end-of-file, then append the new block.
3. **Idempotent update:** if both markers are present, regex-replace the
   block (`re.DOTALL`).
4. **First inject:** if neither marker is present, append the block.

Then `mkdir -p` the rc parent and write.

## Remove algorithm

Regex-strip the marker block (`re.DOTALL`), leaving the rest of the file
byte-identical.  No-op if the block is absent or the file doesn't exist.

---

## Edge cases (R-7)

- **Missing rc:** created (with `mkdir -p` for the parent).
- **Symlinked rc that resolves inside `~`:** written through (transparent).
- **Symlinked rc that resolves outside `~`:** refused with a named
  `SymlinkRefusalError` message citing the resolved path.  Rationale: a
  symlink pointing outside the home directory is unusual and could indicate
  a misconfigured environment.

---

## Non-TTY / non-interactive (A-8)

When `is_tty=False` (CI, cron, scripted install):

- The shim directory and shims **are still created**.
- The rc write is **skipped**.
- The following message is returned/printed:

  ```
  PATH integration skipped (non-interactive) — run `trailhead config path_integration on` in your shell to enable
  ```

---

## Failure handling

An unwritable rc file raises `PathIntegrationError`:

```
could not write PATH block to <rc>; add <shim-dir> to your PATH manually
```

The caller surfaces this as a nonzero exit.

---

## Removing PATH integration

```
trailhead config path_integration off
```

This calls `remove_path_integration()` which strips the marker block,
leaving the rest of the rc byte-identical.

---

## Python version requirement

`trailhead/paths.py` uses `X | Y` union type annotations, which require
**Python ≥ 3.10**.  macOS ships `/usr/bin/python3` at 3.9.6 — a shim
invoked from a minimal environment (CI, cron, `env -i`) may resolve to that
version and fail with a cryptic `SyntaxError`.

`trailhead doctor` checks that `python3 ≥ 3.10` is on the shim's PATH.
Ensure `asdf` (or Homebrew Python) is on PATH before the shim is invoked
from a non-login shell.

---

## Public API (for Slices 4 and 5)

```python
from trailhead.pathint import (
    install_path_integration,   # create shims + inject rc block
    remove_path_integration,    # strip rc block (config path_integration off)
    resolve_shim_dir,           # pure: return shim_dir Path without creating it
    create_shims,               # create shim_dir + write shims
    inject_path_block,          # inject/replace marker block in a single rc
    remove_path_block,          # strip marker block from a single rc
    PathIntegrationResult,      # return type of install_path_integration
    PathIntegrationError,       # unwritable rc
    ShimDenylistError,          # S-6 denylist violation
    SymlinkRefusalError,        # R-7 symlink outside home
)
```

`install_path_integration` returns a `PathIntegrationResult` with:

- `shim_dir: Path` — the created shim directory (always set).
- `rc_path: Path | None` — the rc file that was written (`None` on non-TTY skip).
- `skip_message: str | None` — the A-8 skip message when `rc_path` is `None`.

The install summary (A-3) should include:

```
PATH: added a shim dir to <rc_path> — remove with `trailhead config path_integration off`.
```
