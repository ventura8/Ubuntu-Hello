#!/usr/bin/env python3
import os
import sys
import urllib.request
import bz2
import shutil

def main():
    if len(sys.argv) < 2:
        print("Usage: download_models.py <dlib_data_dir>")
        sys.exit(1)

    # Skip download on dry run
    if os.environ.get('MESON_INSTALL_DRY_RUN'):
        print("Dry run: skipping model download.")
        sys.exit(0)

    dlib_data_dir = sys.argv[1]
    destdir = os.environ.get('DESTDIR', '')
    if destdir:
        target_dir = os.path.join(destdir, dlib_data_dir.lstrip(os.sep))
    else:
        target_dir = dlib_data_dir

    os.makedirs(target_dir, exist_ok=True)

    models = [
        {
            'filename': 'dlib_face_recognition_resnet_model_v1.dat',
            'url': 'https://github.com/davisking/dlib-models/raw/master/dlib_face_recognition_resnet_model_v1.dat.bz2'
        },
        {
            'filename': 'mmod_human_face_detector.dat',
            'url': 'https://github.com/davisking/dlib-models/raw/master/mmod_human_face_detector.dat.bz2'
        },
        {
            'filename': 'shape_predictor_5_face_landmarks.dat',
            'url': 'https://github.com/davisking/dlib-models/raw/master/shape_predictor_5_face_landmarks.dat.bz2'
        }
    ]

    print("Downloading dlib models to:", target_dir)

    for model in models:
        target_path = os.path.join(target_dir, model['filename'])
        if os.path.exists(target_path):
            print(f"{model['filename']} already exists, skipping.")
            continue

        url = model['url']
        temp_path = target_path + '.tmp'
        try:
            print(f"Downloading {url}...")
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req) as response:
                with open(temp_path, 'wb') as f_temp:
                    shutil.copyfileobj(response, f_temp)
            
            print(f"Decompressing {temp_path} to {target_path}...")
            with bz2.BZ2File(temp_path) as fr, open(target_path, 'wb') as fw:
                shutil.copyfileobj(fr, fw)
            print(f"Successfully installed {model['filename']}.")
        except Exception as e:
            print(f"WARNING: Failed to download or extract {model['filename']}: {e}")
            print("You might need to download the models manually using install.sh.")
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

if __name__ == '__main__':
    main()
