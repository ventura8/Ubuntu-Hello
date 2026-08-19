#!/usr/bin/env bash
# One packaging CI cell: build + smoke-verify + live E2E
# (install → upgrade+config-preserve → remove → reinstall).
# Shared by GitHub Actions check.yml and local scripts/ci-packaging-matrix.sh so
# a packaging failure on GHA also fails ./scripts/ci-pipeline.sh.
#
# Usage: ./scripts/ci-packaging-cell.sh <format>
# Formats: deb | rpm-fedora | rpm-opensuse | arch | snap | appimage | flatpak
#
# Optional env:
#   UH_ARTIFACTS_DIR — artifact output (default: <repo>/artifacts)
#   UH_PACKAGING_ARTIFACTS_ISOLATE=1 — set UH_ARTIFACTS_DIR to
#     artifacts/ci-packaging/<format> (parallel matrix cells)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

FORMAT="${1:?usage: $0 <deb|rpm-fedora|rpm-opensuse|arch|snap|appimage|flatpak>}"
export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"

if [[ "${UH_PACKAGING_ARTIFACTS_ISOLATE:-0}" == "1" ]]; then
	export UH_ARTIFACTS_DIR="${ROOT}/artifacts/ci-packaging/${FORMAT}"
fi
export UH_ARTIFACTS_DIR="${UH_ARTIFACTS_DIR:-${ROOT}/artifacts}"
mkdir -p "${UH_ARTIFACTS_DIR}"

# Host path under the repo → /src/... inside packaging containers (-v "$PWD:/src").
# Absolute paths outside ROOT need an extra bind mount at the same container path.
uh_artifacts_in_container() {
	local host="${1}"
	if [[ "${host}" == "${ROOT}"/* ]]; then
		printf '/src/%s\n' "${host#"${ROOT}"/}"
	elif [[ "${host}" == /* ]]; then
		printf '%s\n' "${host}"
	else
		printf '/src/%s\n' "${host}"
	fi
}

ART_IN_CONTAINER="$(uh_artifacts_in_container "${UH_ARTIFACTS_DIR}")"
ARTIFACT_EXTRA_MOUNTS=()
if [[ "${UH_ARTIFACTS_DIR}" == /* && "${UH_ARTIFACTS_DIR}" != "${ROOT}"/* ]]; then
	# Outside the repo mount: bind the host dir to ART_IN_CONTAINER (absolute path).
	ARTIFACT_EXTRA_MOUNTS+=(-v "${UH_ARTIFACTS_DIR}:${ART_IN_CONTAINER}:rw")
fi

chmod +x \
	scripts/release-deb.sh scripts/release-rpm.sh scripts/release-arch.sh \
	scripts/release-portable.sh scripts/packaging-smoke-verify.sh \
	scripts/packaging-e2e-install.sh scripts/ci-snap-build.sh \
	docker/snap-entrypoint.sh packaging/appimage/build-appimage.sh \
	scripts/ci-packaging-cell.sh 2>/dev/null || true

case "${FORMAT}" in
	deb)
		KIND=docker
		DOCKERFILE=docker/Dockerfile.ppa
		IMAGE=ubuntu-hello-ppa:26.04
		SCRIPT=(./scripts/release-deb.sh)
		DOCKER_FLAGS=()
		;;
	rpm-fedora)
		KIND=docker
		DOCKERFILE=docker/Dockerfile.rpm.fedora
		IMAGE=ubuntu-hello-rpm-fedora:44
		SCRIPT=(./scripts/release-rpm.sh fedora)
		DOCKER_FLAGS=()
		;;
	rpm-opensuse)
		KIND=docker
		DOCKERFILE=docker/Dockerfile.rpm.opensuse
		IMAGE=ubuntu-hello-rpm-opensuse:16.0
		SCRIPT=(./scripts/release-rpm.sh opensuse)
		DOCKER_FLAGS=()
		;;
	arch)
		KIND=docker
		DOCKERFILE=docker/Dockerfile.arch
		IMAGE=ubuntu-hello-arch:base-devel-20260816.0.574111
		SCRIPT=(./scripts/release-arch.sh)
		DOCKER_FLAGS=()
		;;
	snap)
		KIND=snap
		DOCKERFILE=""
		IMAGE=""
		SCRIPT=()
		DOCKER_FLAGS=()
		;;
	appimage)
		KIND=docker
		DOCKERFILE=docker/Dockerfile.release
		IMAGE=ubuntu-hello-release:26.04
		SCRIPT=(./scripts/release-portable.sh appimage)
		DOCKER_FLAGS=(--privileged)
		;;
	flatpak)
		KIND=docker
		DOCKERFILE=docker/Dockerfile.release
		IMAGE=ubuntu-hello-release:26.04
		SCRIPT=(./scripts/release-portable.sh flatpak)
		DOCKER_FLAGS=(--privileged --device /dev/fuse)
		;;
	*)
		echo "error: unknown packaging format: ${FORMAT}" >&2
		exit 1
		;;
esac

echo "==> packaging cell: ${FORMAT} (artifacts=${UH_ARTIFACTS_DIR})"

if [[ "${KIND}" == "snap" ]]; then
	# ci-snap-build.sh builds the snap and runs live E2E inside the systemd container.
	UH_ARTIFACTS_DIR="${UH_ARTIFACTS_DIR}" ./scripts/ci-snap-build.sh
else
	docker build -f "${DOCKERFILE}" -t "${IMAGE}" .
	# DOCKER_FLAGS are format-authored literals (not user input).
	docker run --rm "${DOCKER_FLAGS[@]}" \
		-e "UH_ARTIFACTS_DIR=${ART_IN_CONTAINER}" \
		-v "${ROOT}:/src:rw" "${ARTIFACT_EXTRA_MOUNTS[@]}" -w /src \
		"${IMAGE}" "${SCRIPT[@]}"
fi

./scripts/packaging-smoke-verify.sh "${FORMAT}"

if [[ "${KIND}" != "snap" ]]; then
	# Live install → upgrade (config preserve) → remove → reinstall on /
	# inside the format image (PAM + config.ini).
	docker run --rm "${DOCKER_FLAGS[@]}" \
		-e UH_SKIP_ONBOARD=1 \
		-e "UH_ARTIFACTS_DIR=${ART_IN_CONTAINER}" \
		-v "${ROOT}:/src:rw" "${ARTIFACT_EXTRA_MOUNTS[@]}" -w /src \
		"${IMAGE}" ./scripts/packaging-e2e-install.sh "${FORMAT}"
fi

echo "==> packaging cell OK: ${FORMAT}"
