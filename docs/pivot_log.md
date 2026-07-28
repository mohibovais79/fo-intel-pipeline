# fo-intel-pipeline — Pivot Log

Running log of architectural decisions and dead-ends encountered while
building the pipeline, kept as they happen (not reconstructed at the
end). The brief explicitly rewards "problems you found before we did"
and "what you'd improve" — this is the raw material for that section of
the methodology summary.

## Pivot 1 — ProPublica API cannot filter by foundation type

**Problem:** ProPublica's `/search.json` endpoint filters by `state[id]`
and `c_code[id]` (IRS subsection code), not by `foundation_code`
(private foundation vs. public charity). `c_code=3` returns all 501(c)(3)
orgs in CA (~10,000), of which only a fraction are private foundations
that file 990-PF. There is no API parameter to filter to 990-PF filers
only.

**Pivot:** Use the IRS 990 e-file index CSV
(`https://apps.irs.gov/pub/epostcard/990/xml/{year}/index_{year}.csv`)
as the primary 990-PF filter — it has a `RETURN_TYPE` column that
includes `990PF`. Intersect the IRS index's 990-PF EINs with ProPublica's
CA 501(c)(3) EIN set to get CA private foundations that e-filed.

**Why this is better:** The IRS index is the authoritative source of
"who filed what form." ProPublica's search is a text/org-name search
with subsection filtering, not a form-type filter. Using the IRS index
for form-type filtering and ProPublica for state + financials plays to
each source's actual strength.

## Pivot 2 — ProPublica parsed JSON has no officer names

