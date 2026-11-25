"""Tests for deduplication, card-aware benefit reporting, manual credits, and safe Google Sheets upload."""

import csv
import json
from datetime import datetime

from main import deduplicate_and_finalize_rows
from pkg.ai import GeminiClient
from pkg.common import CSV_FIELDNAMES, atomic_write_csv
from pkg.manual_transaction import add_manual_spend
from pkg.report import calculate_spending
from pkg.upload_to_sheets import upload_transactions


class FakeSheetValues:
    def __init__(self, existing_rows, fail_on_update=False):
        self.existing_rows = existing_rows
        self.fail_on_update = fail_on_update
        self.actions = []

    def get(self, spreadsheetId, range):
        self.actions.append(("get", range))
        return self._exec({"values": self.existing_rows})

    def update(self, spreadsheetId, range, valueInputOption, body):
        self.actions.append(("update", range, body["values"]))
        if self.fail_on_update:
            raise RuntimeError("Network failure during update")
        return self._exec({"updatedCells": len(body["values"]) * len(body["values"][0])})

    def clear(self, spreadsheetId, range):
        self.actions.append(("clear", range))
        return self._exec({})

    @staticmethod
    def _exec(result):
        class _Req:
            def execute(self_inner):
                return result

        return _Req()


class FakeSheetsService:
    def __init__(self, existing_rows, fail_on_update=False):
        self._values = FakeSheetValues(existing_rows, fail_on_update=fail_on_update)

    def spreadsheets(self):
        values = self._values

        class _Spreadsheets:
            def values(self_inner):
                return values

        return _Spreadsheets()


class TestDeduplication:
    def _dummy_ai(self, tmp_path):
        cfg = {
            "paths": {
                "gemini_usage": str(tmp_path / "gemini_usage.json"),
                "gemini_key": str(tmp_path / "missing_key.txt"),
                "category_cache": str(tmp_path / "cache.json"),
            }
        }
        return GeminiClient(cfg, root_dir=str(tmp_path))

    def test_preserves_same_day_identical_purchases_with_different_msg_ids(self, tmp_path):
        rows = [
            {"date": "2026-11-24", "amount": "5.75", "merchant": "Starbucks", "category": "Dining", "card": "Amex Gold", "msg_id": "m1"},
            {"date": "2026-11-24", "amount": "5.75", "merchant": "Starbucks", "category": "Dining", "card": "Amex Gold", "msg_id": "m2"},
            # Exact duplicate of m1 should be removed
            {"date": "2026-11-24", "amount": "5.75", "merchant": "Starbucks", "category": "Dining", "card": "Amex Gold", "msg_id": "m1"},
        ]
        result = deduplicate_and_finalize_rows(
            all_rows=rows,
            ai_client=self._dummy_ai(tmp_path),
            categories={"Dining": ["starbucks"]},
            cache={},
            overrides={},
            rules=[],
            benefits={},
        )
        assert len(result) == 2
        assert result[-1]["cumulative_amount"] == "11.50"

    def test_upgrades_legacy_row_without_duplicating(self, tmp_path):
        rows = [
            # Legacy row (no msg_id or card)
            {"date": "2026-11-20", "amount": "25.00", "merchant": "Chipotle", "category": "Dining"},
            # Newly fetched email matching legacy row
            {"date": "2026-11-20", "amount": "25.00", "merchant": "Chipotle", "category": "", "card": "Chase", "msg_id": "gmail_99"},
        ]
        result = deduplicate_and_finalize_rows(
            all_rows=rows,
            ai_client=self._dummy_ai(tmp_path),
            categories={"Dining": ["chipotle"]},
            cache={},
            overrides={},
            rules=[],
            benefits={},
        )
        assert len(result) == 1
        assert result[0]["msg_id"] == "gmail_99"
        assert result[0]["card"] == "Chase"

    def test_overrides_update_already_categorized_rows(self, tmp_path):
        rows = [
            {"date": "2026-11-20", "amount": "15.00", "merchant": "WALGREENS #123", "category": "Shopping", "msg_id": "m1"},
        ]
        result = deduplicate_and_finalize_rows(
            all_rows=rows,
            ai_client=self._dummy_ai(tmp_path),
            categories={},
            cache={},
            overrides={"WALGREENS": "Healthcare"},
            rules=[],
            benefits={},
        )
        assert result[0]["category"] == "Healthcare"


