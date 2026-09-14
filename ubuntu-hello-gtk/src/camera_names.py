"""Human-readable camera names ("Azurewave USB2.0 HD UVC WebCam") from udev properties."""
from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

_GENERIC = {"webcam", "camera", "usb", "video", "device", "integrated", "generic", "hd", "uvc", "web", "cam"}


def parse_udev_properties(output) -> dict:
	"""KEY=VALUE map from `udevadm info` output (plain or `E: ` prefixed, str or bytes)."""
	if isinstance(output, bytes):
		output = output.decode("utf-8", "replace")
	props = {}
	for line in (output or "").splitlines():
		line = line.strip()
		if line.startswith("E: "):
			line = line[3:]
		key, sep, value = line.partition("=")
		if sep and key and re.fullmatch(r"[A-Z0-9_]+", key):
			props[key] = value.strip().strip('"')
	return props


def _clean(value: str) -> str:
	value = (value or "").replace("_", " ").replace("\\x20", " ")
	value = re.sub(r"\s+", " ", value).strip(" :-")
	return value


def pretty_camera_name(props: dict, fallback: str = "") -> str:
	"""Brand + model, de-duplicated, from udev properties; *fallback* when nothing usable."""
	vendor = _clean(props.get("ID_VENDOR_FROM_DATABASE") or props.get("ID_VENDOR") or props.get("ID_USB_VENDOR") or "")
	model = _clean(props.get("ID_MODEL_FROM_DATABASE") or props.get("ID_MODEL") or props.get("ID_USB_MODEL") or "")
	if not model:
		# V4L product string often repeats itself after a colon: "X: X" -> "X"
		product = _clean(props.get("ID_V4L_PRODUCT") or "")
		model = product.split(":")[0].strip() if product else ""
	if vendor and model and model.lower().startswith(vendor.lower()):
		vendor = ""
	name = " ".join(part for part in (vendor, model) if part)
	# Vendor-only or all-generic names are worse than the kernel's product string
	if not name or set(re.findall(r"[a-z0-9.]+", name.lower())) <= _GENERIC:
		product = _clean(props.get("ID_V4L_PRODUCT") or "")
		name = product.split(":")[0].strip() if product else name
	return name or fallback


def short_device_label(device_path: str) -> str:
	"""Compact disambiguator: 'video-index1' for by-path links, 'video2' for /dev/videoN."""
	base = os.path.basename(device_path or "")
	match = re.search(r"(video-index\d+)$", base)
	return match.group(1) if match else base


def describe_camera(device_path: str, udev_output=None) -> str:
	"""'Azurewave USB2.0 HD UVC WebCam (video-index0)' for *device_path* (never raises)."""
	if udev_output is None:
		try:
			udev_output = subprocess.check_output(
				["udevadm", "info", "--query=property", "--name", os.path.realpath(device_path)], timeout=5)
		except Exception:
			udev_output = ""
	name = pretty_camera_name(parse_udev_properties(udev_output), fallback=os.path.basename(device_path))
	suffix = short_device_label(device_path)
	return f"{name} ({suffix})" if suffix and suffix != name else name


def _udev_props_for(device_path: str) -> dict:
	try:
		out = subprocess.check_output(
			["udevadm", "info", "--query=property", "--name", os.path.realpath(device_path)], timeout=5)
	except Exception:
		return {}
	return parse_udev_properties(out)


def list_capture_devices(by_path_dir: str = "/dev/v4l/by-path", dev_glob: str = "/dev/video*"):
	"""One entry per real capture node: [(stable path, label), ...].

	A camera shows up under several /dev/v4l/by-path links (usb and usbv2
	aliases) plus /dev/videoN, and every USB camera also has a *metadata*
	node (no `:capture:` in udev's ID_V4L_CAPABILITIES) — listing all of them
	showed "the same camera" 4-8 times. Aliases collapse by realpath (the
	first by-path link wins as the stable stored value), metadata nodes are
	skipped, and labels only carry the (videoN) suffix when a brand/model
	name repeats (e.g. an RGB and an IR sensor of one webcam).
	"""
	import glob
	candidates = []
	try:
		candidates += [os.path.join(by_path_dir, d) for d in sorted(os.listdir(by_path_dir))]
	except Exception:
		pass
	candidates += sorted(glob.glob(dev_glob))
	seen = set()
	nodes = []   # (path, name)
	for path in candidates:
		real = os.path.realpath(path)
		if real in seen:
			continue
		seen.add(real)
		props = _udev_props_for(path)
		caps = props.get("ID_V4L_CAPABILITIES")
		if caps is not None and ":capture:" not in caps:
			continue   # metadata / output node, not a camera
		nodes.append((path, pretty_camera_name(props, fallback=os.path.basename(path))))
	counts = {}
	for _path, name in nodes:
		counts[name] = counts.get(name, 0) + 1
	entries = []
	for path, name in nodes:
		label = f"{name} ({os.path.basename(os.path.realpath(path))})" if counts[name] > 1 else name
		entries.append((path, label))
	return entries


def same_device(path_a: str, path_b: str) -> bool:
	"""True when two paths (aliases) point at the same video node."""
	if not path_a or not path_b:
		return False
	return path_a == path_b or os.path.realpath(path_a) == os.path.realpath(path_b)
