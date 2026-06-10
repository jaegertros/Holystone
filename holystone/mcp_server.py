"""holystone recall, exposed to MCP clients.

This is the thin server that lets a Claude Project (or Claude Desktop /
Claude Code) reach the condensed, repaired, verbatim corpus during play.
It is the retrieval half of the drift fix: the narrator calls a tool, gets
back the actual past lines, and re-grounds voice and fact on the record
instead of on a drifting memory of it.

Three tools:

  recall(query, project)      semantic + lexical — "find the scene about X",
                              or re-ground a character's voice on real lines.
  find_quote(phrase, project) full-text only — best for an exact quote or a
                              proper noun ("what did she say about her brother").
  list_sessions(project)      what's indexed, with in-fiction date ranges.

Run it two ways:

  Local (Claude Desktop / Claude Code), stdio transport:
      holystone-mcp

  Remote (claude.ai custom connector needs a public HTTPS URL), HTTP transport:
      holystone-mcp --http --host 0.0.0.0 --port 8000
      # then expose :8000 over HTTPS (tunnel or deploy) and add that URL as a
      # custom connector. SECURITY: this endpoint queries your database and
      # returns your logs — never expose it unauthenticated. Put it behind a
      # tunnel/proxy that enforces auth, or keep to local stdio.

Reads OPENROUTER_API_KEY and DATABASE_URL from the environment / .env, same
as the rest of holystone.
"""

from __future__ import annotations

import argparse
import contextlib
import sys

from dotenv import load_dotenv

from . import corpus, db

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit(
        "The MCP server needs the 'mcp' package. Install it with:\n"
        "    pip install -e .[mcp]\n"
        "or: pip install 'mcp>=1.2'"
    ) from exc

load_dotenv()

mcp = FastMCP("holystone")


def _quiet():
    """Route holystone's own progress prints (e.g. '[embed] ...') to stderr.
    The stdio transport owns stdout for JSON-RPC; a stray print corrupts it."""
    return contextlib.redirect_stdout(sys.stderr)


def _merge(data: dict) -> list[dict]:
    """Merge vector + FTS hits, deduped by (session, chunk), recording which
    retrieval method surfaced each."""
    seen: dict[tuple, dict] = {}
    for method, tag in (("vector", "semantic"), ("fts", "lexical")):
        for r in data.get(method, []):
            key = (r["session"], r["chunk"])
            if key not in seen:
                seen[key] = {**r, "via": set()}
            seen[key]["via"].add(tag)
    return list(seen.values())


def _render(hits: list[dict]) -> str:
    parts: list[str] = []
    for r in hits:
        via = ", ".join(sorted(r.get("via", []))) or "match"
        date = f" — {r['in_fiction_date']}" if r.get("in_fiction_date") else ""
        parts.append(
            f"### {r['session']} #{r['chunk']}{date}  ({via})\n"
            f"{r['content'].strip()}"
        )
    return "\n\n".join(parts)


@mcp.tool()
def recall(query: str, project: str, k: int = 4) -> str:
    """Find past scenes or dialogue by meaning OR exact words.

    Use this to re-ground a character's voice on their real past lines
    before writing them, or to locate what was actually said or established
    about someone or something. Returns verbatim chunks from the condensed,
    fidelity-checked log.

    query: what you're looking for, in natural language or keywords.
    project: the campaign id the log was embedded under (e.g. "marauders").
    k: how many chunks to return per method (default 4).
    """
    with _quiet():
        data = corpus.search(query, project, k=k)
    hits = _merge(data)
    if not hits:
        return f"No matches for {query!r} in project {project!r}."
    return _render(hits)


@mcp.tool()
def find_quote(phrase: str, project: str, k: int = 5) -> str:
    """Find the exact scene where a phrase or proper noun appears.

    Full-text search only — the precise instrument for exact quotes and
    names ("what did she call her brother"). Returns verbatim log chunks.
    """
    with _quiet():
        data = corpus.search(phrase, project, k=k, semantic=False, lexical=True)
    hits = _merge(data)
    if not hits:
        return f"No exact match for {phrase!r} in project {project!r}."
    return _render(hits)


@mcp.tool()
def list_sessions(project: str) -> str:
    """List the indexed sessions for a project, with chunk counts and the
    in-fiction date range of each — so you know what's available to recall."""
    with _quiet(), db.connect() as conn:
        rows = db.list_sessions(conn, project)
    if not rows:
        return f"No sessions indexed for project {project!r}."
    lines = [f"Project {project!r} — {len(rows)} session(s):"]
    for r in rows:
        span = ""
        if r["date_from"] or r["date_to"]:
            span = f"  [{r['date_from']} … {r['date_to']}]"
        lines.append(f"  - {r['session']}: {r['chunks']} chunks{span}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(prog="holystone-mcp", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", action="store_true",
                    help="serve over streamable HTTP (for a remote custom connector) "
                         "instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
