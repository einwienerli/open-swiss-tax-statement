"""Tests for PyInstaller bundle path resolution."""
import os
import sys

from src.main import _get_project_root


class TestProjectRoot:
    def test_dev_mode_returns_repo_root(self):
        root = _get_project_root()
        assert os.path.isfile(os.path.join(root, "pyproject.toml"))

    def test_frozen_mode_uses_meipass(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert _get_project_root() == str(tmp_path)
