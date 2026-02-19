#!/bin/bash
# Download meross_lan integration from Home Assistant for reference
# Non-interactive version with defaults

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Download meross_lan for Reference        ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""

# Check if sshpass is available
if ! command -v sshpass &> /dev/null; then
    echo -e "${RED}Error: 'sshpass' is required but not installed.${NC}"
    echo ""
    echo "Install it with:"
    echo "  sudo apt-get install sshpass"
    echo ""
    exit 1
fi

# Use command line args or defaults
HA_HOST="${1:-homeassistant.local}"
HA_USER="${2:-straplocked}"
HA_PORT="${3:-22}"
HA_PASS="${4}"

# If password not provided, prompt for it
if [ -z "$HA_PASS" ]; then
    echo -e "${BLUE}Please enter your SSH password:${NC}"
    read -s -p "Password: " HA_PASS
    echo ""
    echo ""
fi

echo -e "${BLUE}Connection details:${NC}"
echo "  Host: $HA_HOST"
echo "  User: $HA_USER"
echo "  Port: $HA_PORT"
echo ""

# Create reference directory
mkdir -p ./reference_integrations
echo -e "${GREEN}✓ Created ./reference_integrations/${NC}"
echo ""

# Test connection
echo -e "${YELLOW}Testing SSH connection to $HA_USER@$HA_HOST:$HA_PORT...${NC}"

if ! sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no -o ConnectTimeout=10 $HA_USER@$HA_HOST "echo 'Connected'" 2>/dev/null; then
    echo -e "${RED}Error: Cannot connect to $HA_HOST${NC}"
    echo ""
    echo "Please verify:"
    echo "1. Home Assistant is reachable at: $HA_HOST"
    echo "2. SSH is enabled (Settings → Add-ons → Terminal & SSH)"
    echo "3. Username and password are correct"
    echo "4. SSH port is correct (usually 22 or 22222)"
    exit 1
fi
echo -e "${GREEN}✓ Connected${NC}"
echo ""

# Create tar archive on remote server
echo -e "${YELLOW}Creating archive of meross_lan on HA server...${NC}"

sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST "
    # Clean up any old tar file first
    sudo rm -f /tmp/meross_lan.tar.gz
    
    if [ -d /config/custom_components/meross_lan ]; then
        cd /config/custom_components &&
        sudo tar czf /tmp/meross_lan.tar.gz meross_lan &&
        sudo chown \$USER:\$USER /tmp/meross_lan.tar.gz &&
        sudo chmod 644 /tmp/meross_lan.tar.gz &&
        echo 'Archive created'
    else
        echo 'ERROR: meross_lan not found at /config/custom_components/meross_lan'
        exit 1
    fi
"

if [ $? -ne 0 ]; then
    echo -e "${RED}✗ Failed to create archive${NC}"
    echo ""
    echo "Possible issues:"
    echo "1. meross_lan integration not installed"
    echo "2. Insufficient sudo permissions"
    echo "3. Path /config/custom_components/meross_lan doesn't exist"
    exit 1
fi
echo -e "${GREEN}✓ Archive created${NC}"
echo ""

# Download the archive (using ssh+cat instead of scp due to subsystem issues)
echo -e "${YELLOW}Downloading archive...${NC}"

sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST "cat /tmp/meross_lan.tar.gz" > ./reference_integrations/meross_lan.tar.gz

if [ $? -ne 0 ] || [ ! -f ./reference_integrations/meross_lan.tar.gz ]; then
    echo -e "${RED}✗ Download failed${NC}"
    echo ""
    echo "Trying alternative method with scp..."
    sshpass -p "$HA_PASS" scp -P $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST:/tmp/meross_lan.tar.gz ./reference_integrations/
    if [ $? -ne 0 ]; then
        echo -e "${RED}✗ Both download methods failed${NC}"
        exit 1
    fi
fi
echo -e "${GREEN}✓ Downloaded${NC}"
echo ""

# Extract the archive
echo -e "${YELLOW}Extracting archive...${NC}"
cd ./reference_integrations
tar xzf meross_lan.tar.gz
rm meross_lan.tar.gz
cd ..
echo -e "${GREEN}✓ Extracted${NC}"
echo ""

# Clean up remote temp file
echo -e "${YELLOW}Cleaning up temporary files on HA server...${NC}"

sshpass -p "$HA_PASS" ssh -p $HA_PORT -o StrictHostKeyChecking=no $HA_USER@$HA_HOST "rm /tmp/meross_lan.tar.gz"
echo -e "${GREEN}✓ Cleaned up${NC}"
echo ""

# List downloaded files
echo -e "${YELLOW}Downloaded files:${NC}"
ls -lh ./reference_integrations/meross_lan/
echo ""

# Count Python files
PY_COUNT=$(find ./reference_integrations/meross_lan -name "*.py" 2>/dev/null | wc -l)
YAML_COUNT=$(find ./reference_integrations/meross_lan -name "*.yaml" -o -name "*.yml" 2>/dev/null | wc -l)

echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Download Successful! 🎉            ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}Downloaded Integration:${NC}"
echo "  Location: ./reference_integrations/meross_lan/"
echo "  Python files: $PY_COUNT"
echo "  YAML files: $YAML_COUNT"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "1. Examine services.yaml (if present)"
echo "2. Look at __init__.py for service registration patterns"
echo "3. Compare with your ha_dispatch_client implementation"
echo ""
echo -e "${BLUE}Quick checks:${NC}"

# Check if services.yaml exists
if [ -f ./reference_integrations/meross_lan/services.yaml ]; then
    echo "  ✓ services.yaml found"
    echo ""
    echo "Preview of services.yaml:"
    head -20 ./reference_integrations/meross_lan/services.yaml
else
    echo "  ✗ No services.yaml file"
fi

# Check if services are registered in __init__.py
if [ -f ./reference_integrations/meross_lan/__init__.py ]; then
    if grep -q "async_register_admin_service\|hass.services.async_register" ./reference_integrations/meross_lan/__init__.py 2>/dev/null; then
        echo "  ✓ Service registration code found in __init__.py"
    else
        echo "  ℹ No obvious service registration in __init__.py"
    fi
fi

echo ""
echo -e "${GREEN}You can now examine the meross_lan integration for reference!${NC}"

