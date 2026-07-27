# fo-intel-pipeline — Methodology

This document is the inclusion standard and pipeline description. It is
written *before* scraping starts and updated as the pipeline evolves, so
the gates can be audited against the actual decisions made (per rule #2 of
the differentiator assessment: "decided before you start counting").

## 1. Discovery channels

Minimum 3+ genuinely independent channels, not 3 search queries on the
same channel. Each candidate is tagged with `discovery_source` at the
point of discovery, before any enrichment — this is what proves diversity
later.

| Channel | `DiscoverySource` tag | Status | Notes |
|---|---|---|---|
| IRS Form 990-PF (private foundations) | `990pf` | Pilot — CA | ProPublica Nonprofit Explorer API; $10M+ foundation assets; officers/trustees + mailing address extracted; address + surname clustering across ≥2 foundations = FO fingerprint |
| SEC EDGAR (13F / Form D / ADV full-text) | `sec_edgar` | Planned | 13F filers >$100M AUM; ADV Part 2A brochure text used to confirm "manages family capital" vs "advises third-party clients" |
| State business / LLC registries | (to be added to enum) | Planned | CA SOS business search; confirms entity existence + principal address; independent of federal filings |
| LinkedIn people-search (not company-search) | (to be added) | Planned | People who self-identify as "Family Office CIO" / "Director of Investments at [family surname] Family Office" |

### Source-mix ratio guard

Tracked live, not at the end. If any single channel produces
`> max_single_source_share_at_halfway` (currently 0.60) of candidates by
the halfway point, work stops and a new channel is opened before
continuing. This is enforced in code, not by memory.

### SFO-bias

Deliberate bias toward hard-to-find SFO signals: no marketing site, no
team page, single-family naming patterns, filings under an individual or
family trust name rather than a firm brand. The 990-PF channel is
specifically chosen because it surfaces SFOs with zero web presence —
the foundation's mailing address is frequently the family office's own
address, and the named trustees/officers are very often the family
principals themselves.

## 2. Firm-level qualification gate (PASS/FAIL)

Applied identically to every record. A firm is included only if there is
affirmative evidence of:

1. **Single-family or defined multi-family structure** (`fo_type` ∈
   {`single_family_office`, `multi_family_office`}; never
   `unclear` — unclear is an honest exclusion, not a forced label).
2. **It manages investable wealth for that family** (not advisory
   services to third-party clients).
3. **At least one independent source confirms this beyond the discovery
   source.**

A firm is **never** included on the basis of name, vibe, proximity to
other confirmed FOs, or because it "serves wealthy clients." That is
explicitly the most serious error type.

### Institutional-manager exclusion

Substring match against `EXCLUDE_NAMES.txt`. If `entity_name` contains
any excluded token, the record is auto-rejected with reason
`excluded_institutional_manager` and routed to `ExcludedCandidate`
(audit pool, not delivery). Secondary check via ADV Part 2A brochure
text: if the firm's own ADV says it does not manage family capital or
serves third-party investors exclusively, exclude.

### Thresholds

All numeric gates live in `config.py` as named constants. Current
values:

| Threshold | Value | Rationale |
|---|---|---|
| `aum_minimum_usd_millions` | 25 | Excludes personal-holding vehicles that aren't operating FOs |
| `aum_maximum_usd_millions` | 5,000 | Excludes mega-offices (Cascade, Bezos-style) outside mid-market target |
| `mfo_max_families_served` | 3 | Above this is an advisory firm, not an MFO |
| `foundation_assets_minimum_usd_millions` | 10 | $1M foundations are giving vehicles for modest wealth, not SFO fingerprints |
| `sec_13f_aum_floor` | 100 | Automatic (13F filing threshold) but stated for transparency |
| `contact_decisiveness_minimum_fields` | 1 | Zero usable principal contacts = unactionable even if firm data is solid |
| `target_states` | `["CA"]` | Pilot one state first; expand only after pipeline validated |

### Pilot state: California

Chosen for: highest SFO density proportionally (tech exits create SFOs
faster than old-money regions), better 990-PF and SEC filing trail,
cleaner CA SOS business registry for verification, stronger LinkedIn
presence for cross-checking. Fallback if CA yields slow: Florida
(Miami/Boca wealth-management hub) or New York (old money + finance
wealth). The `state_targeting` field on each record tracks which pilot
state it was discovered under, so the source-mix ratio can be audited
per state, not just nationally.

## 3. Cell-level verification

More forgiving than the firm gate. Every high-value field carries
provenance (`VerifiedField`: `source` + `method` + `checked_on`),
stored alongside the value, not just in the analyst's head.

- "Verified" requires a method, not just a checkmark.
- Unverifiable = blank + `could_not_verify`, never a guess. This is
  rewarded, not penalized.
- Validation gates output: if the email/phone checker flags something
  bad, that value does not reach the delivered field — it is rejected
  in code, not just logged.

## 4. Pipeline mechanics

The final 50 come out of code that is run, not out of copy-pasting from
ChatGPT/Perplexity into a spreadsheet. Manual spot-checks and judgment
calls are fine; manual record compilation is disqualifying.

- **Storage:** SQLite (`data/candidates.db`) as source of truth between
  stages; JSONL exports per stage under `data/discovery/` for
  git-diffability and audit.
- **Git history:** incremental commits per logical step (config →
  denylist → methodology → discovery script → run results → enrichment
  → validation → RAG). No squashed/recreated history.
- **No ZIP submission:** public repo.

## 5. RAG / output layer (planned)

- Grounding control that actually constrains output, not just a system
  prompt saying "only use provided data." Retrieval-then-answer with a
  citation-check step, or a confidence threshold that forces an
  explicit "insufficient evidence" response when retrieval is weak.
- Deployed, public, non-technical-user-facing UI — no bare API/JSON.
  Every response (including failure/empty states) reads like a product.
- Both layers tested separately: (a) is the underlying record actually
  right, (b) does the generated answer stay within what that record
  supports. Examples of both checks logged.

## 6. Documentation trail (built as we go)

- [x] Methodology summary (this document)
- [ ] 3 fully worked example records with full chain-of-evidence
- [ ] Stack / chunking / embedding / retrieval doc + real queries run
- [ ] Build session log (approx time, sessions, what AI produced vs.
      what was manually changed) — kept short, no padding

## 7. Known blind spots

Updated as the pipeline surfaces them. Initial set:

- 990-PF only surfaces foundations that *file* — families that
  structure philanthropy through donor-advised funds at a community
  foundation (e.g. Silicon Valley Community Foundation) will not appear
  as their own 990-PF. This systematically under-represents
  recently-liquid tech wealth that hasn't set up a private foundation
  yet.
- ProPublica's parsed officer data occasionally drops the title field
  for older filings; raw IRS XML would recover it but is not used in
  the pilot (see `Data source` decision: ProPublica API only).
- The institutional-manager denylist is a seed list. False positives
  (a real SFO excluded because its name contains a generic token) are
  recoverable via manual review; false negatives (an institutional firm
  that ships as an SFO) are disqualifying, so the list is intentionally
  conservative.
