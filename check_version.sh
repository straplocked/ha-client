#!/bin/bash
# Check deployed version on HA server

set -e

HA_HOST="${1:-homeassistant.local}"
HA_USER="${2:-straplocked}"
HA_PORT="${3:-22}"

echo "Checking version on HA server..."
echo ""

# Check manifest.json on server
echo "Server manifest.json version:"
ssh -p $HA_PORT $HA_USER@$HA_HOST "grep version /config/custom_components/ha_dispatch_client/manifest.json"
echo ""

echo "Local manifest.json version:"
grep version custom_components/ha_dispatch_client/manifest.json
echo ""

echo "Comparing files..."
REMOTE_MD5=$(ssh -p $HA_PORT $HA_USER@$HA_HOST "md5sum /config/custom_components/ha_dispatch_client/manifest.json | awk '{print \$1}'")
LOCAL_MD5=$(md5sum custom_components/ha_dispatch_client/manifest.json | awk '{print $1}')

echo "Remote MD5: $REMOTE_MD5"
echo "Local MD5:  $LOCAL_MD5"
echo ""

if [ "$REMOTE_MD5" = "$LOCAL_MD5" ]; then
    echo "✓ Files match - manifest.json deployed correctly"
else
    echo "✗ Files DO NOT match - deploy may have failed!"
fi

