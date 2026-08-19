from pathlib import PurePath
import paths


def config_file_path() -> str:
    """Return the path to the config file"""
    return str(paths.config_dir / "config.ini")


def user_models_dir_path() -> PurePath:
    """Return the path to the user models directory"""
    return paths.user_models_dir


def logo_path() -> str:
    """Return the path to the logo file"""
    return str(paths.data_dir / "logo.png")


def onboarding_wireframe_path() -> str:
    """Return the path to the onboarding wireframe file"""
    return str(paths.data_dir / "onboarding.glade")


def main_window_wireframe_path() -> str:
    """Return the path to the main window wireframe file"""
    return str(paths.data_dir / "main.glade")


def dlib_data_dir_path() -> PurePath:
    """Return the path to the dlib data directory"""
    return paths.dlib_data_dir


def keyring_keys_dir_path() -> str:
    """Return the path to the keyring keys directory"""
    return "/etc/ubuntu-hello/keyring-keys"


def tpm_keys_dir_path() -> str:
    """Return the path to the TPM keys directory"""
    return "/etc/ubuntu-hello/tpm-keys"


def keyring_pending_dir_path() -> str:
    """Return the path to the keyring caching pending directory"""
    return "/etc/ubuntu-hello/keyring-caching-pending"


def css_style_path() -> str:
    """Return the path to the CSS style sheet"""
    return str(paths.data_dir / "style.css")


_css_initialized = False


def init_custom_css() -> None:
    """Initialize custom CSS once for the application."""
    global _css_initialized
    if _css_initialized:
        return
    import os
    import sys
    import traceback
    try:
        from gi.repository import Gtk as gtk
        from gi.repository import Gdk as gdk

        css_provider = gtk.CssProvider()

        # Search paths
        local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")
        installed_path = css_style_path()

        loaded = False
        for path in (local_path, installed_path):
            if os.path.exists(path):
                try:
                    css_provider.load_from_path(path)
                    loaded = True
                    print(f"Loaded CSS stylesheet from: {path}")
                    break
                except Exception as e:
                    print(f"Error loading CSS from {path}: {e}", file=sys.stderr)

        if loaded:
            screen = gdk.Screen.get_default()
            if screen:
                gtk.StyleContext.add_provider_for_screen(
                    screen,
                    css_provider,
                    gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
                _css_initialized = True
    except Exception as e:
        print(f"Error initializing custom CSS: {e}\n{traceback.format_exc()}", file=sys.stderr)


def load_custom_css() -> None:
    """Load the custom CSS styling dynamically"""
    init_custom_css()

