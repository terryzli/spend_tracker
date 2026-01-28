import os.path
import base64
import re
import csv
import json
from datetime import datetime
from bs4 import BeautifulSoup # ADD THIS IMPORT

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

def get_email_date(headers):
    for header in headers:
        if header["name"] == "Date":
            return header["value"]
    return ""

def parse_email_body(body):
    """
    Parses the email body to extract transaction details.
    Tries various regex patterns for different bank formats, then HTML parsing.
    Returns a dict with 'amount', 'merchant', and 'date'.
    """
    # 1. Try stripping HTML first to get clean text for regex matching
    # This works for most U.S. Bank emails whether they are plain text or HTML
    try:
        soup_text = BeautifulSoup(body, 'html.parser').get_text(separator=' ')
        # Pattern: "charged $X at Y."
        # Use \s+ to handle potential newlines or multiple spaces between words
        match = re.search(r"charged\s+\$(?P<amount>[\d.]+)\s+at\s+(?P<merchant>[^.]+)\.", soup_text, re.DOTALL)
        if match:
            res = match.groupdict()
            # Clean up merchant name (remove extra whitespace/newlines)
            res['merchant'] = ' '.join(res['merchant'].split())
            return res
    except Exception:
        pass

    # 2. Try Bank of America specific parsing
    try:
        # Bank of America emails have a very specific table structure with labels
        if "bankofamerica.com" in body or "Bank of America" in body:
            soup = BeautifulSoup(body, 'html.parser')
            text = soup.get_text(separator=' ')
            
            amount_match = re.search(r"Amount:\s+\$(?P<amount>[\d,.]+)", text)
            merchant_match = re.search(r"Where:\s+(?P<merchant>.+?)(?:\s{2,}|\n|$)", text)
            date_match = re.search(r"Date:\s+(?P<date>\w+\s+\d{1,2},\s+\d{4})", text)
            
            if amount_match and merchant_match:
                amount = amount_match.group('amount').replace(',', '')
                merchant = merchant_match.group('merchant').strip()
                res = {"amount": amount, "merchant": merchant}
                
                if date_match:
                    try:
                        date_obj = datetime.strptime(date_match.group('date'), '%B %d, %Y')
                        res['date'] = date_obj.strftime('%Y-%m-%d')
                    except ValueError:
                        pass
                return res
    except Exception:
        pass

    # 3. If simple regex fails, try specific HTML structure parsing (e.g., for Amex)
    try:
        soup = BeautifulSoup(body, 'html.parser')
        
        # --- Amex specific parsing ---
        # Look for the section that contains the transaction details.
        # Using a general class mj-column-per-50 to find the transaction detail columns
        transaction_columns = soup.find_all('div', class_=re.compile(r'mj-column-per-50'))

        # Assuming the first mj-column-per-50 is merchant and the second is amount/date
        if len(transaction_columns) >= 2:
            merchant_column = transaction_columns[0]
            amount_date_column = transaction_columns[1]

            # Extract merchant text from the first column
            merchant_p_tag = merchant_column.find('p', string=True) # Changed text=True to string=True
            
            # Extract amount and date from the second column
            amount_p_tag = amount_date_column.find('p', string=re.compile(r'\$[\d.]+')) # Changed text=re.compile to string=re.compile
            date_p_tag = amount_date_column.find('p', string=re.compile(r'Mon|Tue|Wed|Thu|Fri|Sat|Sun, \w{3} \d{1,2}, \d{4}')) # Changed text=re.compile to string=re.compile

            if merchant_p_tag and amount_p_tag and date_p_tag:
                merchant_text = merchant_p_tag.get_text(strip=True)
                amount_text = amount_p_tag.get_text(strip=True)
                date_text = date_p_tag.get_text(strip=True)
                
                # Extract amount (remove '$' and '*' if present)
                amount_value_match = re.search(r'\$([\d.]+)', amount_text)
                amount_value = amount_value_match.group(1) if amount_value_match else None
                
                # Amex date format: "Mon, Jan 12, 2026"
                try:
                    amex_date_obj = datetime.strptime(date_text, '%a, %b %d, %Y')
                    formatted_date = amex_date_obj.strftime('%Y-%m-%d')
                    
                    if amount_value and merchant_text and formatted_date:
                        return {
                            "amount": amount_value,
                            "merchant": merchant_text,
                            "date": formatted_date # Return date parsed from HTML
                        }
                except ValueError:
                    pass # Date parsing failed
    
    except Exception as e:
        # Log the exception for debugging
        print(f"Amex HTML parsing failed: {e}")
        pass

    return None # If no match found by either method

