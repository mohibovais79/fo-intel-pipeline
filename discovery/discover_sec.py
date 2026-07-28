"""
fo-intel-pipeline — SEC EDGAR discovery orchestrator (CA pilot, channel 2)

Pipeline:
  1. Download SEC 13F bulk data sets (quarterly ZIPs from sec.gov)
  2. Aggregate at firm level (not per-accession, to avoid fund-vehicle
     double-counting), filter to 13F-HR only (not 13F-NT notices)
  3. Apply empirically-derived family-office discovery filter:
     - CA headquarters
     - 10-500 holdings (excludes PE/VC vehicles at 1-5; excludes quant
       funds at 1000+ — see docs/pivot_log.md pivot 6)
     - AUM >= $25M (discovery floor; qualification gate trims later)
     - Exclude names matching EXCLUDE_NAMES.txt
  4. For each candidate with a CRD number, fetch IAPD structured fields
     as secondary verification (ERA status, otherNames, relying advisors)
  5. Persist to SQLite (raw_candidates) + JSONL export

Every candidate is tagged discovery_source=sec_edgar and state_targeting=CA
at creation, before any enrichment.

Usage:
    uv run python -m discovery.discover_sec
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from discovery import sec_13f, sec_adv, storage


def _record_id(crd: str, firm: str) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in firm)[:30]
    return f"sec-ca-{crd or 'nocrd'}-{safe}-{uuid.uuid4().hex[:8]}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def run() -> dict[str, int]:
    run_id = f"sec-ca-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    started = _now()
    conn = storage.get_db()

    counts: dict[str, int] = {}
    print(f"\n=== SEC EDGAR CA discovery run {run_id} ===\n")

    # --- Step 1-3: 13F bulk data + CA firm aggregation + discovery filter ---
    print("[1/4] Loading 13F bulk data + applying CA discovery filter...")
    candidates = sec_13f.discover_ca_13f_candidates()
    counts["sec_13f_ca_candidates"] = len(candidates)
    print(f"      -> {len(candidates)} 13F CA candidates\n")

    # --- Step 4: IAPD verification for candidates with CRD ---
    print(f"[2/4] Fetching IAPD structured fields for {len(candidates)} candidates...")
    verified = 0
    could_not_verify = 0
    rejected = 0
    no_crd = 0
    for i, cand in enumerate(candidates, 1):
        crd = cand.get("crd", "").strip()
        adv_result = None
        if crd:
            adv_result = sec_adv.verify_via_iapd(crd)
        else:
            no_crd += 1

        if adv_result:
            status = adv_result["status"]
            if status == "verified":
                verified += 1
            elif status == "rejected":
                rejected += 1
            else:
                could_not_verify += 1
            cand["adv_verification"] = adv_result
        else:
            could_not_verify += 1
            cand["adv_verification"] = {
                "status": "could_not_verify",
                "score": 0,
                "evidence": {},
                "source": None,
                "method": "No CRD number available in 13F COVERPAGE",
            }

        if i % 50 == 0:
            print(f"      ...{i}/{len(candidates)} IAPD-checked "
                  f"(verified={verified}, could_not_verify={could_not_verify}, rejected={rejected})")

    counts["iapd_verified"] = verified
    counts["iapd_could_not_verify"] = could_not_verify
    counts["iapd_rejected"] = rejected
    counts["no_crd_number"] = no_crd
    print(f"      -> verified={verified}, could_not_verify={could_not_verify}, "
          f"rejected={rejected}, no_crd={no_crd}\n")

    # --- Step 5: Persist to SQLite + JSONL ---
    print("[3/4] Persisting candidates to SQLite...")
    for cand in candidates:
        adv = cand.get("adv_verification", {})
        record_id = _record_id(cand.get("crd", ""), cand["firm_name"])
        # Build a discovery note capturing both signals
        note = (
            f"13F-HR: {cand['n_holdings']} holdings, "
            f"${cand['aum_13f_m']:.1f}M AUM, {cand['n_cusips']} CUSIPs, "
            f"{cand['n_filings']} filing(s). "
            f"IAPD: {adv.get('status', 'could_not_verify')} "
            f"(score={adv.get('score', 0)}). "
            f"{adv.get('method', '')}"
        )
        storage.upsert_raw_candidate(
            conn,
            {
                "record_id": record_id,
                "discovery_source": "sec_edgar",
                "state_targeting": "CA",
                "ein": cand.get("crd", ""),  # CRD serves as the SEC entity ID
                "entity_name": cand["firm_name"],
                "city": cand.get("city"),
                "state_region": cand.get("state"),
                "street_address": cand.get("street"),
                "assets_usd": int(cand["aum_13f_m"] * 1_000_000),
                "tax_year": None,
                "officers_json": json.dumps({
                    "n_holdings": cand["n_holdings"],
                    "n_cusips": cand["n_cusips"],
                    "n_filings": cand["n_filings"],
                    "crd": cand.get("crd", ""),
                    "accessions": cand.get("accessions", []),
                }),
                "xml_object_id": None,
                "xml_fetch_status": "n/a",
                "discovered_at": _now(),
                "discovery_note": note,
            },
        )
    counts["persisted"] = len(candidates)
    print(f"      -> {len(candidates)} candidates persisted\n")

    # --- Export + source-mix audit ---
    print("[4/4] Exporting JSONL + source-mix audit...")
    all_raw = conn.execute("SELECT * FROM raw_candidates").fetchall()
    storage.export_jsonl(all_raw, "all_channels_raw")
    sec_rows = conn.execute(
        "SELECT * FROM raw_candidates WHERE discovery_source = 'sec_edgar'"
    ).fetchall()
    storage.export_jsonl(sec_rows, "sec_edgar_ca_raw")

    mix = storage.source_mix_ratio(conn)
    counts["source_mix"] = {k: round(v, 3) for k, v in mix.items()}
    print(f"Source-mix ratio: {counts['source_mix']}")

    storage.log_run(
        conn,
        run_id=run_id,
        stage="sec_edgar_ca_discovery",
        started_at=started,
        finished_at=_now(),
        counts=counts,
        notes=f"CA pilot; {len(candidates)} 13F candidates, {verified} IAPD-verified",
    )

    print(f"\n=== DONE. Counts: {json.dumps(counts, indent=2)} ===\n")
    return counts


if __name__ == "__main__":
    run()
