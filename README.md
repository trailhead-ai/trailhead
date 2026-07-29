# trailhead

An AI-native installer for a suite of agent-plugins — **lore** (project memory),
**camp** (worktree orchestration), **craft** (dev rituals), and **portage** (PR
lifecycle) — into your AI code harness.

trailhead is designed to be driven by an agent. There is no package to download:
you clone the repo and run one command (or hand the repo to your agent and point
it at this README). Everything is CLI- and config-driven, so an agent can install
exactly the pieces you want, into exactly the harness you use.

---

## Quick start (humans)

Clone the repo and run the installer. It auto-detects your harness (e.g. a
`~/.claude` directory → Claude Code), installs every agent-plugin, and puts the
`camp`, `lore`, and `portage` CLIs on your PATH — no prompts:

```sh
git clone <this-repo> trailhead
cd trailhead
./bin/trailhead install
```

`bin/trailhead` runs straight from the checkout — no `pip install` needed
(Python ≥ 3.11, zero third-party dependencies).

After install, **restart your shell** (so `camp`/`lore`/`portage` resolve) and **start a
fresh Claude Code session** (so the plugins load).

If trailhead can't find any harness, it still installs the CLIs, warns you, and
exits non-zero — re-run with `--harness <name>` to install the plugins.

---

## Quick start (agents)

If you're an agent setting trailhead up for a user: clone the repo, then run
`./bin/trailhead install` with the flags below. Everything you need is here — no
hidden interactive steps.

- **Install everything into the detected harness:** `./bin/trailhead install`
- **Target a specific harness:** `./bin/trailhead install --harness claude_code`
  (repeatable; canonical names come from `trailhead/harness/`).
- **Install a subset of plugins:** `./bin/trailhead install --plugin lore --plugin craft`
- **Fine-grained control** (which subagents/skills, local overrides, multiple
  harnesses): write a config file and pass `--config` (see *Config files* below).
- **Add support for a new harness** (Codex, OpenCode, …): implement the
  `Harness` interface in [`trailhead/harness/base.py`](trailhead/harness/base.py)
  and register it in [`trailhead/harness/__init__.py`](trailhead/harness/__init__.py).
  `install`/`uninstall` are harness-agnostic; they compose generic plugin trees
  and delegate the harness-specific registration to your implementation.

---

## CLI

```sh
trailhead install      # install plugins into your harness(es) + the camp/lore/portage CLIs
trailhead uninstall    # remove the ENTIRE install (all plugins + CLIs); keeps your data
trailhead doctor       # read-only report of what's installed
```

### `install` flags

| Flag | Meaning |
|---|---|
| `--harness <name>` | Target a harness explicitly (repeatable). Default: auto-detect. |
| `--plugin <name>` | Install only these agent-plugins (repeatable). Default: all. |
| `--no-camp` / `--no-lore` / `--no-portage` | Skip installing/updating that CLI onto PATH. |
| `--config <path>` | Drive the install from a TOML config (absolute, or relative to the repo `config/` dir). Default: `config/default.toml`. |
| `--quiet` / `--json` | Suppress progress / emit a machine-readable summary. |

CLI flags are **runtime overrides** of the config file. `--plugin` replaces the
resolved plugin set; `--harness` overrides detection.

**Upgrades are additive.** Re-running `install` only adds — removing something
from your config never removes it from the install. To remove pieces, run
`uninstall` and re-install with a narrower config.

**`uninstall` is all-or-nothing.** It removes every plugin from every harness and
all three CLIs. Your data is kept (the lore vault, camp groups, and each plugin's
harness data dir survive a later re-install).

---

## Config files

The `install` step is driven under the covers by a resolved config.
The shipped default, [`config/default.toml`](config/default.toml), installs
everything. Drop your own `*.toml` files in `config/` — everything there except
`default.toml` is gitignored, so your configs never create noise in a checkout.

A config can pick harnesses, plugins, and — going further than the CLI — the
exact subagents/skills per plugin, plus local file overrides:

```toml
install_camp_cli = true
install_lore_cli = false
install_portage_cli = true
install_ranger_cli = true

# Top-level default plugin set, applied to every detected/--harness harness.
plugins = ["camp", "lore", "craft", "portage", "outpost", "ranger"]

[[harness]]
name = "claude_code"
plugins = ["camp", "lore", "craft", "portage", "outpost", "ranger"]

[[harness]]
name = "codex"

    # Map form: pick specific subagents/skills (omit a key to mean "all").
    [[harness.plugins]]
    name = "craft"
    subagents = ["advocate", "artist"]
    skills = ["execute"]

    # Override form: point a subagent/skill at your own file or directory.
    [[harness.plugins]]
    name = "portage"
        [[harness.plugins.subagents]]
        name = "updater"
        file_path = "/path/to/custom/updater.md"
        [[harness.plugins.skills]]
        name = "pull_request"
        file_path = "/path/to/custom/pull_request"
```

A plugin written as a bare string (`plugins = ["camp"]`) expands to **all** of its
subagents and skills. The full schema and resolution rules live in
[`trailhead/install_config.py`](trailhead/install_config.py).

---

## What's included

| Tool | What it covers |
|---|---|
| `lore` | Agent-native project memory — capture, recall, sessions |
| `camp` | Worktree + group orchestration across repos |
| `craft` | Dev rituals — planning, TDD execute, review, council |
| `portage` | PR lifecycle — open, update, watch CI, merge |

`camp` and `lore` also ship standalone CLIs (`camp`, `lore`) that trailhead puts
on your PATH.

## Contributing

If you (or your agent) are contributing, read [`docs/vision.md`](docs/vision.md) first.

## Tool READMEs

- [lore](tools/lore/README.md) — skills, recall mechanics, vault layout
- [craft](tools/craft/README.md) — agents and skills
- [camp](tools/camp/README.md) — worktree commands, group config
- [portage](tools/portage/README.md) — PR lifecycle agents and skills

## License

Apache-2.0. See [LICENSE](LICENSE).
