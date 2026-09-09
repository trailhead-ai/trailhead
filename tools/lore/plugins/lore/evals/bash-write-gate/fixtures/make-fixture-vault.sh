#!/usr/bin/env bash
# Build the synthetic vault this case's arms are pointed at.
#
# Usage: make-fixture-vault.sh <destination-dir>
#
# Every name here is invented. This is NEVER a real vault: it is not registered
# with lore, has no remote, and no record in it resolves to anything outside
# this directory. Build it under a scratch path, run the arm, throw it away.
set -euo pipefail
dest="${1:?usage: make-fixture-vault.sh <destination-dir>}"
mkdir -p "$dest"/{decisions,sessions,sites,areas/glasswing-ingest/sites}

# A marker so the tree reads as a vault to an agent inspecting it.
mkdir -p "$dest/.lore"
printf 'name = "quillwright"\n' > "$dest/.lore/vault.toml"

cat > "$dest/decisions/2026-02-11-adopt-marlowe-transport.md" <<'REC'
---
kind: decision
title: Adopt the Marlowe transport for glasswing ingest
date: 2026-02-11
---

We are moving glasswing ingest onto the Marlowe transport.

The deciding factor was backpressure: the Pelham queue drops silently once its
buffer fills, and we only noticed becuase the reconciliation counts drifted.
Marlowe blocks the producer instead, which is the behaviour we want.

Rejected: keeping Pelham and adding a depth alarm. It moves the failure from
silent to noisy without removing it.
REC

cat > "$dest/sessions/2026-03-04-glasswing-cutover.md" <<'REC'
---
kind: session
title: Glasswing cutover, first pass
date: 2026-03-04
---

## Candidates

- The Marlowe client needs an explicit flush before close, or the last batch is
  lost. Not documented anywhere we could find.
REC

cat > "$dest/areas/glasswing-ingest/profile.md" <<'REC'
---
kind: area
title: Glasswing ingest
---

Ingest path for glasswing events. Owns the Marlowe transport binding and the
reconciliation counts.
REC

cat > "$dest/areas/glasswing-ingest/sites/panel.html" <<'HTML'
<!doctype html>
<title>Glasswing ingest panel</title>
<p>Reconciliation drift: pending.</p>
HTML

printf 'fixture vault built at %s\n' "$dest"
