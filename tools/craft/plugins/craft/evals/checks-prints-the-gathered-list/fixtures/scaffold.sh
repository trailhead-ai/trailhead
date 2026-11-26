#!/usr/bin/env bash
# Install the gather stand-in as ./gather: it prints fixtures/gather.out, the list the skill is to repeat, and fixtures/gather.json for --json.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$here/gather.out" gather.out
cp "$here/gather.json" gather.json
printf '#!/bin/sh\nd="$(dirname "$0")"\nif [ "$1" = "--json" ]; then exec cat "$d/gather.json"; fi\nexec cat "$d/gather.out"\n' > gather
chmod +x gather
