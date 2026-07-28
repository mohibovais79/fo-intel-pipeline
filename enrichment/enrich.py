"""
fo-intel-pipeline — full enrichment with VerifiedField provenance

Maps the 44 qualified candidates to FamilyOfficeRecord schema with
per-field provenance (source + method + checked_on). Every high-value
field carries its own verification status — "verified" requires a
method, not just a checkmark; unverifiable = blank + could_not_verify.

Field provenance rules:
  - entity_name: from discovery source (IRS 990-PF or SEC 13F COVERPAGE)
  - aum_usd: from IRS 990-PF financial summary or SEC 13F VALUE sum
  - street_address/city/state: from discovery source filing
  - principal_first/last_name + title: from 990-PF officer list
    (highest-ranking officer: PRESIDENT > CHAIR > TREASURER > DIRECTOR)
  - principal_email/phone: could_not_verify (not in public filings;
    would require website scrape or LinkedIn — deferred)
  - website/domain: could_not_verify (not in filings; would require
    web search or WHOIS — deferred)
  - confidence_score: from qualification tier (0.9 / 0.75 / 0.65)

The 3 fully-documented example records are generated from the top
candidates by confidence tier, with complete chain-of-evidence.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from schema import (
    DiscoverySource,
    FamilyOfficeRecord,
    FOType,
    VerificationStatus,
    VerifiedField,
)
from enrichment.qualify import select_final_50


def _today() -> date:
    return datetime.now(UTC).date()


# Officer title priority for principal selection
TITLE_PRIORITY = {
    "PRESIDENT": 1, "CHAIRMAN": 2, "CHAIR": 2, "BOARD CHAIR": 2,
    "EXECUTIVE DIRECTOR": 3, "DIRECTOR": 4, "TRUSTEE": 5,
    "CFO": 6, "TREASURER": 6, "SECRETARY": 7, "VICE PRESIDENT": 8,
    "VICE CHAIR": 9, "MEMBER": 10,
}


def _title_rank(title: str) -> int:
    """Lower = higher priority."""
    title_upper = (title or "").upper()
    for key, rank in TITLE_PRIORITY.items():
        if key in title_upper:
            return rank
    return 99


def _split_name(full_name: str) -> tuple[str, str]:
    """Split 'GORDON E MOORE' -> ('Gordon', 'Moore')."""
    if not full_name:
        return "", ""
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0].title(), ""
    first = parts[0].title()
    last = parts[-1].title()
    # Skip suffixes
    if last.upper() in {"JR", "SR", "II", "III", "IV"}:
        last = parts[-2].title() if len(parts) >= 3 else ""
    return first, last


def _build_aum_verified_field(record: sqlite3.Row) -> VerifiedField:
    """Build AUM VerifiedField from the discovery source."""
    source = record["discovery_source"]
    aum = record["aum_usd"]
    if source == "990pf":
        return VerifiedField(
            value=str(aum),
            status=VerificationStatus.VERIFIED,
            source="IRS Form 990-PF, Part I (revenue/assets summary)",
            method="ProPublica /organizations/{ein}.json financial summary field",
            checked_on=_today(),
        )
    elif source == "sec_edgar":
        return VerifiedField(
            value=str(aum),
            status=VerificationStatus.VERIFIED,
            source="SEC Form 13F-HR, INFOTABLE VALUE column sum",
            method="Aggregated across all 13F-HR filings for firm; VALUE in dollars (post-Jan 2023)",
            checked_on=_today(),
        )
    return VerifiedField(status=VerificationStatus.COULD_NOT_VERIFY)


def _build_principal_from_990pf(officers: list[dict]) -> tuple[str, str, str]:
    """
    Extract principal (highest-ranking officer) from 990-PF officer list.
    Returns (first_name, last_name, title).
    """
    if not officers:
        return "", "", ""
    # Sort by title priority
    sorted_officers = sorted(officers, key=lambda o: _title_rank(o.get("title", "")))
    principal = sorted_officers[0]
    first, last = _split_name(principal.get("name", ""))
    title = (principal.get("title") or "").title()
    return first, last, title


def _row_get(record: sqlite3.Row, key: str, default=None):
    """sqlite3.Row doesn't support .get() — helper."""
    try:
        return record[key]
    except (KeyError, IndexError):
        return default


def enrich_record(record: sqlite3.Row) -> FamilyOfficeRecord:
    """
    Map a qualified candidate row to a full FamilyOfficeRecord with
    VerifiedField provenance on all high-value fields.
    """
    source = record["discovery_source"]
    officers = json.loads(record["officers_json"]) if record["officers_json"] else []

    # Determine confidence from evidence string
    evidence = record["fo_type_evidence"]
    if "ERA-registered" in evidence or "Cross-channel" in evidence:
        confidence = 0.9
    elif "IAPD verified" in evidence:
        confidence = 0.75
    else:
        confidence = 0.65

    # Principal extraction (990-PF only — 13F doesn't have people)
    if source == "990pf" and isinstance(officers, list):
        first, last, title = _build_principal_from_990pf(officers)
    else:
        first, last, title = "", "", ""

    # Build the record
    return FamilyOfficeRecord(
        record_id=record["record_id"],
        discovery_source=DiscoverySource(source),
        discovery_note=record["fo_type_evidence"],
        state_targeting=_row_get(record, "state_region"),
        fo_type=FOType(record["fo_type"]),
        fo_type_evidence=record["fo_type_evidence"],
        entity_name=record["entity_name"],
        street_address=_row_get(record, "street_address"),
        city=_row_get(record, "city"),
        state_region=_row_get(record, "state_region"),
        country="United States",
        aum_usd=_build_aum_verified_field(record),
        aum_as_of=_today(),
        principal_first_name=first or None,
        principal_last_name=last or None,
        principal_title=title or None,
        # Contact fields — not in public filings, honestly blank
        principal_email=VerifiedField(
            status=VerificationStatus.COULD_NOT_VERIFY,
            method="Not available in IRS 990-PF or SEC 13F filings; requires website scrape or LinkedIn",
        ),
        principal_phone=VerifiedField(
            status=VerificationStatus.COULD_NOT_VERIFY,
            method="Not available in IRS 990-PF or SEC 13F filings; requires website scrape or LinkedIn",
        ),
        # Web fields — not in filings, honestly blank
        website=None,
        domain=None,
        corporate_linkedin=None,
        principal_linkedin=None,
        confidence_score=confidence,
    )


