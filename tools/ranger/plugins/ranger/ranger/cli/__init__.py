"""The `ranger` CLI package: the subcommand router and per-command handlers.

``dispatch`` builds the argparse tree and routes; each sibling module owns one
command group — its ``add_*_subparser`` registers the subcommand and wires the
handler that drives the matching ``ranger.sweep`` operation. Handlers stay thin
(parse argv, call the domain function, print the agreed output) so the sweep
logic is unit-testable without the CLI shim.
"""
