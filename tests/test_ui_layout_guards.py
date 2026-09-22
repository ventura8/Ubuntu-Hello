"""Regression guards for layout bugs found in manual GTK 4 testing (parsed from the .ui files)."""
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "ubuntu-hello-gtk" / "src"
UI = {name: ET.parse(SRC / name).getroot() for name in ("main.ui", "onboarding.ui")}
CARD_HOSTS = {"box2", "keyring_password_box", "notifications_card", "security_card", "keyring_card", "language_row"}


def _objects(root):
	for obj in root.iter("object"):
		yield obj


def _prop(obj, name):
	for prop in obj.findall("property"):
		if prop.get("name") in (name, name.replace("-", "_")):
			return (prop.text or "").strip()
	return None


def _classes(obj):
	style = obj.find("style")
	return {c.get("name") for c in style.findall("class")} if style is not None else set()


def _by_id(root, obj_id):
	for obj in _objects(root):
		if obj.get("id") == obj_id:
			return obj
	raise AssertionError(f"no object {obj_id}")


def test_camera_previews_and_logos_are_pictures_not_icons():
	"""GtkImage scales pictures down to icon size in GTK 4 (the 'thumbnail preview' bug)."""
	for ui, ids in (("onboarding.ui", ("preview_image", "slide4_preview_image", "image2")),
	                ("main.ui", ("opencvimage", "image1"))):
		for obj_id in ids:
			obj = _by_id(UI[ui], obj_id)
			assert obj.get("class") == "GtkPicture", f"{ui}:{obj_id} must be a GtkPicture"
	for ui, obj_id in (("onboarding.ui", "preview_image"), ("onboarding.ui", "slide4_preview_image"), ("main.ui", "opencvimage")):
		obj = _by_id(UI[ui], obj_id)
		assert _prop(obj, "content-fit") == "contain"
		assert _prop(obj, "can-shrink") == "1"


def test_camera_page_preview_leaves_room_for_the_device_list():
	preview = _by_id(UI["onboarding.ui"], "preview_image")
	assert _prop(preview, "vexpand") == "0", "camera-page preview must not eat the device list's space"


def test_download_output_is_centered():
	label = _by_id(UI["onboarding.ui"], "downloadoutputlabel")
	assert _prop(label, "hexpand") == "1"
	assert _prop(label, "vexpand") == "1"
	assert _prop(label, "halign") == "center"
	assert _prop(label, "valign") == "center"


def test_every_wrapping_label_caps_its_width():
	"""GTK 4 wrap labels request their full-text width: the window grew to the screen."""
	for ui, root in UI.items():
		for obj in _objects(root):
			if obj.get("class") == "GtkLabel" and _prop(obj, "wrap") == "1":
				cap = _prop(obj, "max-width-chars")
				assert cap, f"{ui}:{obj.get('id')} wrapping label without max-width-chars"
				assert int(cap) <= 80, f"{ui}:{obj.get('id')} wrapping label without max-width-chars"


def test_card_class_only_on_group_containers():
	"""A restyle script once put .uh-card on every descendant (nested boxes on the certainty page)."""
	for ui, root in UI.items():
		for obj in _objects(root):
			if "uh-card" in _classes(obj):
				assert obj.get("class") == "GtkBox", f"{ui}: uh-card on {obj.get('class')} {obj.get('id')}"
				assert obj.get("id") in CARD_HOSTS, f"{ui}: uh-card on {obj.get('class')} {obj.get('id')}"
	assert {obj.get("id") for root in UI.values() for obj in _objects(root) if "uh-card" in _classes(obj)} == CARD_HOSTS


def test_every_page_uses_the_shared_page_idiom():
	for slide in ("slide0", "slide1", "slide2", "slide3", "slide4", "slide5", "slide6", "slide7"):
		classes = [c for obj in _by_id(UI["onboarding.ui"], slide).iter("object") for c in _classes(obj)]
		assert "uh-page-title" in classes, f"{slide} has no uh-page-title"
	notebook = _by_id(UI["main.ui"], "notebook")
	pages = [p for p in notebook.iter("object") if p.get("class") == "GtkNotebookPage"]
	assert len(pages) == 7   # Models, Video, Notifications, Security, Keyring, Language, About
	for page in pages:
		classes = [c for obj in page.iter("object") for c in _classes(obj)]
		assert "uh-page-title" in classes, "Settings page without uh-page-title"


def test_no_hard_coded_colours_or_fonts():
	for ui, root in UI.items():
		for attr in root.iter("attribute"):
			assert attr.get("name") not in ("foreground", "background", "underline-color", "font-desc", "family"), f"{ui}: hard-coded {attr.get('name')}"
	css = (SRC / "style.css").read_text(encoding="utf-8")
	assert "font-family: \"Ubuntu\"" not in css
	assert not re.search(r"font-size:\s*\d+px", css), "px font sizes ignore the user's text scaling"
	colours = set(re.findall(r"#[0-9a-fA-F]{6}\b", css))
	assert colours <= {"#000000"}, f"theme colours must come from @theme_* / accent: {colours}"


def test_icons_exist_in_gtk_default_theme():
	"""Under pkexec the app may run with GTK's default (Adwaita) icons: Yaru-only names showed a broken glyph."""
	if not os.path.isdir("/usr/share/icons/Adwaita"):
		pytest.skip("Adwaita icon theme not installed")
	missing = []
	for ui, root in UI.items():
		for obj in _objects(root):
			icon = _prop(obj, "icon-name")
			if icon and icon != "ubuntu-hello-gtk":
				found = any(f.startswith(icon + ".") for _d, _s, files in os.walk("/usr/share/icons/Adwaita") for f in files)
				if not found:
					missing.append(f"{ui}: {icon}")
	assert missing == [], missing
