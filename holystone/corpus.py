"""Corpus operations: embed, recall, eval.

embed  - chunk files, embed as passages, upsert into hs_chunks
recall - embed the question as a query, show vector and FTS results
         side by side (the comparison is the point right now)
eval   - run a query set from eval/queries.yaml through both methods
         and score hits; this is the gate that decides whether pgvector
         earns a permanent slot or FTS stands alone
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from . import db
from .chunking import split_chunks
from .openrouter import OpenRouter


def _embed_model() -> str:
    return os.environ.get(
        "HOLYSTONE_EMBED_MODEL", "nvidia/llama-nemotron-embed-vl-1b-v2:free"
    )


def embed_files(
    paths: list[Path],
    project_id: str,
    session_label: str | None = None,
    in_fiction_date: str | None = None,
) -> None:
    client = OpenRouter()
    model = _embed_model()
    chunk_chars = int(os.environ.get("HOLYSTONE_CHUNK_CHARS", "6000"))

    with db.connect() as conn:
        db.init_schema(conn)
        for path in paths:
            label = session_label or (path.stem.replace(".condensed", "")
                                       .replace(".repaired", "").replace(".stripped", ""))
            text = path.read_text(encoding="utf-8", errors="replace")
            chunks = split_chunks(text, max_chars=chunk_chars)
            print(f"[embed] {path.name}: {len(chunks)} chunks as '{project_id}/{label}'")

            vectors = client.embed(chunks, model=model, input_type="passage")
            db.ensure_vector_column(conn, dim=len(vectors[0]))
            db.upsert_chunks(
                conn, project_id, label, chunks, vectors,
                source_file=path.name, in_fiction_date=in_fiction_date,
            )


def _print_results(title: str, results: list[dict], preview: int = 320) -> None:
    print(f"\n--- {title} ---")
    if not results:
        print("  (no results)")
    for r in results:
        snippet = " ".join(r["content"].split())[:preview]
        print(f"  [{r['session']}#{r['chunk']}] score={r['score']:.4f}")
        print(f"    {snippet}...")


def search(
    query: str,
    project_id: str,
    k: int = 5,
    semantic: bool = True,
    lexical: bool = True,
) -> dict:
    """Run vector and/or full-text recall and return the hits — no printing.
    Shared by the CLI `recall` command and the MCP server."""
    result: dict = {"vector": [], "fts": []}
    with db.connect() as conn:
        if lexical:
            result["fts"] = db.fts_search(conn, project_id, query, k=k)
        if semantic:
            client = OpenRouter()
            qvec = client.embed([query], model=_embed_model(), input_type="query")[0]
            result["vector"] = db.vector_search(conn, project_id, qvec, k=k)
    return result


def recall(query: str, project_id: str, k: int = 5) -> None:
    data = search(query, project_id, k=k)
    print(f"\n[recall] {query!r} in project '{project_id}'")
    _print_results("vector", data["vector"])
    _print_results("full-text", data["fts"])


def run_eval(project_id: str, queries_path: Path, k: int = 5) -> None:
    items = yaml.safe_load(queries_path.read_text(encoding="utf-8")) or []
    if not items:
        raise RuntimeError(f"No queries in {queries_path}.")

    client = OpenRouter()
    model = _embed_model()
    scored = [i for i in items if i.get("expect")]
    vec_hits_total = fts_hits_total = 0

    with db.connect() as conn:
        for item in items:
            query = item["query"]
            expect = (item.get("expect") or "").casefold()
            qvec = client.embed([query], model=model, input_type="query")[0]
            vec_hits = db.vector_search(conn, project_id, qvec, k=k)
            fts_hits = db.fts_search(conn, project_id, query, k=k)

            print(f"\n==== {query} ====")
            _print_results("vector", vec_hits, preview=200)
            _print_results("full-text", fts_hits, preview=200)

            if expect:
                vec_hit = any(expect in r["content"].casefold() for r in vec_hits)
                fts_hit = any(expect in r["content"].casefold() for r in fts_hits)
                vec_hits_total += vec_hit
                fts_hits_total += fts_hit
                print(f"  expect={item['expect']!r}: "
                      f"vector {'HIT' if vec_hit else 'miss'} | "
                      f"full-text {'HIT' if fts_hit else 'miss'}")

    if scored:
        print(f"\n[eval] scored queries: {len(scored)} | "
              f"vector hit@{k}: {vec_hits_total}/{len(scored)} | "
              f"full-text hit@{k}: {fts_hits_total}/{len(scored)}")
        print("[eval] vector earns pgvector only if it visibly beats full-text here.")
