"""CLI domain: camp's verb dispatcher, split one module per command group.

``parser`` owns `CampParser`, the one ``argparse.ArgumentParser`` subclass every
verb declares its flags on — it is what gives camp argparse's tokenizing while
keeping camp's own one-line refusals and exit codes.

``dispatch`` owns the top-level routing (``main``, ``_dispatch_group_command``,
``_resolve_group_for_command``, ``_slug_from_name_or_cwd``) plus the shared
binary-path constants; each sibling module owns one command group's ``_cmd_*``
handlers — ``status`` (version/which/status), ``group`` (group authoring + new),
``lifecycle`` (setup/sync/remove/rebase), ``workspace`` (activate/pwd/list), and
``inject`` (the hidden PostToolUse drain). Cross-module calls go through
``dispatch``'s helpers; heavy collaborators (spine, provision, trailhead.paths)
stay lazily imported inside the handlers so the inject route never pays for them.
"""
