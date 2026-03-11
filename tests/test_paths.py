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

    def test_migrate_legacy_dir_from_open_esteuerauszug(self, tmp_path, monkeypatch):
        """_migrate_legacy_dir() copies old OpenESteuerauszug dir to new location."""
        monkeypatch.setattr("src.ui.paths.platform.system", lambda: "Darwin")
        app_support = tmp_path / "Library" / "Application Support"
        app_support.mkdir(parents=True)
        documents = tmp_path / "Documents"
        documents.mkdir(parents=True)
        monkeypatch.setattr(
            "src.ui.paths.os.path.expanduser",
            lambda p: str(tmp_path / p.lstrip("~/")),
        )
        # Create old dir with content
        old_dir = app_support / "OpenESteuerauszug"
        old_dir.mkdir()
        (old_dir / "statements").mkdir()
        (old_dir / "preferences.json").write_text("{}")

        from src.ui.paths import _migrate_legacy_dir
        _migrate_legacy_dir()

        new_dir = documents / "OpenSwissTaxStatement"
        assert new_dir.is_dir()
        assert (new_dir / "statements").is_dir()
        assert (new_dir / "preferences.json").exists()

    def test_migrate_from_application_support(self, tmp_path, monkeypatch):
        """_migrate_legacy_dir() migrates from ~/Library/Application Support/."""
        monkeypatch.setattr("src.ui.paths.platform.system", lambda: "Darwin")
        app_support = tmp_path / "Library" / "Application Support"
        app_support.mkdir(parents=True)
        documents = tmp_path / "Documents"
        documents.mkdir(parents=True)
        monkeypatch.setattr(
            "src.ui.paths.os.path.expanduser",
            lambda p: str(tmp_path / p.lstrip("~/")),
        )
        # Create old dir in Application Support (previous location)
        old_dir = app_support / "OpenSwissTaxStatement"
        old_dir.mkdir()
        (old_dir / "statements").mkdir()
        (old_dir / "data").mkdir()
        (old_dir / "preferences.json").write_text('{"lang": "de"}')

        from src.ui.paths import _migrate_legacy_dir
        _migrate_legacy_dir()

        new_dir = documents / "OpenSwissTaxStatement"
        assert new_dir.is_dir()
        assert (new_dir / "statements").is_dir()
        assert (new_dir / "data").is_dir()
        assert (new_dir / "preferences.json").read_text() == '{"lang": "de"}'
