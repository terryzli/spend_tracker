#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")/.."

# Load config using jq
CONFIG_FILE="config/spend_tracker.json"
UPLOAD_LOG=$(jq -r '.paths.upload_log' "$CONFIG_FILE")
UPLOAD_ERR=$(jq -r '.paths.upload_err' "$CONFIG_FILE")
CSV_FILE=$(jq -r '.paths.transactions_csv' "$CONFIG_FILE")

# Ensure the log files exist
touch "$UPLOAD_LOG" "$UPLOAD_ERR"

echo "$(date): Upload daemon started (Change-Detection Mode)." >> "$UPLOAD_LOG"

# Helper function to get MD5 hash compatible with both macOS and Linux/Raspberry Pi
get_hash() {
    if command -v md5 >/dev/null 2>&1; then
        # macOS
        md5 -q "$1" 2>/dev/null
    else
        # Linux / Raspberry Pi
        md5sum "$1" 2>/dev/null | cut -d' ' -f1
    fi
}

# Store the initial checksum of the CSV
LAST_HASH=$(get_hash "$CSV_FILE")

while true; do
    CURRENT_HASH=$(get_hash "$CSV_FILE")

    if [ "$CURRENT_HASH" != "$LAST_HASH" ]; then
        echo "$(date): Change detected in transactions.csv. Uploading..." >> "$UPLOAD_LOG"
        ./venv/bin/python pkg/upload_to_sheets.py >> "$UPLOAD_LOG" 2>> "$UPLOAD_ERR"
        
        if [ $? -eq 0 ]; then
            LAST_HASH=$CURRENT_HASH
            echo "$(date): Upload completed successfully." >> "$UPLOAD_LOG"
        else
            echo "$(date): Upload failed. Will retry next check." >> "$UPLOAD_ERR"
        fi
    fi

    # Check for changes every 5 minutes
    sleep 300
done