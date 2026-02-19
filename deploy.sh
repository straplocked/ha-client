#!/bin/bash
# HA Dispatch Client - Unified Deploy Script
# Handles both initial installation and updates

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
HA_HOST="${HA_HOST:-homeassistant.local}"
HA_USER="${HA_USER:-straplocked}"
HA_PORT="${HA_PORT:-22}"
SOURCE_DIR="./custom_components/ha_dispatch_client"
TARGET_DIR="/config/custom_components/ha_dispatch_client"
VERSION=$(cat VERSION 2>/dev/null || echo "unknown")

echo -e "${CYAN}╔════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║    HA Dispatch Client - Deploy Script     ║${NC}"
echo -e "${CYAN}║           Version: ${VERSION}                      ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════╝${NC}"
echo ""

# Check if sshpass is available
if ! command -v sshpass &> /dev/null; then
    echo -e "${RED}Error: 'sshpass' is required but not installed.${NC}"
    echo ""
    echo "Install it with:"
    echo "  sudo apt-get install sshpass"
    exit 1
fi

# Check source directory exists
if [ ! -d "$SOURCE_DIR" ]; then
    echo -e "${RED}Error: Source directory not found: $SOURCE_DIR${NC}"
    echo "Please run this script from the ha-client directory"
    exit 1
fi

# Get password if not provided
if [ -z "$HA_PASS" ]; then
    echo -e "${BLUE}SSH Connection Details:${NC}"
    echo "  Host: $HA_HOST"
    echo "  User: $HA_USER"
    echo "  Port: $HA_PORT"
    echo ""
    read -s -p "SSH Password: " HA_PASS
    echo ""
    echo ""
fi

# Test SSH connection
echo -e "${YELLOW}Testing SSH connection to $HA_USER@$HA_HOST:$HA_PORT...${NC}"
if ! sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no -o ConnectTimeout=10 $HA_USER@$HA_HOST "echo 'Connected'" 2>/dev/null; then
    echo -e "${RED}Error: Cannot connect to $HA_HOST${NC}"
    echo ""
    echo "Please verify:"
    echo "1. Home Assistant is reachable at: $HA_HOST"
    echo "2. SSH is enabled (Settings → Add-ons → Terminal & SSH)"
    echo "3. Username and password are correct"
    exit 1
fi
echo -e "${GREEN}✓ Connected${NC}"
echo ""

# Check if component is already installed
echo -e "${YELLOW}Checking installation status...${NC}"
ALREADY_INSTALLED=$(sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST "test -d $TARGET_DIR && echo 'yes' || echo 'no'")

if [ "$ALREADY_INSTALLED" = "yes" ]; then
    echo -e "${BLUE}Component already installed - performing UPDATE${NC}"
    MODE="update"
else
    echo -e "${BLUE}Component not found - performing FRESH INSTALL${NC}"
    MODE="install"
    
    # Create custom_components directory
    echo -e "${YELLOW}Creating custom_components directory...${NC}"
    sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST "
        sudo mkdir -p /config/custom_components &&
        sudo chown $HA_USER:$HA_USER /config/custom_components
    "
    echo -e "${GREEN}✓ Directory created${NC}"
    echo ""
fi

# Count files to deploy
FILE_COUNT=$(find $SOURCE_DIR -type f | wc -l)
echo -e "${YELLOW}Deploying $FILE_COUNT files...${NC}"

# Create tar archive and deploy
cd custom_components
tar czf - ha_dispatch_client | sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST "
    cd /tmp &&
    tar xzf - &&
    sudo rm -rf $TARGET_DIR &&
    sudo mv ha_dispatch_client $TARGET_DIR &&
    sudo chown -R root:root $TARGET_DIR
"
cd ..

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Files deployed successfully${NC}"
else
    echo -e "${RED}✗ Deployment failed${NC}"
    exit 1
fi
echo ""

# Verify installation
echo -e "${YELLOW}Verifying installation...${NC}"
DEPLOYED_COUNT=$(sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST "find $TARGET_DIR -type f | wc -l")

if [ "$DEPLOYED_COUNT" -ge "$FILE_COUNT" ]; then
    echo -e "${GREEN}✓ Installation verified ($DEPLOYED_COUNT files)${NC}"
else
    echo -e "${RED}✗ Verification failed${NC}"
    echo "Expected: $FILE_COUNT files, Found: $DEPLOYED_COUNT files"
    exit 1
fi
echo ""

# List deployed files
echo -e "${BLUE}Deployed files:${NC}"
sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST "ls -lh $TARGET_DIR/"
echo ""

# Check for key features in the code
echo -e "${YELLOW}Checking integration features...${NC}"
HAS_SERVICES=$(sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST "grep -q 'def setup_services' $TARGET_DIR/__init__.py && echo 'yes' || echo 'no'")
HAS_SERVICES_YAML=$(sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST "test -f $TARGET_DIR/services.yaml && echo 'yes' || echo 'no'")

