# Changelog

All notable, user-visible changes to trailhead are documented here, in the
format described by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

- Fixed two critical argument-injection vulnerabilities in `trailhead update`,
  both reachable unattended from the SessionStart hook: an option-shaped
  `branch` in the install provenance stamp (e.g. `--upload-pack=<command>`)
  could be parsed by `git fetch` as an option and execute an
  attacker-supplied command; an option-shaped `sha` (e.g.
  `--output=<path>`) could be parsed by `git diff` as an option and
  overwrite an arbitrary file. `git fetch`/`merge`/`merge-base` now insert
  `--` before every stamp-derived positional, and the stamp's `branch` and
  `sha` fields are each rejected outright if shaped like an option — the
  general rule is that no value read from the stamp may reach a git argv
  position without being validated to a shape that cannot be parsed as an
  option.
- Fixed `trailhead update`'s rollback guarantee: a failing `claude plugin
  install` call (registering the marketplace, installing a tool, or the
  install half of a rewire) is now detected via its returncode and raises,
  so a failed re-wire during an upgrade correctly triggers the checkout
  rollback instead of silently advancing the provenance stamp past a wire
  that never happened. A failing `claude plugin uninstall` remains tolerated
  by design — a rewire's uninstall half and a plain uninstall's own call are
  both self-heal steps that must not block the install/removal that follows.
- `trailhead update` now refuses to upgrade if the checkout's `origin` remote
  has changed since install (mirroring `--check`'s existing refusal),
  resolves its config before mutating anything, rolls a failed re-wire back
  to the checkout's actual pre-upgrade HEAD (rather than the stamped sha),
  and reports rollback failures truthfully instead of always claiming
  success.
- The install provenance stamp no longer persists an unredacted `origin_url`
  (previously visible via `trailhead doctor --json`); credential redaction
  now also covers the bare-token HTTPS form and `ssh://` URLs. The stamp's
  checkout-path confinement check now fails closed when neither `HOME` nor
  `USERPROFILE` is set, matching the SessionStart hook's own independent
  check, and accepts `USERPROFILE` alongside `HOME` so the feature isn't
  silently disabled on Windows. A stamp that exists but was rejected
  (confinement, an option-shaped field, malformed JSON) is now reported
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
