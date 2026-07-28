"""The sweep domain: queue derivation, the per-vault lock, and the exit report.

Four modules, each with its own contract docstring: ``preflight`` (the startup
checks), ``queue`` (derivation + bucket classification), ``lock`` (the
one-sweep-per-vault mutex), and ``report`` (the durable exit report). The
package itself stays empty — no re-exports — so the CLI's imports have a stable
target and the sweep's domain code never leaks into ``ranger.cli``.
"""
