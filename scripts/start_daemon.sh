#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")/.."

# Interval in seconds: default to 86400 (daily / 24 hours)
# Can be overridden by passing an argument (e.g. ./scripts/start_daemon.sh 3600) or setting SLEEP_INTERVAL
INTERVAL="${1:-${SLEEP_INTERVAL:-86400}}"

echo "$(date): Gmail Spending Tracker daemon started with interval of ${INTERVAL}s." >> spending_tracker.log

# Loop to run the script periodically
while true; do
    echo "$(date): Running scheduled tracker update..." >> spending_tracker.log
    ./scripts/run.sh >> spending_tracker.log 2>> spending_tracker.err
    echo "$(date): Run complete. Sleeping for ${INTERVAL} seconds." >> spending_tracker.log
    sleep "$INTERVAL"
done
