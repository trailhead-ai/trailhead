# Installing trailhead — a guide for agents

You are an AI agent, and your user pointed you here to set up trailhead for
them. This file is your script. Work through it **with** the user: run the
commands yourself, ask the user where a choice is theirs, and check each step
before you move on. When you finish, this computer is a **prepared host**:
trailhead is installed into the harness with the CLIs on the PATH, a forge
credential is in place, Outpost is built and kept running, the lore vaults are
here and synced, vault commits sign without a passphrase, and the computer
knows whether it makes vault content.

The procedure is eight numbered steps. Do them in order. **Every step is
idempotent**: each one says how to tell it is already done, and if it is, skip
it. Step 8 checks all the others and is the only thing that decides whether the
host is prepared.

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

## 1. Install trailhead

Install trailhead from a checkout, into the harness, and put its CLIs on the
PATH. Already done when `type camp lore portage trailhead` (after the
`shellenv` line below) resolves all four and `trailhead doctor` lists the
plugins you want as installed. Then skip to step 2.

**Prerequisites.** Run these and report what's missing before you go further:

| Command | Needed for | Minimum |
|---|---|---|
| `python3 --version` | everything | 3.11 |
| `git --version` | everything | any recent |
| `git config --global user.name` and `user.email` | vault commits (lore commits as this identity) | both set |
| `tmux -V` | camp's workspace sessions | any recent |
| `gh auth status` | portage (PRs), Outpost, and vault remotes (step 2) | logged in |
| `node --version` and `npm --version` | Outpost (step 3) | Node 22 |

trailhead itself has no third-party Python dependencies and needs no
`pip install`. If something is missing, tell the user and let them choose how
to install it. Don't install system packages on your own. A missing git
identity is theirs to supply: ask for the name and email, then run
`git config --global user.name "<name>"` and
`git config --global user.email "<email>"`.

**The checkout.** If you're already reading this from a local clone, use that
clone. Otherwise ask the user where their clone should live (for example
`~/code/trailhead`) and which repository to clone. Use their fork or mirror if
they have one; otherwise:

```sh
git clone https://github.com/trailhead-ai/trailhead.git ~/code/trailhead
```

The checkout **is** the install source. `trailhead update` pulls it forward
later, so it has to stay where it is. Don't clone into a temp directory.

**What they want.** Settle these before installing:

1. **Harness.** trailhead supports **Claude Code** (`claude_code`) today and
   detects it from a `~/.claude` directory. If the user runs a different
   harness (Codex, OpenCode, ...), there's no installer for it yet. Adding one
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

**Install.** For everything, into the detected harness:

```sh
cd ~/code/trailhead
./bin/trailhead install
```

For a subset or a specific harness, use flags
(`--plugin lore --plugin craft`, `--harness claude_code`). For per-plugin
subagent and skill selection, write `config/<name>.toml` and pass
`--config <name>.toml`. Your own configs live in `config/<name>.toml`, which
git ignores. The schema and an annotated example are in
[`README.md`](README.md#config-files) and
[`trailhead/install_config.py`](trailhead/install_config.py).

Install also runs `lore init`, which creates the user's personal **default**
vault. A non-zero exit means something wasn't installed. Read the message: a
missing harness is the common case, and `--harness` fixes it.

**Put the CLIs on PATH.** Install places `camp`, `lore`, and `portage` in a
shim directory. Add one line to the user's shell profile. **Ask first** and
show them the line:

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

## 2. Give the host a forge credential for the vault remotes

Lore vaults sync to a git remote (usually on GitHub), and Outpost's setup
clones its repository with `gh`. Both need a credential that works without
anyone at the keyboard.

Already done when `gh auth status` reports a logged-in account. Then skip to
step 3.

Otherwise ask the user to log in, and let them do it: the login is
interactive, and you must never print or copy the token.

```sh
gh auth login && gh auth setup-git
```

`gh auth setup-git` makes plain `git` use the same credential over https. A
vault whose remote is an ssh URL uses the user's ssh key instead; that key must
load without a passphrase prompt (an agent, or a key with none).

Step 8 probes each vault remote and reports `forge:<vault>` when it is refused.
The probe runs from your shell, so it does not prove the background service
can push.

## 3. Set up Outpost

Outpost is a local daemon and web UI. It watches the user's camp groups (PRs,
CI, workspaces) and serves their vaults as a browsable reader. It is
**required**: a computer that runs no Outpost is not a host. It needs Node 22+
and the `gh` login from step 2.

