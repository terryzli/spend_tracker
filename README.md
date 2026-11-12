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

### 1. Synchronizing Data from Development Machine to Home Assistant
Since `transactions.csv`, `processed_messages.txt`, and credentials are git-ignored, copy your up-to-date data and tokens to Home Assistant once via SSH:
```bash
scp transactions.csv processed_messages.txt category_cache.json token.json credentials.json gemini_key.txt root@<ha-ip>:/config/gmail_spending_tracker/
```

### 2. Daily Automation on Home Assistant (Recommended)
The cleanest way to run the tracker daily on Home Assistant without keeping background loops in a terminal session is using Home Assistant's native `shell_command` and automation:

1. Add to your `configuration.yaml`:
   ```yaml
   shell_command:
     run_spending_tracker: "cd /config/gmail_spending_tracker && ./scripts/run.sh"
   ```
2. Create an automation (in `automations.yaml` or via the Home Assistant UI) to trigger daily (e.g., at 2:00 AM):
   ```yaml
   - alias: "Daily Spending Tracker Update"
     trigger:
       - platform: time
         at: "02:00:00"
     action:
       - service: shell_command.run_spending_tracker
       - service: homeassistant.update_entity
         target:
           entity_id: sensor.spending_tracker
   ```

### 3. Background Daemon on Home Assistant (Alternative)
If you prefer running a continuous background loop inside the **Advanced SSH & Web Terminal** add-on, configure its `startup_commands` in the add-on settings:
```yaml
startup_commands:
  - 'cd /config/gmail_spending_tracker && nohup ./scripts/start_daemon.sh 86400 > /dev/null 2>&1 &'
```
*(By default, `./scripts/start_daemon.sh` sleeps for 86,400 seconds / 24 hours between runs and executes both `main.py` and `pkg/upload_to_sheets.py` via `scripts/run.sh`)*.

### 4. Home Assistant Sensor Configuration
To display your spending and credit card benefits in your Home Assistant dashboard, add the following `command_line` sensor to `configuration.yaml`:

```yaml
command_line:
  - sensor:
      name: "Spending Tracker"
      unique_id: spending_tracker_report
      command: "/config/gmail_spending_tracker/venv/bin/python /config/gmail_spending_tracker/pkg/report.py"
      value_template: "{{ value_json.monthly_spending }}"
      unit_of_measurement: "USD"
      scan_interval: 3600
      json_attributes:
        - monthly_spending
        - yearly_spending
        - benefits

template:
  - sensor:
      - name: "Spending Yearly"
        unique_id: spending_yearly
        unit_of_measurement: "USD"
        state: "{{ state_attr('sensor.spending_tracker', 'yearly_spending') }}"

      - name: "Venture X Travel Credit Remaining"
        unique_id: venture_x_travel_credit_remaining
        unit_of_measurement: "USD"
        state: "{{ state_attr('sensor.spending_tracker', 'benefits')['venture_x']['travel_credit']['remaining'] }}"

      - name: "Amex Gold Uber Credit Remaining"
        unique_id: amex_gold_uber_credit_remaining
        unit_of_measurement: "USD"
        state: "{{ state_attr('sensor.spending_tracker', 'benefits')['amex_gold']['uber_credit']['remaining'] }}"

      - name: "US Altitude Reserve Travel Remaining"
        unique_id: us_altitude_reserve_travel_remaining
        unit_of_measurement: "USD"
        state: "{{ state_attr('sensor.spending_tracker', 'benefits')['us_altitude_reserve']['travel_credit']['remaining'] }}"
```

### macOS (Launch Agent)
1. Edit `scripts/com.user.gmailspendingtracker.plist` with your local paths.
2. Move it to `~/Library/LaunchAgents/`.
3. Load it: `launchctl load ~/Library/LaunchAgents/com.user.gmailspendingtracker.plist`.

## Security & Privacy
- **Private Files**: `credentials.json`, `token.json`, `gemini_key.txt`, `transactions.csv`, and `recurring_expenses.json` are all excluded from Git.
- **History**: The repository history has been purged of sensitive configurations.
- **API Safety**: Gemini AI calls are strictly limited to 10 per day by default to stay within the free-tier quota.