if [ "$HAS_SERVICES" = "yes" ]; then
    echo -e "  ${GREEN}✓${NC} Service registration (singleton pattern)"
else
    echo -e "  ${YELLOW}⚠${NC} Service registration not found"
fi

if [ "$HAS_SERVICES_YAML" = "yes" ]; then
    echo -e "  ${GREEN}✓${NC} Services UI definition (services.yaml)"
else
    echo -e "  ${YELLOW}⚠${NC} services.yaml not found"
fi
echo ""

# Success message
echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║      Deployment Successful! 🎉             ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""

if [ "$MODE" = "update" ]; then
    echo -e "${CYAN}Updated Integration:${NC} HA Dispatch Client v${VERSION}"
    echo -e "${CYAN}Backup Location:${NC} /config/custom_components/$BACKUP_DIR"
else
    echo -e "${CYAN}Installed Integration:${NC} HA Dispatch Client v${VERSION}"
    echo -e "${CYAN}Location:${NC} $TARGET_DIR"
fi
echo ""

# Show what's new/changed
echo -e "${BLUE}Integration Features:${NC}"
echo "  • Client-initiated communication with server"
echo "  • Automatic registration and token management"
echo "  • System metrics collection (CPU, Memory, Disk)"
echo "  • Configuration push from server"
echo "  • Alert generation based on thresholds"
echo "  • 4 debug services for testing"
echo ""

echo -e "${BLUE}Available Services (after restart):${NC}"
echo "  • ha_dispatch_client.send_test_metrics"
echo "  • ha_dispatch_client.trigger_alert"
echo "  • ha_dispatch_client.force_update"
echo "  • ha_dispatch_client.send_custom_metric"
echo ""

# Restart prompt
echo -e "${YELLOW}═══════════════════════════════════════════${NC}"
echo -e "${YELLOW}  RESTART REQUIRED TO APPLY CHANGES${NC}"
echo -e "${YELLOW}═══════════════════════════════════════════${NC}"
echo ""
read -p "Would you like to restart Home Assistant now? (y/n): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo -e "${YELLOW}Restarting Home Assistant...${NC}"
    sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST "sudo ha core restart" 2>/dev/null
    echo -e "${GREEN}✓ Restart initiated${NC}"
    echo ""
    echo -e "${CYAN}Home Assistant is restarting (1-2 minutes)...${NC}"
    echo ""
    echo -e "${BLUE}After restart:${NC}"
    echo ""
    
    if [ "$MODE" = "install" ]; then
        echo -e "${YELLOW}First-time Setup:${NC}"
        echo "  1. Go to: Settings → Devices & Services"
        echo "  2. Click: '+ Add Integration' (bottom right)"
        echo "  3. Search: 'HA Dispatch Client'"
        echo "  4. Enter your HA Dispatch server URL"
        echo "  5. Click Submit"
        echo ""
    fi
    
    echo -e "${YELLOW}Verify Installation:${NC}"
    echo "  1. Go to: Developer Tools → Services"
    echo "  2. Search: 'ha_dispatch_client'"
    echo "  3. Should see 4 services ✅"
    echo ""
    echo -e "${YELLOW}Test It:${NC}"
    echo "  • Call: ha_dispatch_client.send_test_metrics"
    echo "  • Check your HA Dispatch server for metrics"
    echo ""
    echo -e "${YELLOW}Check Status:${NC}"
    echo "  • Go to: Developer Tools → States"
    echo "  • Look for: sensor.ha_dispatch_status"
    echo "  • Should show: 'online'"
    echo ""
else
    echo ""
    echo -e "${YELLOW}Remember to restart Home Assistant to apply changes!${NC}"
    echo ""
    echo "When ready, run:"
    echo "  ssh $HA_USER@$HA_HOST 'sudo ha core restart'"
    echo ""
    echo "Or via Web UI:"
    echo "  Settings → System → Restart"
    echo ""
fi

# Cleanup instructions
if [ "$MODE" = "update" ]; then
    echo -e "${BLUE}───────────────────────────────────────────${NC}"
    echo -e "${BLUE}Backup Management:${NC}"
    echo ""
    echo "Your old version is backed up at:"
    echo "  /config/custom_components/$BACKUP_DIR"
    echo ""
    echo "To restore backup if needed:"
    echo "  ssh $HA_USER@$HA_HOST 'sudo rm -rf $TARGET_DIR && sudo mv /config/custom_components/$BACKUP_DIR $TARGET_DIR && sudo ha core restart'"
    echo ""
    echo "To remove old backups:"
    echo "  ssh $HA_USER@$HA_HOST 'sudo rm -rf /config/custom_components/ha_dispatch_client_backup_*'"
    echo ""
fi

echo -e "${GREEN}Done! 🚀${NC}"

