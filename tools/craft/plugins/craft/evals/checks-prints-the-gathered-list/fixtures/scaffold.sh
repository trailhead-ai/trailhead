#!/usr/bin/env bash
# Install the gather stand-in as ./gather: it prints fixtures/gather.out, the list the skill is to repeat.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$here/gather.out" gather.out
printf '#!/bin/sh\nexec cat "$(dirname "$0")/gather.out"\n' > gather
chmod +x gather
