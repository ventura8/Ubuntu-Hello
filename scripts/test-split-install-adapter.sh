#!/usr/bin/env bash
# Test adapter: invoke uh_split_install_from_list with argv paths (no shell interpolation).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
. scripts/release-common.sh
uh_split_install_from_list "$1" "$2" "$3"
