#!/usr/bin/env python3
"""Gmail Spending Tracker: fetches bank alert emails, parses transactions, categorizes with Gemini, and writes transactions.csv."""

import argparse
import csv
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from pkg.ai import GeminiClient, match_local_category
from pkg.common import (
    CSV_FIELDNAMES,
    ROOT_DIR,
    atomic_write_csv,
    atomic_write_text,
    get_config_path,
    get_credentials,
    load_config,
    load_json_file,
    setup_logging,
    transaction_matches_benefit,
)
from pkg.parser import (
    apply_transaction_rules,
    clean_merchant_name,
    detect_card,
    extract_email_body,
    get_header,
    get_recurring_expenses,
    normalize_amount,
    parse_email_date,
    parse_email_regex,
)

logger = logging.getLogger(__name__)

GMAIL_BASE_QUERY = (
    '("Large Purchase" OR "Transaction Alert" OR "new transaction" OR '
    '"exceeds alert" OR "transaction was charged" OR "transaction charged" OR '
    '"Single Transaction Alert" OR "charged to your card" OR "charged to your account" OR '
    '"transaction was made" OR "You made a" OR "transaction with")'
)


def load_processed_messages(config: Dict[str, Any], root_dir: str = ROOT_DIR) -> Set[str]:
    path = get_config_path(config, "processed_messages", "processed_messages.txt", root_dir=root_dir)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    return set()


def save_processed_messages(
    config: Dict[str, Any],
    processed_ids: Set[str],
    root_dir: str = ROOT_DIR,
) -> None:
    path = get_config_path(config, "processed_messages", "processed_messages.txt", root_dir=root_dir)
    content = "\n".join(sorted(processed_ids)) + ("\n" if processed_ids else "")
    atomic_write_text(path, content)


def check_benefits(transaction: Dict[str, Any], benefits: Dict[str, Any]) -> None:
    for card, card_benefits in benefits.items():
        for benefit, details in card_benefits.items():
            if transaction_matches_benefit(transaction, details):
                logger.debug(
                    "Matched benefit '%s' on %s for %s ($%s)",
                    benefit,
                    card,
                    transaction.get("merchant"),
                    transaction.get("amount"),
                )
                return


def build_gmail_query(
    config: Dict[str, Any],
    csv_path: str,
    full_search: bool = False,
    since_date: Optional[str] = None,
    backfill: bool = False,
) -> str:
    """Constructs the Gmail search query with optional date filtering."""
    after_date_str = ""
    if full_search:
        logger.info("Full search requested: searching without date restriction.")
    elif since_date:
        after_date_str = f" after:{since_date}"
        logger.info("Custom since date requested: %s", since_date)
    elif backfill:
        backfill_date = config.get("backfill_default_date", "2026-07-10")
        after_date_str = f" after:{backfill_date}"
        logger.info("Backfill requested: searching emails after %s", backfill_date)
    elif os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8", newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            dates = [r["date"] for r in reader if r.get("date")]
            if dates:
                try:
                    latest_date = max(datetime.strptime(d, "%Y-%m-%d") for d in dates)
                    # 2-day lookback buffer accounts for timezone differences and delayed alerts.
                    search_date = latest_date - timedelta(days=2)
                    after_date_str = f" after:{search_date.strftime('%Y-%m-%d')}"
                    logger.info("Restricting Gmail search to emails after %s", search_date.strftime("%Y-%m-%d"))
                except ValueError as exc:
                    logger.warning("Failed to parse dates from CSV (%s); running unrestricted query.", exc)

    return GMAIL_BASE_QUERY + after_date_str


