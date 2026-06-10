# holystone

*The stone the morning watch used to scour the deck. Cheap hands, clean surface.*

A local pipeline for making long RP chat logs tolerable, durable, and searchable, using free-tier models for the unskilled labor. Three compression products, each lossy in a different dimension: the **stripped** log (mechanical noise only), the **condensed** log (beats in order, kept dialogue verbatim), and the **recall index** (FTS + vectors over the condensed text). Structured campaign state stays where it belongs — in your state tables; holystone is the texture layer underneath it.

```
raw export ──strip──▶ *.stripped.md ──condense──▶ *.condensed.md ──verify──┐
                                                                            │ altered quotes?
                                       *.repaired.md ◀──repair──────────────┘
                                           │  (verbatim by construction)
                                         embed ──▶ Postgres/Supabase
                                           │       (hs_chunks: text + tsvector + pgvector)
                                           │
                                   recall / eval  (vector vs full-text, side by side)
```

## Stages

- **strip** — deterministic, no model, stdlib only. Auto-detects the export shape: **AI Exporter** files (strips OOC + full-bracket meta) and **claude.ai** files (`# you asked` / `# claude response`, `▼` scene headers — compresses `[Tracker:]` to date+place, drops `[Inventory:]`, and *keeps* `[[OOC]]` / `*[Narrator note]*` as the deliberate-meta correction record). Both normalize curly quotes once so every downstream stage sees one canonical text, and both emit `[PLAYER]` / `[NARRATOR]` turn markers. Prose and dialogue pass through untouched.
- **condense** — the extractive second-model pass. Beats compress; kept dialogue is copied character-for-character. The contract lives in `prompts/condenser.md`. Free chat models are safe for this job *because the contract is machine-checkable* — which is the next stage.
- **verify** — every double-quoted span in the condensed output must string-match the source. This works whether dialogue is embedded in prose (`"..." she said`) or attributed (`**Name:** "..."`) — the quotation marks are the verbatim promise either way. Altered or invented quotes print with a diff against the closest source quote and **fail** the gate (nonzero exit). Quotes that are verbatim but *locally out of source order* are a warning by default (harmless for recall); `--strict` makes them fail too. In practice no free model copies perfectly — which is what the next stage is for.
- **repair** — deterministic, no model. Takes verify's finding to its conclusion: each altered quote is **snapped back to its exact source span**, and quotes with no source match (fabrications) are dropped, their meaning left to the surrounding beat. The output is *verbatim by construction* — run `verify` on a `*.repaired.md` and it passes, whatever model did the condensing. This is what makes a free condenser trustworthy: you don't hope the model behaved, you mechanically fix what it got wrong.
- **embed / recall / eval** — chunk at scene breaks, embed as `passage`, upsert; questions embed as `query`. `recall` and `eval` always show vector and full-text results side by side: the comparison is the point. pgvector earns a permanent slot only if it visibly beats FTS on your own query set.

## Quickstart

```bash
pip install -e .
cp .env.example .env        # fill in OPENROUTER_API_KEY, DATABASE_URL, condenser model

# 1. Clean (works offline, try it on the bundled sample)
holystone strip examples/sample_raw.md -o out/

# 2. Condense, verify, repair (repair makes it verbatim by construction)
holystone condense out/sample_raw.stripped.md -o out/
holystone verify  out/sample_raw.condensed.md --source out/sample_raw.stripped.md
holystone repair  out/sample_raw.condensed.md --source out/sample_raw.stripped.md -o out/
holystone verify  out/sample_raw.repaired.md  --source out/sample_raw.stripped.md   # PASS

# 3. Index + recall (embed the repaired text)
holystone init-db
holystone embed out/sample_raw.repaired.md --project vault49 --session sample
holystone recall "what did Mott say about the registry" --project vault49

# 4. The gate: fill eval/queries.yaml with ~20 real questions, then
holystone eval --project vault49
```

