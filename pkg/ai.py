"""Gemini AI integration with structured outputs, rate limiting, retries, and local matching."""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

from pkg.common import (
    DEFAULT_DAILY_LIMIT,
    DEFAULT_GEMINI_MAX_RETRIES,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_RPM,
    ROOT_DIR,
    atomic_write_json,
    get_config_path,
    load_json_file,
)
from pkg.parser import clean_merchant_name, normalize_amount, normalize_merchant_key

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Result of parsing an email with Gemini."""

    status: str  # "parsed" | "not_transaction" | "skipped_limit" | "no_api_key" | "error"
    transaction: Optional[Dict[str, Any]] = None


def match_local_category(
    merchant: str,
    overrides: Dict[str, str],
    categories: Dict[str, List[str]],
    cache: Dict[str, str],
    use_cache: bool = True,
) -> Optional[str]:
    """
    Resolves a merchant's category locally before invoking Gemini.
    Priority:
      1. Exact/substring match in category_overrides.json (longest pattern first)
      2. Keyword match in config/categories.json (longest keyword first, so e.g. 'uber eats' beats 'uber')
      3. Cache lookup in category_cache.json (exact, case-insensitive, then store-number normalized)
    """
    if not merchant:
        return "Other"

    merchant_upper = merchant.upper()

    # 1. Explicit user overrides (longest pattern wins)
    for pattern, category in sorted(overrides.items(), key=lambda kv: len(kv[0]), reverse=True):
        if pattern and pattern.upper() in merchant_upper:
            return category

    # 2. Configured category keywords from config/categories.json (longest keyword wins)
    keyword_pairs: List[Tuple[str, str]] = []
    for category, keywords in (categories or {}).items():
        if isinstance(keywords, list):
            for kw in keywords:
                if kw:
                    keyword_pairs.append((kw, category))
    keyword_pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    for kw, category in keyword_pairs:
        if kw.upper() in merchant_upper:
            return category

    if not use_cache or not cache:
        return None

    # 3. Cache lookup: exact -> case-insensitive -> normalized (store numbers stripped)
    if merchant in cache and cache[merchant]:
        return cache[merchant]

    norm_target = normalize_merchant_key(merchant)
    norm_match: Optional[str] = None
    for cached_merchant, cached_cat in cache.items():
        if not cached_cat:
            continue
        if cached_merchant.upper() == merchant_upper:
            return cached_cat
        if norm_match is None and norm_target and normalize_merchant_key(cached_merchant) == norm_target:
            norm_match = cached_cat

    return norm_match


def _is_retryable_error(exc: Exception) -> bool:
    """Returns True for transient rate-limit (429) or server (5xx) API errors."""
    status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(status_code, int) and (status_code == 429 or 500 <= status_code < 600):
        return True
    msg = str(exc).lower()
    return any(
        token in msg
        for token in ("429", "resource_exhausted", "rate limit", "quota", "503", "500", "unavailable")
    )


class GeminiClient:
    """Reusable, rate-limited Gemini API wrapper with structured JSON schemas."""

    def __init__(
        self,
        config: Dict[str, Any],
        model: Optional[str] = None,
        root_dir: str = ROOT_DIR,
        client: Optional[Any] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        now_fn: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.config = config
        self.root_dir = root_dir
        gemini_cfg = config.get("gemini", {})
        self.model = model or gemini_cfg.get("model") or DEFAULT_GEMINI_MODEL
        self.daily_limit = int(
            gemini_cfg.get("daily_limit", config.get("daily_limit", DEFAULT_DAILY_LIMIT))
        )
        self.rpm = float(gemini_cfg.get("requests_per_minute", DEFAULT_GEMINI_RPM))
        self.max_retries = int(gemini_cfg.get("max_retries", DEFAULT_GEMINI_MAX_RETRIES))
        self.min_interval = (60.0 / self.rpm) if self.rpm > 0 else 0.0

        self._client = client
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._now = now_fn
        self._last_request_ts: Optional[float] = None
        self._usage_path = get_config_path(config, "gemini_usage", "gemini_usage.json", root_dir=root_dir)
        self._key_path = get_config_path(config, "gemini_key", "gemini_key.txt", root_dir=root_dir)

    def _get_client(self) -> Optional[Any]:
        if self._client is not None:
            return self._client
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key and os.path.exists(self._key_path):
            with open(self._key_path, "r", encoding="utf-8") as f:
                api_key = f.read().strip()
        if not api_key:
            logger.warning("Gemini API key not found at %s or GEMINI_API_KEY.", self._key_path)
            return None
        self._client = genai.Client(api_key=api_key)
        return self._client

    def get_usage_count(self) -> int:
        today = self._now().strftime("%Y-%m-%d")
        data = load_json_file(self._usage_path, default={})
        if data.get("date") == today:
            return int(data.get("count", 0))
        return 0

    def increment_usage(self) -> int:
        today = self._now().strftime("%Y-%m-%d")
        count = self.get_usage_count() + 1
        atomic_write_json(self._usage_path, {"date": today, "count": count})
        return count

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        now_ts = self._monotonic()
        if self._last_request_ts is not None:
            elapsed = now_ts - self._last_request_ts
            remaining = self.min_interval - elapsed
            if remaining > 0:
                logger.debug("Rate limiting Gemini request: sleeping %.2fs", remaining)
                self._sleep(remaining)
        self._last_request_ts = self._monotonic()

    def _generate_json(self, prompt: str, schema: types.Schema) -> Optional[Any]:
        """Calls Gemini with structured output schema, rate limiting, and exponential backoff."""
        client = self._get_client()
        if client is None:
            return None
        if self.get_usage_count() >= self.daily_limit:
            logger.warning("Gemini daily limit (%d) reached; skipping API call.", self.daily_limit)
            return None

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                response = client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=0.0,
                    ),
                )
                self.increment_usage()
                text = (getattr(response, "text", None) or "").strip()
                if not text:
                    logger.warning("Gemini returned an empty response.")
                    return None
                return json.loads(text)
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries and _is_retryable_error(exc):
                    backoff = min(30.0, 2.0 ** attempt)
                    logger.warning(
                        "Gemini transient error (attempt %d/%d): %s; retrying in %.1fs",
                        attempt,
                        self.max_retries,
                        exc,
                        backoff,
                    )
                    self._sleep(backoff)
                    continue
                logger.error("Gemini API call failed: %s", exc)
                raise

        if last_exc:
            raise last_exc
        return None

    def parse_transaction_email(
        self,
        clean_text: str,
        categories: Dict[str, List[str]],
        subject: str = "",
        from_header: str = "",
        email_date: str = "",
    ) -> ParseResult:
        """
        Extracts a transaction from an email using Gemini structured outputs.
        Distinguishes non-transaction emails (is_transaction=False) from API errors.
        """
        if self._get_client() is None:
            return ParseResult(status="no_api_key")
        if self.get_usage_count() >= self.daily_limit:
            return ParseResult(status="skipped_limit")

        allowed_categories = list(categories.keys()) + ["Other"]
        schema = types.Schema(
            type=types.Type.OBJECT,
            properties={
                "is_transaction": types.Schema(
                    type=types.Type.BOOLEAN,
                    description=(
                        "True ONLY if this email confirms an actual purchase or charge. "
                        "False for credit card bill payments, autopay reminders, statements, "
                        "promos, security alerts, or OTPs."
                    ),
                ),
                "amount": types.Schema(
                    type=types.Type.STRING,
                    description="Transaction amount as a decimal string without currency symbol, e.g. '230.42'.",
                ),
                "merchant": types.Schema(
                    type=types.Type.STRING,
                    description="Clean, friendly merchant name (e.g. 'Walmart', 'Lowe\\'s', 'Google').",
                ),
                "date": types.Schema(
                    type=types.Type.STRING,
                    description="Transaction date in YYYY-MM-DD format.",
                ),
                "category": types.Schema(
                    type=types.Type.STRING,
                    enum=allowed_categories,
                    description="Exact spending category from the allowed list.",
                ),
                "card": types.Schema(
                    type=types.Type.STRING,
                    description="Issuing bank or card name if identifiable (e.g. 'Amex Gold', 'Chase', 'Capital One Venture X'), else empty string.",
                ),
            },
            required=["is_transaction"],
        )

        prompt = (
            "Analyze the following email and determine whether it reports a real purchase/charge transaction.\n"
            "If it is NOT a purchase transaction (e.g., credit card bill payment, statement notification, "
            "promotional email, refund notice, or account alert), set is_transaction to false.\n"
            f"If it IS a purchase transaction, extract amount, clean merchant name, date (YYYY-MM-DD, using "
            f"Email Date '{email_date}' if the body omits the year or date), card/bank name, and category "
            f"from: {', '.join(allowed_categories)}.\n\n"
            f"From: {from_header}\n"
            f"Subject: {subject}\n"
            f"Email Date: {email_date}\n"
            f"Body:\n{clean_text[:4000]}"
        )

        try:
            data = self._generate_json(prompt, schema)
        except Exception:
            return ParseResult(status="error")

        if not isinstance(data, dict):
            return ParseResult(status="error")

        if not data.get("is_transaction"):
            return ParseResult(status="not_transaction")

        amount = normalize_amount(data.get("amount"))
        merchant = clean_merchant_name(str(data.get("merchant") or ""))
        if not amount or not merchant:
            logger.warning("Gemini marked email as transaction but missed amount/merchant: %s", data)
            return ParseResult(status="error")

        raw_date = str(data.get("date") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw_date):
            raw_date = email_date or self._now().strftime("%Y-%m-%d")

        category = data.get("category")
        if category not in allowed_categories:
            category = "Other"

        tx = {
            "amount": amount,
            "merchant": merchant,
            "date": raw_date,
            "category": category,
            "card": str(data.get("card") or "").strip(),
        }
        return ParseResult(status="parsed", transaction=tx)

    def categorize_merchants(
        self,
        merchants: List[str],
        categories: Dict[str, List[str]],
        cache: Dict[str, str],
        overrides: Dict[str, str],
        use_cache: bool = True,
        save_cache: bool = True,
        chunk_size: int = 50,
    ) -> Dict[str, str]:
        """
        Categorizes a list of merchants using overrides, local categories.json keywords,
        normalized cache lookup, and batched Gemini structured outputs for remaining unknowns.
        """
        results: Dict[str, str] = {}
        to_ask: List[str] = []
        seen_to_ask: set = set()

        for merchant in merchants:
            local_cat = match_local_category(
                merchant=merchant,
                overrides=overrides,
                categories=categories,
                cache=cache,
                use_cache=use_cache,
            )
            if local_cat is not None:
                results[merchant] = local_cat
            elif merchant not in seen_to_ask:
                to_ask.append(merchant)
                seen_to_ask.add(merchant)

        if not to_ask:
            return results

        if self._get_client() is None:
            for m in to_ask:
                results[m] = "Other"
            return results

        allowed_categories = list(categories.keys()) + ["Other"]
        schema = types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "merchant": types.Schema(type=types.Type.STRING),
                    "category": types.Schema(type=types.Type.STRING, enum=allowed_categories),
                },
                required=["merchant", "category"],
            ),
        )

        # Build concise few-shot examples from categories.json to steer Gemini accurately
        examples_lines = []
        for cat, kws in categories.items():
            if isinstance(kws, list) and kws:
                examples_lines.append(f"- {cat}: {', '.join(kws[:6])}")
        examples_block = "\n".join(examples_lines)

        cache_updated = False
        for idx in range(0, len(to_ask), chunk_size):
            chunk = to_ask[idx : idx + chunk_size]
            if self.get_usage_count() >= self.daily_limit:
                logger.warning("Gemini daily limit reached; assigning 'Other' to remaining merchants.")
                for m in chunk:
                    results[m] = "Other"
                continue

            merchant_list = "\n".join(f"- {m}" for m in chunk)
            prompt = (
                f"Categorize each merchant into one of these exact categories: {', '.join(allowed_categories)}.\n"
                f"Category examples:\n{examples_block}\n\n"
                "Return a JSON array of objects with 'merchant' (exact input name) and 'category'.\n"
                f"Merchants:\n{merchant_list}"
            )
            try:
                batch_items = self._generate_json(prompt, schema)
                if isinstance(batch_items, list):
                    mapped: Dict[str, str] = {}
                    for item in batch_items:
                        if isinstance(item, dict) and "merchant" in item and "category" in item:
                            cat = item["category"]
                            if cat not in allowed_categories:
                                cat = "Other"
                            mapped[item["merchant"]] = cat
                            mapped[item["merchant"].upper()] = cat
                    for m in chunk:
                        cat = mapped.get(m) or mapped.get(m.upper()) or "Other"
                        results[m] = cat
                        cache[m] = cat
                        cache_updated = True
                else:
                    for m in chunk:
                        results[m] = "Other"
            except Exception as exc:
                logger.error("Batch merchant categorization failed for chunk: %s", exc)
                for m in chunk:
                    results[m] = "Other"

        if cache_updated and save_cache:
            cache_path = get_config_path(
                self.config, "category_cache", "category_cache.json", root_dir=self.root_dir
            )
            atomic_write_json(cache_path, cache)

        return results
