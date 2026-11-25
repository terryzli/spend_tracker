#!/usr/bin/env python3
"""Generates a JSON report of monthly/yearly spending and card benefit progress for Home Assistant."""

import csv
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from pkg.common import (
    ROOT_DIR,
    get_config_path,
    get_days_remaining,
    get_period_key,
    is_in_current_period,
    load_config,
    load_json_file,
    transaction_matches_benefit,
)


def calculate_spending(
    root_dir: str = ROOT_DIR,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Calculates total spending and card-aware benefit progress from transactions.csv."""
    config = load_config(root_dir=root_dir)

    benefits_path = get_config_path(config, "benefits", "config/benefits.json", root_dir=root_dir)
    transactions_path = get_config_path(config, "transactions_csv", "transactions.csv", root_dir=root_dir)
    manual_path = get_config_path(config, "manual_credits", "config/manual_credits.json", root_dir=root_dir)

    benefits_config = load_json_file(benefits_path, default={})
    manual_adjustments = load_json_file(manual_path, default={})

    current = now if now is not None else datetime.now().astimezone().replace(tzinfo=None)

    report: Dict[str, Any] = {
        "monthly_spending": 0.0,
        "yearly_spending": 0.0,
        "benefits": {},
    }

    for card, card_benefits in benefits_config.items():
        report["benefits"][card] = {}
        for benefit, details in card_benefits.items():
            reset_cycle = details.get("reset_cycle", "annual")
            period_key = get_period_key(reset_cycle, current, benefit_details=details)
            days_remaining = get_days_remaining(reset_cycle, current, benefit_details=details)
            adj = float(manual_adjustments.get(card, {}).get(benefit, {}).get(period_key, 0.0))
            report["benefits"][card][benefit] = {
                "spent": adj,
                "total": details["total"],
                "remaining": details["total"],
                "used_up": False,
                "percent_used": 0.0,
                "period": period_key,
                "days_remaining": days_remaining,
            }

    if os.path.exists(transactions_path):
        with open(transactions_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    transaction_date = datetime.strptime(row["date"], "%Y-%m-%d")
                    amount = float(row["amount"])

                    if transaction_date.year == current.year:
                        report["yearly_spending"] += amount
                        if transaction_date.month == current.month:
                            report["monthly_spending"] += amount

                    for card, card_benefits in benefits_config.items():
                        for benefit, details in card_benefits.items():
                            reset_cycle = details.get("reset_cycle", "annual")
                            if not is_in_current_period(
                                reset_cycle, transaction_date, current, benefit_details=details
                            ):
                                continue
                            if transaction_matches_benefit(row, details):
                                report["benefits"][card][benefit]["spent"] += amount
                except (ValueError, KeyError, TypeError):
                    continue

    # Finalize rounded totals, remaining credit, percent_used, and used_up status
    report["monthly_spending"] = round(report["monthly_spending"], 2)
    report["yearly_spending"] = round(report["yearly_spending"], 2)
    for card in report["benefits"]:
        for benefit, entry in report["benefits"][card].items():
            spent = round(entry["spent"], 2)
            total = entry["total"]
            remaining = round(max(0.0, total - spent), 2)
            entry["spent"] = spent
            entry["remaining"] = remaining
            entry["used_up"] = remaining <= 0.0
            entry["percent_used"] = round(min(100.0, (spent / total) * 100.0), 1) if total > 0 else 100.0

    return report


def main() -> None:
    """Generates and prints a JSON report of spending and benefit progress."""
    report_data = calculate_spending()
    print(json.dumps(report_data, indent=2))


if __name__ == "__main__":
    main()