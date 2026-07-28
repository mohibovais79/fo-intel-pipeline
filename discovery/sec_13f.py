"""
fo-intel-pipeline — SEC 13F bulk data loader + family-office discovery filter

Downloads the SEC's quarterly 13F data set (pre-parsed TSVs from EDGAR
XML filings), aggregates at the firm level (not per-accession, to avoid
fund-vehicle double-counting), and applies the empirically-derived
family-office discovery filter.

The filter thresholds were locked EMPIRICALLY by inspecting one quarter
of data and comparing a known family office (Cascade Investment Group,
Bill Gates' FO, 146 holdings) against known hedge funds (Citadel 15,551
holdings, AQR 14,495, Millennium 5,978, Two Sigma 3,628). See
docs/pivot_log.md pivot 6.

Key empirical findings:
- Holdings count 1-5: PE/VC fund vehicle (NOT family office) — exclude
- Holdings count 10-500, concentrated: family office signal
- Holdings count 1000+: quant/multi-strat hedge fund — exclude
- AUM discovery floor $25M (qualification gate trims later)
- No AUM ceiling for discovery (Cascade reports $151B in 13F securities
  alone; the $5B ceiling in THRESHOLDS is for the qualification gate,
  not for discovery filtering)
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

_HEADERS = {
    "User-Agent": "fo-intel-pipeline/0.1 (research; mailto:mohibovais79@gmail.com)"
}
_CACHE_DIR = Path("data/cache/13f")

# Empirically-derived discovery filter thresholds.
# These are DISCOVERY filters (cast wide, let qualification gate trim).
# The holdings count band is the key discriminator — see module docstring.
HOLDINGS_MIN = 10
HOLDINGS_MAX = 500
AUM_FLOOR_M = 25  # $25M — matches THRESHOLDS["aum_minimum_usd_millions"]
# AUM ceiling for discovery: $5B, matching the qualification gate's
# aum_maximum_usd_millions. The config comment explicitly states this
# "excludes mega-offices (Cascade, Bezos, Gates-style) that fall
# outside the commercially viable mid-market target band." There's no
# reason to discover candidates we'd immediately exclude at
# qualification. This also cuts mega-institutional managers
# (Capital International $508B, Dodge & Cox $175B) that passed the
# holdings band but are obviously not family offices.
AUM_CEIL_M = 5_000  # $5B — matches THRESHOLDS["aum_maximum_usd_millions"]

# SEC 13F bulk data set URL pattern.
# The page at sec.gov/data-research/sec-markets-data/form-13f-data-sets
# lists quarterly ZIPs. URL pattern:
#   /files/structureddata/data/form-13f-data-sets/{start}_{end}_form13f.zip
# We hardcode recent quarters rather than scraping the page, for simplicity.
QUARTERLY_ZIPS = [
    ("2025q4", "01sep2025-30nov2025_form13f.zip"),
    ("2025q3", "01jun2025-31aug2025_form13f.zip"),
    ("2025q2", "01mar2025-31may2025_form13f.zip"),
]


def _download_quarter(quarter: str, filename: str) -> Path:
    """Download (with cache) a quarterly 13F ZIP, return local path."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _CACHE_DIR / f"{quarter}_form13f.zip"
    if cache_path.exists():
        print(f"  13f {quarter}: cached ({cache_path.stat().st_size} bytes)")
        return cache_path

    url = f"https://www.sec.gov/files/structureddata/data/form-13f-data-sets/{filename}"
    print(f"  13f {quarter}: downloading from {url}")
    with httpx.Client(headers=_HEADERS, timeout=300, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        cache_path.write_bytes(r.content)
    print(f"  13f {quarter}: cached ({cache_path.stat().st_size} bytes)")
    return cache_path


def _load_institutional_exclude_names() -> set[str]:
    """Load EXCLUDE_NAMES.txt as a set of lowercased tokens."""
    names: set[str] = set()
    path = Path("EXCLUDE_NAMES.txt")
    if not path.exists():
        return names
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(line.lower())
    return names


def _is_excluded(name: str, exclude_set: set[str]) -> bool:
    """Case-insensitive substring match against the exclude list."""
    name_lower = name.lower()
    for token in exclude_set:
        if token in name_lower:
            return True
    return False


def _resolve_zip_member(z: zipfile.ZipFile, name: str) -> str:
    """
    Some 13F ZIPs store members at the root (COVERPAGE.tsv), others
    under a dated subdirectory (01JUN2025-31AUG2025_form13f/COVERPAGE.tsv).
    Resolve a logical name to its actual path in the archive.
    """
    if name in z.namelist():
        return name
    for member in z.namelist():
        if member.endswith(f"/{name}"):
            return member
    raise KeyError(f"{name} not found in archive (members: {z.namelist()[:3]}...)")


def load_quarter_firms(zip_path: Path) -> dict[str, dict[str, Any]]:
    """
    Load one quarter's 13F data, aggregate at firm level, return
    {firm_name: {n_holdings, n_cusips, total_value_usd, city, state, crd, street, accession}}.
    Only 13F-HR submissions (not 13F-NT notices) are included.
    """
    with zipfile.ZipFile(zip_path) as z:
        # Filter to 13F-HR only
        sub_path = _resolve_zip_member(z, "SUBMISSION.tsv")
        with z.open(sub_path) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, "utf-8"), delimiter="\t")
            hr_accessions = {
                r["ACCESSION_NUMBER"]
                for r in reader
                if r.get("SUBMISSIONTYPE") == "13F-HR"
            }

        # Cover page: filer info per accession
        cover_path = _resolve_zip_member(z, "COVERPAGE.tsv")
        with z.open(cover_path) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, "utf-8"), delimiter="\t")
            cover = {
                r["ACCESSION_NUMBER"]: r
                for r in reader
                if r["ACCESSION_NUMBER"] in hr_accessions
            }

        # Info table: aggregate holdings per accession
        # NOTE: Since Jan 3, 2023, the SEC reports VALUE in DOLLARS (not
        # thousands as before). Our data is 2025+, so VALUE is in dollars.
        per_acc: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"n_holdings": 0, "cusips": set(), "total_value_usd": 0.0}
        )
        info_path = _resolve_zip_member(z, "INFOTABLE.tsv")
        with z.open(info_path) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, "utf-8"), delimiter="\t")
            for row in reader:
                acc = row["ACCESSION_NUMBER"]
                if acc not in hr_accessions:
                    continue
                s = per_acc[acc]
                s["n_holdings"] += 1
                cusip = row.get("CUSIP", "")
                if cusip:
                    s["cusips"].add(cusip)
                try:
                    s["total_value_usd"] += float(row.get("VALUE", 0) or 0)
                except ValueError:
                    pass

    # Roll up by firm name
    firms: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "n_filings": 0,
            "n_holdings": 0,
            "cusips": set(),
            "total_value_usd": 0.0,
            "city": "",
            "state": "",
            "crd": "",
            "street": "",
            "accessions": [],
        }
    )
    for acc, cv in cover.items():
        s = per_acc.get(acc)
        if not s or s["n_holdings"] == 0:
            continue
        name = (cv.get("FILINGMANAGER_NAME") or "").strip()
        if not name:
            continue
        fs = firms[name]
        fs["n_filings"] += 1
        fs["n_holdings"] += s["n_holdings"]
        fs["cusips"] |= s["cusips"]
        fs["total_value_usd"] += s["total_value_usd"]
        fs["city"] = cv.get("FILINGMANAGER_CITY", "")
        fs["state"] = cv.get("FILINGMANAGER_STATEORCOUNTRY", "")
        fs["crd"] = cv.get("CRDNUMBER", "")
        fs["street"] = cv.get("FILINGMANAGER_STREET1", "")
        fs["accessions"].append(acc)

    return dict(firms)


