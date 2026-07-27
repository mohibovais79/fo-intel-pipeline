"""
fo-intel-pipeline — qualification thresholds and run configuration

All numeric gates live here as named constants, never hardcoded inside
scraper/validator logic. This is what the methodology summary cites so the
inclusion standard is auditable and reproducible.

Rationale per threshold is documented inline so reviewers can see *why* each
fence was set where it was, decided before scraping started (per rule #2 of
the differentiator assessment).
"""

from __future__ import annotations

THRESHOLDS: dict[str, object] = {
    # --- AUM fence (applied at enrichment/qualification, not discovery) ---
    # Floor excludes personal-holding vehicles that aren't real operating FOs;
    # ceiling excludes mega-offices (Cascade, Bezos, Gates-style) that fall
    # outside the commercially viable mid-market target band.
    "aum_minimum_usd_millions": 25,
    "aum_maximum_usd_millions": 5_000,

    # --- Multi-family office gate ---
    # If a firm serves more than this many families it's an advisory firm,
    # not a family office. Verifiable for small N; unverifiable for large N.
    "mfo_max_families_served": 3,

    # --- Geographic scope for this run ---
    # Pilot one state first (CA — highest SFO density, cleanest SOS registry,
    # strongest 990-PF trail). Expand only after the pipeline is validated.
    "target_states": ["CA"],
    "target_countries": ["United States"],

    # --- Form 990-PF discovery filter ---
    # $10M+ foundation assets signals real family wealth (a $1M foundation is
    # usually just a giving vehicle for modest wealth, not an SFO fingerprint).
    # This is the *discovery* filter — the AUM fence above is applied later
    # during enrichment once we have a real investable-wealth estimate.
    "foundation_assets_minimum_usd_millions": 10,

    # --- SEC 13F discovery filter ---
    # All 13F filers are already >$100M by filing threshold, so this is
    # automatic but stated explicitly for methodology transparency.
    "sec_13f_aum_floor": 100,

    # --- Contact decisiveness ---
    # A record with zero usable principal contacts is unactionable even if
    # firm-level data is solid. Require at least one of email/phone.
    "contact_decisiveness_minimum_fields": 1,

    # --- Source-mix diversity guard (rule #1) ---
    # If any single discovery channel produces more than this fraction of
    # candidates by the halfway point, stop and open a new channel before
    # continuing. Tracked live, not at the end.
    "max_single_source_share_at_halfway": 0.60,
}

# --- ProPublica Nonprofit Explorer API ---
# Public, no key, rate-limited. Be polite.
PROPUBLICA_BASE_URL = "https://projects.propublica.org/nonprofits/api/v2"
PROPUBLICA_REQUEST_DELAY_SECONDS = 0.5  # throttle between paginated calls
PROPUBLICA_PAGE_SIZE = 100               # max results per page

# --- Output paths ---
# SQLite is the source of truth between pipeline stages; JSONL exports per
# stage are git-diffable for audit. Both written under data/.
DATABASE_PATH = "data/candidates.db"
DISCOVERY_OUTPUT_DIR = "data/discovery"

# --- Qualification gate ---
# Substring match against EXCLUDE_NAMES.txt entries. If an entity name
# contains any excluded token, it is auto-rejected with this reason code
# and routed to ExcludedCandidate (audit pool, not delivery).
EXCLUDE_NAMES_FILE = "EXCLUDE_NAMES.txt"
EXCLUSION_REASON_INSTITUTIONAL = "excluded_institutional_manager"
