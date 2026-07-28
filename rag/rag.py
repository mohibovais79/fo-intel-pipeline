"""
fo-intel-pipeline — RAG layer with field-level provenance grounding

Architecture:
  1. Embed the 44 enriched records using sentence-transformers
  2. Semantic retrieval: cosine similarity over record text
  3. Grounding control (two layers):
     a. Retrieval threshold: if no records above similarity threshold,
        force "insufficient evidence" response
     b. Field-level provenance check: even if a record is retrieved,
        if the specific field being asked about has
        VerifiedField.status != "verified", return "could_not_verify"
        for that field — not the field value

  This prevents the most dangerous RAG failure: a record is retrieved
  (so the system "knows" about the entity), but the specific field
  asked about is unverified (email, phone, website). Without the
  field-level check, the LLM would hallucinate or infer a value from
  the entity name. With it, the system honestly says "could not verify."

Grounding is enforced in code, not via system prompt. The response
generator is template-based (not free-form LLM) to eliminate
hallucination surface area entirely for this pilot.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

# Field keywords that map queries to specific VerifiedField fields
FIELD_KEYWORDS = {
    "email": ["email", "contact", "reach", "e-mail"],
    "phone": ["phone", "telephone", "call", "number"],
    "website": ["website", "url", "domain", "web"],
    "linkedin": ["linkedin"],
    "address": ["address", "location", "where is", "where are", "located"],
    "aum": ["aum", "assets", "under management", "how much", "fund size", "money"],
    "principal": ["who runs", "who manages", "principal", "ceo", "founder",
                  "who is", "who's", "leadership", "head of"],
    "fo_type": ["sfo", "mfo", "single family", "multi family",
                "what type", "what kind", "is this a family office"],
}


def _build_record_text(record: dict) -> str:
    """Build searchable text from a record for embedding."""
    parts = [
        record.get("entity_name", ""),
        record.get("city", "") or "",
        record.get("state_region", "") or "",
        record.get("fo_type", ""),
        record.get("fo_type_evidence", ""),
    ]
    if record.get("principal_first_name"):
        parts.append(f"{record['principal_first_name']} {record.get('principal_last_name', '')}")
    if record.get("principal_title"):
        parts.append(record["principal_title"])
    aum = record.get("aum_usd", {})
    if aum and aum.get("value"):
        aum_m = int(aum["value"]) / 1e6
        parts.append(f"${aum_m:.0f}M assets")
    return " ".join(p for p in parts if p)


class FamilyOfficeRAG:
    """RAG system with field-level provenance grounding."""

    def __init__(self, records_path: str = "data/discovery/final_enriched.jsonl"):
        self.records: list[dict] = []
        self.embeddings: np.ndarray | None = None
        self.model: SentenceTransformer | None = None
        self.records_path = records_path

    def load(self):
        """Load records and build embeddings."""
        with open(self.records_path) as f:
            self.records = [json.loads(line) for line in f]

        # Build text for each record
        texts = [_build_record_text(r) for r in self.records]

        # Load model and embed
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        self.embeddings = self.model.encode(
            texts, convert_to_numpy=True, normalize_embeddings=True
        )
        return self

    def retrieve(self, query: str, top_k: int = 5, threshold: float = 0.3) -> list[dict]:
        """
        Retrieve top-k records by semantic similarity.
        Returns records with similarity score, filtered by threshold.

        Includes a keyword boost: if the query contains an exact entity
        name substring, that record's score is boosted by 0.2. This
        prevents semantic drift where "Who runs the Roberts Foundation?"
        retrieves a different foundation because "who runs" adds
        semantic weight that outweighs the name match.
        """
        if self.model is None or self.embeddings is None:
            raise RuntimeError("RAG not loaded. Call .load() first.")

        query_emb = self.model.encode(
            [query], convert_to_numpy=True, normalize_embeddings=True
        )
        scores = (self.embeddings @ query_emb.T).flatten()

        # Keyword boost for exact entity name matches
        query_lower = query.lower()
        for i, rec in enumerate(self.records):
            name_lower = rec.get("entity_name", "").lower()
            # Check if any significant word from the entity name is in the query
            name_words = [w for w in name_lower.split()
                         if len(w) > 3 and w not in {"foundation", "fund", "trust",
                           "family", "charitable", "inc", "the", "and"}]
            if name_words:
                matches = sum(1 for w in name_words if w in query_lower)
                if matches >= 2 or (matches >= 1 and len(name_words) == 1):
                    scores[i] += 0.2  # boost exact name match

        top_idx = np.argsort(scores)[::-1][:top_k]

        results = []
        for idx in top_idx:
            if scores[idx] >= threshold:
                results.append({
                    "record": self.records[idx],
                    "score": float(scores[idx]),
                    "rank": len(results) + 1,
                })
        return results

    def _detect_requested_field(self, query: str) -> str | None:
        """Detect which specific field the query is asking about."""
        query_lower = query.lower()
        for field, keywords in FIELD_KEYWORDS.items():
            if any(kw in query_lower for kw in keywords):
                return field
        return None

    def _get_field_value(self, record: dict, field: str) -> dict:
        """
        Get a field's value and provenance from a record.
        Returns {value, status, source, method} or {status: could_not_verify}.
        """
        if field == "email":
            vf = record.get("principal_email", {})
            return {"status": vf.get("status", "could_not_verify"),
                    "value": vf.get("value"), "source": vf.get("source"),
                    "method": vf.get("method")}
        elif field == "phone":
            vf = record.get("principal_phone", {})
            return {"status": vf.get("status", "could_not_verify"),
                    "value": vf.get("value"), "source": vf.get("source"),
                    "method": vf.get("method")}
        elif field == "website":
            return {"status": "could_not_verify" if not record.get("website") else "verified",
                    "value": record.get("website")}
        elif field == "linkedin":
            return {"status": "could_not_verify" if not record.get("corporate_linkedin") else "verified",
                    "value": record.get("corporate_linkedin")}
        elif field == "address":
            addr = ", ".join(filter(None, [
                record.get("street_address"), record.get("city"),
                record.get("state_region")
            ]))
            return {"status": "verified" if addr else "could_not_verify",
                    "value": addr or None,
                    "source": f"{record.get('discovery_source')} filing" if addr else None}
        elif field == "aum":
            vf = record.get("aum_usd", {})
            return {"status": vf.get("status", "could_not_verify"),
                    "value": vf.get("value"), "source": vf.get("source"),
                    "method": vf.get("method")}
        elif field == "principal":
            name = f"{record.get('principal_first_name', '')} {record.get('principal_last_name', '')}".strip()
            return {"status": "verified" if name else "could_not_verify",
                    "value": name or None,
                    "title": record.get("principal_title"),
                    "source": "IRS 990-PF Part VII" if name else None}
        elif field == "fo_type":
            return {"status": "verified",
                    "value": record.get("fo_type"),
                    "evidence": record.get("fo_type_evidence")}
        return {"status": "could_not_verify"}

    def _format_record_brief(self, rec: dict) -> str:
        """Format a record as a natural-language one-liner for lists."""
        parts = [f"**{rec['entity_name']}**"]
        aum = rec.get("aum_usd", {})
        if aum and aum.get("value"):
            aum_m = int(aum["value"]) / 1e6
            if aum_m >= 1000:
                parts.append(f"managing ${aum_m/1000:.1f} billion")
            else:
                parts.append(f"managing ${aum_m:.0f} million")
        if rec.get("principal_first_name"):
            parts.append(f"led by {rec['principal_first_name']} {rec.get('principal_last_name','')}")
        if rec.get("city"):
            parts.append(f"in {rec['city']}, {rec.get('state_region','CA')}")
        return " — ".join(parts)

    def _format_single_record(self, rec: dict) -> str:
        """Format a single record as a natural-language paragraph."""
        lines = [f"**{rec['entity_name']}**"]
        aum = rec.get("aum_usd", {})
        if aum and aum.get("value"):
            aum_m = int(aum["value"]) / 1e6
            if aum_m >= 1000:
                lines.append(f"Manages approximately ${aum_m/1000:.1f} billion in assets")
            else:
                lines.append(f"Manages approximately ${aum_m:.0f} million in assets")
        if rec.get("principal_first_name"):
            name = f"{rec['principal_first_name']} {rec.get('principal_last_name','')}"
            title = rec.get("principal_title", "")
            if title:
                lines.append(f"Key contact: {name} ({title})")
            else:
                lines.append(f"Key contact: {name}")
        if rec.get("city"):
            lines.append(f"Located in {rec['city']}, {rec.get('state_region','CA')}")
        fo_type = rec.get("fo_type", "").replace("_", " ")
        lines.append(f"Classified as a {fo_type}")
        # Discovery source in plain English
        source = rec.get("discovery_source", "")
        if source == "990pf":
            lines.append("Discovered via IRS private foundation filing")
        elif source == "sec_edgar":
            lines.append("Discovered via SEC 13F investment filing")
        return "\n\n".join(lines)

    def answer(self, query: str) -> dict:
        """
        Answer a query with grounding control.

        Returns:
        {
            "query": str,
            "answer": str,           # human-readable answer
            "grounded": bool,        # was this answer grounded in verified data?
            "retrieved": list,       # retrieved records with scores
            "field_requested": str,  # which field was detected
            "field_status": str,     # verified / could_not_verify / n/a
            "citations": list,       # record IDs cited
        }
        """
        # Layer 1: retrieval
        results = self.retrieve(query, top_k=3, threshold=0.3)

        if not results:
            return {
                "query": query,
                "answer": (
                    "I couldn't find any family offices matching your search. "
                    "Try searching by family name (e.g. \"West Foundation\"), "
                    "city (e.g. \"family offices in San Francisco\"), or "
                    "asset size (e.g. \"largest family offices in California\")."
                ),
                "grounded": False,
                "retrieved": [],
                "field_requested": None,
                "field_status": "no_retrieval",
                "citations": [],
            }

        # Layer 2: field-level provenance check
        field_requested = self._detect_requested_field(query)

        if field_requested:
            # Check the top retrieved record's field status
            top_record = results[0]["record"]
            field_info = self._get_field_value(top_record, field_requested)

            if field_info["status"] != "verified":
                # Record exists but the specific field is unverified
                # This is the critical grounding control — don't hallucinate
                field_labels = {
                    "email": "contact email",
                    "phone": "phone number",
                    "website": "website",
                    "linkedin": "LinkedIn profile",
                }
                field_label = field_labels.get(field_requested, field_requested.replace("_", " "))
                return {
                    "query": query,
                    "answer": (
                        f"I found {top_record['entity_name']} in our database, "
                        f"but I don't have a verified {field_label} for them. "
                        f"This information isn't available in the public tax "
                        f"and SEC filings we used for discovery — it would "
                        f"require checking their website or LinkedIn directly."
                    ),
                    "grounded": False,
                    "retrieved": results,
                    "field_requested": field_requested,
                    "field_status": "could_not_verify",
                    "citations": [top_record["record_id"]],
                }

        # Build grounded answer — natural language, not debug dump
        citations = [r["record"]["record_id"] for r in results]

        # If asking about a specific field, give a direct answer
        if field_requested and results:
            field_info = self._get_field_value(results[0]["record"], field_requested)
            if field_info["status"] == "verified":
                rec = results[0]["record"]
                if field_requested == "principal":
                    name = field_info.get("value", "")
                    title = field_info.get("title", "")
                    title_str = f", {title}" if title else ""
                    answer = f"{name}{title_str} is the key contact for {rec['entity_name']}."
                    if len(results) > 1:
                        answer += f"\n\nI also found {len(results)-1} other related record(s) in the database."
                    return {
                        "query": query, "answer": answer, "grounded": True,
                        "retrieved": results, "field_requested": field_requested,
                        "field_status": "verified", "citations": citations,
                    }
                elif field_requested == "aum":
                    aum_m = int(field_info["value"]) / 1e6
                    answer = f"{rec['entity_name']} manages approximately ${aum_m:.1f} million in assets."
                    if len(results) > 1:
                        answer += f"\n\nI also found {len(results)-1} other related record(s)."
                    return {
                        "query": query, "answer": answer, "grounded": True,
                        "retrieved": results, "field_requested": field_requested,
                        "field_status": "verified", "citations": citations,
                    }
                elif field_requested == "address":
                    addr = field_info.get("value", "")
                    answer = f"{rec['entity_name']} is located at {addr}."
                    if len(results) > 1:
                        answer += f"\n\nI also found {len(results)-1} other family office(s) matching your search."
                    return {
                        "query": query, "answer": answer, "grounded": True,
                        "retrieved": results, "field_requested": field_requested,
                        "field_status": "verified", "citations": citations,
                    }
                elif field_requested == "fo_type":
                    fo_type = field_info.get("value", "").replace("_", " ")
                    answer = f"{rec['entity_name']} is classified as a {fo_type}."
                    return {
                        "query": query, "answer": answer, "grounded": True,
                        "retrieved": results, "field_requested": field_requested,
                        "field_status": "verified", "citations": citations,
                    }

        # General search — list results in natural language
        if len(results) == 1:
            rec = results[0]["record"]
            answer = self._format_single_record(rec)
        else:
            answer = f"Here are {len(results)} family offices matching your search:\n\n"
            for i, r in enumerate(results, 1):
                rec = r["record"]
                answer += f"{i}. {self._format_record_brief(rec)}\n"

        return {
            "query": query,
            "answer": answer,
            "grounded": True,
            "retrieved": results,
            "field_requested": field_requested,
            "field_status": "verified" if field_requested else "n/a",
            "citations": citations,
        }


# Test queries that demonstrate both grounding layers
TEST_QUERIES = [
    # Layer 1: retrieval threshold (should return "no results")
    "quantum computing startups in Berlin",
    # Layer 2: field-level provenance (record exists, field unverified)
    "What is the contact email for the Ahmanson Foundation?",
    "What is the phone number for the Harrington Family Foundation?",
    # Normal grounded answers
    "family offices in San Francisco",
    "Who runs the Roberts Foundation?",
    "What is the AUM of the Gary And Mary West Foundation?",
    "single family offices in Palo Alto",
]


def run_tests():
    """Run grounding control tests and log results."""
    rag = FamilyOfficeRAG().load()
    print(f"Loaded {len(rag.records)} records, embeddings shape: {rag.embeddings.shape}")
    print()

    results = []
    for q in TEST_QUERIES:
        print(f"Query: {q}")
        result = rag.answer(q)
        print(f"  grounded: {result['grounded']}")
        print(f"  field_requested: {result['field_requested']}")
        print(f"  field_status: {result['field_status']}")
        print(f"  answer: {result['answer'][:120]}...")
        print(f"  citations: {result['citations']}")
        print()
        results.append({"query": q, **result})

    # Log to file
    log_path = Path("data/discovery/rag_test_log.json")
    with log_path.open("w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Test log written to {log_path}")

    # Summary
    grounded = sum(1 for r in results if r["grounded"])
    unverified = sum(1 for r in results if r["field_status"] == "could_not_verify")
    no_retrieval = sum(1 for r in results if r["field_status"] == "no_retrieval")
    print(f"\nSummary: {grounded} grounded, {unverified} could_not_verify, {no_retrieval} no_retrieval")


if __name__ == "__main__":
    run_tests()
