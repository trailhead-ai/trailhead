#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARM=unknown exec bash "$here/../../distill-sweep-builds-its-queue-from-lore/fixtures/scaffold.sh"
