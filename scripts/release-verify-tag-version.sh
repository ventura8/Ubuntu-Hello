#!/usr/bin/env bash
# Validate GITHUB_REF_NAME is vN.N.N and matches repo-root VERSION before release builds.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

tag="${GITHUB_REF_NAME:-}"
if [[ ! "${tag}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
	echo "error: release tag must match vN.N.N, got: ${tag:-<empty>}" >&2
	exit 1
fi

tag_ver="${tag#v}"
repo_ver="$(python3 scripts/read-version.py)"
if [[ "${tag_ver}" != "${repo_ver}" ]]; then
	echo "error: tag version ${tag_ver} != VERSION file ${repo_ver}" >&2
	exit 1
fi

if [[ -n "${GITHUB_ENV:-}" ]]; then
	echo "VERSION=${tag_ver}" >>"${GITHUB_ENV}"
fi
echo "Extracted version: ${tag_ver}"
