#!/bin/bash
cd "$(dirname "$0")/.."

echo "$(date): Starting Gmail Spending Tracker run..."
./venv/bin/python main.py "$@"
STATUS=$?

if [ $STATUS -eq 0 ]; then
    echo "$(date): Main run completed successfully. Uploading to Google Sheets..."
    ./venv/bin/python pkg/upload_to_sheets.py
    echo "$(date): Full sync finished."
else
    echo "$(date): Main run failed with status $STATUS. Skipping Google Sheets upload." >&2
fi
