#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../../../../../.." && pwd)"
bash "$here/seed.sh"
mkdir -p lore-plugin bin
cp -r "$repo/tools/lore/plugins/lore/cli" "$repo/tools/lore/plugins/lore/lore" lore-plugin/
[ -f "$repo/tools/lore/plugins/lore/_bootstrap.py" ] && cp "$repo/tools/lore/plugins/lore/_bootstrap.py" lore-plugin/ || true
py=$(sed -n 's/.*exec \([^ ]*\) .*/\1/p' lore | head -1)
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")" && pwd)\nCLAUDE_CODE_SESSION_ID=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee LORE_EMAIL=eval@example.invalid PYTHONPATH="$here" exec %s "$here/lore-plugin/cli/lore" "$@"\n' "$py" > lore
chmod +x lore
printf '#!/bin/sh\nhere=$(cd "$(dirname "$0")/.." && pwd)\nexec "$here/lore" "$@"\n' > bin/lore
chmod +x bin/lore
printf "# CLAUDE.md\n\n## Project Maturity\n\nearly\n" > CLAUDE.md
SPEC='# Recipe box

## Problem

A home cook keeps a few dozen recipes and wants to find, add, correct and drop them from the terminal. Sorting by a column and colouring by tag are conveniences for a long list; the list is usable without them.

## Acceptance Criteria

- **AC1.** `recipes list` prints every saved recipe.

- **AC2.** `recipes add <name>` saves a new recipe.

- **AC3.** `recipes edit <id> <name>` changes the name of a saved recipe.

- **AC4.** `recipes delete <id>` removes a saved recipe.

- **AC5.** `recipes list --sort <column>` prints the saved recipes ordered by the chosen column.

- **AC6.** `recipes list` colours each recipe by its tag.
'
printf '%s
' "$SPEC" | ./lore record create --kind spec --title 'Recipe box' --status ready >/dev/null
