"""
Export final_enriched.jsonl to CSV for the assessment deliverable.

Flattens the nested VerifiedField structures (aum_usd, principal_email,
principal_phone) into columns with _status / _source / _method suffixes,
preserving the full provenance chain that the assessment requires.

Usage:
    uv run python export_csv.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


def _flatten_verifiedfield(record: dict, field: str, prefix: str, out: dict):
    """Flatten a VerifiedField dict into _value/_status/_source/_method columns."""
    vf = record.get(field, {})
    out[f"{prefix}_value"] = vf.get("value")
    out[f"{prefix}_status"] = vf.get("status", "could_not_verify")
    out[f"{prefix}_source"] = vf.get("source")
    out[f"{prefix}_method"] = vf.get("method")


def main():
    project_root = Path(__file__).resolve().parent
    in_path = project_root / "data" / "discovery" / "final_enriched.jsonl"
    out_path = project_root / "data" / "discovery" / "family_offices.csv"

    with in_path.open() as f:
        records = [json.loads(line) for line in f]

    # Define column order — grouped logically for a reader
    columns = [
        # Identity
        "record_id",
        "entity_name",
        "fo_type",
        "fo_type_evidence",
        "confidence_score",
        "discovery_source",
        "discovery_note",
        # Location
        "street_address",
        "city",
        "state_region",
        "country",
        # Financials
        "aum_usd_value",
        "aum_usd_status",
        "aum_usd_source",
        "aum_usd_method",
        "aum_as_of",
        # Principal
        "principal_first_name",
        "principal_last_name",
        "principal_title",
        "principal_linkedin",
        "principal_email_value",
        "principal_email_status",
        "principal_email_source",
        "principal_email_method",
        "principal_phone_value",
        "principal_phone_status",
        "principal_phone_source",
        "principal_phone_method",
        # Entity digital presence
        "website",
        "corporate_linkedin",
        "domain",
        # Intelligence layers
        "investment_thesis",
        "investing_sectors",
        "description",
        "recent_activity",
    ]

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            row: dict = {}
            # Copy flat fields
            for col in columns:
                if col.startswith("aum_usd_"):
                    continue
                if col.startswith("principal_email_"):
                    continue
                if col.startswith("principal_phone_"):
                    continue
                # Handle list/dict fields — serialize as JSON for CSV cell
                val = rec.get(col)
                if isinstance(val, (list, dict)):
                    val = json.dumps(val) if val else ""
                row[col] = val
            # Flatten VerifiedField structures
            _flatten_verifiedfield(rec, "aum_usd", "aum_usd", row)
            _flatten_verifiedfield(rec, "principal_email", "principal_email", row)
            _flatten_verifiedfield(rec, "principal_phone", "principal_phone", row)
            writer.writerow(row)

    print(f"Exported {len(records)} records to {out_path}")
    print(f"Columns: {len(columns)}")


if __name__ == "__main__":
    main()
