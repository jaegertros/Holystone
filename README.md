# holystone

*The stone the morning watch used to scour the deck. Cheap hands, clean surface.*

A local pipeline for making long RP chat logs tolerable, durable, and searchable, using free-tier models for the unskilled labor. Three compression products, each lossy in a different dimension: the **stripped** log (mechanical noise only), the **condensed** log (beats in order, kept dialogue verbatim), and the **recall index** (FTS + vectors over the condensed text). Structured campaign state stays where it belongs — in your state tables; holystone is the texture layer underneath it.

```
raw export ──strip──▶ *.stripped.md ──condense──▶ *.condensed.md ──verify──▶ ✓
                                           │
                                         embed ──▶ Postgres/Supabase
                                           │       (hs_chunks: text + tsvector + pgvector)
                                           │
                                   recall / eval  (vector vs full-text, side by side)
```

## Stages

- **strip** — deterministic, no model, stdlib only. Removes exporter page artifacts, OOC blocks, tracker mirror lines, duplicate regenerated paragraphs; normalizes curly quotes once so every downstream stage sees one canonical text; marks `[PLAYER]` / `[NARRATOR]` turns in export-shaped files. Dialogue passes through untouched.
- **condense** — the extractive second-model pass. Beats compress; kept dialogue is copied character-for-character. The contract lives in `prompts/condenser.md`. Free chat models are safe for this job *because the contract is machine-checkable* — which is the next stage.
- **verify** — every `**Name:** "line"` in the condensed output must string-match the source, in source order. Violations print with a diff against the closest source line. Nonzero exit on failure, so it can gate a pipeline. A condensation that fails verify is corrupted, not condensed.
- **embed / recall / eval** — chunk at scene breaks, embed as `passage`, upsert; questions embed as `query`. `recall` and `eval` always show vector and full-text results side by side: the comparison is the point. pgvector earns a permanent slot only if it visibly beats FTS on your own query set.

## Quickstart

```bash
pip install -e .
cp .env.example .env        # fill in OPENROUTER_API_KEY, DATABASE_URL, condenser model

# 1. Clean (works offline, try it on the bundled sample)
holystone strip examples/sample_raw.md -o out/

# 2. Condense + verify
holystone condense out/sample_raw.stripped.md -o out/
holystone verify out/sample_raw.condensed.md --source out/sample_raw.stripped.md

# 3. Index + recall
holystone init-db
holystone embed out/sample_raw.condensed.md --project vault49 --session sample
holystone recall "what did Mott say about the registry" --project vault49

# 4. The gate: fill eval/queries.yaml with ~20 real questions, then
holystone eval --project vault49
```

PDF exports: convert first (`pdftotext -layout session.pdf session.txt`), then strip.

## Environment

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | for condense / embed / recall / eval |
| `DATABASE_URL` | Postgres connection string (Supabase session pooler works) |
| `HOLYSTONE_EMBED_MODEL` | default `nvidia/llama-nemotron-embed-vl-1b-v2:free` |
| `HOLYSTONE_EMBED_INPUT_TYPE_MODE` | `param` \| `prefix` \| `none` — see below |
| `HOLYSTONE_CONDENSER_MODEL` | any current `:free` chat model |
| `HOLYSTONE_CHUNK_CHARS` / `HOLYSTONE_CONDENSE_WINDOW_CHARS` | chunk sizing |

## Caveats worth knowing

- **Free endpoints and your logs.** Free OpenRouter endpoints often carry provider data-logging policies. Check the endpoint's policy on the model page before sending logs you care about; the pipeline doesn't depend on any one provider.
- **Asymmetric embedder.** The default model distinguishes `query` from `passage` embeddings. NIM-style providers take it as a request parameter (`param` mode); if the endpoint rejects or ignores it, switch to `prefix` mode. Mixing modes between corpus and queries silently degrades retrieval — re-embed if you change it.
- **Vector dimension is auto-detected.** The embedding column is added on first `embed` with whatever dimension the model returns. Switching embedders against an existing corpus errors on purpose: corpus and query vectors must come from the same model.
- **Lock-in is soft by design.** The default embedder is open-weights (self-hostable at 1B), and the corpus is condensed text — re-embedding the whole thing with a replacement model costs minutes and pennies. That only stays true if you embed condensed logs, not raw ones.
- **Rate limits.** Free-tier caps are handled with request batching and backoff-with-retry; for big backfills, raise `HOLYSTONE_EMBED_BATCH_SIZE` so daily request caps go further.
- **No ANN index on purpose.** At hundreds-to-low-thousands of chunks per project, exact scan is fast and exact. Add HNSW when row counts earn it.
