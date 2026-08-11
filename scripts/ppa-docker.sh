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

echo "==> Importing GPG key"
GPG_KEY_FILE=$(mktemp)
printf '%s\n' "${GPG_PRIVATE_KEY}" > "$GPG_KEY_FILE"
gpg --batch --import "$GPG_KEY_FILE"
rm -f "$GPG_KEY_FILE"
GPG_FINGERPRINT=$(gpg --list-secret-keys --with-colons | awk -F: '/^fpr:/ {print $10; exit}')
echo "${GPG_FINGERPRINT}:6:" | gpg --import-ownertrust
GPG_KEY_ID=$(gpg --list-secret-keys --with-colons --keyid-format long | awk -F: '/^sec:/ {print $5; exit}')
echo "GPG_KEY_ID=${GPG_KEY_ID}"

PPA_VERSION="${VERSION}-1ppa1~${DISTRO}1"
cat > debian/changelog <<EOF
ubuntu-hello (${PPA_VERSION}) ${DISTRO}; urgency=medium

  * Release ${VERSION}
  * See https://github.com/ventura8/ubuntu-hello/releases/tag/v${VERSION}

 -- ${MAINTAINER_NAME} <${MAINTAINER_EMAIL}>  $(date -R)
EOF
echo "--- debian/changelog ---"
cat debian/changelog

cat << 'EOF' > sign-code.sh
#!/bin/sh
exec gpg --batch --pinentry-mode loopback --passphrase "$GPG_PASSPHRASE" "$@"
EOF
chmod +x sign-code.sh

echo "==> Building signed source package"
dpkg-buildpackage -S -sa -k"$GPG_KEY_ID" --sign-command=./sign-code.sh

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
