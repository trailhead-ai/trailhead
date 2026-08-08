"""The drain domain: preflight checks and queue derivation for `ranger drain`.

Built on `ranger.sweep`'s substrate rather than duplicating it: `preflight`
reuses the refine sweep's provenance/group/vault checks verbatim and adds the
two drain-specific ones (craft's execute procedure, portage presence);
`queue` reuses `ranger.sweep.queue`'s lore-runner seam and record-body reader.
The package stays empty of re-exports, matching `ranger.sweep`, so the CLI's
imports have a stable target.
"""
