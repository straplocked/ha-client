#!/bin/bash
# Direct service check without ha CLI

# Read password securely
read -s -p "Enter SSH password for straplocked@homeassistant.local: " SSH_PASS
echo ""

HA_HOST="homeassistant.local"
HA_USER="straplocked"

echo "=== Checking HA Dispatch Services ==="
echo ""

echo "1. Finding log files..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'find /config -name "*.log" -type f 2>/dev/null'
echo ""

echo "2. Checking integration files are present..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'ls -lh /config/custom_components/ha_dispatch_client/ | grep -E "init|services"'
echo ""

echo "3. Looking for ha_dispatch in home-assistant.log..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'grep -i "ha_dispatch" /config/home-assistant.log 2>/dev/null | tail -30' || {
    echo "  → home-assistant.log not found at /config/, trying other locations..."
    sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'find /config -name "home-assistant.log*" -type f 2>/dev/null | head -3'
}
echo ""

echo "4. Checking for Python errors..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'grep -i "error\|traceback\|exception" /config/home-assistant.log 2>/dev/null | grep -i "ha_dispatch\|custom_component" | tail -20' || echo "  → No log file found"
echo ""

echo "5. Checking if integration is in configuration.yaml..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'grep -i "ha_dispatch" /config/configuration.yaml 2>/dev/null || echo "  → Not in configuration.yaml (this is OK for config_flow integrations)"'
echo ""

echo "6. Checking .storage for integration config..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'grep -l "ha_dispatch" /config/.storage/* 2>/dev/null | head -3'
echo ""

echo "7. Trying to read integration state..."
sshpass -p "$SSH_PASS" ssh $HA_USER@$HA_HOST 'cat /config/.storage/core.config_entries 2>/dev/null | grep -A 10 "ha_dispatch" | head -15' || echo "  → Cannot read config_entries"
echo ""

echo "=== Check Complete ==="
echo ""
echo "Next steps:"
echo "1. Check Developer Tools → Actions and search for 'dispatch'"
echo "2. Check Settings → Devices & Services → HA Dispatch Client"
echo "3. Check Settings → System → Logs for errors"

