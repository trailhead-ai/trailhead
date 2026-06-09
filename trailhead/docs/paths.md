# Path Resolution Contract

`trailhead/paths.py` provides three pure resolver functions and one creator helper
for locating per-OS config, state, and cache directories.

## Core contract

**Resolvers are pure.** `config_dir`, `state_dir`, and `cache_dir` return a
`pathlib.Path` and never create anything on disk. Call `ensure_dir(path)` to
materialize a directory.

**`ensure_dir` is the only creator.** It calls `mkdir(parents=True, exist_ok=True)`,
sets the requested mode (default `0o700`), and returns the path. All callers that
need the directory to exist must go through `ensure_dir`.


## Pinned paths per OS

### Linux (`sys.platform` starts with `"linux"`)

| Semantic | Resolution |
|----------|------------|
| config   | `$XDG_CONFIG_HOME/<app>` → `~/.config/<app>` |
| state    | `$XDG_STATE_HOME/<app>` → `~/.local/state/<app>` |
| cache    | `$XDG_CACHE_HOME/<app>` → `~/.cache/<app>` |

### macOS (`sys.platform == "darwin"`)

| Semantic | Resolution |
|----------|------------|
| config   | `$XDG_CONFIG_HOME/<app>` → `~/Library/Application Support/<app>` |
| **state** | `$XDG_STATE_HOME/<app>` → **`~/Library/Application Support/<app>`** |
| cache    | `$XDG_CACHE_HOME/<app>` → `~/Library/Caches/<app>` |

**Pinned macOS state path:** state is `~/Library/Application Support/<app>`, not a
separate directory. This mirrors the [`platformdirs`](https://github.com/tox-dev/platformdirs)
convention (`user_state_path` → Application Support on macOS). A future swap from
this custom resolver to `platformdirs` would be mechanical because the paths match.

XDG variables are honored on macOS when the user has explicitly set them (same
variables as Linux). An empty XDG variable falls through to the macOS default above.

### Windows (`sys.platform == "win32"`)

| Semantic | Resolution |
|----------|------------|
| config   | `%APPDATA%/<app>` |
| state    | `%LOCALAPPDATA%/<app>` |
| cache    | `%LOCALAPPDATA%/<app>` |

An unset `%APPDATA%` or `%LOCALAPPDATA%` raises `PathResolutionError` immediately.
No `None`-joined paths are ever constructed.


## Per-app environment override

Each function checks a per-app override variable before consulting the OS default.
The convention is: `{UPPER_APP}_{SEMANTIC}_DIR`.

| Call | Override variable |
|------|-------------------|
| `config_dir("camp")` | `CAMP_CONFIG_DIR` |
| `state_dir("camp")` | `CAMP_STATE_DIR` |
| `cache_dir("camp")` | `CAMP_CACHE_DIR` |

The override variable wins over any XDG or OS-native path, for that app only.
Other apps are unaffected.


## Override validation rules

These rules apply to both XDG variables (`XDG_CONFIG_HOME`, etc.) and per-app
override variables (`CAMP_STATE_DIR`, etc.):

| Value | Behaviour |
|-------|-----------|
| Empty string (`""`) | Ignored — falls through to OS default |
| Absolute path | Used as-is |
| Relative path | `PathResolutionError` — never silently build a cwd-relative path |

The empty-string-is-ignored rule lets callers safely set `XDG_CONFIG_HOME=` to
mean "use the default" without triggering an error.


## app argument rules

The `app` argument must be a plain name:

- No `/` (forward slash)
- No `\` (backslash)
- No `..` component
- No `os.sep` equivalent

Violations raise `PathResolutionError`. This prevents traversal attacks via a
crafted app name.


## Unset HOME

On Linux and macOS, if `HOME` is not set and no XDG override is present,
the resolvers raise `PathResolutionError` with a message naming `HOME`. The raw
`RuntimeError` from `Path.expanduser()` is never allowed to leak.


## Named error

All error conditions raise `PathResolutionError(Exception)` with a message that
names the specific cause: the OS, the missing variable, or the bad input. Never
raises `ValueError`, `KeyError`, or bare `RuntimeError`.


## Testability

All three resolver functions accept keyword arguments `platform` and `env` for
injection:

```python
config_dir("myapp", platform="linux", env={"HOME": "/tmp/fake"})
```

This allows all three OS branches to be exercised on a single dev machine without
mocking `os.environ` or `sys.platform` globally.


## camp consumer paths (D6)

On a standard Linux machine, camp's paths resolve to:

| Semantic | Path |
|----------|------|
| config   | `~/.config/camp` |
| state    | `~/.local/state/camp` |
| cache    | `~/.cache/camp` |

These match the XDG specification defaults. The `CAMP_CONFIG_DIR`, `CAMP_STATE_DIR`,
and `CAMP_CACHE_DIR` overrides let existing `DEV_ENV_STATE_DIR`-style patterns
migrate forward without breakage.