def load_benefits():
    """Loads benefit configuration from benefits.json."""
    with open("benefits.json", "r") as f:
        return json.load(f)

def check_benefits(transaction, benefits):
    """Checks if a transaction applies to any benefit and prints a notification."""
    for card, card_benefits in benefits.items():
        for benefit, details in card_benefits.items():
            for keyword in details["keywords"]:
                if keyword.lower() in transaction["merchant"].lower():
                    print(
                        f"  -> Found transaction for '{benefit}' credit on your {card} card."
                    )
                    return # Avoid multiple notifications for the same transaction

def load_processed_messages():
    """Loads the set of processed message IDs."""
    if os.path.exists("processed_messages.txt"):
        with open("processed_messages.txt", "r") as f:
            return set(f.read().splitlines())
    return set()

def save_processed_messages(processed_ids):
    """Saves the set of processed message IDs."""
    with open("processed_messages.txt", "w") as f:
        for msg_id in processed_ids:
            f.write(f"{msg_id}\n")

def process_recurring_expenses(processed_ids, benefits):
    """
    Checks for recurring expenses that should be logged today.
    """
    path = "recurring_expenses.json"
    if not os.path.exists(path):
        return False

    with open(path, "r") as f:
        recurring = json.load(f)

    now = datetime.now()
    new_found = False
    
    # We'll open the file in the main logic, but for recurring we can just return the objects
    to_log = []

    for item in recurring:
        # Check if today is the day (or if we past the day but haven't logged it for this month yet)
        # To be safe and simple: if today is >= scheduled day and we haven't logged it for this MONTH/YEAR yet.
        pseudo_id = f"{item['id_prefix']}_{now.strftime('%Y_%m')}"
        
        if now.day >= item['day'] and pseudo_id not in processed_ids:
            transaction = {
                "date": now.strftime("%Y-%m-%d"), # Log it as today
                "amount": str(item['amount']),
                "merchant": item['name']
            }
            to_log.append((transaction, pseudo_id))
            new_found = True

    return to_log

