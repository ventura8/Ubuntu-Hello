#!/usr/bin/env bash
# Build signed source package and (optionally) upload to PPA inside Ubuntu 26.04.
# Required env: VERSION, DISTRO, MAINTAINER_NAME, MAINTAINER_EMAIL
# For upload: GPG_PRIVATE_KEY, GPG_PASSPHRASE; set UPLOAD_PPA=1
# For binary .deb only: set BUILD_BINARY=1
set -euo pipefail

cd /src

MODE="${1:-source}"

if [[ "${MODE}" == "binary" || "${BUILD_BINARY:-0}" == "1" ]]; then
  echo "==> Building binary packages (dpkg-buildpackage -b)"
  dpkg-buildpackage -b -us -uc
  mkdir -p /src/artifacts
  cp ../*.deb /src/artifacts/ 2>/dev/null || true
  ls -la /src/artifacts/
  exit 0
fi

: "${VERSION:?VERSION required}"
: "${DISTRO:?DISTRO required}"
: "${MAINTAINER_NAME:?MAINTAINER_NAME required}"
: "${MAINTAINER_EMAIL:?MAINTAINER_EMAIL required}"
: "${GPG_PRIVATE_KEY:?GPG_PRIVATE_KEY required}"
: "${GPG_PASSPHRASE?GPG_PASSPHRASE required (may be empty)}"

echo "==> Importing GPG key"
export GNUPGHOME="${GNUPGHOME:-$(mktemp -d /tmp/uh-gnupg.XXXXXX)}"
GPG_KEY_FILE=$(mktemp)
# GitHub secrets sometimes store armored keys with literal \n sequences.
printf '%s\n' "${GPG_PRIVATE_KEY}" | sed 's/\\n/\n/g' > "$GPG_KEY_FILE"
gpg --batch --import "$GPG_KEY_FILE"
rm -f "$GPG_KEY_FILE"

GPG_FINGERPRINT=$(gpg --list-secret-keys --with-colons | awk -F: '/^fpr:/ {print $10; exit}')
if [[ -z "${GPG_FINGERPRINT}" ]]; then
  echo "ERROR: no secret key fingerprint after import" >&2
  exit 1
fi
echo "${GPG_FINGERPRINT}:6:" | gpg --import-ownertrust
echo "GPG_FINGERPRINT=${GPG_FINGERPRINT}"

PPA_VERSION="${VERSION}-1ppa1~${DISTRO}1"
cat > debian/changelog <<EOF
ubuntu-hello (${PPA_VERSION}) ${DISTRO}; urgency=medium

  * Release ${VERSION}
  * See https://github.com/ventura8/ubuntu-hello/releases/tag/v${VERSION}

 -- ${MAINTAINER_NAME} <${MAINTAINER_EMAIL}>  $(date -R)
EOF
echo "--- debian/changelog ---"
cat debian/changelog

# Passphrase-capable gpg wrapper. Must be used with --sign-backend=gpg:
# dpkg auto prefers Sequoia-style args (--cleartext/--signer) when a custom
# --sign-command is set and a key id needs a keystore; those args are invalid
# for classic gpg and surface as "key is not signature-capable".
SIGN_CMD=$(mktemp /tmp/uh-sign-XXXXXX.sh)
cat > "$SIGN_CMD" <<'EOF'
#!/bin/sh
exec gpg --batch --pinentry-mode loopback --passphrase "$GPG_PASSPHRASE" "$@"
EOF
chmod 700 "$SIGN_CMD"

echo "==> Building signed source package (OpenPGP backend=gpg)"
dpkg-buildpackage -S -sa \
  --sign-backend=gpg \
  --sign-command="$SIGN_CMD" \
  -k"$GPG_FINGERPRINT"

rm -f "$SIGN_CMD"

if [[ "${UPLOAD_PPA:-0}" == "1" ]]; then
  cat > ~/.dput.cf <<EOF
[ubuntu-hello-ppa]
fqdn = ppa.launchpad.net
method = ftp
incoming = ~ventura8/ubuntu-hello/ubuntu/
login = anonymous
allow_unsigned_uploads = 0
EOF
  CHANGES_FILE=$(ls ../*.changes | head -1)
  echo "Uploading: $CHANGES_FILE"
  dput ubuntu-hello-ppa "$CHANGES_FILE"
fi

echo "==> PPA source build complete"
