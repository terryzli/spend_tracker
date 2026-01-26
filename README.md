# Gmail Spending Tracker

This project provides a Python script to track your spending by scraping transaction emails from your Gmail account. It automatically categorizes spending, tracks progress towards credit card benefits, and syncs data to Google Sheets and Home Assistant.

## Features

- **Automated Scraping**: Scans Gmail for transaction alerts from U.S. Bank and American Express.
- **Robust Parsing**: Handles various email formats including plain text and complex HTML (Amex).
- **Recurring Expenses**: Supports scheduled monthly expenses (e.g., donations, subscriptions) via a local configuration file.
- **Benefit Tracking**: Real-time progress tracking for specific credit card credits (Amex Dining, Uber, etc.).
- **Smart Data Management**:
  - Automatic chronological sorting by date.
  - **Cumulative Spending**: A running total column helps you see your total spend over time.
  - Duplicate prevention using unique Gmail message IDs.
- **Integrations**:
  - **Google Sheets**: Daily automated upload of your full transaction history.
  - **Home Assistant**: Custom sensors for monthly/yearly spend and benefit progress.

## Setup Instructions

To get started, you'll need to create a project in the Google Cloud Console, enable the Gmail and Google Sheets APIs, and download your credentials.

**Step 1: Go to the Google Cloud Console**
   - Open your web browser and navigate to the [Google Cloud Console](https://console.cloud.google.com/).
   - If you don't have a project already, create a new one.

**Step 2: Enable APIs**
   - Enable the **Gmail API** and the **Google Sheets API**.

**Step 3: Create Credentials**
   - Create an **OAuth 2.0 Client ID** (Desktop app).
   - Download the JSON file, rename it to `credentials.json`, and move it into the `gmail_spending_tracker` directory.

## Customization

### Recurring Expenses
You can add monthly recurring expenses by creating a `recurring_expenses.json` file in the project directory. This file is ignored by Git to protect your privacy.

```json
[
    {
        "name": "Description of Expense",
        "amount": 100.00,
        "day": 15,
        "id_prefix": "unique_identifier"
    }
]
```
The script will automatically log these on or after the specified day each month.

### Benefit Tracking
Edit `benefits.json` to define your credit cards and their respective benefits. You can set keywords for merchant matching and the total value of the credit.

### Bank Support
The script currently supports:
- **U.S. Bank**: "Large Purchase Approved" and "New Transaction" alerts.
- **American Express**: Standard transaction notifications (parsed from HTML).

## Running the Script as a Daemon (macOS)

1. Move the `.plist` file:
   ```bash
   mv ~/gmail_spending_tracker/com.user.gmailspendingtracker.plist ~/Library/LaunchAgents/
   ```
2. Load the job:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.user.gmailspendingtracker.plist
   ```

## Running the Script as a Daemon (Home Assistant)

Add the following to your "Advanced SSH & Web Terminal" add-on `startup_commands`:

```yaml
startup_commands:
  - 'cd /config/gmail_spending_tracker && nohup ./start_daemon.sh &'
  - 'cd /config/gmail_spending_tracker && nohup ./upload_daemon.sh &'
```

### Home Assistant Sensor Configuration

Add `command_line` sensors to your `configuration.yaml` to display your data:

```yaml
sensor:
  - platform: command_line
    name: "Monthly Spending"
    command: "/config/gmail_spending_tracker/venv/bin/python /config/gmail_spending_tracker/report.py"
    value_template: "{{ value_json.monthly_spending }}"
    unit_of_measurement: "$"
    scan_interval: 3600
```

## Security & Privacy
- **Credentials**: `credentials.json` and `token.json` are excluded from Git.
- **Transaction Data**: `transactions.csv` and `recurring_expenses.json` are excluded from Git to keep your financial details local and secure.