#!/usr/bin/env bash
# sets this case's parameters, then runs the shared docket scaffold
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export FILLER_LIVE=14 FILLER_DONE=6
exec bash "$here/../../docket-scaffold.sh"