**Access:** the daemon's repository is private. Run
`gh repo view trailhead-ai/outpost` first. If it fails, stop and tell the user
Outpost isn't available to them yet; the host cannot be reported prepared
without it.

Already done when `trailhead doctor` reports `[ok] outpost`. Then skip to
step 4. Otherwise doctor's `outpost` line says what is wrong and prints the fix; each
fix is safe to re-run, and you can run it as printed. Re-run doctor after each
fix, since one fix can move Outpost on to the next state (a fresh clone is
then reported as not built). The states:

1. **No config.** There is no `config.toml` in Outpost's config directory
   (`$OUTPOST_CONFIG_DIR` if set, else `$XDG_CONFIG_HOME/outpost/`, else
   `~/.config/outpost/`). Fix: clone the checkout (unless it exists), then
   write a config naming it. Clone with `gh`: the repository is private, and
   `gh` handles the credentials.

   ```sh
   gh repo clone trailhead-ai/outpost ~/code/outpost
   ```

   ```toml
   checkout = "/home/<user>/code/outpost"

   [daemon]
   author = "<github-login>"   # `gh api user --jq .login`
   ```

2. **Config without a top-level `checkout`.** The file exists but names no
   checkout, so trailhead has nothing to manage. Fix: put a
   `checkout = "<absolute path>"` line at the very top of the file. It must
   come **before** any `[table]`, or TOML reads it as part of that table.
   Keep the rest of the file.
3. **Configured checkout absent.** `checkout` names a directory that is not
   there. Fix: `gh repo clone trailhead-ai/outpost <that path>`.
4. **Not built.** The checkout is there but has no `dist/server/index.js`.
   Fix: `cd <checkout> && npm ci && npm run build`.
5. **Malformed config.** The file is not valid TOML, `checkout` is not an
   absolute path, or it names something that is not a directory. Fix: open it
   (`${EDITOR:-vi} <config path>`) and correct it.

Doctor reports `[could-not-check] outpost`, with no fix, when it cannot look
at the config at all: the config directory cannot be resolved, the config path
exists but is not a regular file, or the file cannot be read. Fix the named
path by hand, then re-run doctor.

The `checkout` key is what tells trailhead that Outpost is configured. From
then on, `trailhead update` fast-forwards, rebuilds, and restarts Outpost
along with trailhead. Starting it is step 7.

## 4. Have the host's vault copies

A **vault** is a git repository of lore records. Every user has exactly one
**default** vault: it's personal, holds their session logs, and step 1
created it. Beyond that, they can add vaults at four scopes, from most to
least specific: `repo`, `product`, `suite`, `team`. When a record could go to
more than one vault, it goes to the most specific one that accepts its kind.

Already done when `trailhead doctor` reports every `vault:<name>` as `[ok]`. Then skip to
step 5. If a vault is not ok, run `lore status`: it prints the remedy for each
vault (a missing clone, a copy that is not a git repository, no remote, or
unsynced records).

Check the starting point with `lore vault ls`. Every vault needs a remote so
its records are backed up off this disk, and that includes the default vault.
If the default vault has none, create an empty remote (ask the user where, for
example `gh repo create <org>/<name> --private`), then:

```sh
git -C <vaults-dir>/default remote add origin <git-url>
lore sync --vault default
```

Vaults live under lore's vaults directory: `$LORE_STATE_DIR/vaults` if that's
set, else `$XDG_STATE_HOME/lore/vaults`, else `~/.local/state/lore/vaults`.
Below, `<vaults-dir>` means that path. Ask the user which of the following they
want.

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