PDF exports: convert first (`pdftotext -layout session.pdf session.txt`), then strip.

## Use it live: recall inside a Claude Project

The point of the index isn't to read it yourself — it's for the narrator to reach during play, so a character's voice and the facts of record come from the actual log instead of a drifting memory of it. `holystone-mcp` exposes recall as an MCP server with three tools:

| Tool | For |
|---|---|
| `recall(query, project)` | semantic + lexical — "find the scene about X", or re-ground a character's voice on their real past lines before writing them |
| `find_quote(phrase, project)` | full-text only — the precise instrument for an exact quote or a proper noun ("what did she call her brother") |
| `list_sessions(project)` | what's indexed, with in-fiction date ranges |

Because the corpus is the **repaired** log, the lines it returns are verbatim — so grounding on them pulls voice toward the record, never toward paraphrase.

**Local — Claude Desktop / Claude Code (stdio, zero deployment):**

```bash
pip install -e .[mcp]
# Claude Code:
claude mcp add holystone -- holystone-mcp
# Claude Desktop: add to claude_desktop_config.json ->
#   "mcpServers": { "holystone": { "command": "holystone-mcp" } }
```

**Remote — claude.ai custom connector (needs a public HTTPS URL):**

```bash
holystone-mcp --http --host 0.0.0.0 --port 8000
# expose :8000 over HTTPS (a tunnel like cloudflared, or a deploy), then add
# that URL as a custom connector in the Project's settings.
```

> ⚠️ **Security.** The HTTP endpoint queries your database and returns your logs. Never expose it unauthenticated — put it behind a tunnel/proxy that enforces auth, or stick to local stdio. It reads `OPENROUTER_API_KEY` and `DATABASE_URL` from the environment / `.env` like the rest of holystone.

A recall tool the narrator never calls is dead weight: post-hoc grounding (between sessions) is more reliable than hoping for an in-play call, so lean on it there first.

### Online — REST surface for a Claude artifact (claude.ai web)

Claude.ai web Projects can't run a local stdio server, and a true custom connector wants OAuth. The lighter path: serve the same tools over HTTP so a **Claude artifact** (client-side JS, runs from Anthropic's origin) can `fetch` them.

```bash
HOLYSTONE_REST_TOKEN=$(openssl rand -hex 16) \
  holystone-mcp --http --host 0.0.0.0 --port 8000
# then expose :8000 over HTTPS — a tunnel (cloudflared/ngrok) or a deploy.
```

`--http` mounts, alongside the MCP streamable endpoint:

```
POST /rest/recall        {"project":"marauders","query":"...","k":4}  -> {"ok":true,"result":"..."}
POST /rest/find_quote    {"project":"marauders","phrase":"...","k":5}
POST /rest/list_sessions {"project":"marauders"}
GET  /health
```

Every POST needs `Authorization: Bearer $HOLYSTONE_REST_TOKEN` (CORS is open so the artifact can reach it; the token is what protects the data). `artifacts/recall.html` is a ready-made console for this surface — open it in a browser or paste it into a claude.ai HTML artifact, set the URL/token/project once, and search. (This mirrors the narrator-state tracker's `api.ts` connected-mode client, so one artifact pattern serves both.)

> The artifact route means *the artifact* calls recall, not the model itself. Great for a search box or for pre-loading voice anchors the model then reads; for the model to call recall mid-prose on its own, use stdio (Desktop/Code) or a real OAuth connector.

### One endpoint for state *and* recall

If you already run a narrator-state (or other) FastMCP server, mount holystone's recall tools onto it instead of running a second process — one URL, one token:

```python
# in your narrator-state launcher, before mcp.run(...)
from holystone.mcp_server import register as register_recall
register_recall(server.mcp)          # adds recall / find_quote / list_sessions
```

`register(mcp)` attaches the three tools to any FastMCP instance; they reach the same `DATABASE_URL` corpus. Your existing REST patch then exposes them at `/rest/recall` automatically.

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
