# fo-intel-pipeline — Methodology

This document is the inclusion standard and pipeline description. It is
written *before* scraping starts and updated as the pipeline evolves, so
the gates can be audited against the actual decisions made (per rule #2 of
the differentiator assessment: "decided before you start counting").

## 1. Discovery channels

Two genuinely independent channels, not 2 search queries on the same
channel. Each candidate is tagged with `discovery_source` at the point
of discovery, before any enrichment — this is what proves diversity
later. The source-mix ratio is tracked live; no single channel may
produce >60% of candidates by the halfway point.

| Channel | `DiscoverySource` tag | Status | Notes |
|---|---|---|---|
| IRS Form 990-PF (private foundations) | `990pf` | Built — CA pilot | IRS 990 e-file index CSV (filters `RETURN_TYPE=990PF`) intersected with ProPublica's CA 501(c)(3) EIN set → CA private foundations that e-filed. ProPublica `/organizations/{ein}.json` for financials + mailing address. Pre-XML noise filter: name-keyword exclusion of operating-charity supporting foundations (hospital/university/church). Raw 990-PF XML from GivingTuesday 990 Data Lake for officer/trustee name + title extraction. Post-XML filter: soft fingerprint score (shared surname +2, small board +1, medium board 0, large board -1, attached schedule +1, no officers -2; pass threshold >= 1). Paper-filed returns with no XML are flagged as a known blind spot. Full pivot history in `docs/pivot_log.md`. |
| SEC EDGAR (13F + IAPD/ADV structured fields) | `sec_edgar` | Built — CA pilot | SEC quarterly 13F bulk data sets (pre-parsed TSVs from EDGAR XML). 13F-HR only (not 13F-NT notices). Aggregated at firm level (not per-accession, to avoid fund-vehicle double-counting). Discovery filter empirically derived from inspection of known family offices vs hedge funds (pivot 6): CA HQ, 10-500 holdings (excludes PE/VC vehicles at 1-5; excludes quant/multi-strat funds at 1000+ — Cascade/Bill Gates FO = 146 holdings vs Citadel 15,551), AUM $25M-$5B, exclude names matching EXCLUDE_NAMES.txt. IAPD structured-field verification as secondary signal: ERA registration (+2), "family" in otherNames/firm name (+1), small relying-advisor count ≤5 (+1). ADV Part 2A brochure text not accessible via public API (pivot 6) — deferred to future headless-browser improvement. 706 CA candidates, 8 IAPD-verified. |

### Source-mix ratio guard

Tracked at TWO points, not just one:

1. **Discovery-stage guard** (`max_single_source_share_at_halfway`:
   0.60): checked mid-run on raw candidate volume. If either channel
   produces >60% of raw candidates by the halfway point, work stops
   and the under-producing channel is expanded before continuing.

2. **Post-qualification guard** (`max_single_source_share_final`:
   0.65): checked AFTER enrichment, on the final qualified set. The
   discovery-volume ratio can diverge sharply from the final-output
   ratio after the qualification gate trims — e.g., if 990-PF supplies
   40+ of the final 50 while SEC EDGAR supplies only its 8 IAPD-
   verified, the final ratio would be ~84/16, violating the spirit of
   the brief's rule ("most of your file traces to one source") even
   though the discovery ratio was balanced. If the post-qualification
   guard is violated, the under-producing channel must be expanded
   (wider filter, more states, or a 3rd channel) before delivery.

Both guards are enforced in code (`storage.source_mix_ratio` and
`storage.source_mix_ratio_qualified`), not by memory.

**Achieved ratios (CA pilot):**
- Discovery stage: 52.1% 990pf / 47.9% sec_edgar (768 + 706 = 1,474
  raw candidates). Within the 0.60 guard.
- Post-qualification: TBD — checked after enrichment runs.

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
(audit pool, not delivery). For SEC EDGAR candidates, secondary check
via IAPD structured fields: large relying-advisor count (>10) flags
institutional pattern; ERA registration + "family" in name confirms
family-office pattern. ADV Part 2A brochure text verification (the
strongest affirmative "manages family capital" signal) is a known gap
— not accessible via public API, deferred to future headless-browser
improvement (see `docs/pivot_log.md` pivot 6).

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
- ProPublica's parsed JSON filings contain only financial summary
  fields — officer/trustee **names** are not in the API response.
  Officer names are extracted from the raw IRS 990 e-file XML via the
  GivingTuesday 990 Data Lake (the original AWS `irs-form-990` bucket
  was discontinued Dec 31, 2021). Foundations that paper-filed
  (pre-e-file mandate or non-e-filed returns) have no XML available;
  these are flagged in the audit record as `no_xml_filing` and
  excluded from officer-name extraction. This is an honest, disclosed
  gap — the alternative (PDF text parsing of Part VII tables) is
  brittle across preparer/software layout variation, and dropping
  officer names entirely would reduce the channel to noisy
  mailing-address clustering (registered agents and law firms serve
  as mailing address for dozens of unrelated foundations). Full
  pivot history in `docs/pivot_log.md`.
- The GivingTuesday data lake was last updated 2023-10-28. Tax year
  2024+ filings may not be present; for the CA pilot (recent filings
  2019-2023) this is acceptable. Scaling to 2024+ would require a
  fallback to IRS monthly ZIP bundles with stream-extraction by
  OBJECT_ID.
- The pre-XML noise filter (operating-charity keyword exclusion) is
  conservative. It will not catch a supporting foundation whose name
  doesn't contain a keyword (e.g. "XYZ Foundation" supporting a
  hospital). The post-XML officer-count and surname filters catch
  these — but only after an XML fetch has been spent.
- The institutional-manager denylist is a seed list. False positives
  (a real SFO excluded because its name contains a generic token) are
  recoverable via manual review; false negatives (an institutional firm
  that ships as an SFO) are disqualifying, so the list is intentionally
  conservative.
- **ADV Part 2A brochure text is not accessible via public API.** The
  IAPD system serves brochures through a JS-rendered SPA, not a
  programmatic endpoint. The 2024 ADV amendment's structured Item 5.D
  "family offices" client-type checkbox (which would have been the
  cleanest signal) is not exposed through `api.adviserinfo.sec.gov`.
  ADV forms are not in EDGAR full-text search (filed through IARD, not
  EDGAR). Current SEC EDGAR verification uses 13F portfolio
  concentration (empirically validated) + IAPD structured metadata
  (ERA status, otherNames, relying-advisor count) as a 2-signal proxy.
  A headless-browser approach (Playwright + pdfplumber + LLM
  classification on Item 7 client types) is the path to the full
  brochure text — deferred to after the RAG layer. See
  `docs/pivot_log.md` pivot 6.
- **13F discovery misses non-13F filers.** Family offices managing
  <$100M in 13(f) securities don't file 13F at all. This channel
  structurally misses smaller SFOs that hold wealth in real estate,
  private company stock, or other non-13(f) assets. The 990-PF channel
  partially compensates (foundations file regardless of investment
  asset mix), but families without a private foundation and without
  $100M+ in exchange-traded equities are invisible to both channels.
