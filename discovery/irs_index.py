"""
fo-intel-pipeline — IRS 990 e-file index CSV loader

The IRS publishes a yearly index CSV mapping (EIN, tax_period, RETURN_TYPE)
to an OBJECT_ID that uniquely identifies the e-filed XML. We download the
index for the years we care about, filter to RETURN_TYPE='990PF', and
build an EIN → [(object_id, tax_period), ...] lookup.

This is the authoritative source of "who filed a 990-PF" — ProPublica's
search API cannot filter by form type (see docs/pivot_log.md pivot 1).

Index CSVs are ~50-80MB each; we cache them under data/cache/ so re-runs
don't re-download. The 990PF-only subset is small enough to hold in
memory (~125k rows/year).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import httpx

from config import IRS_INDEX_BASE_URL, IRS_INDEX_YEARS

_HEADERS = {"User-Agent": "fo-intel-pipeline/0.1 (research)"}
_CACHE_DIR = Path("data/cache")


def _index_url(year: int) -> str:
    return f"{IRS_INDEX_BASE_URL}/{year}/index_{year}.csv"


def _download_index(year: int) -> str:
    """Download (with cache) the index CSV for a year, return text content."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _CACHE_DIR / f"index_{year}.csv"
    if cache_path.exists():
        print(f"  index {year}: cached ({cache_path.stat().st_size} bytes)")
        return cache_path.read_text()

    url = _index_url(year)
    print(f"  index {year}: downloading from {url}")
    with httpx.Client(headers=_HEADERS, timeout=180.0, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        text = r.text
    cache_path.write_text(text)
    print(f"  index {year}: cached ({len(text)} bytes)")
    return text


def build_990pf_lookup() -> dict[str, list[dict[str, str]]]:
    """
    Returns {EIN: [{object_id, tax_period, taxpayer_name}, ...]} for every
    990-PF e-filing across IRS_INDEX_YEARS. Multiple filings per EIN are
    kept (most recent first by tax_period) so we can fall back to older
    years if the latest XML isn't in the GivingTuesday mirror.
    """
    lookup: dict[str, list[dict[str, str]]] = {}
    for year in IRS_INDEX_YEARS:
        text = _download_index(year)
        reader = csv.DictReader(io.StringIO(text))
        count = 0
        for row in reader:
            if row.get("RETURN_TYPE") != "990PF":
                continue
            ein = row.get("EIN", "").strip()
            if not ein:
                continue
            lookup.setdefault(ein, []).append(
                {
                    "object_id": row["OBJECT_ID"],
                    "tax_period": row.get("TAX_PERIOD", ""),
                    "taxpayer_name": row.get("TAXPAYER_NAME", ""),
                }
            )
            count += 1
        print(f"  index {year}: {count} 990-PF rows")

    # Sort each EIN's filings by tax_period descending (most recent first)
    for ein in lookup:
        lookup[ein].sort(key=lambda x: x["tax_period"], reverse=True)
    return lookup
