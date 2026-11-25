#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin="$(cd "$here/../../.." && pwd)"
bash "$here/../../slice-asks-before-dropping-an-urgent-entry/fixtures/scaffold.sh"
# The seeded conversation's skill launch names the plugin as ./craft: install that copy here.
mkdir craft
cp -R "$plugin/skills" "$plugin/scripts" craft/
find craft -name __pycache__ -type d -prune -exec rm -rf {} +
