# Build Session Log

## Overview

Built a family office intelligence pipeline for California with two
discovery channels (IRS 990-PF + SEC EDGAR 13F), a deterministic
qualification gate, cell-level provenance, and a grounded RAG output
layer with a deployed web UI.

## Timeline

### Session 1 (~4 hours): 990-PF channel + schema

- Designed `FamilyOfficeRecord` schema with `VerifiedField` provenance
- Built IRS 990-PF discovery: ProPublica API → IRS XML parsing →
  officer extraction → soft fingerprint filter
- First run: 768 CA candidates
- AI produced: schema, discovery modules, XML parser, filter logic
- I changed: filter thresholds (tightened officer count), added
  institutional denylist, wrote methodology before building

### Session 2 (~6 hours): SEC EDGAR channel + qualification

- Built SEC EDGAR 13F discovery: bulk ZIP download → TSV parsing →
  empirical filter derivation (Cascade 146 vs Citadel 15,551 holdings)
- Built IAPD ADV verification module (structured fields, no brochure
  text — Pivot 6)
- Fixed critical units bug: SEC changed VALUE column from thousands
  to dollars on Jan 3, 2023 (was inflating AUMs 1000x)
- Built enrichment + qualification gate with confidence tiers
- AI produced: 13F loader, IAPD verifier, qualification logic
- I changed: cross-channel matching approach (required family surname,
  not any officer surname), added corporate foundation filter, fixed
  surname extraction bugs (title fragments as false surnames)

### Session 3 (~3 hours): RAG layer + UI + documentation

- Built RAG layer with two-layer grounding control
- Built FastAPI web UI with premium design
- Wrote README, RAG stack doc, build session log
- AI produced: embedding pipeline, retrieval logic, UI template
- I changed: response templates (rewrote from debug dump to natural
  language), field detection keywords (removed "office" triggering
  address field), UI design (full redesign for premium feel)

## What AI produced vs. what I changed

**AI produced (initial version):**
- All Python modules (schema, discovery, enrichment, RAG)
- Initial filter logic and thresholds
- Initial UI template
- Initial response templates

**I changed (judgment calls):**
- Officer filter: hard cap → soft fingerprint scoring
- Cross-channel matching: any officer surname → foundation family
  surname only (fixed 19 false positives in the 0.9 tier)
- Corporate foundation filter: added after discovering Visa, Salesforce
  labeled as SFOs
- Source-mix approach: accepted 86/14 imbalance honestly instead of
  weakening SEC EDGAR's evidence bar
- RAG responses: rewrote from debug dump to natural language
- UI: full redesign for premium UX
- Principal extraction: prefer family members over hired staff

## Pivots (6 total)

1. Officer count: hard cap → soft scoring
2. CA SOS channel: planned → dropped (time management)
3. SEC EDGAR filter: derived empirically from known FOs vs hedge funds
4. ADV brochure text: inaccessible via public API (Pivot 6)
5. 13F units: thousands → dollars (SEC changed Jan 2023)
6. Source-mix: accepted imbalance as structural finding

See `docs/pivot_log.md` for full details.

## Git history

15 commits across 3 sessions, all incremental (no squashed history).
Author: mohibovais79 throughout.
