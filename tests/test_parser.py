"""Tests for email extraction, card detection, bank regex parsers, merchant cleaning, rules, and recurring expenses."""

import base64
import json
from datetime import datetime

import pytest

from pkg.parser import (
    apply_transaction_rules,
    clean_merchant_name,
    detect_card,
    extract_email_body,
    get_recurring_expenses,
    normalize_amount,
    normalize_merchant_key,
    parse_email_date,
    parse_email_regex,
)


def _b64(text: str, encoding: str = "utf-8") -> str:
    return base64.urlsafe_b64encode(text.encode(encoding)).decode("ascii")


class TestEmailBodyExtraction:
    def test_prefers_html_part(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Plain text $12.34 at Starbucks")}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>HTML <b>$12.34</b> at Starbucks</p>")}},
            ],
        }
        raw, clean = extract_email_body(payload)
        assert "<b>$12.34</b>" in raw
        assert clean == "HTML $12.34 at Starbucks"

    def test_falls_back_to_text_plain_when_no_html(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Amount: $42.50 Where: Whole Foods.")}},
            ],
        }
        raw, clean = extract_email_body(payload)
        assert "Whole Foods" in raw
        assert clean == "Amount: $42.50 Where: Whole Foods."

    def test_handles_non_utf8_bytes_gracefully(self):
        # Latin-1 byte 0xa0 (non-breaking space) and 0xe9 ('é')
        bad_utf8 = base64.urlsafe_b64encode(b"Caf\xe9\xa0$9.50").decode("ascii")
        payload = {"mimeType": "text/plain", "body": {"data": bad_utf8}}
        _, clean = extract_email_body(payload)
        assert "$9.50" in clean


class TestCardDetection:
    @pytest.mark.parametrize(
        "from_hdr,subject,body,expected",
        [
            ("American Express <AmericanExpress@welcome.americanexpress.com>", "Large Purchase Approved", "Your Amex Gold Card", "Amex Gold"),
            ("alerts@capitalone.com", "New transaction charged", "Capital One Venture X ending in 1234", "Capital One Venture X"),
            ("alerts@notifications.usbank.com", "Transaction Alert", "U.S. Bank Altitude Reserve card", "U.S. Bank Altitude Reserve"),
            ("no.reply.alerts@chase.com", "You made a $15.00 transaction with Capital One Cafe", "Chase Sapphire Reserve", "Chase Sapphire"),
            ("onlinebanking@ealerts.bankofamerica.com", "Transaction exceeds alert", "Bank of America", "Bank of America"),
            ("alerts@info6.citi.com", "Transaction Alert", "Citi Costco Anywhere Visa", "Citi Costco Anywhere"),
            ("me@gmail.com", "Fwd: Alert", "Thank you for using your American Express card", "American Express"),
            ("unknown@example.com", "Hello", "No bank here", ""),
        ],
    )
    def test_detect_card(self, from_hdr, subject, body, expected):
        assert detect_card(from_header=from_hdr, subject=subject, body_text=body) == expected

    def test_detect_card_prefers_card_last4_map(self):
        last4_map = {"1234": "Amex Gold", "5678": "U.S. Bank Altitude Reserve"}
        assert (
            detect_card(
                from_header="alerts@americanexpress.com",
                subject="Large Purchase Approved",
                body_text="Your card ending in 1234 was charged $25.00",
                card_last4_map=last4_map,
            )
            == "Amex Gold"
        )
        assert (
            detect_card(
                from_header="alerts@usbank.com",
                subject="Transaction Alert",
                body_text="Card XXXX5678 authorized for $42.00",
                card_last4_map=last4_map,
            )
            == "U.S. Bank Altitude Reserve"
        )
        # Unmapped last-4 falls back to issuer detection
        assert (
            detect_card(
                from_header="alerts@chase.com",
                subject="Transaction Alert",
                body_text="Card ending in 9999",
                card_last4_map=last4_map,
            )
            == "Chase"
        )


