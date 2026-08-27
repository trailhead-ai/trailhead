# Changelog

All notable, user-visible changes to trailhead are documented here, in the
format described by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