**Problem:** ProPublica's `/organizations/{ein}.json` endpoint returns
`filings_with_data` with ~100 financial summary fields, but officer/
trustee **names** are not among them. The `compofficers` field is a
dollar total (total officer compensation), not a name list. The entire
value of the 990-PF channel per the original spec ("names trustees/
officers/directors — which is very often the family principals
themselves") depends on getting names, which ProPublica's API does not
expose.

**Pivot:** Fetch the raw 990-PF XML per candidate EIN and parse
officer names from the `OfficerDirTrstKeyEmplGrp` element. Verified
against live data: the COBALT FOUNDATION (EIN 611378956) returns
officers `DAVID S BLUE, TODD L BLUE, KAREN BLUE` — a clear shared-
surname family signal that would be invisible through ProPublica's
API alone.

**Decision point raised with user:** three options were on the table
(ProPublica + IRS XML / ProPublica only with address clustering /
ProPublica + PDF text parsing). User chose ProPublica + IRS XML with
the reasoning that PDF parsing of Part VII tables is brittle across
preparer layouts, and address-only clustering is noisy (registered
agents and law firms serve as mailing address for dozens of unrelated
foundations). The XML route is more upfront engineering but reliable,
and "built a real data pipeline vs. scraped a lookup" is a distinction
a senior reviewer notices.

## Pivot 3 — IRS AWS S3 bucket discontinued; GivingTuesday data lake is the live source

**Problem:** The original AWS Open Data `irs-form-990` S3 bucket
(`https://s3.amazonaws.com/irs-form-990/{OBJECT_ID}_public.xml`) was
discontinued on Dec 31, 2021. Direct single-XML fetches by OBJECT_ID
return 404 for both pre-2021 and post-2021 OBJECT_IDs. The IRS now
publishes XML data as monthly ZIP bundles on `apps.irs.gov`
(~100-160MB each), which would require downloading ~1.8GB/year to
extract a few hundred candidate XMLs.

**Pivot:** The GivingTuesday 990 Data Lake
(`gt990datalake-rawdata` S3 bucket) mirrors the IRS e-file XMLs as
individual files under `EfileData/XmlFiles/{OBJECT_ID}_public.xml`,
accessible via public HTTPS with no AWS account. Verified: a single
122KB 990-PF XML fetches in <1s. This gives per-candidate XML download
without monthly-ZIP bulk transfers.

**Caveat:** The GivingTuesday bucket was last updated 2023-10-28
(per S3 `LastModified` timestamps). For tax years 2024+ it may lag
the IRS's own monthly ZIPs. For the pilot (CA, recent filings) this
is acceptable — 2021-2023 tax year filings are well-covered. If the
pipeline scales to 2024+ data, a fallback to IRS monthly ZIPs with
stream-extraction by OBJECT_ID would be needed.

## Pivot 4 — Surname/officer-count filter cannot run pre-XML

**Problem:** The natural noise filter for step 4 (exclude hospital/
university supporting foundations that file 990-PF but aren't family
offices) would ideally use officer count and shared-surname patterns.
But officer data only exists in the XML, which is fetched in step 5.
So the filter has to split:

- **Pre-XML (step 4):** name-keyword exclusion of operating-charity
  patterns ("Hospital", "University", "College", "School", "Medical
  Center", "Health System", "Church", "Diocese", etc.). Conservative;
  catches the bulk of obvious noise.
- **Post-XML (step 6):** officer count + shared surname. This is the
  real family-foundation fingerprint and can only run after XML parse.
  Cross-foundation surname clustering (≥2 foundations sharing a
  surname) is the strongest signal but needs the full parsed set.

**Why this matters:** A foundation called "Stanford Hospital Foundation"
files 990-PF, has $100M+ assets, and is not a family office. Without
the name-keyword pre-filter, it would pass step 4 and waste an XML
fetch. Without the post-XML officer filter, it would pass step 6 too
(hospital supporting foundations often have 10+ board members with no
shared surname). Both filters are needed; pretending step 4 can do
surname filtering would be dishonest about where the signal actually
lives.

## Pivot 5 — Officer-count hard cap was a false-negative bug

**Problem:** The original post-XML filter used a hard cutoff at
`officer_count <= 6`. This excluded real family foundations that have
larger boards because the family includes grown children, spouses, and
a few independent trustees:

- Sandberg Goldberg Bernthal Family Charitable Foundation — 10
  officers, clearly a family foundation by name, excluded.
- Eric And Wendy Schmidt Fund For Strategic Innovation — 10 officers,
  clearly a family foundation by name, excluded.

A hard cap at 6 was too tight for the reality of family foundations
with extended families on the board. This was a correctness bug in
the filter, not a calibration preference — the 195 "excluded" and 575
"passed" pools from the first run were both wrong.

**Pivot:** Replaced the hard cutoff with a soft scoring system:
  +2 shared_surname (strongest signal)
  +1 officer_count <= 6
   0 officer_count 7-12
  -1 officer_count > 12
  +1 attached_schedule (if count <= 12)
  -2 no officers parsed
Pass threshold: score >= 1.

This lets shared-surname foundations with 10-12 officers pass (score
2+0=2) while still excluding medium-board foundations with no family
signal (score 0+0=0) and large institutional boards (score 0-1=-1).
The re-run against the existing 768 raw candidates is cheap — no
re-fetching, just re-applying the filter to cached parse results.

**Why a soft score, not a higher hard cap:** A higher hard cap (say
12) would let through every medium-board foundation regardless of
whether there's any family signal. The soft score makes shared
surname the discriminating signal at higher board sizes — which is
exactly what it should be, since shared surname is the actual
family-foundation evidence and officer count is only a proxy.

## Pivot 6 — ADV brochure text not accessible via public API; using IAPD structured fields instead

**Problem:** The plan for SEC EDGAR channel 2 was 13F discovery + ADV
Part 2A brochure text verification (LLM classification of "manages
family capital vs. third-party clients"). Three access routes all
failed:

1. **IAPD API** (`api.adviserinfo.sec.gov/search/firm/{crd}`): returns
   brochure metadata (version ID, name, date) but NOT the brochure
   text, and NOT the Part 1A Item 5.D client-type breakdown (the
   2024 ADV amendment added "family offices" as a structured checkbox
   category, but that data isn't exposed through the public API).
2. **files.adviserinfo.sec.gov brochure endpoint**
   (`/IAPD/Content/Common/crd_iapd_Brochure.aspx?BRCHR_VRSN_ID={id}`):
   the host responds (200, not 404) but returns "No data found" for
   both fresh IDs (ICONIQ 1027115, ProVise 1032294) and a known-good
   example ID (833997). The server rotates/supersedes brochure
   versions and old IDs stop resolving. The www.adviserinfo.sec.gov
   host returns a JS-rendered SPA shell, not the PDF.
3. **EDGAR full-text search** (`efts.sec.gov`): ADV forms aren't
   indexed — they're filed through IARD, not EDGAR. Searching
   `forms=ADV` returns 0 results. "Family office" mentions in EDGAR
   are all in 10-K/8-K/proxy filings, not RIA filings.

**Pivot:** Use 13F holdings-concentration as the primary family-office
discriminator (empirically validated: Cascade Investment Group = Bill
Gates' FO, 146 holdings; Citadel 15,551; AQR 14,495; Millennium 5,978;
Two Sigma 3,628 — family offices run concentrated books, quant/multi-
strat funds run thousands). Use IAPD structured fields as secondary
confirmation: `otherNames` containing "family", ERA (Exempt Reporting
Adviser) status, relying-advisor count. Skip brochure text entirely.

**What this costs:** The ADV brochure's explicit "we manage family
capital" statement would have been the strongest affirmative
verification signal for the 13F channel — stronger than portfolio
shape alone. Without it, 13F candidates are qualified by portfolio
concentration + IAPD metadata, which is a weaker (but still real and
empirically grounded) signal. The firm-level qualification gate
(schema.FamilyOfficeRecord) will make the final SFO/MFO/unclear call
during enrichment, and candidates that can't be confidently classified
will be marked `unclear` and excluded — not force-labeled.

**Future improvement:** A headless-browser approach (Playwright) could
load the IAPD SPA, capture the PDF download URL from network traffic,
fetch the PDF, extract text, and run LLM classification on Item 7
(client types). This is ~2-3 hours of additional work and adds browser
+ PDF-parsing dependencies. Deferred until after the RAG layer is
built; documented here as a known gap, not hidden.
