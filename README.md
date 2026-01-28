# Gmail Spending Tracker

An automated financial tool that scrapes transaction emails from Gmail, categorizes spending, tracks credit card benefits, and syncs data to Google Sheets and Home Assistant.

## Features

- **Multi-Bank Support**: Automated regex parsing for:
  - **U.S. Bank**: "Large Purchase" and "New Transaction" alerts.
  - **American Express**: Standard transaction notifications.
  - **Bank of America**: "Transaction exceeds limit" alerts.
  - **Capital One**: "New transaction charged" alerts (Venture X, etc.).
- **AI-Powered Fallback**: Uses the latest **Gemini 2.5 Flash Lite** (via the modern `google-genai` SDK) to parse unknown email formats if regex fails.
- **Recurring Expenses**: Support for scheduled monthly transactions (e.g., donations, rent) via a private `recurring_expenses.json`.
- **Advanced Data Management**:
  - **Deduplication**: Every run automatically cleans the CSV of duplicate entries.
  - **Chronological Sorting**: Transactions are always kept in order by date.
  - **Cumulative Spending**: A running total column is automatically calculated and synced.
- **Integrations**:
  - **Google Sheets**: Daily automated upload to a dedicated `Transactions` sheet (Dashboard friendly).
  - **Home Assistant**: REST/Command Line sensors for real-time spending and benefit progress.

## Setup Instructions

### 1. Google Cloud Configuration
- Enable the **Gmail API**, **Google Sheets API**, and **Generative Language API** in the [Google Cloud Console](https://console.cloud.google.com/).
- Create an **OAuth 2.0 Client ID** (Desktop app) and save the JSON as `credentials.json` in the project root.
- Generate a Gemini API Key at [Google AI Studio](https://aistudio.google.com/app/apikey) and save it in `gemini_key.txt`.

### 2. Local Configuration
Create these files locally (they are ignored by Git for privacy):
- `gemini_key.txt`: Your Gemini API key string.
- `recurring_expenses.json`: (Optional)
  ```json
  [{"name": "Donation", "amount": 50.00, "day": 29, "id_prefix": "donation_id"}]
  ```

### 3. Installation
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Automating the Tracker

### macOS (Launch Agent)
1. Edit `com.user.gmailspendingtracker.plist` with your local paths.
2. Move it to `~/Library/LaunchAgents/`.
3. Load it: `launchctl load ~/Library/LaunchAgents/com.user.gmailspendingtracker.plist`.

### Home Assistant (Raspberry Pi)
Add the following to your "Advanced SSH & Web Terminal" add-on configuration:
```yaml
startup_commands:
  - 'cd /config/gmail_spending_tracker && nohup ./start_daemon.sh &'
  - 'cd /config/gmail_spending_tracker && nohup ./upload_daemon.sh &'
```

## Security & Privacy
- **Private Files**: `credentials.json`, `token.json`, `gemini_key.txt`, `transactions.csv`, and `recurring_expenses.json` are all excluded from Git.
- **History**: The repository history has been purged of sensitive configurations.
- **API Safety**: Gemini AI calls are strictly limited to 10 per day by default to stay within the free-tier quota.
