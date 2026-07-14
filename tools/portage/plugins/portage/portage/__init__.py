"""portage — PR lifecycle, CI watch, and merge ordering for a camp group.

The importable package behind the ``portage`` CLI. Domain logic (pair parsing,
sidecar token parsing) lives in top-level modules here; the subcommand router
and per-command handlers live under ``portage.cli``. Every command is a thin
consumer of ``trailhead.vcs``: it parses argv, calls the matching provider
method, and reproduces the JSON output + exit codes the flow contracts on.
"""
