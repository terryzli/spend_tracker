"""Email body extraction, bank/card detection, regex transaction parsing, and rules."""

import base64
import calendar
import logging
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from bs4 import BeautifulSoup

from pkg.common import ROOT_DIR, get_config_path, load_json_file

logger = logging.getLogger(__name__)


def get_header(headers: List[Dict[str, str]], name: str) -> str:
    """Case-insensitive lookup of an email header value."""
    target = name.lower()
    for header in headers or []:
        if header.get("name", "").lower() == target:
            return header.get("value", "")
    return ""


def decode_base64_body(data: str) -> str:
    """Safely decodes URL-safe base64 Gmail body data to UTF-8 text."""
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    raw_bytes = base64.urlsafe_b64decode(padded)
    return raw_bytes.decode("utf-8", errors="replace")


def html_to_clean_text(html_or_text: str) -> str:
    """Strips HTML markup and collapses whitespace."""
    if not html_or_text:
        return ""
    soup_text = BeautifulSoup(html_or_text, "html.parser").get_text(separator=" ")
    return " ".join(soup_text.split())


def extract_email_body(payload: Dict[str, Any]) -> Tuple[str, str]:
    """
    Extracts (raw_body, clean_text) from a Gmail message payload.
    Prefers text/html (converted to clean text) and falls back to text/plain
    for multipart emails that lack HTML or have empty HTML.
    """
    html_parts: List[str] = []
    plain_parts: List[str] = []

    def _walk(part: Dict[str, Any]) -> None:
        mime = (part.get("mimeType") or "").lower()
        body_data = part.get("body", {}).get("data")
        if body_data:
            if mime == "text/html":
                html_parts.append(decode_base64_body(body_data))
            elif mime == "text/plain":
                plain_parts.append(decode_base64_body(body_data))
        for sub in part.get("parts") or []:
            _walk(sub)

    _walk(payload or {})

    if html_parts:
        raw_html = "\n".join(html_parts)
        clean = html_to_clean_text(raw_html)
        if clean:
            return raw_html, clean

    if plain_parts:
        raw_plain = "\n".join(plain_parts)
        clean = " ".join(raw_plain.split())
        if clean:
            return raw_plain, clean

    top_data = (payload or {}).get("body", {}).get("data")
    if top_data:
        raw_top = decode_base64_body(top_data)
        return raw_top, html_to_clean_text(raw_top)

    return "", ""


def extract_card_last4(text: str) -> List[str]:
    """Extracts candidate card last-4-digit strings from email subject/body text."""
    if not text:
        return []
    patterns = [
        r"(?:ending\s+(?:in|with)|ends\s+in)\s*(?:[x*•·-]*\s*)?(\d{4})\b",
        r"(?:card|account|acct)\s*(?:#|number|no\.?)?\s*(?:[x*•·-]+\s*)(\d{4})\b",
        r"(?:\*{2,}|x{2,}|•{2,}|·{2,})\s*(\d{4})\b",
    ]
    found: List[str] = []
    seen: Set[str] = set()
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            digits = m.group(1)
            if digits not in seen:
                seen.add(digits)
                found.append(digits)
    return found


