#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")"

# Loop to run the script periodically
while true; do
    echo "Running Gmail Spending Tracker..."
    # Activate virtual environment and run the script
    ./venv/bin/python main.py >> spending_tracker.log 2>> spending_tracker.err
    echo "Script finished. Sleeping for 1 hour."
    sleep 3600
done
