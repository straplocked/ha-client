#!/bin/bash
# Bump version for HA Dispatch Client

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Read current version
CURRENT_VERSION=$(cat VERSION 2>/dev/null || echo "0.0.0")

echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║         Version Bump Tool                  ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Current version: ${CURRENT_VERSION}${NC}"
echo ""

# Parse version
IFS='.' read -r -a VERSION_PARTS <<< "$CURRENT_VERSION"
MAJOR="${VERSION_PARTS[0]}"
MINOR="${VERSION_PARTS[1]}"
PATCH="${VERSION_PARTS[2]}"

echo "Choose version bump type:"
echo "  1) Patch (bug fixes)        → $MAJOR.$MINOR.$((PATCH+1))"
echo "  2) Minor (new features)     → $MAJOR.$((MINOR+1)).0"
echo "  3) Major (breaking changes) → $((MAJOR+1)).0.0"
echo "  4) Custom version"
echo ""
read -p "Select (1-4): " -n 1 -r
echo ""
echo ""

case $REPLY in
    1)
        NEW_VERSION="$MAJOR.$MINOR.$((PATCH+1))"
        TYPE="patch"
        ;;
    2)
        NEW_VERSION="$MAJOR.$((MINOR+1)).0"
        TYPE="minor"
        ;;
    3)
        NEW_VERSION="$((MAJOR+1)).0.0"
        TYPE="major"
        ;;
    4)
        read -p "Enter new version (e.g., 1.2.3): " NEW_VERSION
        TYPE="custom"
        ;;
    *)
        echo -e "${RED}Invalid selection${NC}"
        exit 1
        ;;
esac

echo -e "${YELLOW}Version Change:${NC}"
echo "  From: ${CURRENT_VERSION}"
echo "  To:   ${NEW_VERSION}"
echo ""
read -p "What changed in this version? " -r
CHANGE_DESCRIPTION="$REPLY"
echo ""

echo -e "${YELLOW}Updating files...${NC}"

# Update VERSION file
echo "$NEW_VERSION" > VERSION
echo "  ✓ Updated VERSION file"

# Update manifest.json
if [ -f "custom_components/ha_dispatch_client/manifest.json" ]; then
    sed -i "s/\"version\": \".*\"/\"version\": \"$NEW_VERSION\"/" custom_components/ha_dispatch_client/manifest.json
    echo "  ✓ Updated manifest.json"
else
    echo "  ⚠ manifest.json not found"
fi

# Add to CHANGELOG
if [ -f "CHANGELOG.md" ]; then
    DATE=$(date +%Y-%m-%d)
    # Create new entry at the top (after header)
    {
        head -3 CHANGELOG.md
        echo ""
        echo "## [$NEW_VERSION] - $DATE"
        echo ""
        echo "### Changed"
        echo "- $CHANGE_DESCRIPTION"
        echo ""
        tail -n +4 CHANGELOG.md
    } > CHANGELOG.md.tmp
    mv CHANGELOG.md.tmp CHANGELOG.md
    echo "  ✓ Updated CHANGELOG.md"
else
    echo "  ⚠ CHANGELOG.md not found"
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║      Version Bumped Successfully! 🎉      ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}New Version:${NC} ${NEW_VERSION}"
echo -e "${CYAN}Change:${NC} $CHANGE_DESCRIPTION"
echo ""
echo -e "${YELLOW}Files Updated:${NC}"
echo "  • VERSION"
echo "  • custom_components/ha_dispatch_client/manifest.json"
echo "  • CHANGELOG.md"
echo ""
echo -e "${BLUE}Next Steps:${NC}"
echo "  1. Review CHANGELOG.md"
echo "  2. Test your changes"
echo "  3. Deploy: ${GREEN}./deploy.sh${NC}"
echo ""