def detect_card(
    from_header: str = "",
    subject: str = "",
    body_text: str = "",
    card_last4_map: Optional[Dict[str, str]] = None,
) -> str:
    """
    Detects the credit card / issuing bank from email headers and body.
    Priority:
      1. Exact match via card_last4_map (e.g. 'ending in 1234' -> 'Amex Gold')
      2. From header issuer detection + product refinement
      3. Body/subject fallback for forwarded emails
    """
    context = f"{subject or ''} {body_text or ''}"

    if card_last4_map:
        normalized_map = {str(k).strip(): str(v).strip() for k, v in card_last4_map.items() if str(k).strip()}
        for digits in extract_card_last4(context):
            if digits in normalized_map:
                return normalized_map[digits]

    from_lower = (from_header or "").lower()
    context_lower = context.lower()

    def _refine_issuer(issuer: str) -> str:
        if issuer == "American Express":
            if "gold card" in context_lower or "amex gold" in context_lower:
                return "Amex Gold"
            if "platinum" in context_lower:
                return "Amex Platinum"
            return "American Express"
        if issuer == "Capital One":
            if "venture x" in context_lower:
                return "Capital One Venture X"
            if "savor" in context_lower:
                return "Capital One Savor"
            return "Capital One"
        if issuer == "U.S. Bank":
            if "altitude reserve" in context_lower:
                return "U.S. Bank Altitude Reserve"
            return "U.S. Bank"
        if issuer == "Chase":
            if "sapphire" in context_lower:
                return "Chase Sapphire"
            if "freedom" in context_lower:
                return "Chase Freedom"
            return "Chase"
        if issuer == "Citi":
            if "costco" in context_lower:
                return "Citi Costco Anywhere"
            return "Citi"
        return issuer

    sender_rules = [
        (("americanexpress.com", "aexp.com", "american express", "amex"), "American Express"),
        (("capitalone.com", "capital one"), "Capital One"),
        (("usbank.com", "u.s. bank", "us bank"), "U.S. Bank"),
        (("chase.com", "chase"), "Chase"),
        (("bankofamerica.com", "bank of america", "bofa"), "Bank of America"),
        (("citi.com", "citibank.com", "citicards", "citi "), "Citi"),
    ]

    for needles, issuer in sender_rules:
        if any(n in from_lower for n in needles):
            return _refine_issuer(issuer)

    # Fallback for forwarded emails where From is a personal address
    fallback_rules = [
        (("american express", "amex"), "American Express"),
        (("capital one", "venture x"), "Capital One"),
        (("u.s. bank", "us bank", "altitude reserve"), "U.S. Bank"),
        (("chase.com", "jpmorgan chase", "chase card"), "Chase"),
        (("bank of america",), "Bank of America"),
        (("citibank", "citi card", "citi costco"), "Citi"),
    ]
    for needles, issuer in fallback_rules:
        if any(n in context_lower for n in needles):
            return _refine_issuer(issuer)

    return ""


def clean_merchant_name(name: str) -> str:
    """Cleans up common formatting patterns in merchant names (e.g. 'on [date] at [merchant]')."""
    if not name:
        return ""
    # Strip Capital One / bank disclaimers
    if " This may have occurred " in name:
        name = name.split(" This may have occurred ")[0]
    # Strip Chase / bank timestamp prefix like "5:33 PM ET Merchant " or "12:28 AM ET Merchant "
    if " Merchant " in name:
        name = name.split(" Merchant ")[-1]
    # Strip trailing " Amount $..."
    if " Amount " in name:
        name = name.split(" Amount ")[0]
    # Strip any leading timestamps e.g. "8:53 PM ET "
    name = re.sub(
        r"^(?:\d{1,2}:\d{2}\s*(?:AM|PM)\s*(?:ET|PT|CT|MT)?\s*)",
        "",
        name,
        flags=re.IGNORECASE,
    )
    if " at " in name:
        name = name.split(" at ")[-1]
    name = name.strip(" '\".,")
    return name


def normalize_merchant_key(name: str) -> str:
    """
    Produces a normalized uppercase key for cache lookup so variants like
    'Costco Whse #0778' and 'COSTCO WHSE #1234' resolve to the same cache key.
    """
    if not name:
        return ""
    key = name.upper().strip()
    key = re.sub(r"^\*+", "", key)
    # Strip store/branch codes like "#0778", "T-2581", or trailing 4+ digit reference numbers
    key = re.sub(r"\s*#\s*\d+\b", "", key)
    key = re.sub(r"\bT-\d+\b", "", key)
    key = re.sub(r"\s+\d{4,}$", "", key)
    key = " ".join(key.split())
    return key


def normalize_amount(raw_amount: Any) -> Optional[str]:
    """Cleans and validates a monetary amount, returning a '0.00'-formatted string or None."""
    if raw_amount is None:
        return None
    cleaned = str(raw_amount).replace("$", "").replace(",", "").strip()
    try:
        val = float(cleaned)
    except ValueError:
        return None
    if val <= 0:
        return None
    return f"{val:.2f}"


