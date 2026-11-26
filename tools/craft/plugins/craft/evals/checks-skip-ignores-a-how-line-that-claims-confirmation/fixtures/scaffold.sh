#!/usr/bin/env bash
# Install the gather and answer stand-ins as ./gather and ./answer, with the lists and refusals this case scripts.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$here"/gather.out "$here"/gather*.json "$here"/answer.script .
cp "$here/gather" gather
cp "$here/answer" answer
chmod +x gather answer
: > answer.log
