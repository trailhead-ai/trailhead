#!/usr/bin/env bash
# Build this case's run directory with the corpus's shared merge-tier fixture.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$here/../../monitor-merge-tier-fixture-env.sh" fails
