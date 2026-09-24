# Changelog

All notable, user-visible changes to trailhead are documented here, in the
format described by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

- `trailhead update` now upgrades your Outpost too, when
  `~/.config/outpost/config.toml` names a `checkout`. After the trailhead
  upgrade it fast-forwards that checkout, reinstalls its dependencies
  (`npm ci`), and rebuilds it, restarting the daemon if it is running. A dirty
  or branchless Outpost checkout refuses the whole upgrade before anything
  moves; an Outpost build or restart that fails rolls Outpost alone back to its
  previous commit and build, keeps the trailhead upgrade, and exits nonzero.
  `trailhead update --check` and the session-start notice also report how far
  the Outpost checkout is behind its tracked branch (`--json` schema version 4
  adds an `outpost` field).
- A new [`INSTALL.md`](INSTALL.md) is written for an agent to follow with you:
  point your agent at it and it walks you through choosing a harness and
  plugins, installing, joining or creating lore vaults, and setting up Outpost.

- **Breaking:** camp no longer takes over tmux's prefix+`c`. Creating a
  workspace session installs no key binding, so prefix+`c` opens an ordinary
  shell window everywhere, and `camp window unbind` is gone with the binding it
  removed. Start a conversation by running `claude` in any pane of the
  workspace session: the camp plugin now ships a SessionStart hook that records
  each conversation against the tmux window it started in (one per window — a
  resume or `/clear` replaces the window's earlier one), so reaching the
  workspace through the door after its session has died still brings every
  recorded conversation's window back. A tmux server still carrying the old
  binding keeps it until the server restarts, or until
  `tmux bind-key -T prefix c new-window` puts the stock binding back.
- Every pane in a workspace's tmux session now starts on the group's declared
  `[launch] account` with the harness's parent-session variables removed —
  the first pane and any pane opened by hand, not only the window prefix+`c`
  used to compose. camp states the scrub and the account on the session itself
  when it creates (or resurrects) it, overriding whatever the tmux server's
  own environment carries, such as a server started from inside an agent
  session. A group whose declared account camp cannot bind now refuses to
  create the session instead of opening it on the wrong account.

- **Breaking:** A record create, a record update, or a default `lore flush` no
  longer waits on a commit-and-push round trip — each schedules its own
  debounced, single-flight `lore publish --vault NAME` for the vault it wrote,
  in the background, and returns immediately. `lore flush --wait` keeps the
  previous default: the full commit → pull → push flow, run in-process, before
  flush returns. `lore flush --no-sync` is unchanged (no git action of any
  kind). A vault with `auto_publish: false` in `config.json` is opted out of
  the write-triggered publish entirely; it still requires an explicit
  `lore sync` (or `lore flush --wait`) to converge. A `shared: true` vault now
  publishes automatically by default too, unless it sets `auto_publish: false`
  itself — the write-triggered path no longer exempts shared vaults the way
  `lore flush`'s old sync tail did. If the last automatic publish for a vault
  did not succeed, the next `lore record create`/`update` into it prints one
  stderr line naming the vault and `lore sync` as the remedy.
- A resolver failure reported by `lore sync` is now worded plainly and carries
  no version-control vocabulary: the messages that embedded git's own stderr,
  or named a rebase, now say what could not be done in terms of the vault. The
  text is the same on every host, because these failures are raised before the
  host's `makes_vault_content` declaration is consulted and so cannot be worded
  per host — which is what a host declaring it authors nothing requires. Git's
  account of the failure is not lost: it is written to the failed-vault marker
  under `state_dir("lore")/resolve`, where anyone debugging the host reads it.
  Wording only — no outcome, exit code, or `--json` field changes. The
  interactive `lore resolve take` / `take-file` verbs are unchanged: a person
  who typed one is in a version-control workflow and git's detail is useful to
  them.

- **Breaking:** `lore sync` no longer aborts a rebase conflict and reports a
  `lore resolve` remedy with a non-zero exit at either replay site (the pull's
  rebase, the push's moved-history replay). It now hands the conflict to the
  resolver: a conflict every side moved a disjoint field on settles field-wise
  and publishes automatically (`published`, exit 0, no remedy printed — a
  script or cron wrapper keyed on the old non-zero exit for this case now sees
  zero). A conflict that genuinely needs a person's judgment reports the new
  `awaiting-person` outcome instead of `holding` (still exit non-zero) and
  leaves the vault clean and diverged, marked held, for `lore resolve <vault>`
  to settle by hand. `SYNC_OUTCOMES` and the `--json` schema gain the
  `awaiting-person` literal; nothing is retired. A `holding` outcome now also
  carries a `reason` (`policy-failure` or `remote-rejection`) distinguishing a
  resolver failure from a forge rejection.
- A host can now declare in lore's own `config.json` whether it authors vault
  content, with `"makes_vault_content": false`. On a host that declares itself
  a non-author, a conflict the structure cannot settle no longer waits for a
  person: the vault is reset to the published history and ends `converged`,
  discarding that host's unpublished commits rather than parking them. Those
  commits are not destroyed outright — they stay reachable by sha through the
  vault's reflog, so they survive until the reflog expires them (30 days by
  git's default) or a `gc` prunes them, not indefinitely. No version-control
  vocabulary reaches the person either way. The key is
  host-local by design — read from this host's config file, never from any
  vault's contents — so nothing a teammate syncs can flip a host into
  discarding its own work. It is also fail-safe: a missing file, unreadable
  file, invalid JSON, or an absent key all read as *author*, the branch that
  keeps work. A key that is present but not a boolean is refused rather than
  coerced; that vault reports `holding` with reason `policy-failure` and the
  rest of the sweep continues.
- `scripts/bootstrap-venv` creates and converges the project `.venv` carrying
  pytest and pytest-xdist, and `.envrc` now calls it rather than carrying its
  own copy. The bootstrap has one home, so a caller that cannot rely on direnv
  reaches the same venv: direnv trusts by path, so every git worktree starts
  untrusted, and a non-interactive shell never loads direnv at all. The script
  picks the newest 3.11+ interpreter on PATH and exits non-zero naming the floor
  when there is none, instead of silently leaving no venv behind.
- `CLAUDE.md` now tells agents to invoke `.venv/bin/python` explicitly. An
  agent's tool calls run in a non-interactive shell, where bare `python3` is the
  system interpreter — 3.9 on macOS — and the root `pyproject.toml`'s `-n auto`
  fails against it for want of xdist.

- The leak gate now runs as a pre-commit hook for this repo. `pre-commit
  install` wires it alongside ruff and the Conventional Commits check, so a
  private string on a shippable plugin surface is refused at commit time
  rather than after it ships. A committed denylist of structural seams gives
  the gate a floor that holds on every machine and on CI; the machine-local
  denylist of identifying tokens layers on top through the gate's new
  `--optional-denylist`, which is skipped silently when absent so a fresh
  clone is not blocked. Run it by hand with `scripts/leak-gate`.
- `trailhead update` refuses a tracked upstream branch whose name is
  option-shaped. Reading the branch from git rather than from a file is not
  on its own enough to make it safe as a git argument: only `git branch`
  rejects a name beginning with `-`, so a remote named `--output=<path>` with
  a matching remote-tracking ref makes git report that name back as the
  upstream, and `git diff` then parses it as an option and truncates that
  path.
- An update check no longer goes permanently inconclusive when the wired
  commit is force-pushed away, amended, or garbage-collected. That hop
  reports as unknown on its own while the checkout-versus-branch verdict
  stays correct.
- `trailhead update` no longer refuses a checkout that is merely ahead of its
  tracked branch as "diverged". A checkout carrying local commits has nothing
  to fast-forward, so a stale install on top of one is re-wired instead of
  being turned away with a merge command that would do nothing.

- The install provenance stamp now records only the checkout path and the
  commit that was wired — the two values that cannot be re-derived later. The
  tracked upstream branch and the `origin` URL are read live from the checkout
  at check time. The only stamped value still reaching git is the checkout
  path, as the `-C` argument, which consumes the token after it whatever its
  shape. `trailhead update --check --json` moves to schema
  version 3.
- `trailhead update` now reports the two gaps separately: how far your install
  is behind the checkout it was wired from, and how far that checkout is
  behind its tracked branch. Previously the checkout-versus-branch count was
  reported as though it were the install's, and an install left stale by a
  manual `git pull` was reported as up to date. Applying an upgrade in that
  state now re-wires instead of doing nothing.

- Closed a fence-containment bypass in the changelog delta shown at session
  start. The sanitizer preserves ZWJ and the directionality marks so emoji
  sequences and bidi prose render correctly, but those codepoints are
  invisible, so backticks interleaved with them stepped around a fence check
  keyed on the literal ``` substring and reached the agent as a working
  closing fence. Any run of three or more backticks joined only by
  zero-width or directionality codepoints is now neutralized, in both the
  producer and the hook's independent re-check.
- The stamp's `sha` validator is anchored with `\A`/`\Z`, so a value with a
  trailing newline is no longer accepted as an exact 40-character sha.

- Fixed two critical argument-injection vulnerabilities in `trailhead update`,
  both reachable unattended from the SessionStart hook: an option-shaped
  branch name (e.g. `--upload-pack=<command>`) could be parsed by `git fetch`
  as an option and execute an attacker-supplied command; an option-shaped
  `sha` (e.g. `--output=<path>`) could be parsed by `git diff` as an option
  and overwrite an arbitrary file. `git fetch`/`merge`/`merge-base` now insert
  `--` before the ref wherever git accepts an end-of-options marker, the
  tracked upstream branch is refused if it is option-shaped, and the stamp's
  `sha` is rejected outright unless it is exactly 40 hex characters — the general rule is that no value
  read from the stamp may reach a git argv position without being validated to
  a shape that cannot be parsed as an option.
- Fixed `trailhead update`'s rollback guarantee: a failing `claude plugin
  install` call (registering the marketplace, installing a tool, or the
  install half of a rewire) is now detected via its returncode and raises,
  so a failed re-wire during an upgrade correctly triggers the checkout
  rollback instead of silently advancing the provenance stamp past a wire
  that never happened. A failing `claude plugin uninstall` remains tolerated
  by design — a rewire's uninstall half and a plain uninstall's own call are
  both self-heal steps that must not block the install/removal that follows.
- `trailhead update` now resolves its config before mutating anything, rolls a failed re-wire back
  to the checkout's actual pre-upgrade HEAD (rather than the stamped sha),
  and reports rollback failures truthfully instead of always claiming
  success.
- Credential redaction in reported git errors now also covers the bare-token
  HTTPS form and `ssh://` URLs. The stamp's
  checkout-path confinement check now fails closed when neither `HOME` nor
  `USERPROFILE` is set, matching the SessionStart hook's own independent
  check, and accepts `USERPROFILE` alongside `HOME` so the feature isn't
  silently disabled on Windows. A stamp that exists but was rejected
  (confinement, a malformed `sha`, malformed JSON) is now reported
  distinctly from one that was never written, in `trailhead update --check`,
  `trailhead update`, and `trailhead doctor`.
- The changelog-delta sanitizer used by `trailhead update --check` now also
  strips C1 control codepoints, carriage returns, and bidirectional-override
  /isolate/BOM characters, while preserving a tab, the zero-width joiner
  (needed for multi-codepoint emoji), and the left-to-right/right-to-left
  marks (needed for legitimate bidirectional prose) that an earlier pass
  over-broadly stripped. `_run_git` no longer lets a subprocess's
  non-UTF-8 output escape as an unhandled `UnicodeDecodeError`.
- `trailhead update` (no `--check`) now performs the upgrade: fast-forwards the
  stamped checkout, re-wires plugins, and refreshes the provenance stamp.
  Requires an interactive confirmation or `--yes`; `--dry-run` previews with no
  mutation. A dirty or diverged checkout refuses without changing anything, and
  a re-wire failure after a successful fast-forward rolls the checkout back to
  its pre-upgrade sha and restores the prior wiring.
- New first-party `trailhead` plugin, installed by default: a SessionStart
  hook checks whether the install is behind its source checkout and, if so,
  adds a notice to the session's context naming the commit count and the
  command to run, with the changelog delta carried inside a delimited
  untrusted-content block. It never upgrades on its own, degrades to silence
  on any failure, and repeats at most once a day. Disable it with
  `session_start_update_check = false` in your config or the
  `TRAILHEAD_DISABLE_UPDATE_CHECK` environment variable (which always wins).
