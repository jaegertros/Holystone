-- holystone schema. Run via Supabase apply_migration or psql.
-- The embedding column is added by `holystone embed` once the model's
-- vector dimension is known (read off the first response).

create extension if not exists vector;

create table if not exists hs_chunks (
    id              bigint generated always as identity primary key,
    project_id      text not null,
    session_label   text not null,
    chunk_index     int  not null,
    source_file     text,
    in_fiction_date text,
    content         text not null,
    content_tsv     tsvector generated always as (to_tsvector('english', content)) stored,
    created_at      timestamptz not null default now(),
    unique (project_id, session_label, chunk_index)
);

create index if not exists hs_chunks_tsv_idx on hs_chunks using gin (content_tsv);
create index if not exists hs_chunks_project_idx on hs_chunks (project_id);

-- No ANN index on purpose: at hundreds-to-low-thousands of rows per project,
-- exact scan is fast and exact. Add HNSW only if row counts earn it.
