"""
fo-intel-pipeline — ProPublica Nonprofit Explorer client

Two responsibilities:
1. Enumerate all 501(c)(3) EINs in a target state (paginated /search.json).
2. Fetch per-EIN org detail (financials + mailing address) via
   /organizations/{ein}.json.

The search endpoint cannot filter by foundation_code (private foundation
vs. public charity) — that filtering is done downstream via the IRS index
CSV (see irs_index.py). ProPublica's role here is state filtering and
financial/address enrichment, which is what it's actually good at.

See docs/pivot_log.md pivot 1 for why this split exists.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from config import (
    PROPUBLICA_BASE_URL,
    PROPUBLICA_REQUEST_DELAY_SECONDS,
)

_HEADERS = {
    "User-Agent": "fo-intel-pipeline/0.1 (research; mailto:mohibovais79@gmail.com)"
}


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=PROPUBLICA_BASE_URL,
        headers=_HEADERS,
        timeout=30.0,
        follow_redirects=True,
    )


def fetch_state_501c3_eins(state: str) -> set[str]:
    """
    Paginate /search.json with state[id]={state} and c_code[id]=3 (501c3)
    to collect every 501(c)(3) EIN registered in the state.

    ProPublica caps total_results at 10000 per query. For CA this is the
    actual count (~10k 501c3 orgs), so we paginate all pages. If a state
    exceeds 10000 we'd need to sub-query by NTEE major group; not needed
    for the CA pilot.
    """
    eins: set[str] = set()
    with _client() as c:
        page = 0
        while True:
            r = c.get(
                "/search.json",
                params={"state[id]": state, "c_code[id]": "3", "page": page},
            )
            r.raise_for_status()
            data = r.json()
            orgs = data.get("organizations", [])
            if not orgs:
                break
            for o in orgs:
                eins.add(str(o["ein"]))
            total_pages = data.get("num_pages", 0)
            print(f"  page {page}/{total_pages - 1}: +{len(orgs)} (total {len(eins)})")
            if page >= total_pages - 1:
                break
            page += 1
            time.sleep(PROPUBLICA_REQUEST_DELAY_SECONDS)
    return eins


def fetch_org_detail(ein: str) -> dict[str, Any] | None:
    """
    Fetch /organizations/{ein}.json and return the most recent filing_with_data
    plus the org-level address. Returns None if the EIN has no parseable filing.
    """
    with _client() as c:
        r = c.get(f"/organizations/{ein}.json")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        d = r.json()

    org = d.get("organization", {}) or {}
    filings = d.get("filings_with_data", []) or []
    if not filings:
        return None
    # filings_with_data is ordered most-recent-first per ProPublica convention,
    # but sort explicitly by tax_prd_yr descending to be safe.
    filings.sort(key=lambda f: f.get("tax_prd_yr") or 0, reverse=True)
    latest = filings[0]

    return {
        "ein": ein,
        "entity_name": org.get("name"),
        "street_address": org.get("address"),
        "city": org.get("city"),
        "state_region": org.get("state"),
        "zip": org.get("zipcode"),
        "assets_usd": latest.get("totassetsend"),
        "tax_year": latest.get("tax_prd_yr"),
        "formtype": latest.get("formtype"),
        "foundation_code": org.get("foundation_code"),
        "subsection_code": org.get("subsection_code"),
    }
