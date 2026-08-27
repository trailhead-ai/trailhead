# Changelog

All notable, user-visible changes to trailhead are documented here, in the
format described by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