def main():
    """
    Scans Gmail for new transaction emails and appends them to a CSV file.
    """
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

    service = build("gmail", "v1", credentials=creds)
    benefits = load_benefits()
    processed_message_ids = load_processed_messages()
    new_transactions_found = False

    try:
        # --- Handle Recurring Expenses First ---
        recurring_to_log = process_recurring_expenses(processed_message_ids, benefits)
        
        results = (
            service.users()
            .messages()
            .list(userId="me", q='\"Large Purchase Approved\" OR \"Transaction Alert\" OR \"Your U.S. Bank credit card has a new transaction\" OR \"Credit card transaction exceeds alert limit you set\"')
            .execute()
        )
        messages = results.get("messages", [])

        file_exists = os.path.exists("transactions.csv")
        with open("transactions.csv", "a", newline="") as csvfile:
            fieldnames = ["date", "amount", "merchant", "cumulative_amount"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            if not file_exists:
                writer.writeheader()

            for transaction, pseudo_id in recurring_to_log:
                if not new_transactions_found:
                    print("New transactions found (including recurring):")
                    new_transactions_found = True
                
                writer.writerow(transaction)
                print(f"Logged recurring transaction: {transaction}")
                check_benefits(transaction, benefits)
                processed_message_ids.add(pseudo_id)

            for message in messages:

                if not new_transactions_found:
                    print("New transactions found:")
                    new_transactions_found = True

                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=message["id"], format="full")
                    .execute()
                )
                payload = msg["payload"]
                headers = payload["headers"]
                raw_date = get_email_date(headers) # Get raw date here

                transaction = None
                # Check for HTML part first
                html_part_body = None
                plain_text_part_body = None

                if "parts" in payload:
                    for part in payload["parts"]:
                        if part["mimeType"] == "text/html" and part["body"].get("data"):
                            html_part_body = base64.urlsafe_b64decode(part["body"].get("data")).decode("utf-8")
                            # Try parsing HTML first
                            transaction = parse_email_body(html_part_body)
                            if transaction:
                                break
                    if not transaction: # If not found in HTML, try plain text
                        for part in payload["parts"]:
                            if part["mimeType"] == "text/plain" and part["body"].get("data"):
                                plain_text_part_body = base64.urlsafe_b64decode(part["body"].get("data")).decode("utf-8")
                                # Then try parsing plain text
                                transaction = parse_email_body(plain_text_part_body)
                                if transaction:
                                    break
                else: # If no parts, assume full body is plain text or HTML (try both)
                    body_data = payload["body"].get("data")
                    if body_data:
                        decoded_body = base64.urlsafe_b64decode(body_data).decode("utf-8")
                        # Heuristic: if it looks like HTML, try parsing as HTML, else plain text
                        if "<html>" in decoded_body or "<div" in decoded_body:
                            transaction = parse_email_body(decoded_body) # Try as HTML
                        if not transaction:
                            transaction = parse_email_body(decoded_body) # Try as plain text
                            

                if transaction:
                    # Use date from transaction if available (from HTML parsing in parse_email_body)
                    if "date" in transaction:
                        formatted_date = transaction["date"]
                    else:
                        # Otherwise, parse from raw_date header
                        try:
                            # Strip any timezone names in parentheses like (UTC) which strptime doesn't like
                            clean_date = re.sub(r'\s*\([^)]*\)', '', raw_date).strip()
                            date_obj = datetime.strptime(clean_date, '%a, %d %b %Y %H:%M:%S %z')
                            formatted_date = date_obj.strftime('%Y-%m-%d')
                        except (ValueError, TypeError):
                            formatted_date = datetime.now().strftime('%Y-%m-%d')
                    
                    transaction["date"] = formatted_date
                    writer.writerow(transaction)
                    print(f"Logged transaction: {transaction}")
                    check_benefits(transaction, benefits)
                    processed_message_ids.add(message["id"])
                else:
                    # Diagnostic print for un-parsable emails
                    subject = ""
                    for header in headers:
                        if header['name'] == 'Subject':
                            subject = header['value']
                            break
                    print(f"--> FAILED TO PARSE: Subject: '{subject}'")

        # --- Finalize and Deduplicate CSV ---
        # We do this every time to ensure the file is always sorted, 
        # cumulative totals are correct, and no duplicates exist.
        if os.path.exists("transactions.csv"):
            all_rows = []
            with open("transactions.csv", "r", newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                all_rows = list(reader)
            
            if all_rows:
                # Deduplicate based on date, amount, and merchant
                unique_rows = []
                seen_rows = set()
                for row in all_rows:
                    # Create a tuple of the core data to use as a key
                    row_key = (row['date'], row['amount'], row['merchant'])
                    if row_key not in seen_rows:
                        unique_rows.append(row)
                        seen_rows.add(row_key)
                
                # Sort by date
                unique_rows.sort(key=lambda x: x['date'])
                
                # Recalculate cumulative amount
                running_total = 0.0
                for row in unique_rows:
                    running_total += float(row['amount'])
                    row['cumulative_amount'] = round(running_total, 2)
                
                with open("transactions.csv", "w", newline="") as csvfile:
                    fieldnames = ["date", "amount", "merchant", "cumulative_amount"]
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(unique_rows)

    except HttpError as error:
        print(f"An error occurred: {error}")
    
    finally:
        if new_transactions_found:
            save_processed_messages(processed_message_ids)
            print("Finished processing new transactions.")
        else:
            print("No new transactions to process.")

if __name__ == "__main__":
    main()