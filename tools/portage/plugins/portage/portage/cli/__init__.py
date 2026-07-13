"""The `portage` CLI package: the subcommand router and per-command handlers.

``dispatch`` builds the argparse tree and routes; each sibling module owns one
command group (``repos``, ``pr``, ``ci``) — its ``add_*_subparser`` registers
the subcommand and wires the handler that calls the matching ``trailhead.vcs``
provider method. ``get_provider`` is imported at each command module's top level
so tests can patch it (dependency injection, no network / gh / git touched).
"""
