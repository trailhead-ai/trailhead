# Installing trailhead — a guide for agents

You are an AI agent, and your user pointed you here to set up trailhead for
them. This file is your script. Work through it **with** the user: run the
commands yourself, ask the user where a choice is theirs, and check each step
before you move on. When you finish, the user has trailhead installed into
their harness, the CLIs on their PATH, the lore vaults they want, and
(optionally) Outpost running.

The human-facing overview is [`README.md`](README.md). Everything this guide
does is also a plain CLI or config-file step, so nothing here needs a prompt
you can't answer.

## Ground rules

- **Ask before you change anything outside the trailhead checkout**: cloning
  into a directory, editing a shell profile, creating a GitHub repo, installing
  a system package. Say what you're about to do and where.
- **Never edit tracked files in the checkout.** `trailhead update` refuses to
  upgrade a checkout with uncommitted changes. Your own install configs go in
  `config/<name>.toml`, which git ignores (everything in `config/` except
  `default.toml` is ignored).
- **Never write lore vault files directly.** Records are written only through
  the `lore` CLI. Cloning a vault repo into place, as described below, is
  fine: that's git, not a record write.
- **Don't print or copy secrets.** `gh auth status` is fine; the token isn't.

## 1. Check prerequisites

Run these and report what's missing before you go further:

| Command | Needed for | Minimum |
|---|---|---|
| `python3 --version` | everything | 3.11 |
| `git --version` | everything | any recent |
| `tmux -V` | camp's workspace sessions | any recent |
| `gh auth status` | portage (PRs) and Outpost | logged in |
| `node --version` and `npm --version` | Outpost only | Node 22 |

trailhead itself has no third-party Python dependencies and needs no
`pip install`. If something is missing, tell the user and let them choose how
to install it. Don't install system packages on your own.

## 2. Get the checkout

If you're already reading this from a local clone, use that clone. Otherwise
ask the user where their clone should live (for example `~/code/trailhead`)
and which repository to clone. Use their fork or mirror if they have one;
otherwise:

```sh
git clone https://github.com/trailhead-ai/trailhead.git ~/code/trailhead
```

The checkout **is** the install source. `trailhead update` pulls it forward
later, so it has to stay where it is. Don't clone into a temp directory.

## 3. Ask the user what they want

Settle these before installing:

1. **Harness.** trailhead supports **Claude Code** (`claude_code`) today and
   detects it from a `~/.claude` directory. If the user runs a different
   harness (Codex, OpenCode, …), there's no installer for it yet. Adding one
   means implementing the `Harness` interface in
   [`trailhead/harness/base.py`](trailhead/harness/base.py) and registering it
   in [`trailhead/harness/__init__.py`](trailhead/harness/__init__.py). Offer
   to do that as a separate piece of work.
2. **Plugins.** The default is all of them, and that's the recommended choice:

   | Plugin | What it gives the user |
   |---|---|
   | `lore` | Project memory: decisions, lessons, tasks, and session logs in git-backed vaults |
   | `camp` | Worktree workspaces that span several repos, each with its own tmux session |
   | `craft` | Development rituals: brainstorm, plan, test-first execute, review |
   | `portage` | PR lifecycle: open, update, watch CI, merge |
   | `outpost` | Agent-side skills for the Outpost dashboard (for example, publishing a static site into a vault) |
   | `trailhead` | A session-start notice when the install is behind its source |

   **Caveat on subsets:** `trailhead update` re-wires using
   `config/default.toml`, and installs only ever add. A plugin left out of a
   custom install comes back the next time the user runs `trailhead update`.
   If the user wants a subset anyway, tell them this first.
3. **Vaults.** Do they have team or project vaults to join, or want to start
   one? See step 6.
4. **Outpost.** Do they want the Outpost dashboard daemon? It isn't publicly
   released yet. Only offer it if `gh repo view trailhead-ai/outpost` succeeds
   for them. See step 7.

## 4. Install

For everything, into the detected harness:

```sh
cd ~/code/trailhead
./bin/trailhead install
```

