"""ranger — the unattended refine sweep over a camp group's task queue.

The importable package behind the ``ranger`` CLI. Domain logic lives under
``ranger.sweep`` (queue derivation, the one-sweep-per-vault lock, and the exit
report); the subcommand router and per-command handlers live under
``ranger.cli``. The CLI owns everything that reads record bodies, so escalation
text never has to transit the coordinating agent's context.
"""