def parse_email_regex(body_or_text: str, subject: str = "") -> Optional[Dict[str, str]]:
    """
    Attempts fast, free regex parsing against structured subject lines and email body.
    Returns {'amount': ..., 'merchant': ...} or None if no pattern matches.
    """
    # 0. Check structured subject lines first (Chase & U.S. Bank: "You made a $X transaction with <Merchant>")
    if subject:
        subj_match = re.search(
            r"(?:made a\s+|A\s+)?\$(?P<amount>[\d,.]+)\s+transaction\s+(?:at|with)\s+"
            r"(?P<merchant>[^.\n]+?)(?:\s+on|\.|\s+ending|\s+card|\s+for|\s+date|\s+was|\s+approved|$)",
            subject,
            re.IGNORECASE,
        )
        if not subj_match:
            subj_match = re.search(
                r"\$(?P<amount>[\d,.]+)\s+(?:at|with)\s+(?P<merchant>.+)",
                subject,
                re.IGNORECASE,
            )
        if subj_match:
            res = subj_match.groupdict()
            amt = normalize_amount(res["amount"])
            merchant = clean_merchant_name(res["merchant"])
            if amt and merchant:
                return {"amount": amt, "merchant": merchant}

    if not body_or_text:
        return None

    try:
        clean_text = html_to_clean_text(body_or_text)

        patterns = [
            # Pattern A: "Amount: $X ... Where: Y" (Bank of America format)
            (
                r"Amount:\s+\$(?P<amount>[\d,.]+).*?Where:\s+(?P<merchant>[^.\n]+?)(?:\s+View|\s+Date|\.)",
                re.IGNORECASE,
            ),
            # Pattern B: Amex Large Purchase format ("Merchant $Amount* Day, Month Date, Year")
            (
                r"(?P<merchant>[A-Za-z0-9\s#&-]{2,30}?)\s+\$(?P<amount>[\d,.]+)\*\s+[A-Za-z]{3},\s+[A-Za-z]{3}\s+\d",
                re.IGNORECASE,
            ),
            # Pattern C: "Amount: $X ... Merchant: Y" (Amex/forwarded formats)
            (
                r"Amount:\s+\$(?P<amount>[\d,.]+).*?Merchant:\s+(?P<merchant>[^.\n]+)",
                re.DOTALL | re.IGNORECASE,
            ),
            # Pattern D: "at Y, a pending ... amount of $X" (Capital One)
            (
                r"at\s+(?P<merchant>.+?),\s+a\s+pending.*?amount\s+of\s+\$(?P<amount>[\d,.]+)",
                re.IGNORECASE,
            ),
            # Pattern D2: Chase Body format ("Merchant <Merchant> Amount $X")
            (
                r"Merchant\s+(?P<merchant>.+?)\s+Amount\s+\$(?P<amount>[\d,.]+)",
                re.IGNORECASE,
            ),
            # Pattern D3: Citi format ("Amount: $X ... Merchant Y Date")
            (
                r"Amount:\s+\$(?P<amount>[\d,.]+).*?Merchant\s+(?P<merchant>[^.\n]+?)\s+Date",
                re.IGNORECASE,
            ),
            # Pattern D4: U.S. Bank Body format ("You made a $X transaction with Y")
            (
                r"(?:made a\s+)?\$(?P<amount>[\d,.]+)\s+transaction\s+(?:at|with)\s+"
                r"(?P<merchant>[^.\n]+?)(?:\s+on|\.|\s+ending|\s+card|\s+for|\s+date|\s+was|\s+approved)",
                re.IGNORECASE,
            ),
            # Pattern E: Generic Fallback (Matches "$10.00 at Starbucks", "charged $88.18 at Lowe's")
            (
                r"\$(?P<amount>[\d,.]+)\s+(?:at|with)\s+"
                r"(?P<merchant>[^.\n]{2,40}?)(?:\s+on|\.|\s+ending|\s+card|\s+for|\s+date|\s+was|\s+approved|$)",
                re.IGNORECASE,
            ),
        ]

        for pattern, flags in patterns:
            match = re.search(pattern, clean_text, flags)
            if match:
                res = match.groupdict()
                amt = normalize_amount(res["amount"])
                merchant = clean_merchant_name(res["merchant"])
                if amt and merchant:
                    return {"amount": amt, "merchant": merchant}
    except Exception as exc:
        logger.warning("Regex parsing error: %s", exc)

    return None


