"""Tests for Application Support directory paths."""

from __future__ import annotations

import os

from src.ui.paths import (
    get_app_support_dir,
    get_data_dir,
    get_preferences_path,
    get_statements_dir,
)


class TestPaths:
    """Validate path structure and consistency."""

    def test_app_support_dir_is_absolute(self):
        path = get_app_support_dir()
        assert os.path.isabs(path)

    def test_app_support_dir_contains_app_name(self):
        path = get_app_support_dir()
        assert "OpenSwissTaxStatement" in path or "openswisstaxstatement" in path

    def test_statements_dir_under_app_support(self):
        support = get_app_support_dir()
        statements = get_statements_dir()
        assert statements.startswith(support)
        assert statements.endswith("statements")

    def test_data_dir_under_app_support(self):
        support = get_app_support_dir()
        data = get_data_dir()
        assert data.startswith(support)
        assert data.endswith("data")

    def test_preferences_path_under_app_support(self):
        support = get_app_support_dir()
        prefs = get_preferences_path()
        assert prefs.startswith(support)
        assert prefs.endswith("preferences.json")

    def test_all_paths_are_different(self):
        paths = {
            get_app_support_dir(),
            get_statements_dir(),
            get_data_dir(),
            get_preferences_path(),
        }
        assert len(paths) == 4, "All paths should be distinct"

    def test_ensure_dirs_creates_directories(self, tmp_path, monkeypatch):
        """ensure_dirs() creates the directory structure."""
        monkeypatch.setattr(
            "src.ui.paths.get_app_support_dir",
            lambda: str(tmp_path / "TestApp"),
        )
        from src.ui.paths import ensure_dirs
        ensure_dirs()
        assert os.path.isdir(str(tmp_path / "TestApp" / "statements"))
        assert os.path.isdir(str(tmp_path / "TestApp" / "data"))

    def test_macos_uses_documents(self, monkeypatch):
        """On macOS, uses ~/Documents/."""
        monkeypatch.setattr("src.ui.paths.platform.system", lambda: "Darwin")
        path = get_app_support_dir()
        assert "/Documents/OpenSwissTaxStatement" in path

