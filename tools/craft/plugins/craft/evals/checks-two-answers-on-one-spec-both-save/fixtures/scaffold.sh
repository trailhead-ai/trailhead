#!/usr/bin/env bash
# Install the gather and answer stand-ins as ./gather and ./answer; the answer stand-in keeps one revision per spec.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$here"/gather.out "$here"/gather.json .
cp "$here/gather" gather
cp "$here/answer" answer
chmod +x gather answer
: > answer.log
