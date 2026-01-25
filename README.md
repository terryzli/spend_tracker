# Gmail Spending Tracker

This project provides a Python script to track your spending by scraping transaction emails from your Gmail account. It also tracks progress towards specific credit card benefits.

## Setup Instructions

To get started, you'll need to create a project in the Google Cloud Console, enable the Gmail API, and download your credentials.

**Step 1: Go to the Google Cloud Console**
   - Open your web browser and navigate to the [Google Cloud Console](https://console.cloud.google.com/).
   - If you don't have a project already, create a new one.

**Step 2: Enable the Gmail API**
   - In the search bar at the top, type "Gmail API" and select it from the results.
   - Click the "Enable" button.

**Step 3: Create Credentials**
   - After enabling the API, click the "Create Credentials" button.
   - In the "Which API are you using?" dropdown, select "Gmail API".
   - For "What data will you be accessing?", choose "User data".
   - Click "Next".

**Step 4: Configure the OAuth Consent Screen**
   - You'll be prompted to configure the consent screen.
   - Choose "External" for the user type.
   - Fill in the required fields (App name, User support email, and developer contact information). You can use "Gmail Spending Tracker" for the app name.
   - Click "Save and Continue" through the "Scopes" and "Test users" sections. You don't need to add any scopes or test users at this point.
   - Finally, click "Back to Dashboard".

**Step 5: Create OAuth 2.0 Client ID**
   - Go back to the "Credentials" page (you can search for "Credentials" in the search bar).
   - Click "Create Credentials" again and select "OAuth client ID".
   - For "Application type", choose "Desktop app".
   - Give it a name (e.g., "Spending Tracker Client").
   - Click "Create".

**Step 6: Download Your Credentials**
   - A window will pop up with your client ID and secret.
   - Click the "Download JSON" button to download your credentials file.
   - **IMPORTANT:** Rename this file to `credentials.json` and move it into the `gmail_spending_tracker` directory.

## Running the Script as a Daemon (macOS)

We have created all the necessary files to run the script as a background daemon. Here's how to set it up:

**Step 1: Move the `.plist` file**

Move the `com.user.gmailspendingtracker.plist` file from the `gmail_spending_tracker` directory to `~/Library/LaunchAgents/`. You can do this with the following command in your terminal:

```bash
mv /Users/terrenceli/gmail_spending_tracker/com.user.gmailspendingtracker.plist ~/Library/LaunchAgents/
```

**Step 2: Load the `launchd` job**

Use the `launchctl` command to load the job. This will schedule the script to run every hour.

```bash
launchctl load ~/Library/LaunchAgents/com.user.gmailspendingtracker.plist
```

**Step 3: Verify the job**

You can verify that the job is loaded by running this command:

```bash
launchctl list | grep com.user.gmailspendingtracker
```

If you see an entry with the label `com.user.gmailspendingtracker`, the job is loaded successfully.

**How it works:**

*   The `launchd` service will now run your script every hour.
*   The output of the script will be saved to `/Users/terrenceli/gmail_spending_tracker/spending_tracker.log`.
*   Any errors will be saved to `/Users/terrenceli/gmail_spending_tracker/spending_tracker.err`.

You can now check these log files to see the output of the script.

**To stop the daemon:**

You can use the following command:

```bash
launchctl unload ~/Library/LaunchAgents/com.user.gmailspendingtracker.plist
```

## Running the Script as a Daemon (Home Assistant on Raspberry Pi)

To run this script as a daemon on Home Assistant, it's recommended to use the "Advanced SSH & Web Terminal" add-on.

**Step 1: Access Add-on Configuration**

1.  Open your Home Assistant UI in a web browser.
2.  Go to **Settings -> Add-ons**.
3.  Click on the **"Advanced SSH & Web Terminal"** add-on.

**Step 2: Modify the Add-on Configuration**

1.  On the add-on's page, navigate to the **Configuration** tab.
2.  Look for a setting named `startup_commands` or `init_commands`.
3.  Add the following command to the list to run the script automatically when the add-on starts:
    ```yaml
    startup_commands:
      - 'cd /config/gmail_spending_tracker && nohup ./start_daemon.sh &'
      - 'cd /config/gmail_spending_tracker && nohup ./upload_daemon.sh &'
    ```
    (Make sure to maintain proper YAML indentation if there are existing entries).

**Step 3: Save and Restart the Add-on**

1.  After adding the command, click **Save**.
2.  Go back to the add-on's **Info** tab and click **Restart**.

The script will now automatically start whenever the "Advanced SSH & Web Terminal" add-on starts. You can verify it's running by checking the `spending_tracker.log` and `spending_tracker.err` files in `/config/gmail_spending_tracker/` a few minutes after restarting the add-on.

### Home Assistant Sensor Configuration

To display the spending data in your Home Assistant dashboard, you'll need to create `command_line` sensors.

1.  **Edit your `configuration.yaml` file:** Use the **File editor** or **Studio Code Server** add-ons in Home Assistant to edit your `configuration.yaml`.
2.  **Add the sensor configuration:** Add the following YAML to your `configuration.yaml` file.

    ```yaml
    sensor:
      - platform: command_line
        name: "Monthly Spending"
        command: "/config/gmail_spending_tracker/venv/bin/python /config/gmail_spending_tracker/report.py"
        value_template: "{{ value_json.monthly_spending }}"
        unit_of_measurement: "$"
        scan_interval: 3600

      - platform: command_line
        name: "Yearly Spending"
        command: "/config/gmail_spending_tracker/venv/bin/python /config/gmail_spending_tracker/report.py"
        value_template: "{{ value_json.yearly_spending }}"
        unit_of_measurement: "$"
        scan_interval: 3600

      - platform: command_line
        name: "Venture X Travel Credit"
        command: "/config/gmail_spending_tracker/venv/bin/python /config/gmail_spending_tracker/report.py"
        value_template: "{{ value_json.benefits.venture_x.travel_credit.spent }}"
        unit_of_measurement: "$"
        scan_interval: 3600

      - platform: command_line
        name: "Amex Gold Dunkin Credit"
        command: "/config/gmail_spending_tracker/venv/bin/python /config/gmail_spending_tracker/report.py"
        value_template: "{{ value_json.benefits.amex_gold.dunkin_credit.spent }}"
        unit_of_measurement: "$"
        scan_interval: 3600
        
      - platform: command_line
        name: "Amex Gold Dining Credit"
        command: "/config/gmail_spending_tracker/venv/bin/python /config/gmail_spending_tracker/report.py"
        value_template: "{{ value_json.benefits.amex_gold.dining_credit.spent }}"
        unit_of_measurement: "$"
        scan_interval: 3600

      - platform: command_line
        name: "Amex Gold Uber Credit"
        command: "/config/gmail_spending_tracker/venv/bin/python /config/gmail_spending_tracker/report.py"
        value_template: "{{ value_json.benefits.amex_gold.uber_credit.spent }}"
        unit_of_measurement: "$"
        scan_interval: 3600
        
      - platform: command_line
        name: "Amex Gold Resy Credit"
        command: "/config/gmail_spending_tracker/venv/bin/python /config/gmail_spending_tracker/report.py"
        value_template: "{{ value_json.benefits.amex_gold.resy_credit.spent }}"
        unit_of_measurement: "$"
        scan_interval: 3600

      - platform: command_line
        name: "US Altitude Reserve Travel Credit"
        command: "/config/gmail_spending_tracker/venv/bin/python /config/gmail_spending_tracker/report.py"
        value_template: "{{ value_json.benefits.us_altitude_reserve.travel_credit.spent }}"
        unit_of_measurement: "$"
        scan_interval: 3600
    ```

3.  **Check and restart:**
    *   **Check your configuration:** Go to **Developer Tools -> YAML** and click the **"Check Configuration"** button.
    *   **Restart Home Assistant:** If the configuration is valid, restart Home Assistant from **Settings -> System -> Restart**.

Once Home Assistant restarts, you will have new sensors (e.g., `sensor.monthly_spending`, `sensor.amex_gold_resy_credit`, etc.) that you can add to your dashboard.

### Google Sheets Integration

To upload your transaction data to a Google Sheet once a day:

**Step 1: Enable the Google Sheets API**

1.  Go to your [Google Cloud Console](https://console.cloud.google.com/), select the same project you used for the Gmail API.
2.  In the search bar at the top, type **"Google Sheets API"** and select it from the results.
3.  Click the **"Enable"** button.

**Step 2: Create a Google Sheet and Get its ID**

1.  Go to [sheets.new](https://sheets.new) to create a new, blank Google Sheet.
2.  Give it a name, for example, "My Spending Tracker".
3.  The **Sheet ID** is the long string of characters in the URL between `/d/` and `/edit`. For example, in the URL `https://docs.google.com/spreadsheets/d/1qZ_1a2b3c4d5e6f7g8h9i0j/edit`, the Sheet ID is `1qZ_1a2b3c4d5e6f7g8h9i0j`.
4.  Update the `SPREADSHEET_ID` variable in `upload_to_sheets.py` with your Sheet ID.

**Step 3: Re-authenticate for New Permissions**

Since we added a new permission scope for Google Sheets, you need to generate a new `token.json` file.

1.  **Delete `token.json` from your local machine:**
    ```bash
    rm gmail_spending_tracker/token.json
    ```
2.  **Delete `token.json` from your Home Assistant:**
    ```bash
    ssh -i ~/.ssh/ha_id_ed25519 root@192.168.0.155 -p 22 "rm /config/gmail_spending_tracker/token.json"
    ```
3.  On your **desktop computer**, navigate to your local `gmail_spending_tracker` directory.
4.  Run the `main.py` script once:
    ```bash
    ./venv/bin/python main.py
    ```
    This will open a web browser again. This time, when you grant permissions, it will include access to Google Sheets. A new `token.json` file will be created locally.
5.  **Copy this new `token.json` back to your Home Assistant:**
    ```bash
    scp -i ~/.ssh/ha_id_ed25519 -P 22 token.json root@192.168.0.155:/config/gmail_spending_tracker/
    ```

**Step 4: Update Home Assistant Add-on `startup_commands`**

You need to modify the `startup_commands` in your "Advanced SSH & Web Terminal" add-on configuration to start both daemon scripts.

1.  Go to **Settings -> Add-ons -> "Advanced SSH & Web Terminal"**.
2.  Navigate to the **Configuration** tab.
3.  Update your `startup_commands` to include the `upload_daemon.sh` script. It should look like this:

    ```yaml
    startup_commands:
      - 'cd /config/gmail_spending_tracker && nohup ./start_daemon.sh &'
      - 'cd /config/gmail_spending_tracker && nohup ./upload_daemon.sh &'
    ```
    (Ensure correct YAML indentation).

4.  Click **Save**.
5.  Go back to the add-on's **Info** tab and click **Restart**.

After the add-on restarts and you've re-authenticated, your `transactions.csv` will be checked hourly, and the data will be uploaded to your Google Sheet once a day. You can check `upload_daemon.log` and `upload_daemon.err` in `/config/gmail_spending_tracker/` for the upload status.

## Customization

### Transaction Parsing

The current script uses a regular expression to parse U.S. Bank transaction emails. If you have transaction emails from other banks or in different formats, you will need to modify the `parse_email_body` function in `main.py` to add new parsing logic.

### Benefit Tracking

You can customize the benefit tracking by editing the `benefits.json` file in the `gmail_spending_tracker` directory. You can add new cards, new benefits, and update the keywords and total amounts.

### Running Interval

*   **macOS:** You can change how often the daemon runs by editing the `StartInterval` key in the `com.user.gmailspendingtracker.plist` file. The value is in seconds. After editing, you'll need to unload and reload the `launchd` job.
*   **Home Assistant:** You can change the interval by editing the `sleep` value in the `start_daemon.sh` script. The value is in seconds.
