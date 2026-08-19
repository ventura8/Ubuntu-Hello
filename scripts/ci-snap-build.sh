#!/usr/bin/env bash
# Build the Snap package inside docker/Dockerfile.snap, then run live E2E install.
#
# snapd needs a real systemd PID 1 to manage snap mounts, so the container
# boots systemd as CMD and this script drives the build via `docker exec`
# once systemd reports ready, then tears the container down. See
# docker/Dockerfile.snap for why a plain ENTRYPOINT script doesn't work here.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

IMAGE="ubuntu-hello-snap:24.04"
CONTAINER="ubuntu-hello-snap-build-$$"
RUN_E2E="${UH_SNAP_E2E:-1}"
export UH_ARTIFACTS_DIR="${UH_ARTIFACTS_DIR:-${ROOT}/artifacts}"
mkdir -p "${UH_ARTIFACTS_DIR}"

# Host path under the repo → /src/... inside the snap container.
# Absolute paths outside ROOT are bind-mounted at the same container path.
if [[ "${UH_ARTIFACTS_DIR}" == "${ROOT}"/* ]]; then
	ART_IN_CONTAINER="/src/${UH_ARTIFACTS_DIR#"${ROOT}"/}"
	ARTIFACT_EXTRA_MOUNTS=()
elif [[ "${UH_ARTIFACTS_DIR}" == /* ]]; then
	ART_IN_CONTAINER="${UH_ARTIFACTS_DIR}"
	ARTIFACT_EXTRA_MOUNTS=(-v "${UH_ARTIFACTS_DIR}:${ART_IN_CONTAINER}:rw")
else
	ART_IN_CONTAINER="/src/${UH_ARTIFACTS_DIR}"
	ARTIFACT_EXTRA_MOUNTS=()
fi

cleanup() {
	docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build -f docker/Dockerfile.snap -t "${IMAGE}" .

docker run -d --name "${CONTAINER}" --privileged --cgroupns=host \
	-e "UH_ARTIFACTS_DIR=${ART_IN_CONTAINER}" \
	-v /sys/fs/cgroup:/sys/fs/cgroup:rw \
	-v /sys/kernel/security:/sys/kernel/security:rw \
	-v "${ROOT}:/src:rw" "${ARTIFACT_EXTRA_MOUNTS[@]}" -w /src "${IMAGE}" >/dev/null

echo "==> waiting for systemd inside ${CONTAINER} to become ready"
ready=0
for _ in $(seq 1 60); do
	if docker exec "${CONTAINER}" systemctl is-system-running 2>/dev/null | grep -qE '^(running|degraded)$'; then
		ready=1
		break
	fi
	sleep 1
done
if [[ "${ready}" -ne 1 ]]; then
	echo "error: systemd did not become ready inside ${CONTAINER}" >&2
	docker logs "${CONTAINER}" --tail 50 || true
	exit 1
fi

docker exec -w /src -e "UH_ARTIFACTS_DIR=${ART_IN_CONTAINER}" "${CONTAINER}" \
	/usr/local/bin/snap-entrypoint.sh ./scripts/release-portable.sh snap

if [[ "${RUN_E2E}" == "1" ]]; then
	echo "==> Snap packaging E2E install/upgrade/remove/reinstall"
	docker exec -w /src -e UH_SKIP_ONBOARD=1 -e "UH_ARTIFACTS_DIR=${ART_IN_CONTAINER}" \
		"${CONTAINER}" bash ./scripts/packaging-e2e-install.sh snap
fi