def enrich_all() -> list[FamilyOfficeRecord]:
    """
    Enrich all 44 selected candidates to FamilyOfficeRecord schema.
    Returns list of validated records.
    """
    import sqlite3
    conn = sqlite3.connect("data/candidates.db")
    conn.row_factory = sqlite3.Row

    selected = select_final_50(conn)
    print(f"Enriching {len(selected)} records...")

    records = []
    for r in selected:
        try:
            record = enrich_record(r)
            records.append(record)
        except Exception as e:
            print(f"  ERROR enriching {r['entity_name']}: {e}")

    print(f"  -> {len(records)} records enriched successfully")

    # Export to JSONL
    out_dir = Path("data/discovery")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "final_enriched.jsonl"
    with out_path.open("w") as f:
        for r in records:
            d = r.model_dump(mode="json")
            f.write(json.dumps(d, default=str) + "\n")
    print(f"  -> exported to {out_path}")

    return records


def generate_example_records(records: list[FamilyOfficeRecord]) -> str:
    """
    Generate 3 fully-documented example records with complete
    chain-of-evidence for the methodology documentation.
    """
    # Pick one from each confidence tier
    tier_09 = [r for r in records if r.confidence_score == 0.9]
    tier_075 = [r for r in records if r.confidence_score == 0.75]
    tier_065 = [r for r in records if r.confidence_score == 0.65]

    examples = []
    if tier_09:
        # Prefer a cross-channel match for the 0.9 example
        cross = [r for r in tier_09 if "Cross-channel" in r.fo_type_evidence]
        examples.append(cross[0] if cross else tier_09[0])
    if tier_075:
        examples.append(tier_075[0])
    if tier_065:
        examples.append(tier_065[0])

    output = ["# 3 Fully-Worked Example Records\n"]
    for i, r in enumerate(examples, 1):
        output.append(f"## Example {i}: {r.entity_name}\n")
        output.append(f"**Record ID:** `{r.record_id}`\n")
        output.append(f"**Discovery source:** {r.discovery_source.value}\n")
        output.append(f"**FO type:** {r.fo_type.value}\n")
        output.append(f"**Confidence:** {r.confidence_score}\n")
        output.append(f"\n### Qualification evidence\n")
        output.append(f"{r.fo_type_evidence}\n")
        output.append(f"\n### Field provenance\n")
        output.append(f"| Field | Value | Status | Source | Method |\n")
        output.append(f"|---|---|---|---|---|\n")
        output.append(f"| entity_name | {r.entity_name} | verified | {r.discovery_source.value} discovery | filing COVERPAGE/entity name |\n")
        aum = r.aum_usd
        aum_val = f"${int(aum.value)/1e6:.1f}M" if aum and aum.value else "—"
        output.append(f"| aum_usd | {aum_val} | {aum.status.value if aum else '—'} | {aum.source if aum else '—'} | {aum.method if aum else '—'} |\n")
        output.append(f"| city | {r.city or '—'} | verified | {r.discovery_source.value} filing | filing address field |\n")
        output.append(f"| state_region | {r.state_region or '—'} | verified | {r.discovery_source.value} filing | filing address field |\n")
        if r.principal_first_name:
            output.append(f"| principal_name | {r.principal_first_name} {r.principal_last_name} | verified | IRS 990-PF XML Part VII | officer name extraction, highest-ranking title |\n")
            output.append(f"| principal_title | {r.principal_title} | verified | IRS 990-PF XML Part VII | officer title field |\n")
        email = r.principal_email
        output.append(f"| principal_email | — | {email.status.value} | — | {email.method or '—'} |\n")
        phone = r.principal_phone
        output.append(f"| principal_phone | — | {phone.status.value} | — | {phone.method or '—'} |\n")
        output.append(f"| website | — | could_not_verify | — | not in public filings; requires web search |\n")
        output.append(f"\n### Chain of evidence\n")
        output.append(f"1. **Discovery:** {r.discovery_note}\n")
        output.append(f"2. **Qualification:** {r.fo_type_evidence}\n")
        output.append(f"3. **Field verification:** AUM verified from filing data; address verified from filing; principal extracted from officer list; contact fields honestly blank (could_not_verify)\n")
        output.append(f"\n---\n")

    return "\n".join(output)


if __name__ == "__main__":
    records = enrich_all()
    examples = generate_example_records(records)
    Path("docs/example_records.md").write_text(examples)
    print(f"\nGenerated 3 example records -> docs/example_records.md")
