"""
Offline embedding script — run locally to pre-compute record embeddings.

This embeds all enriched records using fastembed (ONNX runtime, ~100MB
resident) and saves the resulting vectors to a static .npy file. The
deployed app loads this .npy instead of re-embedding the dataset, so
the cloud instance only needs to embed the incoming query string at
request time — dramatically reducing memory and compute.

Usage:
    uv run python -m rag.embed_offline

Output:
    data/discovery/record_embeddings.npy  (N x D float32 array)
    data/discovery/record_embeddings.meta.json  (model name, dim, count)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

# Must match the model used at query time in rag.py
EMBED_MODEL = "BAAI/bge-small-en-v1.5"


def _build_record_text(record: dict) -> str:
    """Build searchable text from a record for embedding.

    Mirrors rag._build_record_text — kept in sync manually to avoid
    a circular import (rag.py loads the .npy this script produces).
    """
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


def main():
    project_root = Path(__file__).resolve().parent.parent
    records_path = project_root / "data" / "discovery" / "final_enriched.jsonl"
    out_npy = project_root / "data" / "discovery" / "record_embeddings.npy"
    out_meta = project_root / "data" / "discovery" / "record_embeddings.meta.json"

    with records_path.open() as f:
        records = [json.loads(line) for line in f]
    print(f"Loaded {len(records)} records from {records_path}")

    texts = [_build_record_text(r) for r in records]
    print(f"Built {len(texts)} text blobs")
    for i, t in enumerate(texts[:3]):
        print(f"  [{i}] {t[:100]}...")

    print(f"\nLoading fastembed model: {EMBED_MODEL}")
    model = TextEmbedding(model_name=EMBED_MODEL)

    print("Embedding records...")
    embeddings = np.array(list(model.embed(texts)), dtype=np.float32)
    print(f"Embeddings shape: {embeddings.shape}, dtype: {embeddings.dtype}")

    # L2-normalize so cosine similarity = dot product at query time
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embeddings = embeddings / norms

    np.save(out_npy, embeddings)
    print(f"Saved embeddings to {out_npy}")

    meta = {
        "model": EMBED_MODEL,
        "dim": int(embeddings.shape[1]),
        "count": int(embeddings.shape[0]),
        "normalized": True,
    }
    with out_meta.open("w") as f:
        json.dump(meta, f, indent=2)
    print(f"Saved metadata to {out_meta}")

    # Quick sanity check: self-similarity
    sims = embeddings @ embeddings.T
    print(f"\nSanity check — diagonal (self-sim) min/max: "
          f"{sims.diagonal().min():.4f} / {sims.diagonal().max():.4f}")
    off_diag = sims[~np.eye(len(records), dtype=bool)]
    print(f"Off-diagonal similarity min/max/mean: "
          f"{off_diag.min():.4f} / {off_diag.max():.4f} / {off_diag.mean():.4f}")


if __name__ == "__main__":
    main()
