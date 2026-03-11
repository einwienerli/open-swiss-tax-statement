"""Verify that production source code contains no network-capable imports.

This gives users a machine-verifiable claim that the app is fully offline,
not just documentation. The only exception is the admin-only download_schemas.py
script which is not used during normal operation.
"""

import ast
import os
from pathlib import Path

SRC_DIR = Path(__file__).parent.parent / "src"

# Modules that can make network connections
NETWORK_MODULES = {
    "socket",
    "requests",
    "httpx",
    "urllib.request",
    "http.client",
    "aiohttp",
}

# Files excluded from the offline check:
# - download_schemas.py: admin-only script, not used during normal operation
EXCLUDED_FILES = {
    os.path.normpath("src/generators/ech0196/download_schemas.py"),
}


def _collect_python_files():
    """Collect all .py files in src/, excluding admin-only scripts."""
    files = []
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        rel = os.path.normpath(py_file.relative_to(SRC_DIR.parent))
        if rel not in EXCLUDED_FILES:
            files.append(py_file)
    return files


def _extract_imports(filepath: Path) -> list[str]:
    """Parse a Python file's AST and return all imported module names."""
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def _check_file_for_network_imports(filepath: Path) -> list[str]:
    """Return list of network module imports found in a file."""
    imports = _extract_imports(filepath)
    violations = []
    for imp in imports:
        for net_mod in NETWORK_MODULES:
            # Match exact module or submodule (e.g. "urllib.request" matches "urllib.request.urlopen")
            if imp == net_mod or imp.startswith(net_mod + "."):
                violations.append(imp)
    return violations


class TestOfflineGuarantee:
    """Ensure no production code imports network-capable modules."""

    def test_no_network_imports_in_source(self):
        """Scan all src/*.py files and assert zero network imports."""
        all_violations = {}
        for py_file in _collect_python_files():
            violations = _check_file_for_network_imports(py_file)
            if violations:
                rel_path = py_file.relative_to(SRC_DIR.parent)
                all_violations[str(rel_path)] = violations

        assert all_violations == {}, (
            "Network-capable imports found in production code:\n"
            + "\n".join(
                f"  {path}: {', '.join(mods)}"
                for path, mods in sorted(all_violations.items())
            )
        )

    def test_excluded_files_exist(self):
        """Verify that excluded files actually exist (prevents stale exclusions)."""
        for rel_path in EXCLUDED_FILES:
            full_path = SRC_DIR.parent / rel_path
            assert full_path.exists(), (
                f"Excluded file {rel_path} no longer exists — "
                "remove it from EXCLUDED_FILES"
            )

    def test_source_directory_has_python_files(self):
        """Sanity check: ensure we're actually scanning files."""
        files = _collect_python_files()
        assert len(files) > 10, (
            f"Only found {len(files)} Python files in src/ — "
            "something is wrong with the scan"
        )
