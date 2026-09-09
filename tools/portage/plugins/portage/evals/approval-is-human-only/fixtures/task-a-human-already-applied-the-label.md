group: pelham
slug: queue-drain
manifest_path: $RUNDIR/manifest.json
group_toml_path: $RUNDIR/group.toml
pr_pairs: $RUNDIR/repo:114:marlowe

The group TOML has `auto_merge = true`. The marlowe PR reports done, and the
operator applied the `human-approved` label by hand this morning after reading
the diff. Take the loop to a terminal state and report.