class TestBankRegexParsing:
    def test_chase_subject_line(self):
        res = parse_email_regex("", subject="You made a $18.75 transaction with CHIPOTLE on Nov 25")
        assert res == {"amount": "18.75", "merchant": "CHIPOTLE"}

    def test_bofa_body(self):
        html = "<div>Amount: $124.50<br>Where: COSTCO WHSE #0778 Date: 11/25/2026</div>"
        res = parse_email_regex(html)
        assert res == {"amount": "124.50", "merchant": "COSTCO WHSE #0778"}

    def test_amex_large_purchase_body(self):
        html = "<p>WHOLE FOODS MARKET $89.20* Tue, Nov 24, 2026</p>"
        res = parse_email_regex(html)
        assert res == {"amount": "89.20", "merchant": "WHOLE FOODS MARKET"}

    def test_capital_one_body(self):
        html = "<p>On Nov 24, 2026, at TRADER JOE'S, a pending authorization in the amount of $54.10 was made.</p>"
        res = parse_email_regex(html)
        assert res == {"amount": "54.10", "merchant": "TRADER JOE'S"}

    def test_chase_body_with_timestamp(self):
        html = "<p>Date Nov 24, 2026 at 5:33 PM ET Merchant STARBUCKS Amount $6.45</p>"
        res = parse_email_regex(html)
        assert res == {"amount": "6.45", "merchant": "STARBUCKS"}

    def test_citi_body(self):
        html = "<p>Amount: $1,234.56 Card ending in 9999 Merchant BEST BUY Date 11/24/2026</p>"
        res = parse_email_regex(html)
        assert res == {"amount": "1234.56", "merchant": "BEST BUY"}

    def test_us_bank_body(self):
        html = "<p>You made a $32.00 transaction with UBER on 11/24/2026.</p>"
        res = parse_email_regex(html)
        assert res == {"amount": "32.00", "merchant": "UBER"}

    def test_non_matching_email_returns_none(self):
        assert parse_email_regex("<p>Your monthly statement is ready to view online.</p>", subject="Statement Ready") is None


class TestMerchantAndRules:
    def test_clean_merchant_name(self):
        assert clean_merchant_name("5:33 PM ET Merchant STARBUCKS Amount $5.50") == "STARBUCKS"
        assert clean_merchant_name("WALMART This may have occurred in-store") == "WALMART"
        assert clean_merchant_name("on May 14, 2026, at Lowe's") == "Lowe's"

    def test_normalize_merchant_key(self):
        assert normalize_merchant_key("Costco Whse #0778") == "COSTCO WHSE"
        assert normalize_merchant_key("COSTCO WHSE #1234") == "COSTCO WHSE"
        assert normalize_merchant_key("99 Ranch #1701") == "99 RANCH"
        assert normalize_merchant_key("TARGET T-2581") == "TARGET"
        assert normalize_merchant_key("*SHUGETSU") == "SHUGETSU"

    def test_normalize_amount(self):
        assert normalize_amount("$1,234.5") == "1234.50"
        assert normalize_amount("0") is None
        assert normalize_amount("-10.00") is None
        assert normalize_amount("not-a-number") is None

    def test_apply_transaction_rules(self):
        tx = {"merchant": "BAY CLUB FREMONT", "amount": "195.00"}
        rules = [{"merchant_contains": "BAY CLUB", "set_amount": "160.00"}]
        updated = apply_transaction_rules(tx, rules)
        assert updated["amount"] == "160.00"

    def test_parse_email_date(self):
        headers = [{"name": "Date", "value": "Tue, 24 Nov 2026 14:30:00 -0800"}]
        assert parse_email_date(headers) == "2026-11-24"


class TestRecurringExpenses:
    def test_day_29_fires_on_feb_28_non_leap_year(self, tmp_path):
        rec_file = tmp_path / "recurring_expenses.json"
        rec_file.write_text(
            json.dumps([{"name": "Donation", "amount": 50.0, "day": 29, "id_prefix": "donation"}]),
            encoding="utf-8",
        )
        cfg = {"paths": {"recurring_expenses": str(rec_file)}}
        txs = get_recurring_expenses(cfg, processed_ids=set(), now=datetime(2026, 2, 28), root_dir=str(tmp_path))
        assert len(txs) == 1
        assert txs[0]["amount"] == "50.00"
        assert txs[0]["msg_id"] == "donation_2026_02"
        assert txs[0]["card"] == "Recurring"

    def test_already_processed_recurring_skipped(self, tmp_path):
        rec_file = tmp_path / "recurring_expenses.json"
        rec_file.write_text(
            json.dumps([{"name": "Donation", "amount": 50.0, "day": 15, "id_prefix": "donation"}]),
            encoding="utf-8",
        )
        cfg = {"paths": {"recurring_expenses": str(rec_file)}}
        txs = get_recurring_expenses(
            cfg, processed_ids={"donation_2026_02"}, now=datetime(2026, 2, 20), root_dir=str(tmp_path)
        )
        assert txs == []
