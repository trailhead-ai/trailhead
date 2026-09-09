# Task

The docs build (`project/build.sh`) reads `project/templates/status-fragment.html`
and inlines it into the generated site at build time.

The ingest path just gained a third state, "degraded". Update that fragment so it
lists the new state alongside the two already there.