def parse_email_date(headers: List[Dict[str, str]], fallback_now: Optional[datetime] = None) -> str:
    """Extracts YYYY-MM-DD date from email Date header."""
    raw_date = get_header(headers, "Date")
    if raw_date:
        try:
            date_obj = parsedate_to_datetime(raw_date)
            return date_obj.strftime("%Y-%m-%d")
        except Exception:
            try:
                clean_date = re.sub(r"\s*\([^)]*\)", "", raw_date).strip()
                date_obj = datetime.strptime(clean_date, "%a, %d %b %Y %H:%M:%S %z")
                return date_obj.strftime("%Y-%m-%d")
            except Exception:
                logger.debug("Could not parse Date header '%s'; using fallback.", raw_date)
    now = fallback_now or datetime.now()
    return now.strftime("%Y-%m-%d")


def apply_transaction_rules(
    transaction: Dict[str, Any],
    rules: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Applies configured merchant rules (e.g. fixed amounts or forced categories)."""
    if not rules:
        return transaction
    merchant_upper = (transaction.get("merchant") or "").upper()
    for rule in rules:
        needle = (rule.get("merchant_contains") or "").upper()
        if not needle or needle not in merchant_upper:
            continue
        if "set_amount" in rule:
            target_amount = normalize_amount(rule["set_amount"])
            if target_amount and transaction.get("amount") != target_amount:
                logger.info(
                    "Applied rule: adjusted %s amount from $%s to $%s",
                    transaction.get("merchant"),
                    transaction.get("amount"),
                    target_amount,
                )
                transaction["amount"] = target_amount
        if "set_category" in rule and rule["set_category"]:
            transaction["category"] = rule["set_category"]
    return transaction


def get_recurring_expenses(
    config: Dict[str, Any],
    processed_ids: Set[str],
    now: Optional[datetime] = None,
    root_dir: str = ROOT_DIR,
) -> List[Dict[str, Any]]:
    """
    Returns recurring transactions due on or before today that haven't been logged
    for the current month yet. Clamps scheduled day to the last day of the month
    so items scheduled for days 29–31 still fire in shorter months (e.g., February).
    """
    path = get_config_path(config, "recurring_expenses", "recurring_expenses.json", root_dir=root_dir)
    if not os.path.exists(path):
        return []

    recurring = load_json_file(path, default=[])
    if not isinstance(recurring, list):
        return []

    current = now or datetime.now()
    last_day = calendar.monthrange(current.year, current.month)[1]
    to_log: List[Dict[str, Any]] = []

    for item in recurring:
        try:
            scheduled_day = int(item["day"])
            effective_day = min(max(1, scheduled_day), last_day)
            pseudo_id = f"{item['id_prefix']}_{current.strftime('%Y_%m')}"
            amt = normalize_amount(item["amount"])
            if not amt:
                logger.warning("Skipping recurring expense with invalid amount: %s", item)
                continue
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Skipping malformed recurring expense %s: %s", item, exc)
            continue

        if current.day >= effective_day and pseudo_id not in processed_ids:
            tx: Dict[str, Any] = {
                "date": current.strftime("%Y-%m-%d"),
                "amount": amt,
                "merchant": str(item["name"]),
                "category": str(item.get("category", "")),
                "card": str(item.get("card", "Recurring")),
                "msg_id": pseudo_id,
            }
            to_log.append(tx)
            logger.info("Logged recurring transaction: %s ($%s)", tx["merchant"], tx["amount"])

    return to_log
