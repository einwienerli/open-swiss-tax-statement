#!/usr/bin/env bash
# Bump version, commit, tag, and push to trigger the release workflow.
# Usage: ./scripts/release.sh 2025.3.0
set -euo pipefail

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    echo "Usage: ./scripts/release.sh <version>"
    echo "Example: ./scripts/release.sh 2025.3.0"
    exit 1
fi

# Validate version format (CalVer: YYYY.N.N)
if ! echo "$VERSION" | grep -qE '^[0-9]{4}\.[0-9]+\.[0-9]+$'; then
    echo "Error: Version must be CalVer format (e.g. 2025.3.0)"
    exit 1
fi

# Ensure clean working tree
if [ -n "$(git status --porcelain)" ]; then
    echo "Error: Working tree is not clean. Commit or stash changes first."
    exit 1
fi

# Ensure we're on main
BRANCH=$(git branch --show-current)
if [ "$BRANCH" != "main" ]; then
    echo "Error: Must be on main branch (currently on $BRANCH)"
    exit 1
fi

# Check tag doesn't already exist
if git rev-parse "v$VERSION" >/dev/null 2>&1; then
    echo "Error: Tag v$VERSION already exists"
    exit 1
fi

INIT_FILE="src/__init__.py"
CURRENT=$(python3 -c "from src import __version__; print(__version__)")

echo "Releasing: $CURRENT -> $VERSION"
echo ""

# Bump version in src/__init__.py
sed -i '' "s/__version__ = \"$CURRENT\"/__version__ = \"$VERSION\"/" "$INIT_FILE"

# Commit, tag, push
git add "$INIT_FILE"
git commit -m "release: v$VERSION"
git tag "v$VERSION"
git push && git push --tags

echo ""
echo "Done! Release workflow triggered."
echo "Monitor: https://github.com/einwienerli/open-swiss-tax-statement/actions"
echo "Review:  https://github.com/einwienerli/open-swiss-tax-statement/releases"
