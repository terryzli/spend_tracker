import os.path
import base64
import re
import csv
import json
from datetime import datetime
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

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

GEMINI_KEY_FILE = "gemini_key.txt"
GEMINI_USAGE_FILE = "gemini_usage.json"
DAILY_LIMIT = 10

def get_gemini_usage_count():
    today = datetime.now().strftime('%Y-%m-%d')
    if os.path.exists(GEMINI_USAGE_FILE):
        with open(GEMINI_USAGE_FILE, 'r') as f:
            try:
                data = json.load(f)
                if data.get("date") == today:
                    return data.get("count", 0)
            except: pass
    return 0

def increment_gemini_usage():
    today = datetime.now().strftime('%Y-%m-%d')
    count = get_gemini_usage_count() + 1
    with open(GEMINI_USAGE_FILE, 'w') as f:
        json.dump({"date": today, "count": count}, f)

def load_category_cache():
    path = "category_cache.json"
    if os.path.exists(path):
        with open(path, "r") as f:
            try: return json.load(f)
            except: return {}
    return {}

def save_category_cache(cache):
    with open("category_cache.json", "w") as f:
        json.dump(cache, f, indent=2)

def load_category_overrides():
    path = "category_overrides.json"
    if os.path.exists(path):
        with open(path, "r") as f:
            try: return json.load(f)
            except: return {}
    return {}

def get_batch_ai_categories(merchants, cache, overrides):
    """Categorizes multiple merchants in one Gemini call."""
    results = {}
    to_ask = []
    for merchant in merchants:
        merchant_upper = merchant.upper()
        found = False
        for pattern, cat in overrides.items():
            if pattern.upper() in merchant_upper:
                results[merchant] = cat
                found = True
                break
        if not found:
            if merchant in cache: results[merchant] = cache[merchant]
            else: to_ask.append(merchant)
    if not to_ask: return results
    usage = get_gemini_usage_count()
    if usage >= DAILY_LIMIT:
        for m in to_ask: results[m] = "Other"
        return results
    if not os.path.exists(GEMINI_KEY_FILE): return results
    with open(GEMINI_KEY_FILE, 'r') as f: api_key = f.read().strip()
    try:
        client = genai.Client(api_key=api_key)
        merchant_list = "\n".join([f"- {m}" for m in to_ask])
        prompt = f"Categorize these merchants into a short 1-2 word category (e.g., Dining, Groceries, Travel, Utilities, Shopping, Gym, Investment). Return ONLY a JSON object.\nMerchants:\n{merchant_list}"
        response = client.models.generate_content(model='gemini-2.5-flash-lite', contents=prompt, config=types.GenerateContentConfig(response_mime_type='application/json'))
        increment_gemini_usage()
        if response.text:
            batch_results = json.loads(response.text.strip())
            for m, cat in batch_results.items():
                results[m] = cat
                cache[m] = cat
            save_category_cache(cache)
    except:
        for m in to_ask: results[m] = "Other"
    return results

def process_transaction_rules(transaction):
    """Applies custom rules to modify transaction data (e.g., fixed amounts)."""
    merchant_upper = transaction['merchant'].upper()
    if "BAY CLUB" in merchant_upper:
        if transaction['amount'] != "160.00":
            print(f"  -> Applied rule: Adjusted {transaction['merchant']} amount from ${transaction['amount']} to $160.00")
            transaction['amount'] = "160.00"
    return transaction

def parse_with_gemini(body):
    if not os.path.exists(GEMINI_KEY_FILE): return None
    usage = get_gemini_usage_count()
    if usage >= DAILY_LIMIT: return None
    with open(GEMINI_KEY_FILE, 'r') as f: api_key = f.read().strip()
    try:
        client = genai.Client(api_key=api_key)
        soup_text = BeautifulSoup(body, 'html.parser').get_text(separator=' ')
        clean_text = ' '.join(soup_text.split())[:3000] 
        prompt = f"Extract transaction details. Return ONLY a JSON object with keys: 'amount' (string), 'merchant' (string), 'date' (YYYY-MM-DD). Email: {clean_text}"
        response = client.models.generate_content(model='gemini-2.5-flash-lite', contents=prompt, config=types.GenerateContentConfig(response_mime_type='application/json'))
        increment_gemini_usage()
        if response.text:
            data = json.loads(response.text.strip())
            if data and all(k in data for k in ['amount', 'merchant', 'date']):
                data['amount'] = str(data['amount']).replace('$', '').replace(',', '')
                return data
    except: pass
    return None

def parse_email_body(body):
    try:
        soup_text = BeautifulSoup(body, 'html.parser').get_text(separator=' ')
        match = re.search(r"charged\s+\$(?P<amount>[\d.]+)\s+at\s+(?P<merchant>[^.]+)\.", soup_text, re.DOTALL)
        if not match: match = re.search(r"Amount:\s+\$(?P<amount>[\d.]+).*?Merchant:\s+(?P<merchant>[^.\n]+)", soup_text, re.DOTALL | re.IGNORECASE)
        if match:
            res = match.groupdict()
            res['merchant'] = ' '.join(res['merchant'].split())
            return res
    except: pass
    if "capitalone.com" in body:
        soup_text = BeautifulSoup(body, 'html.parser').get_text(separator=' ')
        cap_match = re.search(r"at\s+(?P<merchant>.+?),\s+a\s+pending.*?amount\s+of\s+\$(?P<amount>[\d,.]+)", soup_text, re.IGNORECASE)
        if cap_match: return {"amount": cap_match.group('amount').replace(',', ''), "merchant": cap_match.group('merchant').strip()}
    return parse_with_gemini(body)

