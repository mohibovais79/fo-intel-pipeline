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
    # With 2 channels the target is a near-even ~50/50 split. If either
    # channel produces more than this fraction of candidates by the
    # halfway point, stop and expand the under-producing channel before
    # continuing. Tracked live, not at the end.
    "max_single_source_share_at_halfway": 0.60,
    # Post-qualification source-mix guard. The discovery-volume ratio
    # (52/48 at raw stage) can diverge sharply from the final-output
    # ratio after the qualification gate trims. If 990-PF supplies 40+
    # of the final 50 while SEC EDGAR supplies only its 8 IAPD-verified,
    # the final ratio would be ~84/16 — violating the spirit of the
    # brief's rule ("most of your file traces to one source") even
    # though the discovery ratio was balanced. This guard is checked
    # AFTER enrichment, on the final qualified set, not just at
    # discovery. If violated, the under-producing channel must be
    # expanded (wider filter, more states, or a 3rd channel) before
    # delivery.
    "max_single_source_share_final": 0.65,
}

# --- ProPublica Nonprofit Explorer API ---
# Public, no key, rate-limited. Be polite.
PROPUBLICA_BASE_URL = "https://projects.propublica.org/nonprofits/api/v2"
PROPUBLICA_REQUEST_DELAY_SECONDS = 0.5  # throttle between paginated calls
PROPUBLICA_PAGE_SIZE = 100               # max results per page

# --- IRS 990 e-file XML ---
# ProPublica's parsed JSON contains only financial summary fields —
# officer/trustee NAMES live only in the raw 990-PF XML. The original
# AWS Open Data `irs-form-990` S3 bucket was discontinued Dec 31, 2021
# (single-XML fetches by OBJECT_ID return 404). The IRS now publishes
# monthly ZIP bundles on apps.irs.gov (~100-160MB each), but downloading
# ~1.8GB/year to extract a few hundred candidate XMLs is wasteful.
#
# The GivingTuesday 990 Data Lake mirrors the IRS e-file XMLs as
# individual files, accessible via public HTTPS with no AWS account.
# This gives per-candidate XML download without bulk ZIP transfers.
# Caveat: GivingTuesday bucket last updated 2023-10-28; for tax years
# 2024+ a fallback to IRS monthly ZIPs with stream-extraction would
# be needed. See docs/pivot_log.md pivot 3.
IRS_INDEX_BASE_URL = "https://apps.irs.gov/pub/epostcard/990/xml"
IRS_XML_SOURCE_URL = "https://gt990datalake-rawdata.s3.amazonaws.com/EfileData/XmlFiles"
IRS_XML_REQUEST_DELAY_SECONDS = 0.3
# Years to pull index + XML for, per candidate EIN (most recent first).
# 990-PF e-filing became effectively mandatory for tax years >= 2021.
IRS_INDEX_YEARS = [2023, 2022, 2021, 2019]

# --- Pre-XML noise filter (step 4) ---
# Operating-charity supporting foundations (hospital, university, church)
# file 990-PF, have $10M+ assets, and are NOT family offices. Name-
# keyword exclusion catches the bulk of this noise before we spend an
# XML fetch. Conservative by design — false positives (a real SFO
# excluded because its name contains a keyword) are recoverable in
# manual review; false negatives (a hospital foundation that ships as
# an SFO) are disqualifying.
#
# This is the PRE-XML filter only. The real family-foundation
# fingerprint (officer count ≤ ~6 AND/OR shared surname) runs post-XML
# in step 6 — see docs/pivot_log.md pivot 4.
OPERATING_CHARITY_KEYWORDS = [
    "hospital", "medical center", "health system", "health care",
    "university", "college", "school", "academy",
    "church", "diocese", "cathedral", "synagogue", "temple", "mosque",
    "archdiocese", "convention", "conference of",
]

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
