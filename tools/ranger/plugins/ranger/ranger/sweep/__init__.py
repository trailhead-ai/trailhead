"""The sweep domain: queue derivation, the per-vault lock, and the exit report.

Five modules, each with its own contract docstring: ``preflight`` (the startup
checks), ``queue`` (derivation + bucket classification), ``lock`` (the
one-sweep-per-vault mutex), ``report`` (the durable exit report), and ``names``
(the shell-safe name confinement ``lock`` and ``report`` both validate vault
and group names against). The package itself stays empty — no re-exports — so
the CLI's imports have a stable target and the sweep's domain code never leaks
into ``ranger.cli``.
"""
