# Gmail Spending Tracker

An automated financial tool that scrapes transaction emails from Gmail, categorizes spending, tracks credit card benefits, and syncs data to Google Sheets and Home Assistant.

## Features

- **Multi-Bank Support & Card Detection**: Automated regex parsing and issuer/card detection for:
  - **U.S. Bank**: "Large Purchase" and "New Transaction" alerts (e.g., Altitude Reserve).
  - **American Express**: Standard transaction notifications (e.g., Amex Gold).
  - **Bank of America**: "Transaction exceeds limit" alerts.
  - **Capital One**: "New transaction charged" alerts (e.g., Venture X).
  - **Chase & Citi**: Structured subject and body transaction alerts.
- **AI-Powered Fallback & Categorization**: Uses **Gemini 3.5 Flash-Lite** (configurable in `config/spend_tracker.json`, via the `google-genai` SDK) with strict structured JSON schemas, local keyword matching from `config/categories.json`, rate limiting, and retry backoff.
- **Recurring Expenses**: Support for scheduled monthly transactions (e.g., donations, rent) via a private `recurring_expenses.json` (including automatic end-of-month clamping for days 29–31).
- **Advanced Data Management**:
  - **Accurate Deduplication**: Tracks Gmail `msg_id` so legitimate same-day repeat purchases are preserved while duplicates are removed.
  - **Card-Aware Benefit Tracking**: Tracks the `Card` column in both `transactions.csv` and Google Sheets so overlapping benefit keywords (like Uber across Amex Gold and U.S. Bank Altitude Reserve) aren't double-counted.
  - **Atomic File Writes & Safe Sheets Sync**: Uses atomic file replacements locally and updates Google Sheets before clearing any trailing rows.
  - **Chronological Sorting & Cumulative Spending**: Running total column is automatically calculated and synced.
- **Integrations**:
  - **Google Sheets**: Daily automated upload (`Date`, `Amount`, `Merchant`, `Category`, `Cumulative Amount`, `Card`) to a dedicated `Transactions` sheet.
  - **Home Assistant**: Command-line sensors for real-time spending and benefit progress.

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
scp transactions.csv processed_messages.txt category_cache.json token.json credentials.json gemini_key.txt root@<ha-ip>:/config/spend_tracker/
```

### 2. Daily Automation on Home Assistant (Recommended)
The cleanest way to run the tracker daily on Home Assistant without keeping background loops in a terminal session is using Home Assistant's native `shell_command` and automation:

1. Add to your `configuration.yaml`:
   ```yaml
   shell_command:
     run_spending_tracker: "cd /config/spend_tracker && ./scripts/run.sh"
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
  - 'cd /config/spend_tracker && nohup ./scripts/start_daemon.sh 86400 > /dev/null 2>&1 &'
```
*(By default, `./scripts/start_daemon.sh` sleeps for 86,400 seconds / 24 hours between runs and executes both `main.py` and `pkg/upload_to_sheets.py` via `scripts/run.sh`)*.

### 4. Home Assistant Sensor Configuration
To display your spending and credit card benefits in your Home Assistant dashboard, add the following `command_line` sensor to `configuration.yaml`:

```yaml
command_line:
  - sensor:
      name: "Spending Tracker"
      unique_id: spending_tracker_report
      command: "/config/spend_tracker/venv/bin/python /config/spend_tracker/pkg/report.py"
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

Each benefit entry in `pkg/report.py` also includes `used_up` (`true`/`false`), `percent_used`, `period`, and `days_remaining` so you can create Home Assistant `binary_sensor` templates or "use-it-or-lose-it" alerts.

### Credit Card Benefits & Last-4 Mapping
- **Last-4 card mapping**: Add `"card_last4_map": {"1234": "Amex Gold", "5678": "U.S. Bank Altitude Reserve"}` in `config/spend_tracker.json` to map card alerts by last 4 digits (useful when you hold multiple cards from the same bank).
- **Category matching**: Benefits in `config/benefits.json` support `"categories"` (e.g., `["Travel", "Dining"]` for U.S. Bank Altitude Reserve) in addition to `"keywords"`.
- **Anniversary reset cycles**: Set `"reset_cycle": "anniversary"` with `"anniversary_month"` and `"anniversary_day"` in `config/benefits.json` for credits that reset on your cardmember anniversary instead of January 1.

### macOS (Launch Agent)
1. Edit `scripts/com.user.gmailspendingtracker.plist` with your local paths.
2. Move it to `~/Library/LaunchAgents/`.
3. Load it: `launchctl load ~/Library/LaunchAgents/com.user.gmailspendingtracker.plist`.

## CLI Usage

```bash
# Standard incremental sync
./venv/bin/python main.py

# Preview changes without modifying CSV, cache, or processed IDs
./venv/bin/python main.py --dry-run -v

# Search since a specific date or override the Gemini model
./venv/bin/python main.py --since 2026-10-01 --model gemini-3.8-flash

# Re-categorize all historical rows using overrides, categories.json keywords, and Gemini
./venv/bin/python main.py --recategorize

# Upload to Google Sheets (supports --dry-run and --force)
./venv/bin/python pkg/upload_to_sheets.py --dry-run
```

## Testing

```bash
./venv/bin/pip install pytest
./venv/bin/pytest -v
```

## Security & Privacy
- **Private Files**: `credentials.json`, `token.json`, `gemini_key.txt`, `transactions.csv`, and `recurring_expenses.json` are all excluded from Git.
- **History**: The repository history has been purged of sensitive configurations.
- **API Safety**: Gemini model, daily request limit, and RPM throttling are configurable under `"gemini"` in `config/spend_tracker.json`.
