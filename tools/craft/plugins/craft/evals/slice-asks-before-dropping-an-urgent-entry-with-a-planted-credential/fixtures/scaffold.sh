#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARM=credential exec bash "$here/../../slice-asks-before-dropping-an-urgent-entry/fixtures/scaffold.sh"
