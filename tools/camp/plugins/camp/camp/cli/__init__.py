"""CLI domain: camp's hand-rolled verb dispatcher, split one module per command group.

``dispatch`` owns the top-level routing (``main``, ``_dispatch_group_command``,
``_resolve_group_for_command``, ``_slug_from_args_or_cwd``) plus the shared
binary-path constants; each sibling module owns one command group's ``_cmd_*``
handlers — ``status`` (version/which/status), ``group`` (group authoring + new),
``lifecycle`` (setup/sync/remove/rebase), ``workspace`` (activate/pwd/list), and
``inject`` (the hidden PostToolUse drain). Cross-module calls go through
``dispatch``'s helpers; heavy collaborators (spine, provision, trailhead.paths)
stay lazily imported inside the handlers so the inject route never pays for them.
"""
