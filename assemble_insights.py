"""
Assemble AI Lead Insights from a contact skeleton + per-company enrichment files.

This is the Option-A path: contacts/firmographics come from Hunter+Apollo (the
`_skeleton.json` produced by the contact fetch) and the rich AI columns come
from research saved as `*_result.json` files (one per company, keyed by contact
email). No Anthropic API key is used here — assembly is pure local merge.

Used by both the CLI (`python assemble_insights.py`) and the Streamlit view.
"""

import json
from pathlib import Path

import client_config
import insights_enrich as ie
import excel_export

HERE = Path(__file__).parent
HEAT_ORDER = {h: i for i, h in enumerate(["1A", "1B", "2", "3", "4"])}


def load_enrichment(result_files: list[Path]) -> tuple[dict, dict]:
    """Return (contact_enrich_by_email, company_enrich_by_email)."""
    contact_enrich, company_enrich = {}, {}
    for f in result_files:
        d = json.loads(Path(f).read_text())
        comp = {k: d.get(k, "") for k in ("company_topline", "company_press", "how_to_target")}
        for email, fields in d.get("contacts", {}).items():
            contact_enrich[email.lower()] = fields
            company_enrich[email.lower()] = comp
    return contact_enrich, company_enrich


def _augment_with_csv_account_data(skeleton: list[dict]) -> None:
    """Backfill account-context fields (outlets, products, fit reason, …) onto
    skeleton companies from the latest leads CSV, matched by brand name."""
    import pandas as pd
    csvs = sorted(HERE.glob("leads_fnb_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not csvs:
        return
    df = pd.read_csv(csvs[0])
    by_brand = {}
    for _, row in df.iterrows():
        key = str(row.get("brand_name") or row.get("company_name") or "").strip().lower()
        if key:
            by_brand[key] = row.to_dict()
    fields = ("num_outlets_sg", "fit_score", "fit_reason", "suggested_products",
              "grant_eligible", "warm_intro", "source_url", "segment", "confidence")
    for s in skeleton:
        co = s["company"]
        key = str(co.get("brand_name") or co.get("company_name") or "").strip().lower()
        row = by_brand.get(key)
        if not row:
            continue
        for f in fields:
            if not co.get(f) and row.get(f) not in (None, ""):
                co[f] = row[f]


def assemble(client_key: str = "oangle",
             skeleton_path: Path = HERE / "_skeleton.json",
             result_glob: str = "*_result.json") -> list[dict]:
    """Merge skeleton + enrichment into sorted, fully-populated records."""
    profile = client_config.get_client(client_key)
    skeleton = json.loads(Path(skeleton_path).read_text())
    _augment_with_csv_account_data(skeleton)
    contact_enrich, company_enrich = load_enrichment(sorted(HERE.glob(result_glob)))

    records = []
    for s in skeleton:
        co, c = s["company"], s["contact"]
        email = (c.get("contact_email") or "").lower()
        if email not in contact_enrich:
            continue  # skip generic inboxes that were never researched
        records.append(ie.build_record(
            profile, co, c, company_enrich[email], contact_enrich[email]))

    records.sort(key=lambda r: HEAT_ORDER.get(r.get("heat_rank", ""), 99))
    return records


def main() -> None:
    profile = client_config.get_client("oangle")
    records = assemble()
    # Persist records for the Streamlit view, plus the final spreadsheet.
    (HERE / "_insights_records.json").write_text(json.dumps(records, indent=2, default=str))
    path = excel_export.write_xlsx(records, profile, filename="AI_Lead_Insights_Oangle.xlsx")
    print(f"Wrote {len(records)} fully-enriched rows -> {path}")
    print(f"{'Name':22} {'Title':30} {'Company':26} {'Heat':5}")
    print("-" * 86)
    for r in records:
        nm = f"{r['first_name']} {r['last_name']}".strip()
        print(f"{nm:22} {r['job_title'][:28]:30} {r['company_name'][:24]:26} {r['heat_rank']:5}")


if __name__ == "__main__":
    main()