def get_email_date(headers):
    for h in headers:
        if h["name"] == "Date": return h["value"]
    return ""

def load_benefits():
    with open("benefits.json", "r") as f: return json.load(f)

def check_benefits(transaction, benefits):
    for card, card_benefits in benefits.items():
        for benefit, details in card_benefits.items():
            for keyword in details["keywords"]:
                if keyword.lower() in transaction["merchant"].lower():
                    print(f"  -> Found transaction for '{benefit}' credit on your {card} card.")
                    return

def load_processed_messages():
    if os.path.exists("processed_messages.txt"):
        with open("processed_messages.txt", "r") as f: return set(f.read().splitlines())
    return set()

def save_processed_messages(processed_ids):
    with open("processed_messages.txt", "w") as f:
        for msg_id in processed_ids: f.write(f"{msg_id}\n")

def main():
    creds = None
    if os.path.exists("token.json"): creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token: token.write(creds.to_json())

    service = build("gmail", "v1", credentials=creds)
    benefits = load_benefits()
    cache = load_category_cache()
    overrides = load_category_overrides()
    processed_message_ids = load_processed_messages()
    new_found = False

    try:
        temp_list = []
        query = '\"Large Purchase Approved\" OR \"Transaction Alert\" OR \"Your U.S. Bank credit card has a new transaction\" OR \"Credit card transaction exceeds alert limit you set\" OR \"Fwd: Large Purchase Approved\" OR \"Fwd: Your U.S. Bank credit card has a new transaction\" OR \"A new transaction was charged to your account\"'
        results = service.users().messages().list(userId="me", q=query).execute()
        messages = results.get("messages", [])
        for message in messages:
            if message["id"] in processed_message_ids: continue
            msg = service.users().messages().get(userId="me", id=message["id"], format="full").execute()
            payload = msg["payload"]
            body = ""
            if "parts" in payload:
                for part in payload["parts"]:
                    if part["mimeType"] == "text/html" and part["body"].get("data"):
                        body = base64.urlsafe_b64decode(part["body"].get("data")).decode("utf-8")
                        break
            transaction = parse_email_body(body)
            if transaction:
                transaction = process_transaction_rules(transaction)
                if "date" not in transaction:
                    try:
                        raw_date = get_email_date(payload["headers"])
                        clean_date = re.sub(r'\s*\([^)]*\)', '', raw_date).strip()
                        date_obj = datetime.strptime(clean_date, '%a, %d %b %Y %H:%M:%S %z')
                        transaction["date"] = date_obj.strftime('%Y-%m-%d')
                    except: transaction["date"] = datetime.now().strftime('%Y-%m-%d')
                transaction['msg_id'] = message["id"]
                temp_list.append(transaction)
                new_found = True

        if temp_list:
            file_exists = os.path.exists("transactions.csv")
            with open("transactions.csv", "a", newline="") as csvfile:
                fieldnames = ["date", "amount", "merchant", "category", "cumulative_amount"]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                if not file_exists: writer.writeheader()
                for t in temp_list:
                    msg_id = t.pop('msg_id')
                    writer.writerow(t)
                    processed_message_ids.add(msg_id)

        if os.path.exists("transactions.csv"):
            all_rows = []
            with open("transactions.csv", "r", newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                all_rows = list(reader)
            if all_rows:
                # Apply rules and categorize history
                merchants_to_cat = list(set(r['merchant'] for r in all_rows if not r.get('category') or r['category'] in ['Other', '']))
                batch_cats = get_batch_ai_categories(merchants_to_cat, cache, overrides)
                unique_rows = []
                seen = set()
                for row in all_rows:
                    row = process_transaction_rules(row)
                    row_key = (row['date'], row['amount'], row['merchant'])
                    if row_key not in seen:
                        if not row.get('category') or row['category'] in ['Other', '']:
                            row['category'] = batch_cats.get(row['merchant'], 'Other')
                        unique_rows.append(row)
                        seen.add(row_key)
                unique_rows.sort(key=lambda x: x['date'])
                total = 0.0
                for row in unique_rows:
                    total += float(row['amount'])
                    row['cumulative_amount'] = round(total, 2)
                    check_benefits(row, benefits)
                with open("transactions.csv", "w", newline="") as csvfile:
                    fieldnames = ["date", "amount", "merchant", "category", "cumulative_amount"]
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(unique_rows)

    except HttpError as e: print(f"Error: {e}")
    finally:
        if new_found: 
            save_processed_messages(processed_message_ids)
            print("Updates complete.")

if __name__ == "__main__":
    main()
