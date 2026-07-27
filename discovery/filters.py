"""
fo-intel-pipeline — discovery-stage filters

Two filters, applied at different stages of the 990-PF pipeline:

1. pre_xml_filter(name) — name-keyword exclusion of operating-charity
   supporting foundations (hospital/university/church). Runs BEFORE the
   XML fetch to avoid wasting a network call on obvious noise. This is
   conservative: it cannot do surname or officer-count filtering because
   officer data only exists in the XML (see docs/pivot_log.md pivot 4).

2. post_xml_family_fingerprint(parsed) — the real family-foundation
   fingerprint. Runs AFTER XML parse. Uses a SOFT SCORING system, not a
   hard officer-count cutoff, because a hard cap at 6 excluded real
   family foundations with grown children + spouses + a few independent
   trustees (e.g. Sandberg Goldberg Bernthal Family Charitable Foundation
   at 10 officers, Eric & Wendy Schmidt Fund at 10). See
   docs/pivot_log.md pivot 5.

   Scoring (pass threshold >= 1):
     +2  shared_surname (strongest signal — family members on board)
     +1  officer_count <= 6 (small board, family-run pattern)
      0  officer_count 7-12 (medium — could be family with extended board)
     -1  officer_count > 12 (large board, institutional pattern)
     +1  officers_attached_schedule (weak positive — consistent with
         family foundations that don't list family inline; only counts
         if officer_count <= 12)
     -2  no officers parsed (can't evaluate)

   Examples:
     shared_surname + count 10:  2 + 0 = 2  PASS (fixes Schmidt/Sandberg)
     shared_surname + count 17:  2 - 1 = 1  PASS (family present, large board)
     no surname + count 4:       0 + 1 = 1  PASS (small board, weak candidate)
     no surname + count 10:      0 + 0 = 0  FAIL (medium board, no family signal)
     no surname + count 25:      0 - 1 = -1 FAIL (institutional)
     attached + count 4:         1 + 1 = 2  PASS
     no officers:               -2         FAIL

Both filters return (passed: bool, reason: str) so the orchestrator can
route failures to the excluded_candidates audit table with a real reason
code, not a silent drop.
"""

from __future__ import annotations

from typing import Any

from config import OPERATING_CHARITY_KEYWORDS

# Score thresholds for the soft fingerprint.
PASS_THRESHOLD = 1


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


def _officer_count_score(count: int) -> int:
    if count <= 6:
        return 1
    if count <= 12:
        return 0
    return -1


def post_xml_family_fingerprint(parsed: dict[str, Any]) -> tuple[bool, str]:
    """
    Returns (passed, reason). passed=True means the foundation has a
    family-office fingerprint worth keeping as a candidate.
    """
    count = parsed.get("officer_count", 0)
    attached = parsed.get("officers_attached_schedule", False)
    shared = parsed.get("has_shared_surname", False)

    if count == 0 and not attached:
        return False, "no_officers_parsed"

    score = 0
    signals: list[str] = []

    if shared:
        score += 2
        signals.append("shared_surname")

    count_score = _officer_count_score(count)
    score += count_score
    if count <= 6:
        signals.append(f"small_board:{count}")
    elif count <= 12:
        signals.append(f"medium_board:{count}")
    else:
        signals.append(f"large_board:{count}")

    if attached and count <= 12:
        score += 1
        signals.append("attached_schedule")

    passed = score >= PASS_THRESHOLD
    reason = f"score={score};" + ",".join(signals)
    return passed, reason
