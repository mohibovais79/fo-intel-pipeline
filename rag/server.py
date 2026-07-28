"""
fo-intel-pipeline — FastAPI web server for the RAG layer.

Serves the deployed UI at / and the search API at /api/search.
The RAG grounding control is enforced in rag.py, not in the API layer.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from rag.rag import FamilyOfficeRAG

app = FastAPI(title="Family Office Intelligence — CA Pilot")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Load RAG once at startup
_rag: FamilyOfficeRAG | None = None


def get_rag() -> FamilyOfficeRAG:
    global _rag
    if _rag is None:
        _rag = FamilyOfficeRAG().load()
    return _rag


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/search")
async def search(q: str):
    rag = get_rag()
    result = rag.answer(q)
    # Convert to JSON-serializable
    # Build citation info with human-readable source labels
    source_labels = {
        "990pf": "IRS 990-PF Filing",
        "sec_edgar": "SEC 13F Filing",
    }
    confidence_labels = {
        0.9: "High Confidence",
        0.75: "Medium Confidence",
        0.65: "Standard Confidence",
    }

    # Map record IDs to their display info for citations
    record_map = {r["record"]["record_id"]: r["record"] for r in result["retrieved"]}

    citations = []
    for cite_id in result["citations"]:
        rec = record_map.get(cite_id, {})
        source = rec.get("discovery_source", "")
        source_label = source_labels.get(source, source)
        entity = rec.get("entity_name", cite_id)
        citations.append({
            "id": cite_id,
            "entity": entity,
            "source": source_label,
        })

    return JSONResponse({
        "query": result["query"],
        "answer": result["answer"],
        "grounded": result["grounded"],
        "field_requested": result["field_requested"],
        "field_status": result["field_status"],
        "citations": citations,
        "retrieved": [
            {
                "score": r["score"],
                "rank": r["rank"],
                "record": {
                    "record_id": r["record"]["record_id"],
                    "entity_name": r["record"]["entity_name"],
                    "discovery_source": r["record"]["discovery_source"],
                    "discovery_source_label": source_labels.get(r["record"]["discovery_source"], ""),
                    "confidence_score": r["record"]["confidence_score"],
                    "confidence_label": confidence_labels.get(r["record"]["confidence_score"], ""),
                    "city": r["record"].get("city"),
                    "state_region": r["record"].get("state_region"),
                },
            }
            for r in result["retrieved"]
        ],
    })


@app.get("/api/records")
async def list_records():
    """List all 44 records (for browsing)."""
    rag = get_rag()
    return JSONResponse([
        {
            "record_id": r["record_id"],
            "entity_name": r["entity_name"],
            "discovery_source": r["discovery_source"],
            "fo_type": r["fo_type"],
            "confidence_score": r["confidence_score"],
            "city": r.get("city"),
            "state_region": r.get("state_region"),
            "aum_usd": r.get("aum_usd", {}).get("value"),
            "principal": f"{r.get('principal_first_name','') or ''} {r.get('principal_last_name','') or ''}".strip(),
        }
        for r in rag.records
    ])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
