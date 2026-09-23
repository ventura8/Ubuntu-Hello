#!/bin/bash
# Download and verify the dlib models the face recogniser needs.
#
# Run by the setup wizard (onboarding.py) as root, from the dlib-data directory.
# A non-zero exit is reported to the user as a failed download.
#
# Every archive is checked against a pinned SHA-256 before it is unpacked -- the
# same pins scripts/package-configure.sh uses for the packaged install path. That
# is the integrity guarantee: a substituted or truncated archive is rejected no
# matter how it was delivered.
#
# Redirects are refused as a second layer. The canonical raw.githubusercontent.com
# URLs serve the files directly, so nothing legitimate needs one. Note that GNU
# Wget's --https-only only restricts *recursive* downloads and cannot pin the
# scheme of a redirect, which is why wget gets --max-redirect=0 instead.
set -eu

BASE_URL="https://raw.githubusercontent.com/davisking/dlib-models/master"

ARCHIVES=(
	dlib_face_recognition_resnet_model_v1.dat.bz2
	mmod_human_face_detector.dat.bz2
	shape_predictor_5_face_landmarks.dat.bz2
)

declare -A SHA256=(
	[dlib_face_recognition_resnet_model_v1.dat.bz2]=abb1f61041e434465855ce81c2bd546e830d28bcbed8d27ffbe5bb408b11553a
	[mmod_human_face_detector.dat.bz2]=db9e9e40f092c118d5eb3e643935b216838170793559515541c56a2b50d9fc84
	[shape_predictor_5_face_landmarks.dat.bz2]=6e787bbebf5c9efdb793f6cd1f023230c4413306605f24f299f12869f95aa472
)

# Show wget's progress bar where this wget supports it.
WGET_PROGRESS=()
if hash wget 2>/dev/null && wget --help | grep -q -- "--show-progress"; then
	WGET_PROGRESS=(-q --show-progress)
fi

fetch() {
	local url="$1"
	local output="$2"
	if hash wget 2>/dev/null; then
		wget ${WGET_PROGRESS[@]+"${WGET_PROGRESS[@]}"} --max-redirect=0 --tries 5 -O "$output" "$url"
	else
		curl --proto "=https" --proto-redir "=https" --max-redirs 0 --fail --show-error --retry 5 -o "$output" "$url"
	fi
}

tmp=""
trap 'rm -f -- "$tmp"' EXIT

echo "Downloading ${#ARCHIVES[@]} required data files..."

for archive in "${ARCHIVES[@]}"; do
	tmp="$(mktemp "./.${archive}.XXXXXX")"

	if ! fetch "$BASE_URL/$archive" "$tmp"; then
		echo "Failed to download $archive" >&2
		exit 1
	fi

	if ! echo "${SHA256[$archive]}  $tmp" | sha256sum --check --status; then
		echo "Checksum mismatch for $archive -- refusing to install it" >&2
		exit 1
	fi

	mv -f -- "$tmp" "$archive"
	tmp=""
done

# Uncompress the data files and delete the original archives
echo " "
echo "Unpacking..."
bzip2 -d -f -- "${ARCHIVES[@]}"
