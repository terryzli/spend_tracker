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

def load_config():
    with open("config/spend_tracker.json", "r") as f:
        return json.load(f)

def get_gemini_usage_count(config):
    today = datetime.now().strftime('%Y-%m-%d')
    if os.path.exists(config["paths"]["gemini_usage"]):
        with open(config["paths"]["gemini_usage"], 'r') as f:
            try:
                data = json.load(f)
                if data.get("date") == today:
                    return data.get("count", 0)
            except: pass
    return 0

def increment_gemini_usage(config):
    today = datetime.now().strftime('%Y-%m-%d')
    count = get_gemini_usage_count(config) + 1
    with open(config["paths"]["gemini_usage"], 'w') as f:
        json.dump({"date": today, "count": count}, f)

def load_category_cache(config):
    path = config["paths"]["category_cache"]
    if os.path.exists(path):
        with open(path, "r") as f:
            try: return json.load(f)
            except: return {}
    return {}

def save_category_cache(config, cache):
    with open(config["paths"]["category_cache"], "w") as f:
        json.dump(cache, f, indent=2)

def load_category_overrides(config):
    path = config["paths"]["category_overrides"]
    if os.path.exists(path):
        with open(path, "r") as f:
            try: return json.load(f)
            except: return {}
    return {}

def clean_merchant_name(name):
    """Cleans up common formatting patterns in merchant names (e.g. 'on [date] at [merchant]')."""
    if " at " in name:
        name = name.split(" at ")[-1]
    name = name.strip(" '\".,")
    return name

def load_categories(config):
    """Loads configured categories from categories.json."""
    path = config["paths"].get("categories", "config/categories.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            try: return json.load(f)
            except: return {}
    return {}

def get_batch_ai_categories(config, merchants, cache, overrides):
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
    usage = get_gemini_usage_count(config)
    if usage >= config["daily_limit"]:
        for m in to_ask: results[m] = "Other"
        return results
    if not os.path.exists(config["paths"]["gemini_key"]): return results
    with open(config["paths"]["gemini_key"], 'r') as f: api_key = f.read().strip()
    try:
        client = genai.Client(api_key=api_key)
        merchant_list = "\n".join([f"- {m}" for m in to_ask])
        categories = load_categories(config)
        categories_str = ", ".join(list(categories.keys()) + ["Other"])
        prompt = f"Categorize these merchants into one of these exact categories: {categories_str}. Return ONLY a JSON object mapping each merchant to its category.\nMerchants:\n{merchant_list}"
        response = client.models.generate_content(model='gemini-2.5-flash-lite', contents=prompt, config=types.GenerateContentConfig(response_mime_type='application/json'))
        increment_gemini_usage(config)
        if response.text:
            batch_results = json.loads(response.text.strip())
            for m, cat in batch_results.items():
                results[m] = cat
                cache[m] = cat
            save_category_cache(config, cache)
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

def parse_with_gemini(config, body):
    if not os.path.exists(config["paths"]["gemini_key"]): return None
    usage = get_gemini_usage_count(config)
    if usage >= config["daily_limit"]: return None
    with open(config["paths"]["gemini_key"], 'r') as f: api_key = f.read().strip()
    try:
        client = genai.Client(api_key=api_key)
        soup_text = BeautifulSoup(body, 'html.parser').get_text(separator=' ')
        clean_text = ' '.join(soup_text.split())[:4000] 
        
        categories = load_categories(config)
        categories_str = ", ".join(list(categories.keys()) + ["Other"])
        
        prompt = (
            f"Analyze the following email from a credit card company or bank. "
            f"Extract the transaction details and return ONLY a JSON object with these keys:\n"
            f"- 'amount': the transaction amount (string, e.g., '230.42')\n"
            f"- 'merchant': the clean, friendly name of the merchant (string, e.g., 'Walmart' instead of 'on May. 14, 2026, at Walmart', 'Lowe's' instead of 'on May. 16, 2026, at Lowe's', 'Google' instead of 'Google *fi')\n"
            f"- 'date': the date of the transaction in YYYY-MM-DD format (extract from email context)\n"
            f"- 'category': classify the transaction into one of these exact categories: {categories_str}. Choose 'Other' if it doesn't fit any.\n\n"
            f"Email body:\n{clean_text}"
        )
        
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type='application/json')
        )
        increment_gemini_usage(config)
        if response.text:
            data = json.loads(response.text.strip())
            if data and all(k in data for k in ['amount', 'merchant', 'date']):
                data['amount'] = str(data['amount']).replace('$', '').replace(',', '')
                data['merchant'] = clean_merchant_name(data['merchant'])
                return data
    except Exception as e:
        print(f"Gemini parsing failed: {e}")
    return None

