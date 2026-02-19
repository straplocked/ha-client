#!/bin/bash
# Clean up old install/update scripts after confirming deploy.sh works

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${YELLOW}╔════════════════════════════════════════════╗${NC}"
echo -e "${YELLOW}║     Clean Up Old Deploy Scripts           ║${NC}"
echo -e "${YELLOW}╚════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${BLUE}This will delete the following OLD scripts:${NC}"
echo ""
echo -e "${YELLOW}Install Scripts (replaced by deploy.sh):${NC}"
echo "  • INSTALL_REMOTE.sh"
echo "  • INSTALL_REMOTE_SUDO.sh"
echo "  • INSTALL_REMOTE_INTERACTIVE.sh"
echo "  • INSTALL_REMOTE_TAR.sh"
echo "  • install.sh"
echo ""
echo -e "${YELLOW}Update Scripts (replaced by deploy.sh):${NC}"
echo "  • UPDATE_REMOTE.sh"
echo "  • UPDATE_WITH_DEBUG.sh"
echo "  • UPDATE_WITH_DEBUG_INTERACTIVE.sh"
echo "  • PUSH_SERVICE_FIX.sh"
echo ""
echo -e "${GREEN}These will be KEPT:${NC}"
echo "  ✓ deploy.sh (NEW unified script)"
echo "  ✓ DEPLOY_README.md"
echo "  ✓ DOWNLOAD_MEROSS_LAN*.sh (for reference)"
echo "  ✓ All documentation files (*.md)"
echo "  ✓ CHECK_SERVICES*.sh"
echo ""
echo -e "${RED}⚠ WARNING: This action cannot be undone!${NC}"
echo ""
read -p "Are you sure deploy.sh works and you want to delete old scripts? (yes/no): " -r
echo ""

if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    echo "Cancelled. No files deleted."
    exit 0
fi

echo ""
echo -e "${YELLOW}Deleting old scripts...${NC}"

# Count deleted files
DELETED=0

# Delete install scripts
for script in INSTALL_REMOTE.sh INSTALL_REMOTE_SUDO.sh INSTALL_REMOTE_INTERACTIVE.sh INSTALL_REMOTE_TAR.sh install.sh; do
    if [ -f "$script" ]; then
        rm "$script"
        echo "  ✓ Deleted: $script"
        ((DELETED++))
    fi
done

# Delete update scripts
for script in UPDATE_REMOTE.sh UPDATE_WITH_DEBUG.sh UPDATE_WITH_DEBUG_INTERACTIVE.sh PUSH_SERVICE_FIX.sh; do
    if [ -f "$script" ]; then
        rm "$script"
        echo "  ✓ Deleted: $script"
        ((DELETED++))
    fi
done

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Cleanup Complete! 🎉               ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}Summary:${NC}"
echo "  • Deleted: $DELETED old scripts"
echo "  • Kept: deploy.sh (your new unified script)"
echo ""
echo -e "${YELLOW}From now on, use:${NC}"
echo "  ./deploy.sh    # For both install and updates"
echo ""
echo -e "${BLUE}Remaining scripts:${NC}"
ls -1 *.sh 2>/dev/null || echo "  (none)"
echo ""