def deduplicate_and_finalize_rows(
    all_rows: List[Dict[str, Any]],
    ai_client: GeminiClient,
    categories: Dict[str, List[str]],
    cache: Dict[str, str],
    overrides: Dict[str, str],
    rules: List[Dict[str, Any]],
    benefits: Dict[str, Any],
    recategorize: bool = False,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """
    Cleans, applies rules, deduplicates (preserving distinct same-day transactions with
    different msg_ids), categorizes, sorts chronologically, and computes cumulative totals.
    """
    unique_rows: List[Dict[str, Any]] = []
    seen_ids: Dict[str, int] = {}
    legacy_by_tuple: Dict[Tuple[str, str, str], int] = {}

    for raw_row in all_rows:
        row = dict(raw_row)
        row["merchant"] = clean_merchant_name(row.get("merchant", ""))
        amt = normalize_amount(row.get("amount"))
        if amt is not None:
            row["amount"] = amt
        row = apply_transaction_rules(row, rules)
        row.setdefault("category", "")
        row.setdefault("card", "")
        row.setdefault("msg_id", "")

        msg_id = (row.get("msg_id") or "").strip()
        tuple_key = (row.get("date", ""), str(row.get("amount", "")), row.get("merchant", ""))

        if msg_id:
            if msg_id in seen_ids:
                continue
            if tuple_key in legacy_by_tuple:
                # Upgrade existing legacy row in-place with msg_id and card info
                idx = legacy_by_tuple.pop(tuple_key)
                unique_rows[idx]["msg_id"] = msg_id
                if not unique_rows[idx].get("card") and row.get("card"):
                    unique_rows[idx]["card"] = row["card"]
                seen_ids[msg_id] = idx
                continue
            seen_ids[msg_id] = len(unique_rows)
            unique_rows.append(row)
        else:
            if tuple_key in legacy_by_tuple:
                continue
            legacy_by_tuple[tuple_key] = len(unique_rows)
            unique_rows.append(row)

    # Always apply explicit category_overrides to all rows (even already categorized ones)
    for row in unique_rows:
        override_cat = match_local_category(
            merchant=row["merchant"],
            overrides=overrides,
            categories={},
            cache={},
            use_cache=False,
        )
        if override_cat is not None:
            row["category"] = override_cat

    # Collect merchants that need categorization
    merchants_to_cat: List[str] = []
    seen_merchants: Set[str] = set()
    for row in unique_rows:
        needs_cat = recategorize or not row.get("category") or row["category"] in ("Other", "")
        if needs_cat and row["merchant"] not in seen_merchants:
            merchants_to_cat.append(row["merchant"])
            seen_merchants.add(row["merchant"])

    if merchants_to_cat:
        batch_cats = ai_client.categorize_merchants(
            merchants=merchants_to_cat,
            categories=categories,
            cache=cache,
            overrides=overrides,
            use_cache=not recategorize,
            save_cache=not dry_run,
        )
        for row in unique_rows:
            if recategorize or not row.get("category") or row["category"] in ("Other", ""):
                row["category"] = batch_cats.get(row["merchant"], row.get("category") or "Other")

    unique_rows.sort(key=lambda r: r.get("date", ""))

    running_total = 0.0
    for row in unique_rows:
        try:
            running_total += float(row["amount"])
        except (ValueError, TypeError):
            logger.warning("Invalid amount in row %s", row)
        row["cumulative_amount"] = f"{running_total:.2f}"
        check_benefits(row, benefits)

    return unique_rows


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape bank transaction emails from Gmail, categorize with Gemini, and update transactions.csv."
    )
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        "--all",
        "--full",
        dest="full_search",
        action="store_true",
        help="Search all Gmail messages without date restriction.",
    )
    date_group.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Search Gmail messages after this date (YYYY-MM-DD).",
    )
    date_group.add_argument(
        "--backfill",
        action="store_true",
        help="Search Gmail messages after the configured backfill_default_date.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse transactions without writing CSV, cache, or processed IDs.",
    )
    parser.add_argument(
        "--recategorize",
        action="store_true",
        help="Re-evaluate categories for all existing transactions using overrides, keywords, and Gemini.",
    )
    parser.add_argument(
        "--model",
        help="Override the Gemini model configured in config/spend_tracker.json.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(verbose=args.verbose)

    config = load_config(root_dir=ROOT_DIR)
    csv_path = get_config_path(config, "transactions_csv", "transactions.csv", root_dir=ROOT_DIR)
    benefits_path = get_config_path(config, "benefits", "config/benefits.json", root_dir=ROOT_DIR)
    categories_path = get_config_path(config, "categories", "config/categories.json", root_dir=ROOT_DIR)
    overrides_path = get_config_path(config, "category_overrides", "config/category_overrides.json", root_dir=ROOT_DIR)
    cache_path = get_config_path(config, "category_cache", "category_cache.json", root_dir=ROOT_DIR)

    benefits = load_json_file(benefits_path, default={})
    categories = load_json_file(categories_path, default={})
    overrides = load_json_file(overrides_path, default={})
    cache = load_json_file(cache_path, default={})
    rules = config.get("transaction_rules", [])
    processed_message_ids = load_processed_messages(config, root_dir=ROOT_DIR)

    creds = get_credentials(config, root_dir=ROOT_DIR)
    service = build("gmail", "v1", credentials=creds)
    ai_client = GeminiClient(config, model=args.model, root_dir=ROOT_DIR)

    stats = {
        "messages_fetched": 0,
        "skipped_processed": 0,
        "parsed_regex": 0,
        "parsed_gemini": 0,
        "skipped_non_transaction": 0,
        "parse_errors": 0,
        "recurring_added": 0,
    }
    new_transactions: List[Dict[str, Any]] = []
    processed_changed = False

    try:
        # 1. Recurring expenses
        recurring_txs = get_recurring_expenses(config, processed_message_ids, root_dir=ROOT_DIR)
        for tx in recurring_txs:
            new_transactions.append(tx)
            processed_message_ids.add(tx["msg_id"])
            processed_changed = True
            stats["recurring_added"] += 1

        # 2. Fetch Gmail messages
        query = build_gmail_query(
            config=config,
            csv_path=csv_path,
            full_search=args.full_search,
            since_date=args.since,
            backfill=args.backfill,
        )

        messages: List[Dict[str, Any]] = []
        next_page_token = None
        while True:
            results = (
                service.users()
                .messages()
                .list(userId="me", q=query, pageToken=next_page_token)
                .execute()
            )
            messages.extend(results.get("messages", []))
            next_page_token = results.get("nextPageToken")
            if not next_page_token:
                break

        stats["messages_fetched"] = len(messages)
        logger.info("Found %d matching Gmail messages.", len(messages))

        for message in messages:
            msg_id = message["id"]
            if msg_id in processed_message_ids:
                stats["skipped_processed"] += 1
                continue

            msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
            payload = msg.get("payload", {})
            headers = payload.get("headers", [])
            subject = get_header(headers, "Subject")
            from_header = get_header(headers, "From")
            email_date = parse_email_date(headers)

            logger.debug("Processing email '%s' from '%s' (ID: %s)", subject, from_header, msg_id)

            raw_body, clean_text = extract_email_body(payload)
            detected_card = detect_card(
                from_header=from_header,
                subject=subject,
                body_text=clean_text,
                card_last4_map=config.get("card_last4_map"),
            )

            transaction = parse_email_regex(raw_body or clean_text, subject=subject)
            if transaction:
                stats["parsed_regex"] += 1
            else:
                parse_res = ai_client.parse_transaction_email(
                    clean_text=clean_text,
                    categories=categories,
                    subject=subject,
                    from_header=from_header,
                    email_date=email_date,
                )
                if parse_res.status == "parsed" and parse_res.transaction:
                    transaction = parse_res.transaction
                    stats["parsed_gemini"] += 1
                elif parse_res.status == "not_transaction":
                    # Mark non-transaction emails as processed so we don't re-send them to Gemini!
                    processed_message_ids.add(msg_id)
                    processed_changed = True
                    stats["skipped_non_transaction"] += 1
                    logger.debug("Marked non-transaction email %s as processed.", msg_id)
                    continue
                else:
                    # Leave unprocessed on API error / quota limit so it retries next run.
                    stats["parse_errors"] += 1
                    logger.warning(
                        "Skipping email %s due to %s (will retry on next run).",
                        msg_id,
                        parse_res.status,
                    )
                    continue

            transaction = apply_transaction_rules(transaction, rules)
            transaction["merchant"] = clean_merchant_name(transaction["merchant"])
            transaction.setdefault("date", email_date)
            if not transaction.get("card"):
                transaction["card"] = detected_card
            transaction["msg_id"] = msg_id

            new_transactions.append(transaction)
            processed_message_ids.add(msg_id)
            processed_changed = True

        # 3. Merge with existing CSV, deduplicate, categorize, sort, and persist atomically
        existing_rows: List[Dict[str, Any]] = []
        if os.path.exists(csv_path):
            with open(csv_path, "r", encoding="utf-8", newline="") as csvfile:
                existing_rows = list(csv.DictReader(csvfile))

        combined_rows = existing_rows + new_transactions
        if combined_rows:
            final_rows = deduplicate_and_finalize_rows(
                all_rows=combined_rows,
                ai_client=ai_client,
                categories=categories,
                cache=cache,
                overrides=overrides,
                rules=rules,
                benefits=benefits,
                recategorize=args.recategorize,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                logger.info(
                    "[DRY RUN] Would write %d total transactions (%d new) to %s.",
                    len(final_rows),
                    len(new_transactions),
                    csv_path,
                )
            else:
                atomic_write_csv(csv_path, CSV_FIELDNAMES, final_rows)
                logger.info(
                    "Saved %d total transactions (%d new) to %s.",
                    len(final_rows),
                    len(new_transactions),
                    csv_path,
                )

    except HttpError as err:
        logger.error("Gmail API error: %s", err)
        return 1
    finally:
        if processed_changed and not args.dry_run:
            save_processed_messages(config, processed_message_ids, root_dir=ROOT_DIR)

    logger.info(
        "Run summary (model=%s): fetched=%d, already_processed=%d, regex=%d, gemini=%d, "
        "non_tx_skipped=%d, recurring=%d, errors=%d",
        ai_client.model,
        stats["messages_fetched"],
        stats["skipped_processed"],
        stats["parsed_regex"],
        stats["parsed_gemini"],
        stats["skipped_non_transaction"],
        stats["recurring_added"],
        stats["parse_errors"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
