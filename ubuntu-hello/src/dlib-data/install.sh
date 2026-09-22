#!/bin/bash

echo "Downloading 3 required data files..."

# Pin both the initial scheme and every redirect target to HTTPS.
HTTPS_ONLY=(--proto "=https" --proto-redir "=https")

# Check if wget is installed
if hash wget;then
	# Check if wget supports the option to only show the progress bar
	wget --help | grep -q "\--show-progress" && \
		_PROGRESS_OPT="-q --show-progress" || _PROGRESS_OPT=""

	# Download the archives. --https-only / --proto-redir '=https' keep every hop on
	# HTTPS: these are the face-recognition models, so a redirect that silently
	# downgrades to plaintext would be a tamperable download path.
	wget $_PROGRESS_OPT --https-only --tries 5 https://github.com/davisking/dlib-models/raw/master/dlib_face_recognition_resnet_model_v1.dat.bz2
	wget $_PROGRESS_OPT --https-only --tries 5 https://github.com/davisking/dlib-models/raw/master/mmod_human_face_detector.dat.bz2
	wget $_PROGRESS_OPT --https-only --tries 5 https://github.com/davisking/dlib-models/raw/master/shape_predictor_5_face_landmarks.dat.bz2

# Otherwise fall back on curl
else
	curl "${HTTPS_ONLY[@]}" --location --retry 5 --output dlib_face_recognition_resnet_model_v1.dat.bz2 https://github.com/davisking/dlib-models/raw/master/dlib_face_recognition_resnet_model_v1.dat.bz2
	curl "${HTTPS_ONLY[@]}" --location --retry 5 --output mmod_human_face_detector.dat.bz2 https://github.com/davisking/dlib-models/raw/master/mmod_human_face_detector.dat.bz2
	curl "${HTTPS_ONLY[@]}" --location --retry 5 --output shape_predictor_5_face_landmarks.dat.bz2 https://github.com/davisking/dlib-models/raw/master/shape_predictor_5_face_landmarks.dat.bz2
fi

# Uncompress the data files and delete the original archive
echo " "
echo "Unpacking..."
bzip2 -d -f ./*.bz2
