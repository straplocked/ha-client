#!/bin/bash
# Quick service check script

# Read password securely
read -s -p "Enter SSH password for straplocked@homeassistant.local: " SSH_PASS
echo ""

HA_HOST="homeassistant.local"
HA_USER="straplocked"

echo "=== Checking HA Dispatch Services ==="
echo ""

echo "1. Checking core configuration..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'ha core check' 2>&1 | head -20
echo ""

echo "2. Looking for ha_dispatch services..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'ha service list | grep -i dispatch'
if [ $? -ne 0 ]; then
    echo "  ✗ No ha_dispatch services found"
else
    echo "  ✓ Services found!"
fi
echo ""

echo "3. Checking integration status..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'ha integrations list | grep -i dispatch'
echo ""

echo "4. Looking for errors in logs..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'find /config -name "*.log" -type f 2>/dev/null | head -5'
echo ""

echo "5. Checking for ha_dispatch errors..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'grep -i "ha_dispatch" /config/home-assistant.log 2>/dev/null | tail -20' || echo "  (Log file location may be different)"
echo ""

echo "=== Check Complete ==="

