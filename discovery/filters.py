"""
fo-intel-pipeline — discovery-stage filters

Two filters, applied at different stages of the 990-PF pipeline:

1. pre_xml_filter(name) — name-keyword exclusion of operating-charity
   supporting foundations (hospital/university/church). Runs BEFORE the
   XML fetch to avoid wasting a network call on obvious noise. This is
   conservative: it cannot do surname or officer-count filtering because
   officer data only exists in the XML (see docs/pivot_log.md pivot 4).

2. post_xml_family_fingerprint(parsed) — the real family-foundation
   fingerprint. Runs AFTER XML parse. Flags a foundation as a family-
   office fingerprint candidate if:
     - officer_count <= MAX_OFFICERS (small board = family-run, not
       institutional), AND
     - has_shared_surname OR officers_attached_schedule (shared surname
       is the strongest signal; 'SEE ATTACHED' is a weaker but acceptable
       proxy since family foundations often attach schedules to avoid
       listing family members inline)

Both filters return (passed: bool, reason: str) so the orchestrator can
route failures to the excluded_candidates audit table with a real reason
code, not a silent drop.
"""

from __future__ import annotations

from typing import Any

from config import OPERATING_CHARITY_KEYWORDS

# Post-XML family-fingerprint thresholds.
# officer_count <= 6: a real family foundation has a small board (the
# family principals + maybe 1-2 independent trustees). A supporting
# foundation for a hospital or university typically has 10+ board
# members drawn from the parent institution's leadership.
MAX_OFFICERS = 6


def pre_xml_filter(name: str) -> tuple[bool, str]:
    """
    Returns (passed, reason). passed=False means the foundation is
    excluded as an operating-charity supporting foundation.
    """
    if not name:
        return False, "empty_name"
    name_lower = name.lower()
    for kw in OPERATING_CHARITY_KEYWORDS:
        if kw in name_lower:
            return False, f"operating_charity_keyword:{kw}"
    return True, "passed_pre_xml"


def post_xml_family_fingerprint(parsed: dict[str, Any]) -> tuple[bool, str]:
    """
    Returns (passed, reason). passed=True means the foundation has a
    family-office fingerprint worth keeping as a candidate.
    """
    if parsed.get("officers_attached_schedule"):
        # 'SEE ATTACHED' — we can't verify surname sharing, but the small
        # board + attached schedule pattern is consistent with family
        # foundations that don't list family members inline.
        if parsed.get("officer_count", 0) <= MAX_OFFICERS:
            return True, "small_board_attached_schedule"
        return False, "large_board_attached_schedule"

    count = parsed.get("officer_count", 0)
    if count == 0:
        return False, "no_officers_parsed"

    if count > MAX_OFFICERS:
        return False, f"officer_count_too_high:{count}"

    if parsed.get("has_shared_surname"):
        return True, f"shared_surname:{count}_officers"

    # Small board, no shared surname — keep as a weaker candidate. The
    # firm-level qualification gate (schema.FamilyOfficeRecord) will
    # make the final SFO/MFO/unclear call during enrichment.
    return True, f"small_board_no_shared_surname:{count}_officers"
