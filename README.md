# Family Office Intelligence Pipeline

**Live demo: https://fo-intel-pipeline.fastapicloud.dev/**

A discovery, qualification, and enrichment pipeline for identifying
single-family offices (SFOs) in California using public filings.
Built as a pilot for the CA market with two independent discovery
channels, a deterministic qualification gate, cell-level provenance,
and a grounded RAG output layer with a deployed web UI.

## What this does

Discovers family office candidates from two independent public data
sources, qualifies them through a deterministic evidence-based gate,
enriches each record with per-field provenance (source + method), and
serves them through a search interface with grounding control that
prevents hallucination.

**Result:** 44 qualified family office records, each with full chain
of evidence, served through a public web UI.

## Pipeline overview

```
Discovery (2 channels)     →  Qualification Gate  →  Enrichment  →  RAG / UI
                           →  (deterministic)      →  (provenance) →  (grounded)

IRS 990-PF (768 raw)       →  Confidence tiers:    →  VerifiedField   →  Embeddings
SEC EDGAR 13F (706 raw)    →  0.9 / 0.75 / 0.65    →  per field       →  Retrieval
                           →  108 qualified         →  44 selected     →  Web UI
```

## Discovery channels

| Channel | Source | Raw candidates | Qualified |
|---------|--------|---------------|-----------|
| IRS 990-PF | Private foundation filings (ProPublica API + IRS XML) | 768 | 102 |
| SEC EDGAR | 13F-HR filings + IAPD ADV verification | 706 | 6 |
| **Total** | | **1,474** | **108** |

## Qualification gate (deterministic)

The gate is code-enforced, not LLM-decided. Three confidence tiers:

| Tier | Confidence | Evidence | Count |
|------|-----------|----------|-------|
| 1 | 0.9 | ERA-registered (SEC family office exemption) OR cross-channel surname match (IRS + SEC) | 10 |
| 2 | 0.75 | IAPD verified (score ≥ 2) + concentrated portfolio (≤150 holdings) | 2 |
| 3 | 0.65 | 990-PF-only: family structure + investment officer + distinctive surname + $50M+ assets | 96 |

## Key design decisions

- **No LLM in the qualification gate.** The gate is deterministic —
  evidence thresholds coded in Python, not model judgments.
- **Cell-level provenance on every high-value field.** Each field
  carries source + method + checked_on. Unverifiable = blank +
  "could_not_verify", never a guess.
- **Grounding control in the RAG layer.** Two layers: (1) retrieval
  threshold forces "insufficient evidence" when no records match,
  (2) field-level provenance check forces "could not verify" when a
  record is retrieved but the specific field asked about is unverified.
- **Source-mix imbalance accepted honestly.** Post-qualification ratio
  is 94/6 (990pf/sec_edgar), violating the 0.65 guard. This is a
  structural finding about the population, not a filter bug. Documented
  in methodology rather than papered over.

## Tech stack

- **Language:** Python 3.11+
- **Data:** SQLite (candidates database)
- **Schema:** Pydantic v2 (FamilyOfficeRecord + VerifiedField)
- **Discovery:** httpx (IRS ProPublica API, SEC EDGAR bulk 13F)
- **Embeddings:** sentence-transformers (all-MiniLM-L6-v2, 384-dim)
- **Retrieval:** numpy cosine similarity (44 records, no vector DB needed)
- **Web UI:** FastAPI + Jinja2 templates
- **Package manager:** uv

## Project structure

```
fo-intel-pipeline/
├── schema.py                    # FamilyOfficeRecord + VerifiedField schema
├── config.py                    # Thresholds, guards, denylists
├── discovery/
│   ├── discover_990pf.py        # IRS 990-PF channel orchestrator
│   ├── discover_sec.py          # SEC EDGAR channel orchestrator
│   ├── irs_index.py             # IRS 990-PF index fetching
│   ├── irs_xml.py               # 990-PF XML parsing
│   ├── propublica_client.py     # ProPublica API client
│   ├── sec_13f.py               # SEC 13F bulk data loader
│   ├── sec_adv.py               # IAPD ADV verification
│   ├── filters.py               # Soft fingerprint scoring
│   └── storage.py               # SQLite persistence + source-mix guards
├── enrichment/
│   ├── qualify.py               # Deterministic qualification gate
│   └── enrich.py                # VerifiedField provenance mapping
├── rag/
│   ├── rag.py                   # Embeddings + retrieval + grounding control
│   ├── server.py                # FastAPI web server
│   └── templates/index.html     # UI
├── docs/
│   ├── methodology.md           # Inclusion standard + pipeline description
│   ├── pivot_log.md             # Architectural decisions + dead-ends
│   └── example_records.md       # 3 fully-worked example records
└── data/
    ├── candidates.db            # SQLite database (raw + qualified + excluded)
    └── discovery/
        ├── final_enriched.jsonl # 44 enriched records with provenance
        └── rag_test_log.json    # RAG grounding control test results
```

## Running

```bash
# Install dependencies
uv sync

# Run discovery (both channels)
uv run python -m discovery.discover_990pf
uv run python -m discovery.discover_sec

# Run qualification + enrichment
uv run python -m enrichment.qualify
uv run python -m enrichment.enrich

# Run RAG tests (grounding control verification)
uv run python -m rag.rag

# Start web UI
uv run python -m rag.server
# → http://localhost:8000
```

## Documentation

- [Methodology](docs/methodology.md) — inclusion standard, pipeline
  description, source-mix analysis, known blind spots
- [Pivot Log](docs/pivot_log.md) — 6 architectural pivots with
  reasoning (officer filter, ADV brochure gap, units bug, etc.)
- [Example Records](docs/example_records.md) — 3 fully-worked records
  with complete chain-of-evidence across all confidence tiers
- [RAG / Stack Doc](docs/rag_stack.md) — embedding/retrieval
  architecture, grounding control design, real test queries

## Known blind spots

1. **Source-mix imbalance (86/14).** SEC EDGAR's 13F channel produces
   few independently-qualifiable candidates (6 of 706). The 990-PF
   channel dominates the final output. This is documented honestly
   rather than fixed by weakening evidence standards.
2. **ADV brochure text inaccessible.** IAPD API gives structured
   metadata but not brochure text. Item 5.D (family office services)
   would strengthen SEC EDGAR verification but isn't available via
   public API (Pivot 6).
3. **Contact fields unverified.** Email, phone, website, LinkedIn are
   all `could_not_verify` — not in public tax/SEC filings. Would
   require website scrape or LinkedIn search (deferred).
4. **2 channels, not 3+.** The assessment rules suggest 3+ channels.
   We explicitly chose 2 for time management, documented the tension.
