#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$here/../../../../../experiments/ab-test-rules-slimming/scaffold/scaffold.sh" cli