class TestCardAwareBenefitsAndReport:
    def _setup_workspace(self, tmp_path, rows):
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "spend_tracker.json").write_text(
            json.dumps({
                "paths": {
                    "benefits": "config/benefits.json",
                    "transactions_csv": "transactions.csv",
                    "manual_credits": "config/manual_credits.json",
                }
            }),
            encoding="utf-8",
        )
        (cfg_dir / "benefits.json").write_text(
            json.dumps({
                "amex_gold": {
                    "uber_credit": {
                        "keywords": ["uber"],
                        "card_keywords": ["amex", "american express"],
                        "total": 10,
                        "reset_cycle": "monthly",
                    },
                    "resy_credit": {
                        "keywords": ["resy"],
                        "card_keywords": ["amex", "american express"],
                        "total": 50,
                        "reset_cycle": "biannual_jan_jun",
                    },
                },
                "us_altitude_reserve": {
                    "travel_credit": {
                        "keywords": ["uber"],
                        "card_keywords": ["u.s. bank", "us bank", "altitude reserve"],
                        "total": 325,
                        "reset_cycle": "annual",
                    }
                },
            }),
            encoding="utf-8",
        )
        atomic_write_csv(str(tmp_path / "transactions.csv"), CSV_FIELDNAMES, rows)

    def test_uber_not_double_counted_across_cards(self, tmp_path):
        rows = [
            {"date": "2026-11-10", "amount": "8.00", "merchant": "UBER EATS", "category": "Dining", "cumulative_amount": "8.00", "card": "Amex Gold", "msg_id": "1"},
            {"date": "2026-11-12", "amount": "25.00", "merchant": "UBER TRIP", "category": "Travel", "cumulative_amount": "33.00", "card": "U.S. Bank Altitude Reserve", "msg_id": "2"},
        ]
        self._setup_workspace(tmp_path, rows)

        report = calculate_spending(root_dir=str(tmp_path), now=datetime(2026, 11, 25))
        assert report["monthly_spending"] == 33.0
        assert report["yearly_spending"] == 33.0
        # Only the $8 Amex charge counts toward Amex Gold; only the $25 US Bank charge counts toward Altitude Reserve
        amex_uber = report["benefits"]["amex_gold"]["uber_credit"]
        assert amex_uber["spent"] == 8.0
        assert amex_uber["remaining"] == 2.0
        assert amex_uber["used_up"] is False
        assert amex_uber["percent_used"] == 80.0
        assert amex_uber["period"] == "2026-11"
        assert amex_uber["days_remaining"] == 5

        us_travel = report["benefits"]["us_altitude_reserve"]["travel_credit"]
        assert us_travel["spent"] == 25.0
        assert us_travel["remaining"] == 300.0
        assert us_travel["used_up"] is False
        assert us_travel["days_remaining"] == 36

    def test_category_matching_used_up_and_ignores_legacy_empty_card(self, tmp_path):
        self._setup_workspace(tmp_path, [])
        # Configure category-based matching + anniversary cycle
        benefits_path = tmp_path / "config" / "benefits.json"
        benefits_path.write_text(
            json.dumps({
                "us_altitude_reserve": {
                    "travel_credit": {
                        "keywords": ["uber"],
                        "categories": ["Travel", "Dining"],
                        "card_keywords": ["u.s. bank", "altitude reserve"],
                        "total": 325,
                        "reset_cycle": "anniversary",
                        "anniversary_month": 6,
                        "anniversary_day": 15,
                    }
                }
            }),
            encoding="utf-8",
        )
        rows = [
            # Before anniversary (June 15, 2026) -> should NOT count in current anniversary year
            {"date": "2026-06-01", "amount": "200.00", "merchant": "UNITED", "category": "Travel", "cumulative_amount": "200.00", "card": "U.S. Bank Altitude Reserve", "msg_id": "1"},
            # Legacy row with empty card -> should NOT match via broad category
            {"date": "2026-07-10", "amount": "150.00", "merchant": "CHIPOTLE", "category": "Dining", "cumulative_amount": "350.00", "card": "", "msg_id": "2"},
            # Different card -> should NOT count
            {"date": "2026-08-10", "amount": "120.00", "merchant": "DELTA", "category": "Travel", "cumulative_amount": "470.00", "card": "Amex Gold", "msg_id": "3"},
            # Valid Altitude Reserve Dining + Travel in current anniversary window (2026-06-15 to 2027-06-14)
            {"date": "2026-09-01", "amount": "200.00", "merchant": "DELTA", "category": "Travel", "cumulative_amount": "670.00", "card": "U.S. Bank Altitude Reserve", "msg_id": "4"},
            {"date": "2026-11-20", "amount": "150.00", "merchant": "CHIPOTLE", "category": "Dining", "cumulative_amount": "820.00", "card": "U.S. Bank Altitude Reserve", "msg_id": "5"},
        ]
        atomic_write_csv(str(tmp_path / "transactions.csv"), CSV_FIELDNAMES, rows)

        report = calculate_spending(root_dir=str(tmp_path), now=datetime(2026, 11, 25))
        credit = report["benefits"]["us_altitude_reserve"]["travel_credit"]
        assert credit["spent"] == 350.0
        assert credit["remaining"] == 0.0
        assert credit["used_up"] is True
        assert credit["percent_used"] == 100.0
        assert credit["period"] == "anniv-2026-06-15"
        assert credit["days_remaining"] == (datetime(2027, 6, 14) - datetime(2026, 11, 25)).days

    def test_manual_spend_adds_to_current_period(self, tmp_path):
        self._setup_workspace(tmp_path, [])
        ok = add_manual_spend(20.0, "amex_gold:resy_credit", root_dir=str(tmp_path), now=datetime(2026, 11, 25))
        assert ok is True

        report = calculate_spending(root_dir=str(tmp_path), now=datetime(2026, 11, 25))
        assert report["benefits"]["amex_gold"]["resy_credit"]["spent"] == 20.0
        assert report["benefits"]["amex_gold"]["resy_credit"]["remaining"] == 30.0
        assert report["benefits"]["amex_gold"]["resy_credit"]["used_up"] is False
        assert report["benefits"]["amex_gold"]["resy_credit"]["percent_used"] == 40.0


