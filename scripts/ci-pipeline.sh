#!/usr/bin/env bash
# Full local CI gate (fail-fast between stages):
#   1) lint image      — clang-tidy + py_compile
#   2) coverage image  — pytest coverage floors + meson C++ tests
#   3) DE compat matrix — parallel per-DE build/test (no cov floors / no clang-tidy)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
mkdir -p logs

echo "==> pipeline: stage lint"
UH_CI_STAGE=lint ./scripts/ci-docker.sh 2>&1 | tee logs/ci-lint.log

echo "==> pipeline: stage coverage"
UH_CI_STAGE=coverage ./scripts/ci-docker.sh 2>&1 | tee logs/ci-coverage.log

echo "==> pipeline: stage compat matrix"
./scripts/ci-matrix.sh 2>&1 | tee logs/ci-matrix-summary.log

echo "==> pipeline: all stages passed (lint + coverage + compat matrix)"
