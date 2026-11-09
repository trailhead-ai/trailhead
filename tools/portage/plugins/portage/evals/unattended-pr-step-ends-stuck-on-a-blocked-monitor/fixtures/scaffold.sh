#!/usr/bin/env bash
# Thin scaffold: this case's parameters, then the corpus's shared scaffold.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a
# shellcheck disable=SC1091
. "$here/params"
set +a
exec bash "$here/../../pr-step-scaffold.sh"
