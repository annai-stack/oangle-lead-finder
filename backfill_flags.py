#!/usr/bin/env python
"""Backfill warm_intro / grant_eligible onto an existing leads CSV.

Older CSVs (and any run made before a client gained a warm-brand or grant
config) have no such columns, which made the UI report "0 warm intros" when the
truth was "never collected". This adds them without re-running discovery:

  warm_intro     — deterministic, from client_config['warm_intro_brands']
  grant_eligible — one model call, judged by client_config['grant_logic']

Usage:
  .venv/bin/python backfill_flags.py leads_fnb_20260603_232817.csv [--client oangle]
                                     [--no-grant]   # skip the model call (free)
"""
import argparse
import csv
import json
import re
import sys
from datetime import datetime

import client_config as cc
import discover

MODEL = "claude-sonnet-4-6"


def grant_flags(profile: dict, rows: list[dict]) -> dict[str, str]:
    """Ask the model to judge grant eligibility for every row in one call."""
    import anthropic
    import tomllib
    secrets = tomllib.load(open(".streamlit/secrets.toml", "rb"))
    client = anthropic.Anthropic(api_key=secrets["ANTHROPIC_API_KEY"])

    listing = "\n".join(
        f"{i+1}. {r.get('brand_name') or r.get('company_name')} — "
        f"{r.get('segment', '')}; {r.get('fit_reason', '')[:160]}"
        for i, r in enumerate(rows)
    )
    msg = client.messages.create(
        model=MODEL, max_tokens=2048,
        system=(f"You assess grant eligibility for {profile['client_name']} leads.\n"
                f"{profile['grant_logic']}\n"
                "Judge each company on the evidence given. Return ONLY a JSON array "
                'of {"n": <number>, "grant": "Y|N"} — no prose.'),
        messages=[{"role": "user", "content": listing}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        print("  [WARN] could not parse grant response; leaving blank", file=sys.stderr)
        return {}
    out = {}
    for item in json.loads(m.group()):
        idx = int(item.get("n", 0)) - 1
        if 0 <= idx < len(rows):
            key = rows[idx].get("brand_name") or rows[idx].get("company_name")
            out[key] = "Y" if str(item.get("grant", "")).upper().startswith("Y") else "N"
    usage = msg.usage
    print(f"  grant pass: {usage.input_tokens} in / {usage.output_tokens} out tokens")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--client", default="oangle")
    ap.add_argument("--no-grant", action="store_true", help="skip the model call")
    args = ap.parse_args()

    profile = cc.get_client(args.client)
    rows = list(csv.DictReader(open(args.csv_path)))
    print(f"Loaded {len(rows)} leads from {args.csv_path}")

    # warm_intro — free and deterministic
    discover.apply_account_flags(profile, rows)
    warm = sum(1 for r in rows if r.get("warm_intro") == "Y")
    print(f"  warm_intro: {warm} flagged "
          f"({', '.join(r['brand_name'] for r in rows if r.get('warm_intro') == 'Y') or '—'})")

    # grant_eligible — model judgement, per the client's configured rule
    if args.no_grant or not profile.get("grant_logic"):
        print("  grant_eligible: skipped")
    else:
        flags = grant_flags(profile, rows)
        for r in rows:
            key = r.get("brand_name") or r.get("company_name")
            r["grant_eligible"] = flags.get(key, "N")
        print(f"  grant_eligible: {sum(1 for r in rows if r.get('grant_eligible') == 'Y')} flagged")

    rows = discover._warm_first(rows)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"leads_fnb_{stamp}.csv"
    cols = list(dict.fromkeys([c for r in rows for c in r]))
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"→ wrote {out_path} ({len(cols)} columns; original left untouched)")


if __name__ == "__main__":
    main()
