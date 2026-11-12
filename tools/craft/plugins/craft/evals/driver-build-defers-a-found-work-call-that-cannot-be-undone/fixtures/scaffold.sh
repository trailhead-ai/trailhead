#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$here/../../build-asks-before-a-found-work-call-that-cannot-be-undone/fixtures/scaffold.sh"
