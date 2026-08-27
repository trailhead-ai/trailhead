# Changelog

All notable, user-visible changes to trailhead are documented here, in the
format described by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

- Fixed a critical argument-injection vulnerability in `trailhead update`: an
  option-shaped `branch` in the install provenance stamp (e.g.
  `--upload-pack=<command>`) could be parsed by `git fetch` as an option and
  execute an attacker-supplied command, reachable unattended from the
  SessionStart hook. `git fetch`/`merge`/`merge-base` now insert `--` before
  every stamp-derived positional, and the stamp's `branch` field is rejected
  outright if it is shaped like an option.
- Fixed `trailhead update`'s rollback guarantee: a failing `claude plugin
  install`/`uninstall` call is now detected (via its returncode) and raises,
  so a failed re-wire during an upgrade correctly triggers the checkout
  rollback instead of silently advancing the provenance stamp past a wire
  that never happened.
- `trailhead update` now refuses to upgrade if the checkout's `origin` remote
  has changed since install (mirroring `--check`'s existing refusal),
  resolves its config before mutating anything, rolls a failed re-wire back
  to the checkout's actual pre-upgrade HEAD (rather than the stamped sha),
  and reports rollback failures truthfully instead of always claiming
  success.
- The install provenance stamp no longer persists an unredacted `origin_url`
  (previously visible via `trailhead doctor --json`); credential redaction
  now also covers the bare-token HTTPS form and `ssh://` URLs. The stamp's
  checkout-path confinement check now fails closed when `HOME` is unset,
  matching the SessionStart hook's own independent check.
- The changelog-delta sanitizer used by `trailhead update --check` now also
  strips C1 control codepoints, carriage returns, zero-width characters, and
  bidirectional-override/isolate characters.
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
