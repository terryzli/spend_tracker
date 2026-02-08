import json
import os
import sys
from datetime import datetime

# Path to the data files
MANUAL_CREDITS_PATH = os.path.join(os.path.dirname(__file__), 'manual_credits.json')
BENEFITS_PATH = os.path.join(os.path.dirname(__file__), 'benefits.json')

def get_period_key(reset_cycle, date_obj):
    """Generates a unique key for the current period based on the reset cycle."""
    if reset_cycle == 'monthly':
        return date_obj.strftime('%Y-%m')
    elif reset_cycle == 'annual':
        return date_obj.strftime('%Y')
    elif reset_cycle == 'biannual_jan_jun':
        period = 1 if 1 <= date_obj.month <= 6 else 2
        return f"{date_obj.year}-P{period}"
    return 'all'

def add_manual_spend(amount, benefit_key):
    """Records manual spending for a specific benefit period."""
    if not os.path.exists(BENEFITS_PATH):
        print("Error: benefits.json not found.")
        return

    with open(BENEFITS_PATH, 'r') as f:
        benefits_config = json.load(f)

    # Find the card for this benefit key
    target_card = None
    reset_cycle = 'annual'
    for card, card_benefits in benefits_config.items():
        if benefit_key in card_benefits:
            target_card = card
            reset_cycle = card_benefits[benefit_key].get('reset_cycle', 'annual')
            break
    
    if not target_card:
        print(f"Error: Benefit key '{benefit_key}' not found.")
        return

    # Load existing manual spend data
    data = {}
    if os.path.exists(MANUAL_CREDITS_PATH):
        with open(MANUAL_CREDITS_PATH, 'r') as f:
            try:
                data = json.load(f)
            except:
                data = {}

    now = datetime.now()
    period_key = get_period_key(reset_cycle, now)
    
    if target_card not in data:
        data[target_card] = {}
    if benefit_key not in data[target_card]:
        data[target_card][benefit_key] = {}
    
    # Add the spend to the current period's manual total
    current_val = data[target_card][benefit_key].get(period_key, 0.0)
    data[target_card][benefit_key][period_key] = round(current_val + float(amount), 2)

    with open(MANUAL_CREDITS_PATH, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"Recorded ${amount} manual spend for {benefit_key} ({period_key}).")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 manual_transaction.py <amount> <benefit_key>")
    else:
        add_manual_spend(sys.argv[1], sys.argv[2])
