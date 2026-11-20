#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARIANT=nostop exec bash "$here/../../unattended-plan-narrows-an-oversized-slice/fixtures/scaffold.sh"
