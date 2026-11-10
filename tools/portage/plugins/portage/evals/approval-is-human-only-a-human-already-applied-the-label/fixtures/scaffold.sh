#!/usr/bin/env bash
# Build this case's run directory with the corpus's shared approval fixture.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$here/../../approval-fixture-env.sh" a-human-already-applied-the-label