class TestSafeSheetsUpload:
    def _write_csv(self, path, rows):
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)

    def test_updates_before_clearing_trailing_rows(self, tmp_path):
        csv_path = tmp_path / "transactions.csv"
        self._write_csv(
            csv_path,
            [{"date": "2026-11-24", "amount": "12.00", "merchant": "Starbucks", "category": "Dining", "cumulative_amount": "12.00", "card": "Chase", "msg_id": "m1"}],
        )
        cfg = {
            "paths": {"transactions_csv": str(csv_path)},
            "google_sheets": {"spreadsheet_id": "sheet_123", "sheet_name": "Transactions"},
        }
        # Existing sheet had 5 rows; forcing upload of 2 rows (header + 1 tx) should update A1 FIRST, then clear A3:Z5
        service = FakeSheetsService(existing_rows=[["Date"], ["r1"], ["r2"], ["r3"], ["r4"]])
        ok = upload_transactions(cfg, service, root_dir=str(tmp_path), force=True)
        assert ok is True

        actions = service._values.actions
        assert actions[0] == ("get", "Transactions!A:A")
        assert actions[1][0] == "update"
        assert actions[1][1] == "Transactions!A1"
        assert actions[1][2][0] == ["Date", "Amount", "Merchant", "Category", "Cumulative Amount", "Card"]
        assert actions[1][2][1] == ["2026-11-24", "12.00", "Starbucks", "Dining", "12.00", "Chase"]
        assert actions[2] == ("clear", "Transactions!A3:Z5")

    def test_aborts_stale_upload_without_force(self, tmp_path):
        csv_path = tmp_path / "transactions.csv"
        self._write_csv(
            csv_path,
            [{"date": "2026-11-24", "amount": "12.00", "merchant": "Starbucks", "category": "Dining", "cumulative_amount": "12.00", "card": "Chase", "msg_id": "m1"}],
        )
        cfg = {
            "paths": {"transactions_csv": str(csv_path)},
            "google_sheets": {"spreadsheet_id": "sheet_123", "sheet_name": "Transactions"},
        }
        service = FakeSheetsService(existing_rows=[["Date"], ["r1"], ["r2"], ["r3"]])
        ok = upload_transactions(cfg, service, root_dir=str(tmp_path), force=False)
        assert ok is False
        assert len(service._values.actions) == 1  # Only 'get', never 'update' or 'clear'
