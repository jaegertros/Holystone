"""REST + CORS wrapper for a holystone FastMCP server.

Mounts a thin REST surface alongside the standard MCP streamable-http
endpoint, so a Claude *artifact* (which runs from an opaque Anthropic
origin and can't speak MCP) can reach recall over plain `fetch`:

    POST   /rest/{tool_name}    JSON body in, JSON out -> {"result": ...}
    OPTIONS /rest/{tool_name}   CORS preflight
    GET    /health              liveness + tool list

This mirrors the Allegheny narrator-state `server_rest_patch.py` so the
two servers speak the same wire protocol and a single artifact client can
talk to either. Apply it before `mcp.run(transport="streamable-http")`:

    from holystone import rest_patch
    rest_patch.apply(mcp)

Auth: if HOLYSTONE_REST_TOKEN is set, every POST must carry
`Authorization: Bearer <token>` (or `X-Holystone-Token`) or it gets a 401.
SECURITY: this queries your database and returns your logs — set the token
and serve over HTTPS; never expose it open to the internet.
"""

from __future__ import annotations

import inspect
import json
import os
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS, GET",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Holystone-Token",
    "Access-Control-Max-Age": "600",
}


def _cors(response: Response) -> Response:
    for k, v in _CORS_HEADERS.items():
        response.headers[k] = v
    return response


def _discover_tools(mcp) -> dict[str, Callable[..., Any]]:
    """name -> undecorated callable, pulled from the FastMCP tool manager."""
    tools: dict[str, Callable[..., Any]] = {}
    tm = getattr(mcp, "_tool_manager", None)
    if tm is not None and hasattr(tm, "_tools"):
        for name, tool in tm._tools.items():
            fn = getattr(tool, "fn", None)
            if callable(fn):
                tools[name] = fn
    return tools


def _coerce_args(fn: Callable[..., Any], payload: dict) -> dict:
    accepted = set(inspect.signature(fn).parameters)
    return {k: v for k, v in payload.items() if k in accepted}


def apply(mcp) -> list[str]:
    """Mount /rest/{tool}, /health onto the given FastMCP instance.
    Returns the list of exposed tool names."""
    tool_map = _discover_tools(mcp)
    token = os.environ.get("HOLYSTONE_REST_TOKEN", "").strip() or None

    def _authorized(request: Request) -> bool:
        if not token:
            return True
        auth = request.headers.get("Authorization", "")
        bearer = auth[7:].strip() if auth.startswith("Bearer ") else ""
        return bearer == token or request.headers.get("X-Holystone-Token", "") == token

    async def dispatch(request: Request) -> Response:
        if not _authorized(request):
            return _cors(JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401))
        name = request.path_params.get("tool_name", "")
        fn = tool_map.get(name)
        if fn is None:
            return _cors(JSONResponse(
                {"ok": False, "error": f"unknown tool: {name!r}", "available": sorted(tool_map)},
                status_code=404))
        try:
            raw = await request.body()
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            return _cors(JSONResponse({"ok": False, "error": f"invalid JSON: {e}"}, status_code=400))
        if not isinstance(payload, dict):
            return _cors(JSONResponse({"ok": False, "error": "body must be a JSON object"}, status_code=400))
        try:
            result = fn(**_coerce_args(fn, payload))
        except TypeError as e:
            return _cors(JSONResponse({"ok": False, "error": f"call error: {e}"}, status_code=400))
        except Exception as e:  # noqa: BLE001
            return _cors(JSONResponse(
                {"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500))
        return _cors(JSONResponse({"ok": True, "result": result}))

    async def preflight(request: Request) -> Response:
        return _cors(Response(status_code=204))

    async def health(request: Request) -> Response:
        return _cors(JSONResponse({"ok": True, "tools": sorted(tool_map), "auth": bool(token)}))

    mcp.custom_route("/rest/{tool_name}", methods=["POST"])(dispatch)
    mcp.custom_route("/rest/{tool_name}", methods=["OPTIONS"])(preflight)
    mcp.custom_route("/health", methods=["GET", "OPTIONS"])(health)
    return sorted(tool_map)
