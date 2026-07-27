"""
fo-intel-pipeline — IRS 990-PF XML fetch + officer parsing

Fetches individual 990-PF XMLs from the GivingTuesday 990 Data Lake
(the original AWS `irs-form-990` bucket was discontinued Dec 2021 —
see docs/pivot_log.md pivot 3) and extracts officer/trustee names +
titles from the OfficerDirTrstKeyEmplGrp element.

Verified against live data: COBALT FOUNDATION (EIN 611378956) returns
officers ['DAVID S BLUE', 'TODD L BLUE', 'KAREN BLUE'] — a clear
shared-surname family signal invisible through ProPublica's API alone.

Some filings list 'SEE ATTACHED' instead of inline names (the foundation
attaches a schedule rather than populating the XML table). These are
flagged as officers_attached_schedule=True so downstream filtering
doesn't treat them as 1-officer foundations.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from lxml import etree

from config import IRS_XML_REQUEST_DELAY_SECONDS, IRS_XML_SOURCE_URL

_HEADERS = {"User-Agent": "fo-intel-pipeline/0.1 (research)"}
_NS = {"efile": "http://www.irs.gov/efile"}
_OFFICER_GROUP = "OfficerDirTrstKeyEmplGrp"


def fetch_xml(object_id: str) -> str | None:
    """Fetch a single 990 XML by OBJECT_ID. Returns None on 404/missing."""
    url = f"{IRS_XML_SOURCE_URL}/{object_id}_public.xml"
    with httpx.Client(headers=_HEADERS, timeout=30.0, follow_redirects=True) as c:
        r = c.get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text


def parse_officers(xml_text: str) -> dict[str, Any]:
    """
    Parse officer/trustee names + titles from a 990-PF XML.

    Returns:
        officers: list of {name, title}
        officer_count: int (len of officers, 0 if 'SEE ATTACHED')
        officers_attached_schedule: bool — True if the filing says
            'SEE ATTACHED' rather than listing names inline
        has_shared_surname: bool — True if >=2 officers share a surname
    """
    root = etree.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    groups = root.findall(f".//{{{ _NS['efile'] }}}{_OFFICER_GROUP}")

    officers: list[dict[str, str]] = []
    attached = False
    for g in groups:
        nm = g.find("efile:PersonNm", _NS)
        title = g.find("efile:TitleTxt", _NS)
        name = (nm.text or "").strip() if nm is not None and nm.text else ""
        title_txt = (title.text or "").strip() if title is not None and title.text else ""
        if not name:
            continue
        if name.upper() == "SEE ATTACHED":
            attached = True
            continue
        officers.append({"name": name, "title": title_txt})

    surnames = [_surname(o["name"]) for o in officers]
    has_shared = len(surnames) != len(set(surnames)) and len(surnames) > 1

    return {
        "officers": officers,
        "officer_count": len(officers),
        "officers_attached_schedule": attached,
        "has_shared_surname": has_shared,
    }


def _surname(full_name: str) -> str:
    """Extract a normalized surname for clustering. Last token, uppercased."""
    # Handles 'DAVID S BLUE' -> 'BLUE', 'R Stuart Bewley' -> 'BEWLEY'
    tokens = full_name.strip().split()
    return tokens[-1].upper() if tokens else ""


def fetch_and_parse(object_id: str) -> dict[str, Any] | None:
    """Convenience: fetch + parse. Returns None if XML unavailable."""
    xml = fetch_xml(object_id)
    time.sleep(IRS_XML_REQUEST_DELAY_SECONDS)
    if xml is None:
        return None
    parsed = parse_officers(xml)
    parsed["xml_object_id"] = object_id
    return parsed
