#!/usr/bin/env python3
"""
Quick single-segment test — prints to terminal, no CSV export.
Use this to verify the agent before running the full pipeline.

Usage:
    python test_run.py
"""

import anthropic
from lead_finder import MODEL, search_segment

TEST_SEGMENT = "bubble tea and kiosk outlets"

client = anthropic.Anthropic()

print(f"Test run: '{TEST_SEGMENT}'")
print(f"Model: {MODEL}")
print("=" * 50)

leads = search_segment(client, TEST_SEGMENT)

if not leads:
    print("No leads returned — check ANTHROPIC_API_KEY and stderr for details.")
else:
    for i, lead in enumerate(leads, 1):
        print(f"\n{i}. {lead.get('brand_name')} ({lead.get('company_name')})")
        print(f"   Outlets : {lead.get('num_outlets_sg')}")
        print(f"   Website : {lead.get('website')}")
        print(f"   Source  : {lead.get('source_url', '(none)')}")
        print(f"   Score   : {lead.get('fit_score')}/5 — {lead.get('fit_reason')}")
        print(f"   Conf    : {lead.get('confidence')}  |  Grant: {lead.get('grant_eligible')}  |  Warm: {lead.get('warm_intro')}")
        print(f"   Products: {', '.join(lead.get('suggested_products', []))}")

print(f"\nDone. {len(leads)} lead(s) returned.")