For a subset or a specific harness, use flags
(`--plugin lore --plugin craft`, `--harness claude_code`). For per-plugin
subagent and skill selection, write `config/<name>.toml` and pass
`--config <name>.toml`. The schema and an annotated example are in
[`README.md`](README.md#config-files) and
[`trailhead/install_config.py`](trailhead/install_config.py).

Install also runs `lore init`, which creates the user's personal **default**
vault. A non-zero exit means something wasn't installed. Read the message: a
missing harness is the common case, and `--harness` fixes it.

## 5. Put the CLIs on PATH

Install places `camp`, `lore`, and `portage` in a shim directory. Add one line
to the user's shell profile. **Ask first** and show them the line:

- zsh (`~/.zshrc`) or bash (`~/.bashrc`):
  `eval "$(~/code/trailhead/bin/trailhead shellenv --shell zsh)"` (or `--shell bash`)
- fish (`~/.config/fish/config.fish`):
  `~/code/trailhead/bin/trailhead shellenv --shell fish | source`

Use the real checkout path. Naming the shell explicitly matters: without
`--shell`, shellenv guesses from `$SHELL`, which may not be the shell the
user actually runs.

**Your own shell won't pick this up.** The shell you run commands in doesn't
read the user's profile, so `trailhead`, `lore`, and `camp` aren't on your
PATH yet. For the rest of this guide, start every command with
`eval "$(<checkout>/bin/trailhead shellenv --shell bash)";`, for example:

```sh
eval "$(~/code/trailhead/bin/trailhead shellenv --shell bash)"; lore vault ls
```

`shellenv` defines `camp` and `trailhead` as shell functions, so
`type camp lore portage trailhead` is the right check (`command -v` prints a
bare name for a function). All four should resolve.

## 6. Configure vaults

A **vault** is a git repository of lore records. Every user has exactly one
**default** vault: it's personal, holds their session logs, and was created in
step 4. Beyond that, they can add vaults at four scopes, from most to least
specific: `repo`, `product`, `suite`, `team`. When a record could go to more
than one vault, it goes to the most specific one that accepts its kind.

Check the starting point with `lore vault ls`.

Vaults live under lore's vaults directory: `$LORE_STATE_DIR/vaults` if that's
set, else `$XDG_STATE_HOME/lore/vaults`, else `~/.local/state/lore/vaults`.
Below, `<vaults-dir>` means that path.

### Join an existing vault

Ask the user for the vault's name, its git URL, and its scope. Then:

```sh
git clone <git-url> <vaults-dir>/<name>
lore vault add <name> --scope <team|product|suite|repo>
```

`lore vault add` registers the clone and indexes the records already in it. It
reports the count ("indexed N record(s)"). Add `--shared` only for a vault
whose content the user doesn't trust as their own, such as one written by
people outside their team. Search results from a shared vault are fenced off
as untrusted. `--record <kind>` (repeatable) limits a vault to certain record
kinds.

To check, run `lore vault ls` and `lore search <a word you expect in it>`.

### Start a new vault

```sh
lore vault add <name> --scope <team|product|suite|repo>
```

This creates `<vaults-dir>/<name>` as a new git repository. To share it,
create an empty remote repository (ask the user where, for example
`gh repo create <org>/<name> --private`), then:

```sh
git -C <vaults-dir>/<name> remote add origin <git-url>
lore sync --vault <name>
```

`lore sync` commits and pushes. Teammates then join the vault as described
above.

### Send a workspace's records to a vault

A non-default vault only receives records that name its scope. Records get
that scope from an explicit flag (`lore record create --team <name>`) or, more
usefully, from the camp group the user is working in. Add a binding to that
group's config at `~/.config/camp/groups/<group>.toml`:

```toml
[[lore_scopes]]
scope = "product"
name  = "<vault-name>"
```

Any record captured inside that group's workspaces then goes to the vault
automatically. If the user has no camp group yet,
`camp group <name> --member NAME=PATH [--member NAME=PATH ...]` creates one.
[`tools/camp/README.md`](tools/camp/README.md) covers the rest.

## 7. Set up Outpost (optional)

Outpost is a local daemon and web UI. It watches the user's camp groups
(PRs, CI, workspaces) and serves their vaults as a browsable reader. It needs
Node 22+ and a logged-in `gh`.

**Access:** the daemon's repository is not public yet. Run
`gh repo view trailhead-ai/outpost` first. If it fails, skip this step and tell
the user Outpost isn't available to them yet. The `outpost` plugin installed
in step 4 still works without the daemon.

1. Clone it next to the trailhead checkout, then build it. Clone with `gh`:
   the repository is private, and `gh` handles the credentials.

   ```sh
   gh repo clone trailhead-ai/outpost ~/code/outpost
   cd ~/code/outpost && npm ci && npm run build
   ```

2. Write `config.toml` in Outpost's config directory. That's
   `$OUTPOST_CONFIG_DIR` if set, else `$XDG_CONFIG_HOME/outpost/`, else
   `~/.config/outpost/`. `checkout` must be an absolute path and
   must come **before** any `[table]`:

   ```toml
   checkout = "/Users/<user>/code/outpost"

   [daemon]
   author = "<github-login>"   # `gh api user --jq .login`
   ```

   The `checkout` key is what tells trailhead that Outpost is configured. From
   then on, `trailhead update` fast-forwards, rebuilds, and restarts Outpost
   along with trailhead.

3. Start it. `trailhead outpost enable` registers it with launchd (macOS) or
   systemd (Linux), so it starts at login and restarts after a crash. This
   needs `node`, `git`, and `lore` on the PATH, so prefix it with the
   `shellenv` eval from step 5. For a one-off run
   instead, use `trailhead outpost start`.

4. Check it: `trailhead outpost status` should exit 0. Then
   `trailhead outpost open` opens `http://127.0.0.1:7313`.

## 8. Verify and hand back

Run these checks, and don't report success until they pass:

- `trailhead doctor`: every plugin you installed is reported as installed.
- `trailhead update --check`: reports up to date, plus an `outpost:` line if
  Outpost is configured.
- `lore vault ls`: lists the default vault plus every vault from step 6.

Then tell the user three things:

1. **Restart the shell** so the PATH change takes effect.
2. **Start a new harness session** so the plugins load.
3. **Updating later** means running `trailhead update`, which asks before
   changing anything. With the `trailhead` plugin installed, a new session
   mentions it when an update is available.
