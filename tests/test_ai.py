"""Tests for Gemini AI client, structured outputs, rate limiting, retries, and local category matching."""

import json
from datetime import datetime
from types import SimpleNamespace

from pkg.ai import GeminiClient, match_local_category


class FakeModels:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return SimpleNamespace(text=nxt)


class FakeGenAIClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)


SAMPLE_CATEGORIES = {
    "Dining": ["uber eats", "starbucks", "chipotle"],
    "Groceries": ["costco", "trader joe's", "safeway"],
    "Travel": ["uber", "united", "delta"],
}


class TestLocalCategoryMatching:
    def test_override_beats_keywords_and_cache(self):
        cat = match_local_category(
            merchant="COSTCO WHSE #0778",
            overrides={"COSTCO": "Wholesale"},
            categories=SAMPLE_CATEGORIES,
            cache={"COSTCO WHSE #0778": "Shopping"},
        )
        assert cat == "Wholesale"

    def test_longest_keyword_wins_uber_eats_vs_uber(self):
        assert match_local_category("UBER EATS *ORDER", {}, SAMPLE_CATEGORIES, {}) == "Dining"
        assert match_local_category("UBER TRIP", {}, SAMPLE_CATEGORIES, {}) == "Travel"

    def test_case_insensitive_and_store_number_normalized_cache_lookup(self):
        cache = {"Costco Whse #0778": "Groceries", "Acme Bakery #12": "Dining"}
        assert match_local_category("ACME BAKERY #99", {}, {}, cache) == "Dining"
        assert match_local_category("acme bakery #12", {}, {}, cache) == "Dining"


class TestGeminiClient:
    def _make_config(self, tmp_path, **gemini_overrides):
        gemini_cfg = {
            "model": "gemini-3.5-flash-lite",
            "daily_limit": 10,
            "requests_per_minute": 15,
            "max_retries": 3,
        }
        gemini_cfg.update(gemini_overrides)
        return {
            "paths": {
                "gemini_usage": str(tmp_path / "gemini_usage.json"),
                "gemini_key": str(tmp_path / "gemini_key.txt"),
                "category_cache": str(tmp_path / "category_cache.json"),
            },
            "gemini": gemini_cfg,
        }

    def test_parse_transaction_email_uses_configured_model_and_schema(self, tmp_path):
        fake = FakeGenAIClient([
            json.dumps({
                "is_transaction": True,
                "amount": "$45.20",
                "merchant": "Blue Bottle Coffee",
                "date": "2026-11-24",
                "category": "Dining",
                "card": "Amex Gold",
            })
        ])
        cfg = self._make_config(tmp_path)
        client = GeminiClient(cfg, root_dir=str(tmp_path), client=fake, sleep_fn=lambda _: None)

        res = client.parse_transaction_email(
            clean_text="You spent $45.20 at Blue Bottle Coffee",
            categories=SAMPLE_CATEGORIES,
            subject="Purchase Alert",
            from_header="alerts@americanexpress.com",
            email_date="2026-11-24",
        )
        assert res.status == "parsed"
        assert res.transaction == {
            "amount": "45.20",
            "merchant": "Blue Bottle Coffee",
            "date": "2026-11-24",
            "category": "Dining",
            "card": "Amex Gold",
        }
        assert fake.models.calls[0]["model"] == "gemini-3.5-flash-lite"
        assert fake.models.calls[0]["config"].response_mime_type == "application/json"
        assert fake.models.calls[0]["config"].response_schema is not None
        assert client.get_usage_count() == 1

    def test_parse_non_transaction_email_returns_not_transaction(self, tmp_path):
        fake = FakeGenAIClient([json.dumps({"is_transaction": False})])
        cfg = self._make_config(tmp_path)
        client = GeminiClient(cfg, root_dir=str(tmp_path), client=fake, sleep_fn=lambda _: None)

        res = client.parse_transaction_email(
            clean_text="Thank you for your credit card payment of $500.00.",
            categories=SAMPLE_CATEGORIES,
            email_date="2026-11-24",
        )
        assert res.status == "not_transaction"
        assert res.transaction is None

    def test_rate_limit_and_retry_on_429(self, tmp_path):
        sleeps = []
        clock = [100.0]

        def fake_monotonic():
            return clock[0]

        def fake_sleep(dt):
            sleeps.append(dt)
            clock[0] += dt

        fake = FakeGenAIClient([
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            json.dumps([{"merchant": "Unknown Cafe", "category": "Dining"}]),
        ])
        cfg = self._make_config(tmp_path, requests_per_minute=15, max_retries=3)
        client = GeminiClient(
            cfg,
            root_dir=str(tmp_path),
            client=fake,
            sleep_fn=fake_sleep,
            monotonic_fn=fake_monotonic,
            now_fn=lambda: datetime(2026, 11, 25),
        )

        cache = {}
        result = client.categorize_merchants(
            merchants=["Unknown Cafe", "Starbucks #102"],
            categories=SAMPLE_CATEGORIES,
            cache=cache,
            overrides={},
        )
        # Starbucks matched locally via categories.json; only Unknown Cafe sent to Gemini!
        assert result["Starbucks #102"] == "Dining"
        assert result["Unknown Cafe"] == "Dining"
        assert len(fake.models.calls) == 2  # 1 failed 429 + 1 retry succeeded
        assert any(s >= 2.0 for s in sleeps)  # backoff sleep occurred
        assert cache["Unknown Cafe"] == "Dining"
        assert (tmp_path / "category_cache.json").exists()

    def test_daily_limit_stops_calls(self, tmp_path):
        usage_file = tmp_path / "gemini_usage.json"
        usage_file.write_text(json.dumps({"date": "2026-11-25", "count": 5}), encoding="utf-8")

        fake = FakeGenAIClient([])
        cfg = self._make_config(tmp_path, daily_limit=5)
        client = GeminiClient(
            cfg,
            root_dir=str(tmp_path),
            client=fake,
            now_fn=lambda: datetime(2026, 11, 25),
        )

        res = client.parse_transaction_email("Some body", SAMPLE_CATEGORIES, email_date="2026-11-25")
        assert res.status == "skipped_limit"
        assert len(fake.models.calls) == 0
