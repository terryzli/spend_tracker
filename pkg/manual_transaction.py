#!/usr/bin/env python3
"""Records manual spending toward a specific credit card benefit period."""

import argparse
import os
import sys
from datetime import datetime
from typing import List, Optional

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from pkg.common import (
    ROOT_DIR,
    atomic_write_json,
    get_config_path,
    get_period_key,
    load_config,
    load_json_file,
)


def add_manual_spend(
    amount: float,
    benefit_key: str,
    root_dir: str = ROOT_DIR,
    now: Optional[datetime] = None,
) -> bool:
    """Records manual spending for a specific benefit period atomically."""
    config = load_config(root_dir=root_dir)
    benefits_path = get_config_path(config, "benefits", "config/benefits.json", root_dir=root_dir)
    manual_credits_path = get_config_path(
        config, "manual_credits", "config/manual_credits.json", root_dir=root_dir
    )

    if not os.path.exists(benefits_path):
        print(f"Error: benefits.json not found at {benefits_path}.", file=sys.stderr)
        return False

    benefits_config = load_json_file(benefits_path, default={})

    # Support "card:benefit_key", "card.benefit_key", or "card/benefit_key"
    specified_card = None
    for sep in (":", ".", "/"):
        if sep in benefit_key:
            specified_card, benefit_key = benefit_key.split(sep, 1)
            break

    target_card = None
    benefit_details = {}
    reset_cycle = "annual"

    if specified_card and specified_card in benefits_config:
        if benefit_key in benefits_config[specified_card]:
            target_card = specified_card
            benefit_details = benefits_config[target_card][benefit_key]
            reset_cycle = benefit_details.get("reset_cycle", "annual")
        else:
            print(
                f"Error: Benefit key '{benefit_key}' not found in card '{specified_card}'.",
                file=sys.stderr,
            )
            return False
    else:
        for card, card_benefits in benefits_config.items():
            if benefit_key in card_benefits:
                target_card = card
                benefit_details = card_benefits[benefit_key]
                reset_cycle = benefit_details.get("reset_cycle", "annual")
                break

    if not target_card:
        print(f"Error: Benefit key '{benefit_key}' not found in {benefits_path}.", file=sys.stderr)
        return False

    data = load_json_file(manual_credits_path, default={})
    current = now or datetime.now()
    period_key = get_period_key(reset_cycle, current, benefit_details=benefit_details)

    data.setdefault(target_card, {}).setdefault(benefit_key, {})
    current_val = float(data[target_card][benefit_key].get(period_key, 0.0))
    data[target_card][benefit_key][period_key] = round(current_val + float(amount), 2)

    atomic_write_json(manual_credits_path, data)
    print(f"Recorded ${float(amount):.2f} manual spend for {target_card}:{benefit_key} ({period_key}).")
    return True


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record manual spending for a credit card benefit.")
    parser.add_argument("amount", type=float, help="Dollar amount spent.")
    parser.add_argument(
        "benefit_key",
        help="Benefit key (e.g. 'uber_credit' or 'amex_gold:uber_credit').",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return 0 if add_manual_spend(args.amount, args.benefit_key) else 1


if __name__ == "__main__":
    sys.exit(main())
