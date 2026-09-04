#!/usr/bin/env python3
"""Stdio MCP for photoshop-ai-bridge. Six tools that wrap the local REST API. No extra deps."""
from __future__ import annotations

import json, os, sys, urllib.error, urllib.parse, urllib.request

API = os.environ.get("BRIDGE_API", "http://127.0.0.1:18080").rstrip("/")
PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "get_health",
        "description": "Check host-bridge reachability, whether Photoshop is open, and the active document name (null if none).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_state",
        "description": "Current PSD name, selected layer (id/bounds/type/text), last export. Call this first.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_layers",
        "description": "Full layer tree with bounds, opacity (op), blend mode (bm), text, font size, color. Use refresh=true to bypass cache.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {"type": "boolean", "description": "Force a live Photoshop read", "default": False}
            },
        },
    },
    {
        "name": "get_slices",
        "description": "Photoshop slices (index, name, bounds). Empty array if the document has no slices.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_exports",
        "description": "Export manifest (files previously exported to the host export dir).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_thumbnail",
        "description": "JPEG thumbnail. Prefer layer id (nested OK). Legacy: top-level idx.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Stable Photoshop layer.id (preferred)"},
                "idx": {"type": "integer", "description": "Legacy top-level layer index"},
            },
        },
    },
    {
        "name": "export_png",
        "description": "Export a PNG with optional visibility map and crop [left,top,right,bottom]. Appends to the export manifest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "visibility": {
                    "type": "object",
                    "description": "Legacy: top-level layer index -> boolean visible",
                    "additionalProperties": {"type": "boolean"},
                },
                "visibilityById": {
                    "type": "object",
                    "description": "Preferred: stable layer.id -> boolean visible (works inside groups)",
                    "additionalProperties": {"type": "boolean"},
                },
                "crop": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                },
                "note": {"type": "string"},
            },
            "required": ["filename"],
        },
    },
]


def http_json(method: str, path: str, body: dict | None = None, query: dict | None = None):
    url = API + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return raw, ctype, resp.status
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(f"HTTP {e.code} {path}: {err}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot reach {API} ({e.reason}). Start the bridge with ./start.sh."
        ) from e


def call_tool(name: str, args: dict) -> dict:
    args = args or {}
    if name == "get_health":
        raw, _, _ = http_json("GET", "/api/health")
        return {"content": [{"type": "text", "text": raw.decode()}]}
    if name == "get_state":
        raw, _, _ = http_json("GET", "/api/state")
        return {"content": [{"type": "text", "text": raw.decode()}]}
    if name == "get_layers":
        q = {}
        if args.get("refresh"):
            q["refresh"] = "true"
        raw, _, _ = http_json("GET", "/api/layers", query=q or None)
        return {"content": [{"type": "text", "text": raw.decode()}]}
    if name == "get_slices":
        raw, _, _ = http_json("GET", "/api/slices")
        return {"content": [{"type": "text", "text": raw.decode()}]}
    if name == "get_exports":
        raw, _, _ = http_json("GET", "/api/exports")
        return {"content": [{"type": "text", "text": raw.decode()}]}
    if name == "get_thumbnail":
        import base64
        if args.get("id") is not None:
            raw, ctype, _ = http_json("GET", f"/api/thumbnail/id/{int(args['id'])}")
        elif args.get("idx") is not None:
            raw, ctype, _ = http_json("GET", f"/api/thumbnail/{int(args['idx'])}")
        else:
            raise RuntimeError("get_thumbnail requires id or idx")
        b64 = base64.standard_b64encode(raw).decode("ascii")
        return {"content": [{"type": "image", "data": b64, "mimeType": "image/jpeg"}]}
    if name == "export_png":
        payload = {"filename": args["filename"]}
        if args.get("visibility") is not None:
            payload["visibility"] = args["visibility"]
        if args.get("visibilityById") is not None:
            payload["visibilityById"] = args["visibilityById"]
        if "crop" in args:
            payload["crop"] = args["crop"]
        if "note" in args:
            payload["note"] = args["note"]
        raw, _, _ = http_json("POST", "/api/export", body=payload)
        return {"content": [{"type": "text", "text": raw.decode()}]}
    raise RuntimeError(f"unknown tool {name}")


def reply(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(msg: dict):
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        reply(
            msg_id,
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "photoshop-ai-bridge", "version": "1.0.0"},
                "instructions": (
                    "REST wrapper for a live Photoshop document. "
                    "Call get_state then get_layers. Requires ./start.sh and Photoshop open. "
                    "Localhost only. Six tools, not a full Photoshop MCP."
                ),
            },
        )
        return
    if method == "notifications/initialized" or method == "initialized":
        return
    if method == "ping":
        reply(msg_id, {})
        return
    if method == "tools/list":
        reply(msg_id, {"tools": TOOLS})
        return
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            reply(msg_id, call_tool(name, arguments))
        except Exception as e:
            reply(
                msg_id,
                {"content": [{"type": "text", "text": str(e)}], "isError": True},
            )
        return
    if msg_id is not None:
        reply(msg_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(msg)


if __name__ == "__main__":
    main()
