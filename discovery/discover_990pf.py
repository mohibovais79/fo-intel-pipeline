"""
fo-intel-pipeline — 990-PF discovery orchestrator (CA pilot)

Pipeline:
  1. ProPublica /search.json -> set of CA 501(c)(3) EINs (~10k)
  2. IRS index CSVs (2023, 2022, 2021, 2019) -> 990-PF EIN -> OBJECT_ID lookup
  3. Intersect -> CA private foundations that e-filed 990-PF
  4. ProPublica /organizations/{ein}.json -> financials + mailing address
     Filter: assets >= $10M (foundation_assets_minimum_usd_millions)
     Filter: pre_xml_filter (operating-charity keyword exclusion)
  5. GivingTuesday XML fetch + officer parse for each surviving candidate
  6. post_xml_family_fingerprint (officer count + shared surname)
  7. Persist to SQLite (raw_candidates) + JSONL export per stage

Every candidate is tagged discovery_source=990pf and state_targeting=CA
at creation, before any enrichment — per rule #1 of the differentiator
assessment (source diversity must be auditable without re-deriving it).

Usage:
    uv run python -m discovery.discover_990pf
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from config import THRESHOLDS
from discovery import (
    filters,
    irs_index,
    irs_xml,
    propublica_client,
    storage,
)


def _record_id(ein: str) -> str:
    return f"990pf-CA-{ein}-{uuid.uuid4().hex[:8]}"


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def run() -> dict[str, int]:
    run_id = f"990pf-ca-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    started = _now()
    conn = storage.get_db()

    counts: dict[str, int] = {}
    print(f"\n=== 990-PF CA discovery run {run_id} ===\n")

    # --- Step 1: CA 501(c)(3) EINs from ProPublica ---
    print("[1/6] Fetching CA 501(c)(3) EINs from ProPublica...")
    ca_eins = propublica_client.fetch_state_501c3_eins("CA")
    counts["ca_501c3_eins"] = len(ca_eins)
    print(f"      -> {len(ca_eins)} CA 501(c)(3) EINs\n")

    # --- Step 2: IRS 990-PF index lookup ---
    print("[2/6] Building IRS 990-PF index lookup...")
    pf_lookup = irs_index.build_990pf_lookup()
    counts["irs_990pf_eins_national"] = len(pf_lookup)
    print(f"      -> {len(pf_lookup)} 990-PF EINs nationally\n")

    # --- Step 3: Intersect ---
    ca_pf_eins = ca_eins & set(pf_lookup.keys())
    counts["ca_990pf_intersect"] = len(ca_pf_eins)
    print(f"[3/6] CA ∩ 990-PF intersect: {len(ca_pf_eins)} foundations\n")

    # --- Step 4: ProPublica detail + asset threshold + pre-XML filter ---
    print("[4/6] Fetching ProPublica detail + applying asset/keyword filters...")
    asset_floor = int(
        THRESHOLDS["foundation_assets_minimum_usd_millions"] * 1_000_000
    )
    post_step4: list[dict] = []
    excluded_pre_xml: list[dict] = []
    for i, ein in enumerate(sorted(ca_pf_eins), 1):
        detail = propublica_client.fetch_org_detail(ein)
        if detail is None:
            counts.setdefault("no_propublica_detail", 0)
            counts["no_propublica_detail"] += 1
            continue
        assets = detail.get("assets_usd") or 0
        name = detail.get("entity_name") or ""
        if assets < asset_floor:
            counts.setdefault("below_asset_floor", 0)
            counts["below_asset_floor"] += 1
            continue
        passed, reason = filters.pre_xml_filter(name)
        if not passed:
            excluded_pre_xml.append(
                {
                    "record_id": _record_id(ein),
                    "discovery_source": "990pf",
                    "entity_name": name,
                    "reason_excluded": reason,
                    "fo_type_considered": "unclear",
                }
            )
            counts.setdefault("excluded_pre_xml", 0)
            counts["excluded_pre_xml"] += 1
            continue
        detail["record_id"] = _record_id(ein)
        detail["irs_filings"] = pf_lookup[ein]
        post_step4.append(detail)
        if i % 50 == 0:
            print(f"      ...{i}/{len(ca_pf_eins)} processed, {len(post_step4)} passed")
    counts["passed_step4_asset_and_keyword"] = len(post_step4)
    print(f"      -> {len(post_step4)} candidates passed asset + keyword filter\n")

    # Persist pre-XML exclusions to audit table
    for e in excluded_pre_xml:
        storage.insert_excluded(conn, e)

    # --- Step 5: XML fetch + officer parse ---
    print(f"[5/6] Fetching + parsing 990-PF XML for {len(post_step4)} candidates...")
    parsed_candidates: list[dict] = []
    no_xml_count = 0
    xml_error_count = 0
    for i, cand in enumerate(post_step4, 1):
        ein = cand["ein"]
        parsed = None
        for filing in cand["irs_filings"]:  # most-recent-first
            parsed = irs_xml.fetch_and_parse(filing["object_id"])
            if parsed is not None:
                break
        if parsed is None:
            no_xml_count += 1
            storage.upsert_raw_candidate(
                conn,
                {
                    "record_id": cand["record_id"],
                    "discovery_source": "990pf",
                    "state_targeting": "CA",
                    "ein": ein,
                    "entity_name": cand["entity_name"],
                    "city": cand.get("city"),
                    "state_region": cand.get("state_region"),
                    "street_address": cand.get("street_address"),
                    "assets_usd": cand.get("assets_usd"),
                    "tax_year": cand.get("tax_year"),
                    "officers_json": None,
                    "xml_object_id": None,
                    "xml_fetch_status": "no_xml",
                    "discovered_at": _now(),
                    "discovery_note": (
                        f"990-PF filing for EIN {ein} not present in "
                        "GivingTuesday data lake (paper-filed or post-2023). "
                        "Flagged as no_xml_filing blind spot."
                    ),
                },
            )
            continue
        # Persist with parsed officer data
        storage.upsert_raw_candidate(
            conn,
            {
                "record_id": cand["record_id"],
                "discovery_source": "990pf",
                "state_targeting": "CA",
                "ein": ein,
                "entity_name": cand["entity_name"],
                "city": cand.get("city"),
                "state_region": cand.get("state_region"),
                "street_address": cand.get("street_address"),
                "assets_usd": cand.get("assets_usd"),
                "tax_year": cand.get("tax_year"),
                "officers_json": json.dumps(parsed["officers"]),
                "xml_object_id": parsed["xml_object_id"],
                "xml_fetch_status": "ok",
                "discovered_at": _now(),
                "discovery_note": (
                    f"990-PF XML {parsed['xml_object_id']}; "
                    f"{parsed['officer_count']} officers parsed; "
                    f"shared_surname={parsed['has_shared_surname']}; "
                    f"attached_schedule={parsed['officers_attached_schedule']}"
                ),
            },
        )
        cand["parsed"] = parsed
        parsed_candidates.append(cand)
        if i % 25 == 0:
            print(f"      ...{i}/{len(post_step4)} XML-fetched, {len(parsed_candidates)} parsed ok")
    counts["xml_parsed_ok"] = len(parsed_candidates)
    counts["xml_no_xml"] = no_xml_count
    counts["xml_error"] = xml_error_count
    print(f"      -> {len(parsed_candidates)} parsed ok, {no_xml_count} no XML\n")

    # --- Step 6: Post-XML family fingerprint ---
    print("[6/6] Applying post-XML family-fingerprint filter...")
    family_candidates: list[dict] = []
    excluded_post_xml: list[dict] = []
    for cand in parsed_candidates:
        passed, reason = filters.post_xml_family_fingerprint(cand["parsed"])
        if passed:
            family_candidates.append(cand)
        else:
            excluded_post_xml.append(
                {
                    "record_id": cand["record_id"],
                    "discovery_source": "990pf",
                    "entity_name": cand["entity_name"],
                    "reason_excluded": reason,
                    "fo_type_considered": "unclear",
                }
            )
    for e in excluded_post_xml:
        storage.insert_excluded(conn, e)
    counts["family_fingerprint_passed"] = len(family_candidates)
    counts["excluded_post_xml"] = len(excluded_post_xml)
    print(f"      -> {len(family_candidates)} family-fingerprint candidates\n")

    # --- Export JSONL for audit ---
    all_raw = conn.execute("SELECT * FROM raw_candidates").fetchall()
    storage.export_jsonl(all_raw, "990pf_ca_raw")
    family_rows = conn.execute(
        "SELECT * FROM raw_candidates WHERE xml_fetch_status='ok'"
    ).fetchall()
    storage.export_jsonl(family_rows, "990pf_ca_xml_parsed")

    # --- Source-mix audit (live, per rule #1) ---
    mix = storage.source_mix_ratio(conn)
    counts["source_mix"] = {k: round(v, 3) for k, v in mix.items()}
    print(f"Source-mix ratio so far: {counts['source_mix']}")

    storage.log_run(
        conn,
        run_id=run_id,
        stage="990pf_ca_discovery",
        started_at=started,
        finished_at=_now(),
        counts=counts,
        notes=f"CA pilot; {len(family_candidates)} family-fingerprint candidates",
    )

    print(f"\n=== DONE. Counts: {json.dumps(counts, indent=2)} ===\n")
    return counts


if __name__ == "__main__":
    run()
