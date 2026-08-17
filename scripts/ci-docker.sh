#!/usr/bin/env bash
# Build and run Ubuntu Hello CI inside Docker (ubuntu:26.04).
#
# Stages (UH_CI_STAGE):
#   lint      — docker/Dockerfile.ci.lint; meson/ninja + clang-tidy + py_compile + i18n-lint
#   coverage  — docker/Dockerfile.ci.coverage; meson/ninja + pytest coverage + meson tests
#   compat    — per-DE docker/Dockerfile.ci[.de]; build + pytest (no cov floors) + meson tests
#
# Dockerfiles live under docker/ (repo root stays clean; see AGENTS.md §4.6.1).
#
# Compat DE selection: UH_CI_DE=baseline|gnome|kde|xfce|cinnamon|mate|budgie|lxqt
#
# Caching (speed; does not change quality gates):
#   DOCKER_BUILDKIT=1 always for image builds
#   UH_CI_DOCKER_CACHE=gha|local|none  (default: local)
#     gha   — buildx + GitHub Actions cache (type=gha); use in check.yml
#     local — buildx + .cache/docker-ci/<scope> + skip rebuild if Dockerfile digest matches image label
#     none  — plain docker build (BuildKit layer cache only)
#   UH_CI_FORCE_BUILD=1 — always rebuild (ignore digest skip)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UH_CI_STAGE="${UH_CI_STAGE:-compat}"
UH_CI_DE="${UH_CI_DE:-baseline}"
UH_CI_DOCKER_CACHE="${UH_CI_DOCKER_CACHE:-local}"
UH_CI_FORCE_BUILD="${UH_CI_FORCE_BUILD:-0}"

resolve_dockerfile_and_image() {
  # Image tags use an explicit version (never the implicit Docker ":latest" tag).
  case "${UH_CI_STAGE}" in
    lint)
      DOCKERFILE="docker/Dockerfile.ci.lint"
      IMAGE="${UBUNTU_HELLO_CI_IMAGE:-ubuntu-hello-ci-lint:26.04}"
      BUILD_DIR="${UBUNTU_HELLO_CI_BUILD_DIR:-build-ci-lint}"
      ;;
    coverage)
      DOCKERFILE="docker/Dockerfile.ci.coverage"
      IMAGE="${UBUNTU_HELLO_CI_IMAGE:-ubuntu-hello-ci-coverage:26.04}"
      BUILD_DIR="${UBUNTU_HELLO_CI_BUILD_DIR:-build-ci-coverage}"
      ;;
    compat)
      case "${UH_CI_DE}" in
        baseline)
          DOCKERFILE="docker/Dockerfile.ci"
          ;;
        gnome|kde|xfce|cinnamon|mate|budgie|lxqt)
          DOCKERFILE="docker/Dockerfile.ci.${UH_CI_DE}"
          ;;
        *)
          echo "error: unknown UH_CI_DE='${UH_CI_DE}' (expected: baseline gnome kde xfce cinnamon mate budgie lxqt)" >&2
          exit 1
          ;;
      esac
      IMAGE="${UBUNTU_HELLO_CI_IMAGE:-ubuntu-hello-ci-${UH_CI_DE}:26.04}"
      BUILD_DIR="${UBUNTU_HELLO_CI_BUILD_DIR:-build-ci-${UH_CI_DE}}"
      ;;
    *)
      echo "error: unknown UH_CI_STAGE='${UH_CI_STAGE}' (expected: lint coverage compat)" >&2
      exit 1
      ;;
  esac
}

cache_scope_name() {
  if [[ "${UH_CI_STAGE}" == "compat" ]]; then
    echo "ubuntu-hello-ci-${UH_CI_STAGE}-${UH_CI_DE}"
  else
    echo "ubuntu-hello-ci-${UH_CI_STAGE}"
  fi
}

dockerfile_digest() {
  # Fingerprint the stage Dockerfile only (images do not COPY sources).
  sha256sum "${DOCKERFILE}" | awk '{print $1}'
}

image_has_digest() {
  local want="$1"
  local got
  if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    return 1
  fi
  got="$(docker image inspect "${IMAGE}" --format '{{index .Config.Labels "ubuntu-hello.ci.dockerfile-digest"}}' 2>/dev/null || true)"
  [[ -n "${got}" && "${got}" == "${want}" ]]
}

