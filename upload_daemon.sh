#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")"

# Log file for upload daemon
UPLOAD_LOG="/config/gmail_spending_tracker/upload_daemon.log"
UPLOAD_ERR="/config/gmail_spending_tracker/upload_daemon.err"
CSV_FILE="/config/gmail_spending_tracker/transactions.csv"

# Ensure the log files exist
touch "$UPLOAD_LOG" "$UPLOAD_ERR"

echo "$(date): Upload daemon started (Change-Detection Mode)." >> "$UPLOAD_LOG"

# Store the initial checksum of the CSV
LAST_HASH=$(md5sum "$CSV_FILE" 2>/dev/null)

while true; do
    CURRENT_HASH=$(md5sum "$CSV_FILE" 2>/dev/null)

    if [ "$CURRENT_HASH" != "$LAST_HASH" ]; then
        echo "$(date): Change detected in transactions.csv. Uploading..." >> "$UPLOAD_LOG"
        ./venv/bin/python upload_to_sheets.py >> "$UPLOAD_LOG" 2>> "$UPLOAD_ERR"
        
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