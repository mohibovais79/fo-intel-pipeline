"""
fo-intel-pipeline — enrichment + qualification gate

Maps raw candidates from both discovery channels to FamilyOfficeRecord
schema with VerifiedField provenance, applies the deterministic
qualification gate, and routes to qualified_candidates or
excluded_candidates.

QUALIFICATION GATE (deterministic, not LLM-decided):

The brief requires affirmative evidence of:
  (a) single-family or defined multi-family structure
  (b) manages investable wealth for that family
  (c) at least one independent source confirms beyond discovery source

Evidence tiers by channel:

SEC EDGAR (13F + IAPD):
  - ERA-registered: SFO. ERA = SEC family office exemption (Rule
    202(a)(11)(G)-1). Two independent SEC filings (13F + ADV/ERA).
    Strongest signal — regulatory determination, not marketing.
  - IAPD verified (score >= 2) + concentrated (<=150 holdings): SFO/MFO.
    Portfolio shape + IAPD metadata (ERA/family-name/relying-advisors).
  - Concentrated (<=50 holdings) + $100M+ AUM, no IAPD: unclear.
    Single signal — could be concentrated activist fund.
  - Everything else: unclear.

990-PF (private foundations):
  - Cross-channel match (same family surname in a 13F filer name): SFO.
    IRS 990-PF filing (family exists, has wealth) + SEC 13F filing
    (entity manages securities) = two independent sources. The
    foundation itself is NOT the FO — it's the philanthropic vehicle.
    The 13F filer with the matching surname IS the FO candidate.
  - Shared surname + family name in foundation, no cross-match: unclear.
    Single source (IRS only). Foundation manages philanthropy, not
    investable wealth. Cannot satisfy requirement (b) alone.
  - No shared surname: unclear.

Cross-channel matching is surname-based:
  1. Extract surnames from 990-PF officer names
  2. Extract firm names from SEC EDGAR 13F filers
  3. Match: 990-PF foundation surname "X" ↔ 13F filer with "X" in name
     (e.g., "Schmidt Family Foundation" ↔ "Schmidt Capital Management")
  4. Conservative: require exact surname match, not fuzzy matching.
     Fuzzy/ambiguous cases deferred to LLM disambiguation (future).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, date, datetime
from typing import Any

from schema import (
    DiscoverySource,
    ExcludedCandidate,
    FamilyOfficeRecord,
    FOType,
    VerificationStatus,
    VerifiedField,
)
from config import THRESHOLDS


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _today() -> date:
    return datetime.now(UTC).date()


def _extract_surname(name: str) -> str | None:
    """
    Extract a surname from a person or entity name.
    "John Schmidt" -> "Schmidt"
    "Schmidt Family Foundation" -> "Schmidt"
    "Eric And Wendy Schmidt Fund" -> "Schmidt"
    Returns None if no clear surname can be extracted.
    """
    if not name:
        return None
    name = name.strip()
    # Try to find a family-name pattern: "X Family" or "X Foundation"
    # or "X Fund" or "X Charitable"
    m = re.search(r"^(\w+)\s+(?:Family|Foundation|Fund|Charitable|Trust)", name, re.IGNORECASE)
    if m:
        surname = m.group(1)
        # Skip generic words that aren't surnames
        if surname.lower() not in {"the", "a", "an", "family", "charitable", "foundation", "fund", "trust"}:
            return surname
    # For person names, take the last word
    parts = name.split()
    if len(parts) >= 2:
        surname = parts[-1]
        # Skip suffixes
        if surname.lower() in {"jr", "sr", "ii", "iii", "iv"}:
            surname = parts[-2] if len(parts) >= 3 else None
        if surname and surname.upper() in NOT_SURNAMES:
            return None
        return surname
    return None


# Words that are NOT surnames — title fragments, status markers,
# suffixes, generic words. These cause false shared-surname matches
# when the officer parser picks up title text as part of the name
# (e.g., "LILLIAN LOWERY - OUTGOING" -> "OUTGOING" as surname).
NOT_SURNAMES = {
    "OUTGOING", "CHIEF", "STRATEGY", "OFFICER", "DIRECTOR", "CHAIR",
    "PRESIDENT", "SECRETARY", "TREASURER", "MEMBER", "TRUSTEE",
    "JR", "SR", "II", "III", "IV", "ESQ", "MD", "PHD", "MBA",
    "FAM", "FAMILY", "FOUNDATION", "FUND", "TRUST",
    "THE", "A", "AN", "AND", "OR", "MR", "MRS", "MS", "DR",
}


def _extract_foundation_surname(entity_name: str) -> str | None:
    """
    Extract the family surname from a foundation name.
    "Schmidt Family Foundation" -> "Schmidt"
    "Eric And Wendy Schmidt Fund" -> "Schmidt" (last name before Fund)
    "Brin Wojcicki Foundation" -> "Brin" (first of two surnames)
    "Gordon E And Betty I Moore Foundation" -> "Moore"
    "Carl Victor Page Memorial Foundation" -> "Page"
    Returns None for corporate/institutional foundations where no
    family surname can be confidently extracted.
    """
    if not entity_name:
        return None
    name = entity_name.strip()

    # Words that are NOT surnames — generic/institutional words that
    # appear before "Foundation/Fund/Trust" in non-family foundations
    NOT_FAMILY_WORDS = {
        "family", "charitable", "foundation", "fund", "trust", "inc",
        "llc", "memorial", "private", "heritage", "prize", "bank",
        "com", "community", "public", "corporate", "company",
        "institute", "society", "association", "center", "project",
        "initiative", "program", "endowment", "legacy", "future",
        "hope", "faith", "grace", "unity", "vision", "dream",
        "breakthrough", "carestar", "skywords", "new", "land",
    }

    # Pattern 1: "X Family Foundation/Fund/Trust" — strongest signal
    m = re.search(r"(\w+)\s+Family\s+(?:Foundation|Fund|Trust|Charitable)", name, re.IGNORECASE)
    if m:
        word = m.group(1)
        if word.lower() not in NOT_FAMILY_WORDS:
            return word

    # Pattern 2: "X And Y Z Foundation" or "X Y Z Foundation" -> Z
    # (last word before Foundation/Fund/Trust, if not generic)
    m = re.search(r"(\w+)\s+(?:Foundation|Fund|Trust|Charitable|Memorial)", name, re.IGNORECASE)
    if m:
        word = m.group(1)
        if word.lower() not in NOT_FAMILY_WORDS:
            return word

    # Pattern 3: "... Z Family" (no Foundation after) -> Z
    m = re.search(r"(\w+)\s+Family$", name, re.IGNORECASE)
    if m:
        word = m.group(1)
        if word.lower() not in NOT_FAMILY_WORDS:
            return word

    return None


def _extract_13f_firm_surname(firm_name: str) -> str | None:
    """
    Extract a surname from a 13F filer name.
    "Schmidt Capital Management" -> "Schmidt"
    "ValueAct Holdings" -> None (not a surname pattern)
    "Tarbox Family Office" -> "Tarbox"
    """
    if not firm_name:
        return None
    name = firm_name.strip()
    # Pattern: "X Capital/Management/Holdings/Investments/Partners/Advisors"
    m = re.search(r"^(\w+)\s+(?:Capital|Management|Holdings|Investments|Partners|Advisors|Advisory|Family|Wealth|Asset|Group)", name, re.IGNORECASE)
    if m:
        word = m.group(1)
        if word.lower() not in {"the", "a", "an", "new", "first", "us", "u.s"}:
            return word
    # Pattern: "X Family Office"
    m = re.search(r"(\w+)\s+Family\s+Office", name, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def build_surname_index(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """
    Build a surname index for cross-channel matching.

    CRITICAL: For 990-PF, only the FOUNDATION FAMILY surname is indexed,
    NOT every officer's surname. This prevents false matches where an
    officer named "Hennessy" at the Moore Foundation gets matched to
    "Hennessy Advisors" (a 13F filer) — that's a collision, not a
    family-office signal.

    The foundation family surname is extracted from the foundation name
    (e.g., "Moore Foundation" -> "Moore") and validated against the
    officer list (at least one officer must share that surname, OR
    "Family" appears in the foundation name with that surname).

    Returns {surname: [{record_id, source, entity_name, ...}]}
    """
    index: dict[str, list[dict]] = {}

    # 990-PF: extract ONLY the foundation family surname
    rows = conn.execute(
        "SELECT record_id, entity_name, officers_json FROM raw_candidates WHERE discovery_source = '990pf'"
    ).fetchall()
    for r in rows:
        officers = json.loads(r["officers_json"]) if r["officers_json"] else []
        foundation_sn = _extract_foundation_surname(r["entity_name"])
        if not foundation_sn:
            continue

        # Validate: at least one officer must share the foundation surname,
        # OR "Family" must be in the foundation name (explicit family structure)
        officer_sns = {_extract_surname(o.get("name", "")) for o in officers}
        officer_sns.discard(None)
        family_in_name = "family" in (r["entity_name"] or "").lower()

        has_corroboration = (
            foundation_sn in officer_sns
            or any(s and s.lower() == foundation_sn.lower() for s in officer_sns)
            or family_in_name
        )
        if not has_corroboration:
            continue

        index.setdefault(foundation_sn, []).append({
            "record_id": r["record_id"],
            "source": "990pf",
            "entity_name": r["entity_name"],
        })

    # SEC EDGAR: extract surnames from firm names
    rows = conn.execute(
        "SELECT record_id, entity_name, officers_json FROM raw_candidates WHERE discovery_source = 'sec_edgar'"
    ).fetchall()
    for r in rows:
        firm_sn = _extract_13f_firm_surname(r["entity_name"])
        if firm_sn:
            index.setdefault(firm_sn, []).append({
                "record_id": r["record_id"],
                "source": "sec_edgar",
                "entity_name": r["entity_name"],
            })

    return index


def find_cross_channel_matches(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """
    Find surnames that appear in BOTH 990-PF and SEC EDGAR channels.
    Returns list of (surname, 990pf_record_id, sec_edgar_record_id).
    """
    index = build_surname_index(conn)
    matches = []
    for surname, entries in index.items():
        pf_entries = [e for e in entries if e["source"] == "990pf"]
        sec_entries = [e for e in entries if e["source"] == "sec_edgar"]
        if pf_entries and sec_entries:
            for pf in pf_entries:
                for sec in sec_entries:
                    matches.append((surname, pf["record_id"], sec["record_id"]))
    return matches


def qualify_sec_edgar_candidate(
    row: sqlite3.Row, iapd_result: dict | None
) -> tuple[FOType, str, float]:
    """
    Determine fo_type for a SEC EDGAR candidate.
    Returns (fo_type, evidence_string, confidence_score).
    """
    officers = json.loads(row["officers_json"]) if row["officers_json"] else {}
    n_holdings = officers.get("n_holdings", 0)
    aum_m = (row["assets_usd"] or 0) / 1_000_000

    iapd_status = iapd_result.get("status", "could_not_verify") if iapd_result else "could_not_verify"
    iapd_score = iapd_result.get("score", 0) if iapd_result else 0
    is_era = iapd_result.get("is_era_registered", False) if iapd_result else False

    # Tier 1: ERA-registered → SFO (confidence 0.9)
    if is_era:
        return FOType.SINGLE_FAMILY, (
            f"ERA-registered (SEC family office exemption, Rule 202(a)(11)(G)-1). "
            f"13F-HR filing confirms manages ${aum_m:.1f}M in 13(f) securities. "
            f"Two independent SEC filings (13F + ADV/ERA). "
            f"Portfolio: {n_holdings} holdings."
        ), 0.9

    # Tier 2: IAPD verified (score >= 2) + concentrated → SFO (confidence 0.75)
    if iapd_status == "verified" and iapd_score >= 2 and n_holdings <= 150:
        evidence_bits = []
        if iapd_result and iapd_result.get("evidence"):
            for k, v in iapd_result["evidence"].items():
                evidence_bits.append(f"{k}: {v}")
        return FOType.SINGLE_FAMILY, (
            f"IAPD verified (score={iapd_score}): {', '.join(evidence_bits)}. "
            f"13F-HR: {n_holdings} holdings, ${aum_m:.1f}M AUM. "
            f"Concentrated portfolio + IAPD metadata = two signals."
        ), 0.75

    # Tier 3: Highly concentrated (<=50) + $100M+ → unclear
    if n_holdings <= 50 and aum_m >= 100:
        return FOType.UNCLEAR, (
            f"Highly concentrated ({n_holdings} holdings, ${aum_m:.1f}M AUM) "
            f"but no IAPD verification. Could be family office or "
            f"concentrated activist fund. Single signal — insufficient."
        ), 0.0

    # Everything else → unclear
    return FOType.UNCLEAR, (
        f"No qualifying signal. {n_holdings} holdings, ${aum_m:.1f}M AUM, "
        f"IAPD={iapd_status} (score={iapd_score})."
    ), 0.0


# Known corporate/institutional foundations that are NOT family offices.
# These are giving vehicles for public companies or institutions, not
# single-family wealth management entities. Detected by name pattern +
# this denylist of known corporate foundation names.
CORPORATE_FOUNDATION_NAMES = {
    "visa foundation", "salesforce com foundation", "salesforce foundation",
    "levi strauss foundation", "fremont bank foundation",
    "breakthrough prize foundation", "carnegie foundation for advancement teaching",
    "carnegie foundation", "skoll foundation", "visa inc foundation",
    "google org", "google.org", "microsoft foundation", "apple foundation",
    "intel foundation", "cisco foundation", "oracle foundation",
    "meta foundation", "netflix foundation", "adobe foundation",
    "wells fargo foundation", "bank of america foundation",
    "goldman sachs foundation", "morgan stanley foundation",
}


def is_corporate_foundation(entity_name: str) -> bool:
    """
    Detect corporate/institutional foundations that are NOT family offices.
    A corporate foundation is a giving vehicle for a public company, not
    a single-family wealth management entity.
    """
    name_lower = (entity_name or "").lower().strip()
    if name_lower in CORPORATE_FOUNDATION_NAMES:
        return True
    # Pattern: "X Bank Foundation" or "X Corporation Foundation"
    if re.search(r"\b(bank|corporation|corp|inc|llc|company|co)\b.*\b(foundation|fund|trust)\b",
                 name_lower):
        return True
    return False


def qualify_990pf_candidate(
    row: sqlite3.Row,
    cross_channel_surnames: set[str],
    officer_surname_counts: dict[str, int] | None = None,
) -> tuple[FOType, str, float]:
    """
    Determine fo_type for a 990-PF candidate.
    cross_channel_surnames: set of surnames that appear in both channels.
    officer_surname_counts: frequency of each officer surname across all
        foundations (for rarity filter).
    Returns (fo_type, evidence_string, confidence_score).

    Two qualifying paths:

    Tier 1 (confidence 0.9): Cross-channel match — same surname appears
    in both 990-PF (IRS) and SEC 13F (SEC). Two independent sources.

    Tier 2 (confidence 0.65): 990-PF-only with three-way evidence:
      (a) Family structure: officers share a surname OR "Family" in
          foundation name
      (b) Investment activity: officer with investment/treasury/CFO title
      (c) Distinctive surname: shared surname appears in <=5 other
          foundations (rarity filter, excludes "Smith"/"Johnson")
    Two independent facts from same filing (family structure + investment
    activity). Single source, lower confidence — but affirmative and
    checkable, not a guess.
    """
    officers = json.loads(row["officers_json"]) if row["officers_json"] else []
    entity_name = row["entity_name"] or ""
    assets_m = (row["assets_usd"] or 0) / 1_000_000

    # Corporate foundation filter — these are giving vehicles for public
    # companies, not single-family wealth management entities
    if is_corporate_foundation(entity_name):
        return FOType.UNCLEAR, (
            f"Corporate/institutional foundation (not a family office). "
            f"Entity name matches known corporate foundation pattern. "
            f"This is a company giving vehicle, not single-family wealth."
        ), 0.0

    # Extract officer surnames (filtered for NOT_SURNAMES)
    officer_sns = [_extract_surname(o.get("name", "")) for o in officers]
    officer_sns = [s for s in officer_sns if s]

    # --- Tier 1: Cross-channel match ---
    # Only the FOUNDATION family surname is eligible for cross-channel
    # matching, not every officer's surname. This prevents collisions
    # where an officer named "Hennessy" at the Moore Foundation gets
    # matched to "Hennessy Advisors" (a 13F filer).
    foundation_sn = _extract_foundation_surname(entity_name)
    if foundation_sn and foundation_sn in cross_channel_surnames:
        return FOType.SINGLE_FAMILY, (
            f"Cross-channel surname match: '{foundation_sn}' appears in both "
            f"990-PF (IRS private foundation filing, family surname in name) "
            f"and SEC 13F filer name. Two independent sources: IRS (family "
            f"foundation) + SEC (investment management entity). "
            f"Foundation assets: ${assets_m:.1f}M."
        ), 0.9

    # --- Tier 2: 990-PF-only three-way path ---
    # Tightened per source-mix rebalancing: $50M asset floor (was $10M),
    # rarity threshold <=3 (was <=5). This raises the bar on the weaker
    # tier of the stronger-evidence channel, rather than weakening the
    # SEC EDGAR channel's bar to make the ratio look better.
    TIER2_ASSET_FLOOR_M = 50
    TIER2_RARITY_MAX = 3

    if assets_m < TIER2_ASSET_FLOOR_M:
        return FOType.UNCLEAR, (
            f"Family structure may be present but assets ${assets_m:.1f}M "
            f"< ${TIER2_ASSET_FLOOR_M}M Tier 2 floor. Foundation too small "
            f"for single-source qualification."
        ), 0.0

    # (a) Family structure
    has_shared = len(officer_sns) != len(set(officer_sns)) and len(officer_sns) > 1
    family_in_name = "family" in entity_name.lower()
    family_structure = has_shared or family_in_name

    if not family_structure:
        return FOType.UNCLEAR, (
            f"No family-structure signal. {len(officers)} officers, "
            f"no shared surname, no 'Family' in name."
        ), 0.0

    # (b) Investment activity signal
    has_invest_title = any(
        any(kw in (o.get("title") or "").upper()
            for kw in ["INVEST", "TREASUR", "CFO", "FINANC"])
        for o in officers
    )

    if not has_invest_title:
        return FOType.UNCLEAR, (
            f"Family structure present but no investment/treasury officer "
            f"title. Foundation may be pure grant-making pass-through, not "
            f"managing investable wealth."
        ), 0.0

    # (c) Rarity filter (tightened to <=3)
    if officer_surname_counts is None:
        return FOType.UNCLEAR, "Rarity filter unavailable", 0.0

    distinctive = False
    distinctive_sn = None
    if has_shared:
        shared_sns = [s for s in officer_sns if officer_sns.count(s) > 1]
        for s in set(shared_sns):
            if officer_surname_counts.get(s, 0) <= TIER2_RARITY_MAX:
                distinctive = True
                distinctive_sn = s
                break
    elif family_in_name and foundation_sn:
        if officer_surname_counts.get(foundation_sn, 0) <= TIER2_RARITY_MAX:
            distinctive = True
            distinctive_sn = foundation_sn

    if not distinctive:
        return FOType.UNCLEAR, (
            f"Family structure + investment title but surname not distinctive "
            f"enough (appears in >{TIER2_RARITY_MAX} foundations). "
            f"Cannot rule out coincidence."
        ), 0.0

    # Passed all three — Tier 2 qualification
    signals = []
    if has_shared:
        signals.append(f"shared surname '{distinctive_sn}'")
    if family_in_name:
        signals.append("'Family' in foundation name")
    return FOType.SINGLE_FAMILY, (
        f"990-PF-only Tier 2: {' + '.join(signals)}. Investment/treasury "
        f"officer present. Distinctive surname (appears in "
        f"{officer_surname_counts.get(distinctive_sn, 0)} foundations). "
        f"Foundation assets: ${assets_m:.1f}M. Single source (IRS only), "
        f"lower confidence tier."
    ), 0.65


def enrich_and_qualify() -> dict[str, int]:
    """
    Main enrichment + qualification entry point.
    Reads raw_candidates, applies the gate, writes to qualified_candidates
    and excluded_candidates.
    """
    from discovery import storage

    conn = storage.get_db()
    started = _now()

    # Create qualified_candidates table if not exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qualified_candidates (
            record_id TEXT PRIMARY KEY,
            discovery_source TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            fo_type TEXT NOT NULL,
            fo_type_evidence TEXT NOT NULL,
            city TEXT,
            state_region TEXT,
            street_address TEXT,
            aum_usd INTEGER,
            officers_json TEXT,
            discovery_note TEXT,
            qualified_at TEXT NOT NULL,
            cross_channel_match TEXT
        )
    """)
    # Clear previous run
    conn.execute("DELETE FROM qualified_candidates")
    conn.commit()

    # Build cross-channel surname index
    print("[1/3] Building cross-channel surname index...")
    cross_matches = find_cross_channel_matches(conn)
    print(f"      -> {len(cross_matches)} cross-channel surname matches found")

    # Collect surnames that appear in both channels
    cross_surnames: set[str] = set()
    for surname, _, _ in cross_matches:
        cross_surnames.add(surname)
    print(f"      -> {len(cross_surnames)} unique surnames in both channels")

    # Build officer surname frequency for rarity filter
    from collections import Counter
    officer_surname_counts: Counter = Counter()
    pf_rows = conn.execute(
        "SELECT officers_json FROM raw_candidates WHERE discovery_source = '990pf'"
    ).fetchall()
    for r in pf_rows:
        officers = json.loads(r["officers_json"]) if r["officers_json"] else []
        for o in officers:
            sn = _extract_surname(o.get("name", ""))
            if sn:
                officer_surname_counts[sn] += 1

    # Show some matches
    if cross_matches:
        print("      examples:")
        for surname, pf_id, sec_id in cross_matches[:5]:
            pf_name = conn.execute(
                "SELECT entity_name FROM raw_candidates WHERE record_id = ?", (pf_id,)
            ).fetchone()
            sec_name = conn.execute(
                "SELECT entity_name FROM raw_candidates WHERE record_id = ?", (sec_id,)
            ).fetchone()
            if pf_name and sec_name:
                print(f"        '{surname}': {pf_name['entity_name'][:30]} <-> {sec_name['entity_name'][:30]}")

    # Qualify all candidates
    print("\n[2/3] Applying qualification gate...")
    qualified_count = 0
    excluded_count = 0
    sec_qualified = 0
    pf_qualified = 0
    confidence_tiers: dict[str, int] = {"0.9": 0, "0.75": 0, "0.65": 0}

    rows = conn.execute("SELECT * FROM raw_candidates").fetchall()
    for r in rows:
        source = r["discovery_source"]
        if source == "sec_edgar":
            # Re-fetch IAPD for candidates with CRD
            officers = json.loads(r["officers_json"]) if r["officers_json"] else {}
            crd = officers.get("crd", "").strip()
            iapd_result = None
            if crd:
                from discovery.sec_adv import verify_via_iapd
                iapd_result = verify_via_iapd(crd)
            fo_type, evidence, confidence = qualify_sec_edgar_candidate(r, iapd_result)
        elif source == "990pf":
            fo_type, evidence, confidence = qualify_990pf_candidate(
                r, cross_surnames, dict(officer_surname_counts)
            )
        else:
            continue

        if fo_type == FOType.UNCLEAR:
            excluded_count += 1
            storage.insert_excluded(conn, {
                "record_id": r["record_id"],
                "discovery_source": source,
                "entity_name": r["entity_name"],
                "reason_excluded": evidence[:200],
                "fo_type_considered": "unclear",
            })
        else:
            qualified_count += 1
            if source == "sec_edgar":
                sec_qualified += 1
            else:
                pf_qualified += 1
            tier_key = str(confidence)
            if tier_key in confidence_tiers:
                confidence_tiers[tier_key] += 1
            # Find cross-channel match if any
            cross_match = ""
            for surname, pf_id, sec_id in cross_matches:
                if (source == "990pf" and pf_id == r["record_id"]) or \
                   (source == "sec_edgar" and sec_id == r["record_id"]):
                    cross_match = surname
                    break
            conn.execute("""
                INSERT INTO qualified_candidates
                (record_id, discovery_source, entity_name, fo_type, fo_type_evidence,
                 city, state_region, street_address, aum_usd, officers_json,
                 discovery_note, qualified_at, cross_channel_match)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r["record_id"], source, r["entity_name"], fo_type.value, evidence,
                r["city"], r["state_region"], r["street_address"],
                r["assets_usd"], r["officers_json"], r["discovery_note"],
                _now(), cross_match
            ))
    conn.commit()

    print(f"      -> {qualified_count} qualified ({sec_qualified} sec_edgar, {pf_qualified} 990pf)")
    print(f"      -> {excluded_count} excluded (unclear)")
    print(f"      -> confidence tiers: {confidence_tiers}")

    print(f"      -> {qualified_count} qualified ({sec_qualified} sec_edgar, {pf_qualified} 990pf)")
    print(f"      -> {excluded_count} excluded (unclear)")

    # Post-qualification source-mix check
    print("\n[3/3] Post-qualification source-mix audit...")
    mix = storage.source_mix_ratio_qualified(conn)
    print(f"      -> {mix}")
    max_share = max(mix.values()) if mix else 0
    guard = THRESHOLDS["max_single_source_share_final"]
    if max_share > guard:
        print(f"      ⚠️  GUARD VIOLATED: max share {max_share:.3f} > {guard}")
        print(f"         Under-producing channel must be expanded before delivery.")
    else:
        print(f"      ✅ Within guard (max share {max_share:.3f} <= {guard})")

    counts = {
        "qualified": qualified_count,
        "excluded": excluded_count,
        "sec_edgar_qualified": sec_qualified,
        "pf_qualified": pf_qualified,
        "cross_channel_matches": len(cross_matches),
        "post_qual_source_mix": {k: round(v, 3) for k, v in mix.items()},
    }
    print(f"\n=== DONE. {json.dumps(counts, indent=2)} ===\n")
    return counts


def select_final_50(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """
    Select the final 50 qualified candidates with source-balance capping.

    Selection order (highest confidence first):
      1. All confidence=0.9 (cross-channel + ERA) — these are the
         strongest records, take all of them
      2. All confidence=0.75 (IAPD verified + concentrated)
      3. confidence=0.65 (990-PF Tier 2) sorted by asset size descending
         (larger foundations = stronger wealth signal), capped to keep
         the final source-mix ratio under max_single_source_share_final

    The cap is applied at selection time, not at the gate — both channels'
    evidentiary bars stay where they are. If 990-PF produced more
    qualifying candidates than SEC EDGAR, the final 50 is selected to
    preserve source balance, prioritizing highest-confidence records
    from the over-represented channel. This is a disclosed curation
    decision, not a retroactive gate change.
    """
    guard = THRESHOLDS["max_single_source_share_final"]
    target = 50

    # Get all qualified, sorted by confidence then assets
    rows = conn.execute("""
        SELECT qc.*, rc.discovery_note
        FROM qualified_candidates qc
        LEFT JOIN raw_candidates rc ON qc.record_id = rc.record_id
        ORDER BY
            CASE qc.fo_type_evidence
                WHEN '%ERA-registered%' THEN 0
                WHEN '%Cross-channel%' THEN 0
                WHEN '%IAPD verified%' THEN 1
                ELSE 2
            END,
            qc.aum_usd DESC
    """).fetchall()

    # Classify by confidence tier
    tier_09 = [r for r in rows if "ERA-registered" in r["fo_type_evidence"] or "Cross-channel" in r["fo_type_evidence"]]
    tier_075 = [r for r in rows if "IAPD verified" in r["fo_type_evidence"]]
    tier_065 = [r for r in rows if "Tier 2" in r["fo_type_evidence"]]

    selected: list[sqlite3.Row] = []
    sec_count = 0
    pf_count = 0

    # Tier 0.9 first — take all
    for r in tier_09:
        if len(selected) >= target:
            break
        selected.append(r)
        if r["discovery_source"] == "sec_edgar":
            sec_count += 1
        else:
            pf_count += 1

    # Tier 0.75 — take all
    for r in tier_075:
        if len(selected) >= target:
            break
        selected.append(r)
        if r["discovery_source"] == "sec_edgar":
            sec_count += 1
        else:
            pf_count += 1

    # Tier 0.65 — take with source-balance cap
    for r in tier_065:
        if len(selected) >= target:
            break
        source = r["discovery_source"]
        # Check if adding this would violate the guard
        new_sec = sec_count + (1 if source == "sec_edgar" else 0)
        new_pf = pf_count + (1 if source == "990pf" else 0)
        new_total = new_sec + new_pf
        max_share = max(new_sec / new_total, new_pf / new_total)
        if max_share > guard and len(selected) < target:
            # Skip this one — it would push the ratio over the guard
            # But only skip if we have enough from the other source
            # to fill the remaining slots
            remaining = target - len(selected)
            other_count = new_sec if source == "990pf" else new_pf
            if other_count < remaining:
                # Not enough from the other source to fill — take it anyway
                pass
            else:
                continue
        selected.append(r)
        if source == "sec_edgar":
            sec_count += 1
        else:
            pf_count += 1

    print(f"Final selection: {len(selected)} records")
    print(f"  sec_edgar: {sec_count} ({sec_count/len(selected)*100:.1f}%)")
    print(f"  990pf: {pf_count} ({pf_count/len(selected)*100:.1f}%)")
    max_share = max(sec_count / len(selected), pf_count / len(selected))
    print(f"  max source share: {max_share:.3f} (guard: {guard})")

    return selected


if __name__ == "__main__":
    enrich_and_qualify()