build_ci_image() {
  export DOCKER_BUILDKIT=1
  local digest scope cache_dir
  digest="$(dockerfile_digest)"
  scope="$(cache_scope_name)"

  if [[ "${UH_CI_FORCE_BUILD}" != "1" ]] && image_has_digest "${digest}"; then
    echo "==> reusing ${IMAGE} (Dockerfile digest ${digest:0:12}… unchanged; set UH_CI_FORCE_BUILD=1 to rebuild)"
    return 0
  fi

  echo "==> docker build ${IMAGE} from ${DOCKERFILE} (ubuntu:26.04, stage=${UH_CI_STAGE}, de=${UH_CI_DE}, cache=${UH_CI_DOCKER_CACHE})"

  case "${UH_CI_DOCKER_CACHE}" in
    gha)
      docker buildx build \
        --file "${DOCKERFILE}" \
        --tag "${IMAGE}" \
        --label "ubuntu-hello.ci.dockerfile-digest=${digest}" \
        --cache-from "type=gha,scope=${scope}" \
        --cache-to "type=gha,mode=max,scope=${scope}" \
        --load \
        .
      ;;
    local)
      cache_dir="${ROOT}/.cache/docker-ci/${scope}"
      # Drop corrupt/partial cache from interrupted exports so buildx can start clean.
      if [[ ! -f "${cache_dir}/index.json" ]]; then
        rm -rf "${cache_dir}"
      else
        rm -rf "${cache_dir}/ingest"
      fi
      mkdir -p "${cache_dir}"
      set +e
      docker buildx build \
        --file "${DOCKERFILE}" \
        --tag "${IMAGE}" \
        --label "ubuntu-hello.ci.dockerfile-digest=${digest}" \
        --cache-from "type=local,src=${cache_dir}" \
        --cache-to "type=local,dest=${cache_dir},mode=max" \
        --load \
        .
      local_rc=$?
      set -e
      if [[ "${local_rc}" -ne 0 ]]; then
        # Cache export can fail after a successful --load. Accept the image only
        # when its Dockerfile digest label matches this rebuild — never a stale tag.
        loaded_digest="$(
          docker image inspect "${IMAGE}" \
            --format '{{index .Config.Labels "ubuntu-hello.ci.dockerfile-digest"}}' \
            2>/dev/null || true
        )"
        if [[ "${loaded_digest}" == "${digest}" ]]; then
          echo "warn: local cache export failed; image ${IMAGE} digest matches — continuing"
        else
          echo "==> retrying ${IMAGE} build without local cache export"
          docker buildx build \
            --file "${DOCKERFILE}" \
            --tag "${IMAGE}" \
            --label "ubuntu-hello.ci.dockerfile-digest=${digest}" \
            --load \
            .
        fi
      fi
      ;;
    none)
      docker build \
        --file "${DOCKERFILE}" \
        --tag "${IMAGE}" \
        --label "ubuntu-hello.ci.dockerfile-digest=${digest}" \
        .
      ;;
    *)
      echo "error: unknown UH_CI_DOCKER_CACHE='${UH_CI_DOCKER_CACHE}' (expected: gha local none)" >&2
      exit 1
      ;;
  esac
}

meson_build() {
  echo "==> meson setup (${BUILD_DIR}) with g++ [stage=${UH_CI_STAGE} de=${UH_CI_DE}]"
  rm -rf "${BUILD_DIR}"
  CC=gcc CXX=g++ meson setup "${BUILD_DIR}"
  echo "==> ninja build"
  ninja -C "${BUILD_DIR}"
}

run_clang_tidy() {
  echo "==> clang-tidy (with libstdc++ isystem paths for clang 21 on resolute)"
  CXX_INC="$(ls -d /usr/include/c++/* | sort -V | tail -1)"
  CXX_MULTIARCH="$(ls -d /usr/include/*-linux-gnu/c++/* 2>/dev/null | sort -V | tail -1 || true)"
  EXTRA_ARGS=(--extra-arg=-std=c++17 --extra-arg="-isystem${CXX_INC}")
  if [[ -n "${CXX_MULTIARCH}" ]]; then
    EXTRA_ARGS+=(--extra-arg="-isystem${CXX_MULTIARCH}")
  fi
  mapfile -t SOURCES < <(find ubuntu-hello/src/pam -name '*.cc' | sort)
  SOURCES+=("tests/pam_aes_gcm_uh1_test.cc")
  clang-tidy -p "${BUILD_DIR}" "${EXTRA_ARGS[@]}" "${SOURCES[@]}"
}