def discover_ca_13f_candidates() -> list[dict[str, Any]]:
    """
    Load all configured quarterly 13F ZIPs, aggregate CA filers across
    quarters, apply the empirically-derived discovery filter, and return
    a list of candidate dicts.

    Returns candidates sorted by AUM descending.
    """
    exclude_set = _load_institutional_exclude_names()

    # Aggregate across quarters (a firm may file in multiple quarters;
    # take the most recent quarter's data for each firm)
    all_firms: dict[str, dict[str, Any]] = {}
    for quarter, filename in QUARTERLY_ZIPS:
        zip_path = _download_quarter(quarter, filename)
        quarter_firms = load_quarter_firms(zip_path)
        # Later quarters overwrite earlier ones (more recent data)
        all_firms.update(quarter_firms)
        print(f"  13f {quarter}: {len(quarter_firms)} firms loaded")

    # Filter: CA + holdings band + AUM floor + exclude list
    candidates: list[dict[str, Any]] = []
    excluded_count = 0
    for name, fs in all_firms.items():
        if fs["state"] != "CA":
            continue
        if fs["n_holdings"] < HOLDINGS_MIN or fs["n_holdings"] > HOLDINGS_MAX:
            continue
        # VALUE is in dollars (post-Jan 2023), so divide by 1M for millions
        val_m = fs["total_value_usd"] / 1_000_000
        if val_m < AUM_FLOOR_M or val_m > AUM_CEIL_M:
            continue
        if _is_excluded(name, exclude_set):
            excluded_count += 1
            continue
        candidates.append(
            {
                "firm_name": name,
                "city": fs["city"],
                "state": fs["state"],
                "street": fs["street"],
                "crd": fs["crd"].strip() if fs["crd"] else "",
                "n_holdings": fs["n_holdings"],
                "n_cusips": len(fs["cusips"]),
                "aum_13f_m": val_m,
                "n_filings": fs["n_filings"],
                "accessions": fs["accessions"],
            }
        )

    candidates.sort(key=lambda x: x["aum_13f_m"], reverse=True)
    print(
        f"  13f CA filter: {len(candidates)} candidates passed, "
        f"{excluded_count} excluded by institutional denylist"
    )
    return candidates
