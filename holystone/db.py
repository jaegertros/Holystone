"""Postgres / Supabase layer.

One table, hs_chunks, scoped by project_id like everything else in the
shared database. The embedding column is created on first embed with
whatever dimension the model actually returns — no guessing the dim up
front. FTS comes free from a generated tsvector column.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "001_init.sql"


def connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    return psycopg.connect(url)


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute(MIGRATION.read_text(encoding="utf-8"))
    conn.commit()
    print("[db] schema ensured (hs_chunks + FTS index)")


def ensure_vector_column(conn: psycopg.Connection, dim: int) -> None:
    row = conn.execute(
        """select atttypmod from pg_attribute
           where attrelid = 'hs_chunks'::regclass and attname = 'embedding'"""
    ).fetchone()
    if row is None:
        conn.execute(f"alter table hs_chunks add column embedding vector({int(dim)})")
        conn.commit()
        print(f"[db] added embedding column vector({dim})")
    else:
        existing = row[0]
        if existing not in (-1, dim):
            raise RuntimeError(
                f"hs_chunks.embedding is vector({existing}) but the model returned "
                f"dim {dim}. Different embedder? Re-embed the corpus or use a new table."
            )


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def upsert_chunks(
    conn: psycopg.Connection,
    project_id: str,
    session_label: str,
    chunks: list[str],
    vectors: list[list[float]],
    source_file: str | None = None,
    in_fiction_date: str | None = None,
) -> None:
    with conn.cursor() as cur:
        for idx, (content, vec) in enumerate(zip(chunks, vectors)):
            cur.execute(
                """insert into hs_chunks
                       (project_id, session_label, chunk_index, source_file,
                        in_fiction_date, content, embedding)
                   values (%s, %s, %s, %s, %s, %s, %s::vector)
                   on conflict (project_id, session_label, chunk_index)
                   do update set content = excluded.content,
                                 embedding = excluded.embedding,
                                 source_file = excluded.source_file,
                                 in_fiction_date = excluded.in_fiction_date""",
                (project_id, session_label, idx, source_file,
                 in_fiction_date, content, _vec_literal(vec)),
            )
    conn.commit()
    print(f"[db] upserted {len(chunks)} chunks for {project_id}/{session_label}")


def fts_search(conn: psycopg.Connection, project_id: str, query: str, k: int = 5) -> list[dict]:
    rows = conn.execute(
        """select session_label, chunk_index, content, in_fiction_date,
                  ts_rank(content_tsv, websearch_to_tsquery('english', %s)) as score
           from hs_chunks
           where project_id = %s
             and content_tsv @@ websearch_to_tsquery('english', %s)
           order by score desc
           limit %s""",
        (query, project_id, query, k),
    ).fetchall()
    return [
        {"session": r[0], "chunk": r[1], "content": r[2],
         "in_fiction_date": r[3], "score": float(r[4])}
        for r in rows
    ]


def vector_search(conn: psycopg.Connection, project_id: str, vec: list[float], k: int = 5) -> list[dict]:
    rows = conn.execute(
        """select session_label, chunk_index, content, in_fiction_date,
                  embedding <=> %s::vector as distance
           from hs_chunks
           where project_id = %s and embedding is not null
           order by distance asc
           limit %s""",
        (_vec_literal(vec), project_id, k),
    ).fetchall()
    return [
        {"session": r[0], "chunk": r[1], "content": r[2],
         "in_fiction_date": r[3], "score": float(r[4])}
        for r in rows
    ]


def list_sessions(conn: psycopg.Connection, project_id: str) -> list[dict]:
    rows = conn.execute(
        """select session_label, count(*),
                  min(in_fiction_date), max(in_fiction_date)
           from hs_chunks
           where project_id = %s
           group by session_label
           order by session_label""",
        (project_id,),
    ).fetchall()
    return [
        {"session": r[0], "chunks": r[1], "date_from": r[2], "date_to": r[3]}
        for r in rows
    ]
