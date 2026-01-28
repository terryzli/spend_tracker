import os.path
import csv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Combined scopes for Gmail (to reuse existing credentials) and Sheets
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

# The ID of your Google Sheet.
# Replace this with the ID you copied from the URL.
SPREADSHEET_ID = "14upQxkTP0ZI3cfJTKzH0DcFnerBBvSy2RPy6Posgdow"
RANGE_NAME = "Transactions!A1"  # Start at the top-left of the Transactions sheet

def get_credentials():
    """Gets valid user credentials from storage or initiates the OAuth flow."""
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return creds

def main():
    """Reads transaction data from a CSV and uploads it to a Google Sheet."""
    creds = get_credentials()
    
    try:
        service = build("sheets", "v4", credentials=creds)
        sheet = service.spreadsheets()

        # --- Read data from CSV ---
        transactions_path = 'transactions.csv'
        if not os.path.exists(transactions_path):
            print("transactions.csv not found. Nothing to upload.")
            return

        values_to_upload = []
        with open(transactions_path, 'r') as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            rows = list(reader)
            
            # Sort rows by date
            rows.sort(key=lambda x: x['date'])
            
            # Recalculate cumulative amount to ensure consistency
            running_total = 0.0
            for row in rows:
                running_total += float(row['amount'])
                row['cumulative_amount'] = round(running_total, 2)
            
            # Explicitly define headers to ensure they appear in Sheets
            display_headers = ["Date", "Amount", "Merchant", "Cumulative Amount"]
            values_to_upload.append(display_headers)
            
            # Use original keys to extract values from dicts
            data_keys = ["date", "amount", "merchant", "cumulative_amount"]
            for row in rows:
                values_to_upload.append([row.get(k, '') for k in data_keys])

        if len(values_to_upload) <= 1:
            print("No transaction data to upload.")
            return

        # --- Clear the existing sheet data ---
        print("Clearing existing data from Transactions sheet...")
        sheet.values().clear(
            spreadsheetId=SPREADSHEET_ID, range="Transactions!A1:Z"
        ).execute()

        # --- Write the new data to the sheet ---
        body = {"values": values_to_upload}
        print(f"Uploading {len(values_to_upload)} rows to Google Sheet...")
        result = (
            sheet.values()
            .update(
                spreadsheetId=SPREADSHEET_ID,
                range=RANGE_NAME,
                valueInputOption="USER_ENTERED",
                body=body,
            )
            .execute()
        )
        print(f"{result.get('updatedCells')} cells updated.")
        print("Upload complete.")

    except HttpError as err:
        print(err)

if __name__ == "__main__":
    main()
