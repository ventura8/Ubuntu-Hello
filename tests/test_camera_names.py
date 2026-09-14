import os
import camera_names as cn

UDEV = b"""E: ID_V4L_PRODUCT=USB2.0 HD UVC WebCam: USB2.0 HD
E: ID_MODEL=USB2.0_HD_UVC_WebCam
E: ID_MODEL_ENC=USB2.0\\x20HD\\x20UVC\\x20WebCam
E: ID_VENDOR=Azurewave
E: ID_VENDOR_ID=13d3
"""


def test_parse_udev_properties_handles_prefix_bytes_and_quotes():
	props = cn.parse_udev_properties(UDEV)
	assert props["ID_VENDOR"] == "Azurewave" and props["ID_MODEL"] == "USB2.0_HD_UVC_WebCam"
	assert cn.parse_udev_properties('ID_MODEL="Quoted Cam"\njunk line\n')["ID_MODEL"] == "Quoted Cam"
	assert cn.parse_udev_properties(None) == {}


def test_pretty_name_is_brand_and_model():
	assert cn.pretty_camera_name(cn.parse_udev_properties(UDEV)) == "Azurewave USB2.0 HD UVC WebCam"


def test_pretty_name_prefers_hwdb_names_and_dedupes_vendor():
	props = {"ID_VENDOR_FROM_DATABASE": "Logitech, Inc.", "ID_MODEL_FROM_DATABASE": "Logitech, Inc. HD Pro Webcam C920"}
	assert cn.pretty_camera_name(props) == "Logitech, Inc. HD Pro Webcam C920"
	props = {"ID_VENDOR": "Logitech", "ID_MODEL": "HD_Pro_Webcam_C920"}
	assert cn.pretty_camera_name(props) == "Logitech HD Pro Webcam C920"


def test_pretty_name_falls_back_to_v4l_product_or_path():
	assert cn.pretty_camera_name({"ID_V4L_PRODUCT": "IR Camera Pro: IR Camera Pro"}) == "IR Camera Pro"
	assert cn.pretty_camera_name({"ID_VENDOR": "Generic", "ID_MODEL": "USB Camera", "ID_V4L_PRODUCT": "Integrated IR Camera"}) == "Integrated IR Camera"
	assert cn.pretty_camera_name({}, fallback="video0") == "video0"


def test_describe_camera_adds_short_device_label(monkeypatch):
	assert cn.describe_camera("/dev/v4l/by-path/pci-0000:00:14.0-usb-0:9:1.0-video-index1", UDEV) == "Azurewave USB2.0 HD UVC WebCam (video-index1)"
	assert cn.describe_camera("/dev/video2", b"") == "video2"
	monkeypatch.setattr(cn.subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError("no udevadm")))
	assert cn.describe_camera("/dev/video3") == "video3"
	monkeypatch.setattr(cn.subprocess, "check_output", lambda *a, **k: UDEV)
	assert cn.describe_camera("/dev/video3") == "Azurewave USB2.0 HD UVC WebCam (video3)"


def test_list_capture_devices_collapses_aliases_and_skips_metadata_nodes(tmp_path, monkeypatch):
	dev = tmp_path / "dev"; (dev).mkdir()
	for n in range(4):
		(dev / f"video{n}").write_text("")
	bp = dev / "by-path"; bp.mkdir()
	links = {"pci-usb-0:9:1.0-video-index0": "video0", "pci-usb-0:9:1.0-video-index1": "video1",
	         "pci-usbv2-0:9:1.0-video-index0": "video0", "pci-usbv2-0:9:1.0-video-index1": "video1",
	         "pci-usb-0:9:1.2-video-index0": "video2", "pci-usb-0:9:1.2-video-index1": "video3"}
	for name, target in links.items():
		(bp / name).symlink_to(dev / target)
	caps = {"video0": ":capture:", "video1": ":", "video2": ":capture:", "video3": ":"}

	def props(path):
		node = os.path.basename(os.path.realpath(path))
		return {"ID_VENDOR": "Azurewave", "ID_MODEL": "USB2.0_HD_UVC_WebCam", "ID_V4L_CAPABILITIES": caps[node]}
	monkeypatch.setattr(cn, "_udev_props_for", props)
	entries = cn.list_capture_devices(str(bp), str(dev / "video*"))
	assert [os.path.basename(os.path.realpath(p)) for p, _ in entries] == ["video0", "video2"]
	assert all(p.startswith(str(bp)) for p, _ in entries)            # stable by-path value kept
	assert [l for _, l in entries] == ["Azurewave USB2.0 HD UVC WebCam (video0)", "Azurewave USB2.0 HD UVC WebCam (video2)"]


def test_list_capture_devices_without_udev_keeps_everything_and_plain_labels(tmp_path, monkeypatch):
	dev = tmp_path / "dev"; dev.mkdir(); (dev / "video0").write_text("")
	monkeypatch.setattr(cn, "_udev_props_for", lambda path: {})
	assert cn.list_capture_devices(str(tmp_path / "missing"), str(dev / "video*")) == [(str(dev / "video0"), "video0")]


def test_same_device_matches_aliases(tmp_path):
	node = tmp_path / "video0"; node.write_text(""); link = tmp_path / "by-path-link"; link.symlink_to(node)
	assert cn.same_device(str(link), str(node)) and cn.same_device(str(node), str(node))
	assert not cn.same_device(str(node), str(tmp_path / "other")) and not cn.same_device("", str(node))
