#!/usr/bin/env bash
# Connect this machine and GitHub Actions to the Snap Store. Run once, then again
# when the CI credentials expire (one year by default). Safe to re-run.
#
# Signs in to Ubuntu One (interactive), registers the snap name, and stores CI
# credentials limited to uploading and releasing this snap as a repository secret.
set -euo pipefail

SNAP_NAME="linux-ubuntu-hello"
GITHUB_REPO="ventura8/ubuntu-hello"
CI_ACLS="package_access,package_push,package_update,package_release"

# snapcraft login refuses to run while this is set.
unset SNAPCRAFT_STORE_CREDENTIALS

if ! snapcraft whoami >/dev/null 2>&1; then
	echo "==> Sign in to the Snap Store (this machine)"
	snapcraft login
fi

if snapcraft names | awk '{print $1}' | grep -qx "${SNAP_NAME}"; then
	echo "==> ${SNAP_NAME} is already registered to this account"
else
	echo "==> Registering ${SNAP_NAME}"
	snapcraft register --yes "${SNAP_NAME}"
fi

# export-login refuses to overwrite a file, so give it a path that does not exist yet.
credentials_dir="$(mktemp -d)"
trap 'rm -rf "${credentials_dir}"' EXIT
credentials="${credentials_dir}/credentials"

echo "==> Sign in once more to create the CI credentials"
snapcraft export-login --snaps="${SNAP_NAME}" --channels=stable --acls="${CI_ACLS}" "${credentials}"
gh secret set SNAPCRAFT_STORE_CREDENTIALS --repo "${GITHUB_REPO}" <"${credentials}"

echo "==> Done: snapcraft is signed in and GitHub secret SNAPCRAFT_STORE_CREDENTIALS is set"
