#!/usr/bin/env bash
# Run every packaging format cell in parallel (never sequential).
# Mirrors .github/workflows/check.yml packaging matrix; used by ci-pipeline.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

FORMATS=(deb rpm-fedora rpm-opensuse arch snap appimage flatpak)
LOG_DIR="${ROOT}/logs/ci-packaging"
mkdir -p "${LOG_DIR}"

declare -a PIDS=()
declare -a FMT_FOR_PID=()

echo "==> ci-packaging-matrix: launching ${#FORMATS[@]} format cells in parallel"
for fmt in "${FORMATS[@]}"; do
	log="${LOG_DIR}/${fmt}.log"
	(
		export UH_PACKAGING_ARTIFACTS_ISOLATE=1
		export DOCKER_BUILDKIT=1
		echo "[${fmt}] starting packaging cell (log: ${log})"
		./scripts/ci-packaging-cell.sh "${fmt}"
	) >"${log}" 2>&1 &
	PIDS+=("$!")
	FMT_FOR_PID+=("${fmt}")
	echo "==> started packaging format=${fmt} pid=$! -> ${log}"
done

fail=0
failed_fmts=()
for i in "${!PIDS[@]}"; do
	pid="${PIDS[$i]}"
	fmt="${FMT_FOR_PID[$i]}"
	if wait "${pid}"; then
		echo "==> OK   packaging format=${fmt}"
	else
		echo "==> FAIL packaging format=${fmt} (see logs/ci-packaging/${fmt}.log)" >&2
		fail=1
		failed_fmts+=("${fmt}")
	fi
done

if [[ "${fail}" -ne 0 ]]; then
	echo "error: ci-packaging-matrix failed for: ${failed_fmts[*]}" >&2
	exit 1
fi

echo "==> ci-packaging-matrix: all ${#FORMATS[@]} format cells passed"
