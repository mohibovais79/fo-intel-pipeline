"""
fo-intel-pipeline — record schema

Design principles baked into this schema:
1. A record CANNOT be exported to final CSV unless firm-level qualification
   evidence is present (fo_type + fo_type_evidence). This is the pass/fail
   gate — enforced in code, not by memory.
2. Every high-value cell has its own provenance: source + verification
   method. "Verified" is never a bare boolean.
3. Cell-level uncertainty is allowed and explicit (Optional + status enum).
   Firm-level uncertainty is NOT allowed — either it qualifies or it's dropped.
4. discovery_source is tagged at creation time, before enrichment, so the
   source-diversity ratio can be audited later without re-deriving it.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class DiscoverySource(str, Enum):
    FORM_990PF = "990pf"  # IRS private foundation filings
    SEC_EDGAR = "sec_edgar"  # 13F / Form D / ADV full-text
    CA_SOS = "ca_sos"  # California Secretary of State business registry


class FOType(str, Enum):
    SINGLE_FAMILY = "single_family_office"
    MULTI_FAMILY = "multi_family_office"
    UNCLEAR = "unclear"  # honest exclusion state — never ships


class VerificationStatus(str, Enum):
    VERIFIED = "verified"  # method + source confirm the value
    COULD_NOT_VERIFY = "could_not_verify"  # honest blank — allowed, scored as candor
    REJECTED = "rejected"  # failed its own validation, must not
    # appear in the delivered field


class VerifiedField(BaseModel):
    """Wraps any high-value cell with mandatory provenance."""

    value: Optional[str] = None
    status: VerificationStatus
    source: Optional[str] = None  # e.g. "SEC ADV brochure, Part 2A"
    method: Optional[str] = None  # e.g. "cross-checked against state SOS filing"
    checked_on: Optional[date] = None

    @model_validator(mode="after")
    def value_only_if_verified(self):
        if self.status != VerificationStatus.VERIFIED and self.value is not None:
            raise ValueError(
                "A field can only carry a value when status == VERIFIED. "
                "Rejected/unverified values must be stored in the audit "
                "record, not the delivery field."
            )
        if self.status == VerificationStatus.VERIFIED and not (self.source and self.method):
            raise ValueError("Verified fields require both source and method.")
        return self


class ActivitySignal(BaseModel):
    """A single dated, current intelligence signal (investment, hire, news)."""

    signal_type: str  # "investment" | "hire" | "news" | "fund_commitment"
    description: str
    date_observed: date
    source: str


class FamilyOfficeRecord(BaseModel):
    # --- Discovery / audit trail (mandatory, set at creation) ---
    record_id: str
    discovery_source: DiscoverySource
    discovery_note: str  # what specifically was found where
    state_targeting: Optional[str] = None  # USPS state code for the pilot run
    # this record was discovered under (e.g. "CA").
    # Tracked for methodology transparency so
    # the source-mix ratio can be audited per
    # state, not just nationally.

    # --- Firm-level qualification gate (PASS/FAIL, mandatory) ---
    fo_type: FOType
    fo_type_evidence: str  # required, non-empty; the affirmative
    # evidence establishing this IS a family
    # office and its type

    # --- Core entity attributes ---
    entity_name: str
    description: Optional[str] = None
    investment_thesis: Optional[str] = None
    investing_sectors: list[str] = Field(default_factory=list)
    domain: Optional[str] = None
    website: Optional[str] = None
    corporate_linkedin: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state_region: Optional[str] = None
    country: Optional[str] = None
    aum_usd: Optional[VerifiedField] = None
    aum_as_of: Optional[date] = None

    # --- Principal / decision-maker intelligence ---
    principal_first_name: Optional[str] = None
    principal_last_name: Optional[str] = None
    principal_title: Optional[str] = None
    principal_linkedin: Optional[str] = None
    principal_email: Optional[VerifiedField] = None
    principal_phone: Optional[VerifiedField] = None

    # --- Recent activity / dated signals ---
    recent_activity: list[ActivitySignal] = Field(default_factory=list)

    # --- Overall confidence (must vary — not a constant) ---
    confidence_score: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def firm_gate_enforced(self):
        if self.fo_type == FOType.UNCLEAR:
            raise ValueError(
                f"{self.entity_name}: fo_type is UNCLEAR — this record does "
                "not qualify for the final 50 and must be routed to the "
                "excluded/audit pool, not the delivery dataset."
            )
        if not self.fo_type_evidence.strip():
            raise ValueError(f"{self.entity_name}: fo_type_evidence is required.")
        return self


class ExcludedCandidate(BaseModel):
    """Firms that failed the qualification gate — kept for audit, not delivery."""

    record_id: str
    discovery_source: DiscoverySource
    entity_name: str
    reason_excluded: str
    fo_type_considered: FOType
