"""Tests for paths_factory.py modules (both ubuntu-hello and ubuntu-hello-gtk)."""
import os
import sys
from pathlib import PurePath
from unittest.mock import patch, MagicMock
import importlib
import pytest


class TestPathsFactoryGtk:
    """Tests for ubuntu-hello-gtk/src/paths_factory.py."""

    def _load_gtk_paths_factory(self):
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths_factory_path = os.path.join(src_dir, "ubuntu-hello-gtk", "src", "paths_factory.py")
        spec = importlib.util.spec_from_file_location(
            "paths_factory_gtk",
            paths_factory_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_config_file_path(self):
        pf = self._load_gtk_paths_factory()
        result = pf.config_file_path()
        assert isinstance(result, str)
        assert "config.ini" in result

    def test_user_models_dir_path(self):
        pf = self._load_gtk_paths_factory()
        result = pf.user_models_dir_path()
        assert isinstance(result, PurePath)

    def test_logo_path(self):
        pf = self._load_gtk_paths_factory()
        result = pf.logo_path()
        assert "logo.png" in result

    def test_onboarding_wireframe_path(self):
        pf = self._load_gtk_paths_factory()
        result = pf.onboarding_wireframe_path()
        assert "onboarding.glade" in result

    def test_main_window_wireframe_path(self):
        pf = self._load_gtk_paths_factory()
        result = pf.main_window_wireframe_path()
        assert "main.glade" in result

    def test_dlib_data_dir_path(self):
        pf = self._load_gtk_paths_factory()
        result = pf.dlib_data_dir_path()
        assert isinstance(result, PurePath)

    def test_css_style_path(self):
        pf = self._load_gtk_paths_factory()
        result = pf.css_style_path()
        assert "style.css" in result

    def test_load_custom_css_swallows_gi_errors(self, monkeypatch):
        pf = self._load_gtk_paths_factory()
        import types

        class Boom:
            def __getattr__(self, _name):
                raise ImportError("no gtk")

        monkeypatch.setitem(sys.modules, "gi", types.SimpleNamespace(repository=Boom()))
        monkeypatch.setitem(sys.modules, "gi.repository", Boom())
        pf.load_custom_css()  # must not raise

    def test_load_custom_css_from_local_file(self, tmp_path, monkeypatch):
        pf = self._load_gtk_paths_factory()
        import types

        css = tmp_path / "style.css"
        css.write_text("window {}", encoding="utf-8")

        class FakeProvider:
            def __init__(self):
                self.loaded = None

            def load_from_path(self, path):
                self.loaded = path

        provider_holder = {}

        def make_provider():
            provider_holder["p"] = FakeProvider()
            return provider_holder["p"]

        class FakeStyleContext:
            called = None

            @staticmethod
            def add_provider_for_screen(screen, provider, priority):
                FakeStyleContext.called = True

        fake_gtk = types.SimpleNamespace(
            CssProvider=make_provider,
            StyleContext=FakeStyleContext,
            STYLE_PROVIDER_PRIORITY_APPLICATION=600,
        )
        fake_gdk = types.SimpleNamespace(
            Screen=types.SimpleNamespace(get_default=lambda: object())
        )
        monkeypatch.setitem(
            sys.modules,
            "gi",
            types.SimpleNamespace(repository=types.SimpleNamespace(Gtk=fake_gtk, Gdk=fake_gdk)),
        )
        monkeypatch.setitem(
            sys.modules,
            "gi.repository",
            types.SimpleNamespace(Gtk=fake_gtk, Gdk=fake_gdk),
        )
        monkeypatch.setattr(
            pf,
            "css_style_path",
            lambda: str(tmp_path / "missing.css"),
        )
        # Make local_path resolve to our temp css via dirname/abspath of __file__.
        monkeypatch.setattr(os.path, "dirname", lambda _p: str(tmp_path))
        monkeypatch.setattr(os.path, "abspath", lambda p: p)
        pf.load_custom_css()
        assert provider_holder["p"].loaded == str(css)
        assert FakeStyleContext.called is True


class TestPathsFactoryCore:
    """Tests for ubuntu-hello/src/paths_factory.py loaded explicitly."""

    def _load_core_paths_factory(self):
        """Load the ubuntu-hello version of paths_factory explicitly."""
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths_factory_path = os.path.join(src_dir, "ubuntu-hello", "src", "paths_factory.py")
        spec = importlib.util.spec_from_file_location(
            "paths_factory_core",
            paths_factory_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_dlib_data_dir_path(self):
        pf = self._load_core_paths_factory()
        result = pf.dlib_data_dir_path()
        assert isinstance(result, str)
        assert "dlib-data" in result

    def test_shape_predictor_path(self):
        pf = self._load_core_paths_factory()
        result = pf.shape_predictor_5_face_landmarks_path()
        assert "shape_predictor_5_face_landmarks.dat" in result

    def test_mmod_detector_path(self):
        pf = self._load_core_paths_factory()
        result = pf.mmod_human_face_detector_path()
        assert "mmod_human_face_detector.dat" in result

    def test_dlib_resnet_path(self):
        pf = self._load_core_paths_factory()
        result = pf.dlib_face_recognition_resnet_model_v1_path()
        assert "dlib_face_recognition_resnet_model_v1.dat" in result

    def test_user_model_path(self):
        pf = self._load_core_paths_factory()
        result = pf.user_model_path("testuser")
        assert "testuser.dat" in result

    def test_config_file_path(self):
        pf = self._load_core_paths_factory()
        result = pf.config_file_path()
        assert "config.ini" in result

    def test_default_config_file_path(self):
        import paths
        pf = self._load_core_paths_factory()
        expected = str(paths.data_dir / "config.ini")
        assert pf.default_config_file_path() == expected

    def test_snapshots_dir_path(self):
        pf = self._load_core_paths_factory()
        result = pf.snapshots_dir_path()
        assert isinstance(result, PurePath)

    def test_snapshot_path(self):
        pf = self._load_core_paths_factory()
        result = pf.snapshot_path("test.jpg")
        assert "test.jpg" in result

    def test_user_models_dir_path(self):
        pf = self._load_core_paths_factory()
        result = pf.user_models_dir_path()
        assert isinstance(result, PurePath)

    def test_logo_path(self):
        pf = self._load_core_paths_factory()
        result = pf.logo_path()
        assert "logo.png" in result

    def test_models_list(self):
        pf = self._load_core_paths_factory()
        assert len(pf.models) == 3
        assert "shape_predictor_5_face_landmarks.dat" in pf.models
