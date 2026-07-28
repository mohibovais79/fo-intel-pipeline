# RAG / Stack Documentation

## Architecture

The RAG layer is intentionally simple — 44 records don't need a
vector database, an orchestration framework, or an LLM for generation.
The design prioritizes grounding control over generation flexibility.

```
User Query
    │
    ▼
┌──────────────────┐
│  Embedding Model │  sentence-transformers all-MiniLM-L6-v2
│  (384-dim)       │  Query → vector
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Retrieval       │  numpy cosine similarity
│  (cosine sim)    │  44 record embeddings @ query embedding
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Grounding Layer │  TWO checks:
│  1: Threshold    │  → score < 0.3 = "no results"
│  2: Field proven.│  → field.status != verified = "could not verify"
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Response Gen    │  Template-based (NOT LLM)
│  (templates)     │  Interpolates verified fields into natural language
└──────────────────┘
```

## Why no LLM for generation

The brief identifies hallucination as the most serious error type. A
free-form LLM (GPT-4, Claude) introduces hallucination surface area —
it can infer an email from an entity name, or guess a phone number,
even with a system prompt saying "don't."

Template-based generation **cannot hallucinate**. It can only output
values that exist in retrieved records' verified fields. If a field is
`could_not_verify`, the template outputs "could not verify" — it has
no mechanism to invent a value.

For a 44-record pilot with structured fields, templates produce
better, more consistent answers than an LLM anyway.

## Embedding model

- **Model:** `all-MiniLM-L6-v2` from sentence-transformers
- **Dimensions:** 384
- **Size:** ~90MB
- **Speed:** <100ms for single query embedding
- **No external API calls** — runs entirely locally

### Record text construction

Each record is embedded as a concatenation of searchable fields:

```python
entity_name + city + state + fo_type + fo_type_evidence +
principal_name + principal_title + "$X assets"
```

This gives the embedding model enough context to match on entity
name, location, principal, and asset size without exposing
unverified fields (email, phone) to the retrieval layer.

## Retrieval

- **Method:** numpy cosine similarity (normalized dot product)
- **Top-k:** 3 results
- **Threshold:** 0.30 (below = "no results")
- **Keyword boost:** +0.2 for exact entity name word matches
  (prevents semantic drift where "Who runs Roberts Foundation?"
  retrieves a different foundation because "who runs" adds weight)

No vector database needed — 44 × 384 matrix fits in memory and
similarity search is a single matrix multiplication.

## Grounding control (two layers)

### Layer 1: Retrieval threshold

If no records score above 0.30 similarity, the system returns an
"insufficient evidence" response with search suggestions. This
prevents the system from answering questions about entities not in
the database.

**Test query:** "quantum computing startups in Berlin"
**Result:** `field_status: no_retrieval`
**Response:** "I couldn't find any family offices matching your search.
Try searching by family name, city, or asset size."

### Layer 2: Field-level provenance check

This is the critical layer. Even if a record IS retrieved, the
specific field being asked about may be `could_not_verify`. The
system checks `VerifiedField.status` for the requested field, not
just whether a record was retrieved.

**Test query:** "What is the contact email for the Ahmanson Foundation?"
**Result:** Record retrieved (score 0.767), but `principal_email.status
= could_not_verify`
**Response:** "I found The Ahmanson Foundation in our database, but I
don't have a verified contact email for them. This information isn't
available in the public tax and SEC filings we used for discovery."

Without this layer, an LLM-based system would likely hallucinate an
email address from the entity name. The template-based system with
field-level provenance simply says "could not verify."

## Field detection

Queries are mapped to specific fields via keyword matching:

| Field | Keywords |
|-------|----------|
| email | email, contact, reach, e-mail |
| phone | phone, telephone, call, number |
| website | website, url, domain, web |
| address | address, location, where is, where are, located |
| aum | aum, assets, under management, how much, fund size, money |
| principal | who runs, who manages, principal, ceo, founder, leadership |
| fo_type | sfo, mfo, single family, multi family, what type |

If no field is detected, the system returns a general search result
listing matching records.

## Real test queries

Seven test queries covering all grounding states:

| Query | Grounding | Field | Status |
|-------|-----------|-------|--------|
| quantum computing startups in Berlin | False | — | no_retrieval |
| What is the contact email for the Ahmanson Foundation? | False | email | could_not_verify |
| What is the phone number for the Harrington Family Foundation? | False | phone | could_not_verify |
| family offices in San Francisco | True | — | n/a (list) |
| Who runs the Roberts Foundation? | True | principal | verified |
| What is the AUM of the Gary And Mary West Foundation? | True | aum | verified |
| single family offices in Palo Alto | True | fo_type | verified |

**Summary:** 4 grounded, 2 could_not_verify, 1 no_retrieval

Test log: `data/discovery/rag_test_log.json`

## Web UI

- **Framework:** FastAPI + Jinja2
- **Endpoint:** `GET /` (UI), `GET /api/search?q=...` (API)
- **Design:** Dark theme, Inter typography, animated transitions
- **Status badges:** Green (grounded), amber (could_not_verify), red (no results)
- **Provenance display:** Citation chips, retrieved records with match %
- **Failure states:** Read like a product, not debug output

## What an LLM would add (future work, not built)

- Entity/surname disambiguation ("is this the same Smith family?")
- ADV brochure classification (extract evidence quote from text)
- Contact synthesis (from website scrape + LinkedIn)
- Natural language answer rewriting (turn template output into prose)

These are enrichment-side LLM uses, not generation-side. The RAG
output layer stays template-based to preserve the grounding guarantee.