## 5. Let the host sign vault commits without a passphrase

A host with nobody watching (the sweep, an automatic publish, a session flush
after a reboot) cannot answer a passphrase prompt, and a vault commit that waits
for one hangs.

Already done when `lore signing status` exits 0 ("this host signs
unattended"). Then skip to step 6. Otherwise:

```sh
lore signing enable
```

It generates a new ed25519 key with **no passphrase** under lore's state
directory and uses it for every vault commit on this host. To adopt an existing
key instead, use `lore signing enable --key <path>`; that key must have no
passphrase.

**The trade-off, tell the user:** a key with no passphrase signs for anyone who
can read the file. Its protection is the file mode (`0600`, owner only) and the
account it lives in, and lore checks that mode. That is the price of signing
with nobody present. The key is separate from the user's own signing key, so
the user's personal key never sits unprotected. To get a Verified badge on
GitHub, `enable` prints the `gh ssh-key add ... --type signing` command; it does
not run it, so run it only if the user wants that.

## 6. Ask whether this host makes vault content

Ask the user, in these words or close to them: **"Do you want to be able to
contribute content to the vault(s)?"**

- Yes: record `yes`.
- No: record `no`.
- **Never answer for the person.** If they are unsure, the answer is `yes`.
  A computer that makes content never discards local work, so `yes` is the
  safe default.

Already done when `lore status --json` reports the `author` item as `ok`, or
`trailhead doctor` shows `[ok] author`. Skip if so and the answer is still
right. Otherwise record it:

```sh
lore vault config --makes-vault-content yes
```

Use `no` for a reader. Running the command again with the other answer changes
it later.

## 7. Keep Outpost running

Register Outpost with the host's own supervisor (launchd on macOS, systemd on
Linux), so it starts at login and restarts after a crash. Step 3 must be
`[ok]` first.

Already done when `trailhead doctor` reports `[ok] supervisor`. Then skip to
step 8. Otherwise:

```sh
trailhead outpost enable
```

It needs `node`, `git`, and `lore` on the PATH, so prefix it with the
`shellenv` eval from step 1. For a one-off run instead, use
`trailhead outpost start`. To check: `trailhead outpost status` should exit 0,
and `trailhead outpost open` opens `http://127.0.0.1:7313`.

## 8. Run `trailhead doctor`, and report only what it says

```sh
trailhead doctor
```

Doctor ends with one verdict line. Read it, and nothing else, to decide.

- **`HOST READY`**: the host is prepared. Only now may you tell the user so.
- **`HOST NOT READY: <n> missing, <m> could not be checked`**: not prepared.
  Every `[missing]` item prints a `fix:` line, then a line naming the INSTALL.md
  step it belongs to. Each fix is a single line that runs as
  printed in sh, bash, zsh or fish. Run each fix (or follow that step here),
  then run `trailhead doctor` again. Repeat until the verdict is `HOST READY`.
- **`[could-not-check]`** items count against the verdict, because doctor could
  not confirm them, usually a remote it could not reach. Retry when the machine
  is online. If it still cannot be checked, report the item to the operator and
  say the host is not yet prepared; do not call it ready.

Doctor always exits 0, so read the verdict line, not the exit code.

**Not covered yet.** Joining a multi-host cluster, meaning mesh membership and
the coordinator's registry entry, are steps that arrive with the multi-host
work. Doctor does not check them and neither does this guide.

**Hosts that were set up before this procedure.** Bring an existing host
current by running step 6 (the author answer) and step 8 (doctor). `trailhead
update` now ends by printing the verdict line, so an upgraded host that is not
ready says so without anyone running doctor.

Then tell the user three things:

1. **Restart the shell** so the PATH change takes effect.
2. **Start a new harness session** so the plugins load.
3. **Updating later** means running `trailhead update`, which asks before
   changing anything. With the `trailhead` plugin installed, a new session
   mentions it when an update is available.