run_py_compile() {
  echo "==> Python syntax lint (py_compile)"
  python3 -c "import sys, py_compile, glob; [py_compile.compile(f, doraise=True) for f in glob.glob('**/*.py', recursive=True) if not any(p in f for p in ['.git', '.pytest_cache', 'build', 'builddir', '__pycache__'])]"
}

run_i18n_lint() {
  echo "==> JSON + gettext catalog lint (scripts/i18n-lint.py)"
  python3 scripts/i18n-lint.py
}

run_pytest_coverage() {
  # Isolate coverage DB per build dir (safe if stages ever overlap on one mount).
  export COVERAGE_FILE="${BUILD_DIR}/.coverage"
  rm -f "${COVERAGE_FILE}" "${COVERAGE_FILE}".*
  echo "==> pytest (project coverage >= 90%) [COVERAGE_FILE=${COVERAGE_FILE}]"
  # Ignore real-GTK E2E (compat-only under xvfb).
  pytest --cov=ubuntu-hello-gtk --cov=ubuntu-hello --cov-fail-under=90 tests/ --ignore=tests/e2e
  # pytest-cov can round the printed % while still being under the floor; enforce with coverage CLI.
  python3 -m coverage report --data-file="${COVERAGE_FILE}" --precision=2 --fail-under=90
  echo "==> pytest keyring feature coverage == 100%"
  pytest tests/test_keyring_crypto.py tests/test_cli_keyring_aes.py tests/test_keyring_restore.py tests/test_gtk_tabs.py tests/test_onboarding.py \
    --cov=keyring_crypto \
    --cov=keyring_restore \
    --cov=cli.keyring \
    --cov=tab_keyring \
    --cov-branch \
    --cov-report=term-missing \
    --cov-fail-under=100
}

run_pytest_compat() {
  echo "==> pytest (compat / no coverage floors) [stage=compat de=${UH_CI_DE}]"
  pytest tests/ --ignore=tests/e2e
  echo "==> Settings E2E / UI smoke (real GTK + xvfb) [stage=compat de=${UH_CI_DE}]"
  # Fail-fast: every UH_CI_DE cell must pass Settings E2E (not mocks-only).
  UH_REAL_GTK=1 xvfb-run -a pytest tests/e2e/ -v --tb=short
}

run_meson_tests() {
  echo "==> meson test pam-aes-gcm-uh1 pam-face-skip"
  meson test -C "${BUILD_DIR}" pam-aes-gcm-uh1 pam-face-skip --print-errorlogs --verbose
}

run_inside() {
  cd /src
  case "${UH_CI_STAGE}" in
    lint)
      meson_build
      run_clang_tidy
      run_py_compile
      run_i18n_lint
      ;;
    coverage)
      meson_build
      run_pytest_coverage
      run_meson_tests
      ;;
    compat)
      meson_build
      run_py_compile
      run_pytest_compat
      run_meson_tests
      ;;
  esac
  echo "==> CI checks passed [stage=${UH_CI_STAGE} de=${UH_CI_DE}]"
}

resolve_dockerfile_and_image

if [[ "${1:-}" == "--inside" ]]; then
  run_inside
  exit 0
fi

cd "${ROOT}"
if [[ ! -f "${DOCKERFILE}" ]]; then
  echo "error: missing ${DOCKERFILE} for UH_CI_STAGE=${UH_CI_STAGE} UH_CI_DE=${UH_CI_DE}" >&2
  exit 1
fi
build_ci_image
echo "==> docker run CI suite (BUILD_DIR=${BUILD_DIR})"
docker run --rm \
  -e "UH_CI_STAGE=${UH_CI_STAGE}" \
  -e "UH_CI_DE=${UH_CI_DE}" \
  -e "UBUNTU_HELLO_CI_BUILD_DIR=${BUILD_DIR}" \
  -v "${ROOT}:/src:rw" \
  -w /src \
  "${IMAGE}" \
  ./scripts/ci-docker.sh --inside
