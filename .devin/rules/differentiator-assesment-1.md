---
trigger: always_on
---

1. Discovery — "did I actually find this, or copy it?"
 Minimum 3+ genuinely independent discovery channels, not 3 search queries on the same channel. E.g.: SEC ADV/Form 13F filings, state business/LLC registries, foundation/philanthropic filings (SFOs often surface as the family foundation's manager), local business-press coverage of liquidity events (company sales, IPOs — these create new SFOs), LinkedIn people-search (not company-search), industry conference speaker/attendee lists.
 Log which channel found which firm. Tag every candidate record with its discovery source at the point of discovery, before any enrichment — this is what proves diversity later, and what your methodology summary has to show.
 Track the source-mix ratio as you go, not at the end. If one channel is producing >50-60% of candidates by the halfway point, stop and open a new channel — don't discover this problem after you've built 40 records.
 Bias toward SFO-hard-to-find signals deliberately: no marketing site, no team page, single-family naming patterns, filings under an individual/family trust name rather than a firm brand.
2. Firm-level qualification — the pass/fail gate
 Written inclusion standard, decided before you start counting, e.g.: "included only if I have affirmative evidence of (a) single-family or defined multi-family structure, (b) it manages investable wealth for that family, (c) at least one independent source confirms this beyond the discovery source." Apply it identically to every record.
 A firm is never included on the basis of name, vibe, or proximity to other confirmed FOs, or because it "serves wealthy clients" — that's explicitly called out as the most serious error type.
 If type (SFO vs MFO vs advisory firm) is genuinely unclear, mark it unclear — don't include it, and don't force-label it as SFO to look valuable. Misclassification costs more than an honest exclusion.
 A record that fails this gate doesn't count toward the 50, regardless of how good its other fields are — treat this as a hard filter step in the pipeline, not a manual judgment call at the end.
3. Cell-level verification — separate rule, more forgiving
 Every high-value field carries provenance: source + method, stored alongside the value, not just in your head.
 "Verified" requires a method, not just a checkmark. No unmarked assumptions.
 Unverifiable = blank + "could not verify," never a guess. This is explicitly rewarded, not penalized.
 Validation must gate output, not just log it. If your email/phone checker flags something bad, that value must not reach the final delivered field — build this as an actual filter step (reject/blank on failure), not a report you generate and ignore.
4. Pipeline mechanics (the "not manually assembled" requirement)
 The 50 must come out of code you run, not out of you copy-pasting from ChatGPT/Perplexity into a spreadsheet. Manual spot-checks and judgment calls are fine; manual record compilation is disqualifying.
 Real git history as you build — commit incrementally (discovery script → enrichment → validation → RAG), don't build locally then do one big initial commit. They explicitly check for squashed/recreated history.
 No ZIP submission — public repo or shared repo, or a git bundle only as fallback.
5. RAG / output layer
 Grounding control that actually constrains output, not just a system prompt saying "only use provided data." E.g., retrieval-then-answer with a citation-check step, or a confidence threshold that forces an explicit "insufficient evidence" response when retrieval is weak. You need to demonstrate this triggering in real queries.
 Deployed, public, non-technical-user-facing UI — no bare API/JSON. Every response (including failure/empty states) needs to read like a product, not a debug dump.
 Test both layers separately: (a) is the underlying record actually right, (b) does the generated answer stay within what that record supports. Log examples of both checks.
6. Documentation trail (build these as you go, not at the end)
 Methodology summary (discovery → enrichment → validation → known blind spots)
 3 fully worked example records with full chain-of-evidence
 Stack/chunking/embedding/retrieval doc + real queries you ran
 Build session log (approx time, sessions, what AI produced vs. what you changed) — kept short, no padding