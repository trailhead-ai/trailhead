#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$here/../../build-asks-before-a-found-work-call-outside-its-allowed-actions/fixtures/scaffold.sh"
