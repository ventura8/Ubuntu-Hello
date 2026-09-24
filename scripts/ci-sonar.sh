#!/usr/bin/env bash
# Run SonarQube Cloud analysis for Ubuntu Hello.
#
# The local entrypoint. CI scans with SonarSource/sonarqube-scan-action as a step
# in the `coverage` job of .github/workflows/check.yml (the action derives the
# pull-request parameters from the Actions context), but calls this script with
# --check-token first so an expired token fails with a message that says so.
#
# Usage:
#   ./scripts/ci-sonar.sh                # validate the token, then scan
#   ./scripts/ci-sonar.sh --check-token  # only validate the token
#
# The scanner runs in Docker (sonarsource/sonar-scanner-cli, version-pinned —
# never ":latest", AGENTS.md §4.8), so nothing has to be installed on the host.
# Analysis settings live in sonar-project.properties at the repo root.
#
# Required:
#   SONAR_TOKEN   — SonarQube Cloud user/project token. Locally: export it, or
#                   put it in .sonar-token at the repo root (gitignored).
#                   In CI: the SONAR_TOKEN repository secret.
#
# Optional:
#   UH_SONAR_COVERAGE=1  — run UH_CI_STAGE=coverage first so
#                          artifacts/coverage/coverage.xml is fresh (slow).
#   UH_SONAR_IMAGE       — override the pinned scanner image.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCANNER_IMAGE="${UH_SONAR_IMAGE:-sonarsource/sonar-scanner-cli:12.2.0.4256_8.1.0}"
COVERAGE_XML="${ROOT}/artifacts/coverage/coverage.xml"
SONAR_HOST="https://sonarcloud.io"

cd "${ROOT}"

load_token() {
  if [[ -z "${SONAR_TOKEN:-}" && -f "${ROOT}/.sonar-token" ]]; then
    SONAR_TOKEN="$(tr -d '[:space:]' < "${ROOT}/.sonar-token")"
    export SONAR_TOKEN
    echo "==> SONAR_TOKEN loaded from .sonar-token"
  fi
  if [[ -z "${SONAR_TOKEN:-}" ]]; then
    echo "error: SONAR_TOKEN is not set." >&2
    echo "       Create a token at https://sonarcloud.io/account/security and either" >&2
    echo "       'export SONAR_TOKEN=...' or write it to ${ROOT}/.sonar-token" >&2
    exit 2
  fi
}

# An expired or revoked token otherwise surfaces deep inside the scanner as a
# generic failure that reads like a broken config. Ask SonarQube Cloud directly.
# /api/authentication/validate answers HTTP 200 either way; the body says
# {"valid":true} or {"valid":false}. If the service cannot be reached at all,
# warn and carry on -- the scan itself is then the authority.
check_token() {
  local answer message
  # The token goes to curl on stdin (-K -), not argv, so it never shows up in ps.
  if ! answer="$(printf 'header = "Authorization: Bearer %s"\n' "${SONAR_TOKEN}" |
      curl --silent --show-error --fail --max-time 20 -K - "${SONAR_HOST}/api/authentication/validate" 2>&1)"; then
    echo "warning: could not reach ${SONAR_HOST} to validate SONAR_TOKEN (${answer}); continuing." >&2
    return 0
  fi

  if [[ "${answer}" == *'"valid":true'* ]]; then
    echo "==> SONAR_TOKEN is valid"
    return 0
  fi

  message="SONAR_TOKEN is invalid or expired. Create a new one at ${SONAR_HOST}/account/security, then update .sonar-token (local) and the SONAR_TOKEN repository secret (CI)."
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::error title=SonarQube Cloud token::${message}"
  fi
  echo "error: ${message}" >&2
  exit 2
}

ensure_coverage() {
  if [[ "${UH_SONAR_COVERAGE:-0}" == "1" ]]; then
    echo "==> refreshing coverage (UH_CI_STAGE=coverage ./scripts/ci-docker.sh)"
    UH_CI_STAGE=coverage "${ROOT}/scripts/ci-docker.sh"
  fi
  if [[ ! -f "${COVERAGE_XML}" ]]; then
    echo "==> note: ${COVERAGE_XML#"${ROOT}"/} is missing — analysing without coverage."
    echo "    Run UH_SONAR_COVERAGE=1 $0, or UH_CI_STAGE=coverage ./scripts/ci-docker.sh first."
  fi
}

run_scanner() {
  local version
  version="$(python3 "${ROOT}/scripts/read-version.py")"
  echo "==> sonar-scanner ${SCANNER_IMAGE} (project version ${version})"
  # Run as root inside the container: UH_CI_STAGE=coverage runs its Docker stage
  # as root and leaves build-ci-*/ dirs mode 0700 root-owned in the work tree.
  # The scanner walks the tree before applying sonar.exclusions, so as a normal
  # user it dies with AccessDeniedException on those. coverage.xml is root-owned
  # for the same reason and has to be readable here too.
  docker run --rm \
    --user 0:0 \
    --env SONAR_TOKEN \
    --env "SONAR_SCANNER_OPTS=-Dsonar.projectVersion=${version}" \
    --volume "${ROOT}:/usr/src" \
    "${SCANNER_IMAGE}"

  # Do not leave the scanner's own workdir root-owned for the next local run.
  if [[ -d "${ROOT}/.scannerwork" ]]; then
    docker run --rm --user 0:0 --volume "${ROOT}:/usr/src" --entrypoint chown \
      "${SCANNER_IMAGE}" -R "$(id -u):$(id -g)" /usr/src/.scannerwork || true
  fi
}

load_token
check_token
if [[ "${1:-}" == "--check-token" ]]; then
  exit 0
fi
ensure_coverage
run_scanner
echo "==> analysis submitted; results: https://sonarcloud.io/project/overview?id=ventura8_Ubuntu-Hello"
