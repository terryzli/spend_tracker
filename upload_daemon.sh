#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")"

# Log file for upload daemon
UPLOAD_LOG="/config/gmail_spending_tracker/upload_daemon.log"
UPLOAD_ERR="/config/gmail_spending_tracker/upload_daemon.err"
LAST_UPLOAD_FILE="/config/gmail_spending_tracker/last_upload_date.txt"

# Ensure the log files exist
touch "$UPLOAD_LOG" "$UPLOAD_ERR"

echo "$(date): Upload daemon started." >> "$UPLOAD_LOG"

while true; do
    CURRENT_DATE=$(date +%Y-%m-%d)
    LAST_UPLOAD_DATE=""

    if [ -f "$LAST_UPLOAD_FILE" ]; then
        LAST_UPLOAD_DATE=$(cat "$LAST_UPLOAD_FILE")
    fi

    if [ "$CURRENT_DATE" != "$LAST_UPLOAD_DATE" ]; then
        echo "$(date): New day detected. Running upload to Google Sheets..." >> "$UPLOAD_LOG"
        ./venv/bin/python upload_to_sheets.py >> "$UPLOAD_LOG" 2>> "$UPLOAD_ERR"
        if [ $? -eq 0 ]; then
            echo "$CURRENT_DATE" > "$LAST_UPLOAD_FILE"
            echo "$(date): Upload completed successfully." >> "$UPLOAD_LOG"
        else
            echo "$(date): Upload failed." >> "$UPLOAD_ERR"
        fi
    else
        echo "$(date): Already uploaded today. Skipping." >> "$UPLOAD_LOG"
    fi

    # Sleep for 1 hour before checking again
    sleep 3600
done
