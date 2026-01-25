#!/usr/bin/env python3

import json
import csv
from datetime import datetime
import os

# Get the directory of the current script to locate data files
script_dir = os.path.dirname(os.path.abspath(__file__))

def get_biannual_period(date_obj):
    """Returns the biannual period (1 for Jan-Jun, 2 for Jul-Dec) for a given date."""
    if 1 <= date_obj.month <= 6:
        return 1
    else:
        return 2

def calculate_spending():
    """
    Calculates total spending and benefit progress on the fly from the transactions log.
    """
    benefits_path = os.path.join(script_dir, 'benefits.json')
    transactions_path = os.path.join(script_dir, 'transactions.csv')

    # Load benefit rules
    with open(benefits_path, 'r') as f:
        benefits_config = json.load(f)
    
    # Initialize report structure based on benefits config
    report = {
        'monthly_spending': 0,
        'yearly_spending': 0,
        'benefits': {card: {benefit: {'spent': 0, 'total': details['total']} for benefit, details in card_benefits.items()} for card, card_benefits in benefits_config.items()}
    }

    now = datetime.now()
    # When running in HA, the timezone might be UTC. For accurate date comparison, let's use naive datetimes.
    # A better solution would be to handle timezones consistently everywhere.
    now = now.astimezone().replace(tzinfo=None)


    if not os.path.exists(transactions_path):
        return report

    with open(transactions_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                # --- General Spending Calculation ---
                transaction_date_str = row['date']
                # Parse the 'YYYY-MM-DD' date string
                transaction_date = datetime.strptime(transaction_date_str, '%Y-%m-%d')

                if transaction_date.year == now.year:
                    report['yearly_spending'] += float(row['amount'])
                    if transaction_date.month == now.month:
                        report['monthly_spending'] += float(row['amount'])

                # --- Benefit-Specific Calculation ---
                for card, card_benefits in benefits_config.items():
                    for benefit, details in card_benefits.items():
                        for keyword in details['keywords']:
                            if keyword.lower() in row['merchant'].lower():
                                # Keyword matched, now check if it's in the current reset period
                                reset_cycle = details.get('reset_cycle', 'annual')
                                add_amount = False

                                if reset_cycle == 'monthly':
                                    if transaction_date.month == now.month and transaction_date.year == now.year:
                                        add_amount = True
                                elif reset_cycle == 'biannual_jan_jun':
                                    if get_biannual_period(transaction_date) == get_biannual_period(now) and transaction_date.year == now.year:
                                        add_amount = True
                                elif reset_cycle == 'annual':
                                    if transaction_date.year == now.year:
                                        add_amount = True
                                
                                if add_amount:
                                    report['benefits'][card][benefit]['spent'] += float(row['amount'])
                                    # Break keyword loop once a match is found for a benefit
                                    break
            
            except (ValueError, KeyError) as e:
                # Optional: log parsing errors
                # print(f"Skipping row due to error: {e}")
                continue
    
    # Round all final values for clean output
    report['monthly_spending'] = round(report['monthly_spending'], 2)
    report['yearly_spending'] = round(report['yearly_spending'], 2)
    for card in report['benefits']:
        for benefit in report['benefits'][card]:
            report['benefits'][card][benefit]['spent'] = round(report['benefits'][card][benefit]['spent'], 2)

    return report

def main():
    """Generates and prints a JSON report of spending and benefit progress."""
    report_data = calculate_spending()
    print(json.dumps(report_data, indent=2))

if __name__ == '__main__':
    main()