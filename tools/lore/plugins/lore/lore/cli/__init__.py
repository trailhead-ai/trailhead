"""CLI domain: the ``lore`` argparse dispatcher, split one module per command group.

``dispatch`` owns the top-level parser assembly (``build_parser``), ``main``, and
the unknown-command hint; each sibling module (``init``, ``sync``, ``flush``,
``areas``, ``search``, ``record``, ``vault``, ``session``) owns one command
group's ``cmd_*`` handlers plus an ``add_*`` function that registers its
subparser(s) onto the shared parser tree. ``common`` holds the cross-group helpers.
"""
