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
