#!/usr/bin/env bash
# Run every UH_CI_DE compat cell in parallel (never sequential).
# Lint and coverage are separate stages (see scripts/ci-pipeline.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

DES=(baseline gnome kde xfce cinnamon mate budgie lxqt)
LOG_DIR="${ROOT}/logs/ci-matrix"
mkdir -p "${LOG_DIR}"

declare -a PIDS=()
declare -a DE_FOR_PID=()

echo "==> ci-matrix: launching ${#DES[@]} DE compat cells in parallel"
for de in "${DES[@]}"; do
  log="${LOG_DIR}/${de}.log"
  (
    export UH_CI_STAGE=compat
    export UH_CI_DE="${de}"
    export UH_CI_PARALLEL_BUILD=1
    export UBUNTU_HELLO_CI_BUILD_DIR="build-ci-${de}"
    echo "[${de}] starting compat (log: ${log})"
    ./scripts/ci-docker.sh
  ) >"${log}" 2>&1 &
  PIDS+=("$!")
  DE_FOR_PID+=("${de}")
  echo "==> started UH_CI_STAGE=compat UH_CI_DE=${de} pid=$! -> ${log}"
done

fail=0
failed_des=()
for i in "${!PIDS[@]}"; do
  pid="${PIDS[$i]}"
  de="${DE_FOR_PID[$i]}"
  if wait "${pid}"; then
    echo "==> OK   UH_CI_DE=${de}"
  else
    echo "==> FAIL UH_CI_DE=${de} (see logs/ci-matrix/${de}.log)" >&2
    fail=1
    failed_des+=("${de}")
  fi
done

if [[ "${fail}" -ne 0 ]]; then
  echo "error: ci-matrix failed for: ${failed_des[*]}" >&2
  exit 1
fi

echo "==> ci-matrix: all ${#DES[@]} DE compat cells passed"
