"""Entry point for Open Swiss Tax Statement.

Launches a pywebview native window with the JS API bridge.
"""

import argparse
import os
import sys


def _get_project_root() -> str:
    """Return project root, handling PyInstaller frozen bundles."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



def main():
    import webview

    from src.logging_config import setup_logging
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

    # Create directories and configure logging
    ensure_dirs()
    setup_logging(debug=args.debug)

    statements_dir = get_statements_dir()
    project_root = _get_project_root()
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