def parse_email_body(config, body):
    # 1. Try Gemini first to get a complete, high-quality parse of the entire email
    gemini_res = parse_with_gemini(config, body)
    if gemini_res:
        return gemini_res

    # 2. Fall back to regex parsing if Gemini is unavailable or fails
    try:
        soup_text = BeautifulSoup(body, 'html.parser').get_text(separator=' ')
        match = re.search(r"charged\s+\$(?P<amount>[\d.]+)\s+at\s+(?P<merchant>[^.]+)\.", soup_text, re.DOTALL)
        if not match: match = re.search(r"Amount:\s+\$(?P<amount>[\d.]+).*?Merchant:\s+(?P<merchant>[^.\n]+)", soup_text, re.DOTALL | re.IGNORECASE)
        if match:
            res = match.groupdict()
            res['merchant'] = clean_merchant_name(' '.join(res['merchant'].split()))
            return res
    except: pass
    
    if "capitalone.com" in body:
        soup_text = BeautifulSoup(body, 'html.parser').get_text(separator=' ')
        cap_match = re.search(r"at\s+(?P<merchant>.+?),\s+a\s+pending.*?amount\s+of\s+\$(?P<amount>[\d,.]+)", soup_text, re.IGNORECASE)
        if cap_match: 
            return {
                "amount": cap_match.group('amount').replace(',', ''), 
                "merchant": clean_merchant_name(cap_match.group('merchant').strip())
            }
    return None

def get_email_date(headers):
    for h in headers:
        if h["name"] == "Date": return h["value"]
    return ""

def load_benefits(config):
    with open(config["paths"]["benefits"], "r") as f: return json.load(f)

def check_benefits(transaction, benefits):
    for card, card_benefits in benefits.items():
        for benefit, details in card_benefits.items():
            for keyword in details["keywords"]:
                if keyword.lower() in transaction["merchant"].lower():
                    print(f"  -> Found transaction for '{benefit}' credit on your {card} card.")
                    return

def load_processed_messages(config):
    if os.path.exists(config["paths"]["processed_messages"]):
        with open(config["paths"]["processed_messages"], "r") as f: return set(f.read().splitlines())
    return set()

def save_processed_messages(config, processed_ids):
    with open(config["paths"]["processed_messages"], "w") as f:
        for msg_id in processed_ids: f.write(f"{msg_id}\n")

def process_recurring_expenses(config, processed_ids, benefits):
    """Checks for recurring expenses that should be logged today."""
    path = config["paths"].get("recurring_expenses", "recurring_expenses.json")
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r") as f:
            recurring = json.load(f)
    except Exception as e:
        print(f"Error loading recurring expenses: {e}")
        return []

    now = datetime.now()
    to_log = []

    for item in recurring:
        # Check if today is >= scheduled day and we haven't logged it for this MONTH/YEAR yet.
        pseudo_id = f"{item['id_prefix']}_{now.strftime('%Y_%m')}"
        
        if now.day >= item['day'] and pseudo_id not in processed_ids:
            transaction = {
                "date": now.strftime("%Y-%m-%d"), # Log it as today
                "amount": str(item['amount']),
                "merchant": item['name'],
                "msg_id": pseudo_id
            }
            to_log.append(transaction)
            print(f"  -> Logged recurring transaction: {item['name']} (${item['amount']})")

    return to_log

def main():
    config = load_config()
    creds = None
    if os.path.exists(config["paths"]["token"]): creds = Credentials.from_authorized_user_file(config["paths"]["token"], SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(config["paths"]["credentials"], SCOPES)
            creds = flow.run_local_server(port=8080)
        with open(config["paths"]["token"], "w") as token: token.write(creds.to_json())

    service = build("gmail", "v1", credentials=creds)
    benefits = load_benefits(config)
    cache = load_category_cache(config)
    overrides = load_category_overrides(config)
    processed_message_ids = load_processed_messages(config)
    new_found = False

    try:
        temp_list = []

        # --- Handle Recurring Expenses ---
        recurring_to_log = process_recurring_expenses(config, processed_message_ids, benefits)
        for transaction in recurring_to_log:
            temp_list.append(transaction)
            new_found = True

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
            transaction = parse_email_body(config, body)
            if transaction:
                transaction = process_transaction_rules(transaction)
                transaction['merchant'] = clean_merchant_name(transaction['merchant'])
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
            file_exists = os.path.exists(config["paths"]["transactions_csv"])
            with open(config["paths"]["transactions_csv"], "a", newline="") as csvfile:
                fieldnames = ["date", "amount", "merchant", "category", "cumulative_amount"]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                if not file_exists: writer.writeheader()
                for t in temp_list:
                    msg_id = t.pop('msg_id')
                    writer.writerow(t)
                    processed_message_ids.add(msg_id)

        if os.path.exists(config["paths"]["transactions_csv"]):
            all_rows = []
            with open(config["paths"]["transactions_csv"], "r", newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                all_rows = list(reader)
            if all_rows:
                # Clean up all merchant names in the history first
                for row in all_rows:
                    row['merchant'] = clean_merchant_name(row['merchant'])
                    row = process_transaction_rules(row)

                # Now get the unique set of merchants that need categorization
                merchants_to_cat = list(set(r['merchant'] for r in all_rows if not r.get('category') or r['category'] in ['Other', '']))
                batch_cats = get_batch_ai_categories(config, merchants_to_cat, cache, overrides)
                unique_rows = []
                seen = set()
                for row in all_rows:
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
                with open(config["paths"]["transactions_csv"], "w", newline="") as csvfile:
                    fieldnames = ["date", "amount", "merchant", "category", "cumulative_amount"]
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(unique_rows)

    except HttpError as e: print(f"Error: {e}")
    finally:
        if new_found: 
            save_processed_messages(config, processed_message_ids)
            print("Updates complete.")

if __name__ == "__main__":
    main()
