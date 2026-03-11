"""Entry point for Open Swiss Tax Statement.

Launches a pywebview native window with the JS API bridge.
"""

import argparse
import os
import shutil
import sys


def _get_project_root() -> str:
    """Return project root, handling PyInstaller frozen bundles."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _migrate_legacy_user_data(project_root: str, new_statements_dir: str) -> None:
    """Silently migrate legacy user_data/ contents to Application Support.

    Moves each subfolder from project_root/user_data/ to new_statements_dir/.
    Skips folders that already exist at the destination. Removes the old
    user_data/ directory if it becomes empty after migration.
    """
    old_dir = os.path.join(project_root, "user_data")
    if not os.path.isdir(old_dir):
        return

    for item in os.listdir(old_dir):
        src = os.path.join(old_dir, item)
        dst = os.path.join(new_statements_dir, item)
        if os.path.isdir(src) and not os.path.exists(dst):
            shutil.move(src, dst)

    # Remove old directory if empty
    try:
        os.rmdir(old_dir)
    except OSError:
        pass  # Not empty — user has other files there



def main():
    import webview

    from src.ui.api import Api
    from src.ui.paths import ensure_dirs, get_statements_dir

    parser = argparse.ArgumentParser(
        description="Open Swiss Tax Statement - E-Steuerauszug Generator",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable pywebview dev tools",
    )
    args = parser.parse_args()

    # Create Application Support directories
    ensure_dirs()
    statements_dir = get_statements_dir()

    # Migrate legacy user_data/ if it exists
    project_root = _get_project_root()
    _migrate_legacy_user_data(project_root, statements_dir)

    api = Api(statements_dir)
    html_path = "file://" + os.path.join(project_root, "src", "ui", "index.html")

    webview.create_window(
        "Open Swiss Tax Statement",
        url=html_path,
        js_api=api,
        width=1024,
        height=768,
    )
    webview.start(debug=args.debug)


if __name__ == "__main__":
    main()
