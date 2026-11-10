#!/usr/bin/env bash
# Put the spec under audit in the run directory.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$here/spec.md" spec.md
