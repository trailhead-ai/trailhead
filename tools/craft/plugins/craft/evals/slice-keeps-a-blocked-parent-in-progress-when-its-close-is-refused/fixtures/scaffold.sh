#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARM=blockednorepo exec bash "$here/../../slice-closes-a-stuck-slice-and-chooses-the-next/fixtures/scaffold.sh"
