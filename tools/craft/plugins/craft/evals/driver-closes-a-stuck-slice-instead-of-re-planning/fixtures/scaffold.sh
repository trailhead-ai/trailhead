#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARM=stuck exec bash "$here/../../driver-ends-a-slice-without-asking-about-its-check/fixtures/scaffold.sh"
