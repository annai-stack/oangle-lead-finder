"""
db.py — OPTIONAL Supabase-backed persistence + caching for the lead pipeline.

Purpose: cut token/API cost and latency by never re-paying for work already
done, and never contacting a lead twice.

ENTIRELY OPTIONAL. When Supabase is not configured (no SUPABASE_URL/KEY) or the
`supabase` package isn't installed, get_store() returns a NullStore whose every
method is a no-op — the pipeline runs exactly as before. Every live DB call is
wrapped in try/except so a schema/network problem degrades to "cache miss / not
contacted", never a crash.

Cost levers this enables (highest ROI first):
  * enrichment_cache — reuse Claude company/contact/polish results        (token $)
  * org cache        — reuse Apollo firmographics                          (Apollo $)
  * outreach dedup   — skip contacts already worked → spend nothing on them
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apollo_client import extract_domain

# Cache freshness by kind — research ages, so refuse stale hits (quality lever).
_TTL_DAYS = {"company": 45, "contact": 90, "polish": 90, "org": 60}
# Outreach statuses that suppress a contact from future runs.
_SUPPRESS = {"exported", "sent", "replied", "suppressed"}


def _norm(s) -> str:
    return (str(s).strip().lower()) if s else ""


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# NullStore — the default. Every method a safe no-op.
# ---------------------------------------------------------------------------

class NullStore:
    enabled = False
    hits = 0
    misses = 0
    run_id = None

    def load_suppression(self): pass
    def get(self, kind, key): return None
    def put(self, kind, key, payload): pass
    def get_org(self, domain): return None
    def put_org(self, domain, firmo): pass
    def already_contacted(self, email_norm): return False
    def upsert_company(self, company): return None
    def upsert_contact(self, company_id, rec): return None
    def persist_record(self, rec): return None
    def log_outreach(self, contact_id, status="exported", channel="email"): pass
    def start_run(self, args): return None
    def finish_run(self, n_companies=0, n_contacts=0, cost_usd=0.0): pass
    def cache_stats(self): return {"hits": self.hits, "misses": self.misses}


# ---------------------------------------------------------------------------
# Store — live Supabase implementation.
# ---------------------------------------------------------------------------

class Store(NullStore):
    enabled = True

    def __init__(self, sb, client: str, prompt_version: str = "v1", fresh: bool = False):
        self.sb = sb
        self.client = client
        self.pv = prompt_version
        self.fresh = fresh           # bypass cache READS (still writes fresh results)
        self.run_id = None
        self.hits = 0
        self.misses = 0
        self._contacted: set[str] = set()

    # --- outreach suppression (prefetched once → 0 per-contact queries) ---
    def load_suppression(self):
        self._contacted = set()
        try:
            res = (self.sb.table("contacts")
                   .select("email_norm, outreach(status)")
                   .eq("client", self.client).execute())
            for row in (res.data or []):
                statuses = {o.get("status") for o in (row.get("outreach") or [])}
                if statuses & _SUPPRESS and row.get("email_norm"):
                    self._contacted.add(row["email_norm"])
        except Exception:
            self._contacted = set()

    def already_contacted(self, email_norm) -> bool:
        return _norm(email_norm) in self._contacted

    # --- enrichment cache ---
    def get(self, kind, key):
        key = _norm(key)
        if self.fresh or not key:
            self.misses += 1
            return None
        try:
            q = (self.sb.table("enrichment_cache").select("payload, created_at")
                 .eq("client", self.client).eq("kind", kind)
                 .eq("entity_key", key).eq("prompt_version", self.pv))
            days = _TTL_DAYS.get(kind)
            if days:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
                q = q.gte("created_at", cutoff)
            rows = (q.order("created_at", desc=True).limit(1).execute().data) or []
            if rows:
                self.hits += 1
                return rows[0]["payload"]
        except Exception:
            pass
        self.misses += 1
        return None

    def put(self, kind, key, payload):
        key = _norm(key)
        if not key:
            return
        try:
            self.sb.table("enrichment_cache").upsert(
                {"client": self.client, "kind": kind, "entity_key": key,
                 "prompt_version": self.pv, "payload": payload},
                on_conflict="client,kind,entity_key,prompt_version").execute()
        except Exception:
            pass

    # org firmographics reuse the same cache table (kind='org', key=domain)
    def get_org(self, domain):
        return self.get("org", domain)

    def put_org(self, domain, firmo):
        if firmo and any(firmo.values()):   # never cache an empty/failed lookup
            self.put("org", domain, firmo)

    # --- entity persistence ---
    def upsert_company(self, c: dict):
        domain = extract_domain(c.get("website") or c.get("company_domain") or "")
        name_norm = _norm(c.get("brand_name") or c.get("company_name"))
        if not (domain or name_norm):
            return None
        row = {
            "client": self.client, "domain": domain,
            "name_norm": name_norm or domain,
            "company_name": c.get("company_name") or "",
            "brand_name": c.get("brand_name") or c.get("company_name") or "",
            "segment": c.get("segment") or "",
            "num_outlets_sg": _int(c.get("num_outlets_sg")),
            "fit_score": _int(c.get("fit_score")),
            "fit_reason": c.get("fit_reason") or "",
            "source_url": c.get("source_url") or "",
            "updated_at": _now_iso(),
        }
        # Manual upsert (select-then-insert/update). PostgREST on_conflict can't
        # target the PARTIAL unique indexes used for dedup, so we match by hand.
        try:
            q = self.sb.table("companies").select("id").eq("client", self.client)
            q = q.eq("domain", domain) if domain else q.eq("name_norm", name_norm)
            found = q.limit(1).execute().data or []
            if found:
                cid = found[0]["id"]
                self.sb.table("companies").update(row).eq("id", cid).execute()
                return cid
            res = self.sb.table("companies").insert(row).execute()
            return (res.data or [{}])[0].get("id")
        except Exception:
            return None

    def upsert_contact(self, company_id, rec: dict):
        email_norm = _norm(rec.get("email"))
        row = {
            "client": self.client, "company_id": company_id,
            "email": rec.get("email") or "", "email_norm": email_norm or None,
            "linkedin_url": rec.get("linkedin_url") or "",
            "first_name": rec.get("first_name") or "", "last_name": rec.get("last_name") or "",
            "job_title": rec.get("job_title") or "", "seniority": rec.get("seniority") or "",
            "source": rec.get("source") or "",
            "run_id": self.run_id,                 # ties this lead to the run that generated it
            "email_result": rec.get("email_result") or "",
            "data_confidence": rec.get("data_confidence") or "",
            "verification_notes": rec.get("verification_notes") or "",
            "data": rec, "updated_at": _now_iso(),
        }
        # Manual upsert — see upsert_company (partial unique index on email_norm).
        try:
            if email_norm:
                found = (self.sb.table("contacts").select("id")
                         .eq("client", self.client).eq("email_norm", email_norm)
                         .limit(1).execute().data or [])
                if found:
                    cid = found[0]["id"]
                    self.sb.table("contacts").update(row).eq("id", cid).execute()
                    return cid
            res = self.sb.table("contacts").insert(row).execute()
            return (res.data or [{}])[0].get("id")
        except Exception:
            return None

    def persist_record(self, rec: dict):
        """Upsert the company + contact behind one export record; return contact id."""
        company_id = self.upsert_company({
            "website": rec.get("company_domain") or rec.get("source_url", ""),
            "company_domain": rec.get("company_domain"),
            "company_name": rec.get("company_name"),
            "brand_name": rec.get("company_name"),
            "segment": rec.get("segment"),
            "num_outlets_sg": rec.get("num_outlets_sg"),
            "fit_score": rec.get("fit_score"),
            "fit_reason": rec.get("fit_reason"),
            "source_url": rec.get("source_url"),
        })
        return self.upsert_contact(company_id, rec)

    # --- outreach log + runs ---
    def log_outreach(self, contact_id, status="exported", channel="email"):
        if not contact_id:
            return
        try:
            self.sb.table("outreach").insert(
                {"contact_id": contact_id, "client": self.client,
                 "run_id": self.run_id, "status": status, "channel": channel}).execute()
        except Exception:
            pass

    def start_run(self, args):
        try:
            res = self.sb.table("runs").insert(
                {"client": self.client, "args": args}).execute()
            self.run_id = (res.data or [{}])[0].get("id")
        except Exception:
            self.run_id = None

    def finish_run(self, n_companies=0, n_contacts=0, cost_usd=0.0):
        if not self.run_id:
            return
        try:
            self.sb.table("runs").update(
                {"n_companies": n_companies, "n_contacts": n_contacts,
                 "cost_usd": round(float(cost_usd), 4), "finished_at": _now_iso()}
            ).eq("id", self.run_id).execute()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_store(keys: dict, client: str, prompt_version: str = "v1",
              fresh: bool = False, disabled: bool = False) -> NullStore:
    """Return a live Store if Supabase is configured + importable, else NullStore."""
    url = keys.get("SUPABASE_URL")
    key = keys.get("SUPABASE_KEY")
    if disabled or not (url and key):
        return NullStore()
    try:
        from supabase import create_client
        sb = create_client(url, key)
        store = Store(sb, client, prompt_version, fresh)
        store.load_suppression()
        return store
    except Exception as exc:
        print(f"      [WARN] Supabase unavailable ({exc}); continuing without DB.")
        return NullStore()
