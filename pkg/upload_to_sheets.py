#!/usr/bin/env python3
"""Uploads transaction data from transactions.csv to Google Sheets safely (update before clear)."""

import argparse
import csv
import logging
import os
import sys
from typing import Any, Dict, List, Optional

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from pkg.common import (
    ROOT_DIR,
    SHEETS_COLUMNS,
    get_config_path,
    get_credentials,
    load_config,
    setup_logging,
)

logger = logging.getLogger(__name__)


def build_sheet_values(transactions_path: str) -> List[List[Any]]:
    """Reads transactions.csv, sorts chronologically, recomputes cumulative totals, and builds Sheets rows."""
    with open(transactions_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    rows.sort(key=lambda x: x.get("date", ""))

    running_total = 0.0
    for row in rows:
        try:
            running_total += float(row["amount"])
        except (ValueError, KeyError, TypeError):
            logger.warning("Skipping invalid amount while calculating cumulative total: %s", row)
        row["cumulative_amount"] = f"{running_total:.2f}"

    display_headers = [header for _, header in SHEETS_COLUMNS]
    data_keys = [key for key, _ in SHEETS_COLUMNS]

    values: List[List[Any]] = [display_headers]
    for row in rows:
        values.append([row.get(k, "") for k in data_keys])
    return values


def upload_transactions(
    config: Dict[str, Any],
    service: Any,
    root_dir: str = ROOT_DIR,
    force: bool = False,
    dry_run: bool = False,
) -> bool:
    """
    Uploads transactions.csv to Google Sheets.
    Updates cells BEFORE clearing any trailing surplus rows so an API failure
    mid-upload can never leave the sheet blank.
    """
    transactions_path = get_config_path(config, "transactions_csv", "transactions.csv", root_dir=root_dir)
    if not os.path.exists(transactions_path):
        logger.warning(
            "transactions.csv not found at %s. Nothing to upload. "
            "Run 'python main.py' or sync transactions.csv first.",
            transactions_path,
        )
        return False

    sheets_cfg = config.get("google_sheets", {})
    spreadsheet_id = sheets_cfg.get("spreadsheet_id") or os.environ.get("SPREADSHEET_ID", "")
    sheet_name = sheets_cfg.get("sheet_name", "Transactions")
    if not spreadsheet_id:
        logger.error("No Google Sheets spreadsheet_id configured in config/spend_tracker.json.")
        return False

    values_to_upload = build_sheet_values(transactions_path)
    if len(values_to_upload) <= 1:
        logger.info("No transaction data to upload.")
        return False

    sheet = service.spreadsheets()
    existing_row_count = 0

    try:
        existing_res = (
            sheet.values()
            .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A:A")
            .execute()
        )
        existing_rows = existing_res.get("values", [])
        existing_row_count = len(existing_rows)
        if not force and existing_row_count > len(values_to_upload):
            logger.error(
                "ABORTED: Google Sheet currently has %d rows, but local CSV only has %d rows. "
                "Pass --force to overwrite anyway.",
                existing_row_count,
                len(values_to_upload),
            )
            return False
    except Exception as exc:
        logger.warning("Could not check existing sheet length (%s); proceeding with upload.", exc)

    if dry_run:
        logger.info(
            "[DRY RUN] Would upload %d rows (%d columns) to %s!A1 (existing rows: %d).",
            len(values_to_upload),
            len(values_to_upload[0]),
            sheet_name,
            existing_row_count,
        )
        return True

    logger.info("Uploading %d rows to Google Sheet (%s)...", len(values_to_upload), sheet_name)
    result = (
        sheet.values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": values_to_upload},
        )
        .execute()
    )
    logger.info("%s cells updated.", result.get("updatedCells"))

    # Only clear trailing surplus rows after the update succeeded
    if existing_row_count > len(values_to_upload):
        trailing_start = len(values_to_upload) + 1
        trailing_range = f"{sheet_name}!A{trailing_start}:Z{existing_row_count}"
        logger.info("Clearing %d trailing rows (%s)...", existing_row_count - len(values_to_upload), trailing_range)
        sheet.values().clear(spreadsheetId=spreadsheet_id, range=trailing_range).execute()

    logger.info("Upload complete.")
    return True


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload transactions.csv to Google Sheets.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting the Google Sheet even if it currently has more rows than local CSV.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report what would be uploaded without modifying Google Sheets.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(verbose=args.verbose)

    config = load_config(root_dir=ROOT_DIR)
    creds = get_credentials(config, root_dir=ROOT_DIR, port=0)

    try:
        service = build("sheets", "v4", credentials=creds)
        ok = upload_transactions(
            config=config,
            service=service,
            root_dir=ROOT_DIR,
            force=args.force,
            dry_run=args.dry_run,
        )
        return 0 if ok else 1
    except HttpError as err:
        logger.error("Google Sheets API error: %s", err)
        return 1


if __name__ == "__main__":
    sys.exit(main())
