"""
fo-intel-pipeline — SEC Form ADV IAPD structured-field verification

Originally planned: fetch ADV Part 2A brochure text, LLM-classify
"manages family capital vs. third-party clients." That failed —
brochure text isn't accessible via public API (see docs/pivot_log.md
pivot 6). Pivoted to IAPD structured fields as secondary confirmation
on 13F-discovered candidates.

What the IAPD API (`api.adviserinfo.sec.gov/search/firm/{crd}`) gives us:
  - basicInformation: firmName, otherNames, iaScope, isIAFirm
  - orgScopeStatusFlags: isSECRegistered, isERARegistered
  - relyingAdvisors: list of relying adviser entities
  - brochures: brochureVersionID, brochureName, dateSubmitted
  - registrationStatus: SEC/state jurisdiction + status

Verification signals (none are sufficient alone; combined with 13F
portfolio concentration they form a 2-signal qualification):
  +2  ERA (Exempt Reporting Adviser) status — family offices often
      file as ERA rather than full SEC registration, since they don't
      manage third-party capital. Strong signal.
  +1  otherNames contains "family" — weak but real signal
  +1  Small relying-advisor count (<=5) — family offices have fewer
      sub-entities than large advisory firms
   0  No CRD available — could_not_verify (honest blank)
  -1  Large relying-advisor count (>10) — institutional pattern

Returns (score, evidence_dict) so the orchestrator can combine with
the 13F concentration signal for a composite qualification score.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

_HEADERS = {
    "User-Agent": "fo-intel-pipeline/0.1 (research; mailto:mohibovais79@gmail.com)",
    "Accept": "application/json",
}

IAPD_FIRM_URL = "https://api.adviserinfo.sec.gov/search/firm/{crd}"


def _fetch_iapd_firm(crd: str) -> dict[str, Any] | None:
    """
    Fetch ADV/IAPD data for a firm by CRD number.
    Returns the parsed iacontent dict, or None if not found.
    """
    if not crd or not crd.strip():
        return None
    crd_clean = crd.strip().lstrip("0") or "0"
    url = IAPD_FIRM_URL.format(crd=crd_clean)
    try:
        with httpx.Client(headers=_HEADERS, timeout=30, follow_redirects=True) as c:
            r = c.get(url)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            return None
        src = hits[0].get("_source", {})
        ia_str = src.get("iacontent")
        if isinstance(ia_str, str):
            return json.loads(ia_str)
        return ia_str
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def verify_via_iapd(crd: str) -> dict[str, Any]:
    """
    Verify a 13F candidate using IAPD structured fields.

    Returns:
        status: "verified" | "could_not_verify" | "rejected"
        score: int (composite IAPD signal score)
        evidence: dict of matching signals with values
        source: "SEC ADV/IAPD (CRD {crd})"
        method: description of how verification was done
    """
    ia = _fetch_iapd_firm(crd)
    if ia is None:
        return {
            "status": "could_not_verify",
            "score": 0,
            "evidence": {},
            "source": None,
            "method": "No CRD number or firm not found in IAPD",
        }

    score = 0
    evidence: dict[str, Any] = {}

    # Signal 1: ERA (Exempt Reporting Adviser) status
    flags = ia.get("orgScopeStatusFlags", {})
    is_era = flags.get("isSECERARegistered") == "Y" or flags.get("isStateERARegistered") == "Y"
    if is_era:
        score += 2
        evidence["era_status"] = "ERA-registered (family offices often file as ERA)"

    # Signal 2: otherNames contains "family"
    basic = ia.get("basicInformation", {})
    other_names = basic.get("otherNames", []) or []
    family_names = [n for n in other_names if "family" in n.lower()]
    if family_names:
        score += 1
        evidence["family_in_other_names"] = family_names[:3]

    # Signal 3: relying-advisor count
    relying = ia.get("relyingAdvisors", []) or []
    n_relying = len(relying)
    if n_relying <= 5:
        score += 1
        evidence["relying_advisor_count"] = n_relying
    elif n_relying > 10:
        score -= 1
        evidence["relying_advisor_count"] = n_relying  # institutional pattern

    # Signal 4: firm name itself contains "family"
    firm_name = basic.get("firmName", "") or ""
    if "family" in firm_name.lower():
        score += 1
        evidence["family_in_firm_name"] = firm_name

    # Determine status
    if score >= 2:
        status = "verified"
    elif score <= -1:
        status = "rejected"
    else:
        status = "could_not_verify"

    return {
        "status": status,
        "score": score,
        "evidence": evidence,
        "source": f"SEC ADV/IAPD (CRD {crd})",
        "method": "IAPD structured fields: ERA status, otherNames, relying-advisor count, firm name",
        "firm_name_iapd": firm_name,
        "is_sec_registered": flags.get("isSECRegistered") == "Y",
        "is_era_registered": is_era,
    }
