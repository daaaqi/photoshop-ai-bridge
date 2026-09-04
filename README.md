<p align="center">
  <img src="docs/logo.png" alt="photoshop-ai-bridge" width="128">
</p>

# Photoshop AI Bridge

**加速首稿** · **From PSD to first draft**

A lightweight bridge that exposes Photoshop document structure (layer tree, bounds, text content, slices) as a REST API — so AI coding assistants can turn a live PSD into a first-draft UI.

## Why

Photoshop MCP gives you 100+ tools but `get_layers` **doesn't include bounds**. Without coordinates, AI can't lay out a page — it can only guess spacing from screenshots.

This bridge fills that gap: one `GET /api/layers` call returns every layer's position, dimensions, text content, font size, and color. AI reads the JSON, writes CSS. The goal is **加速首稿** (a faster first draft) — not replacing frontend craft.

## How it works

```
┌──────────┐     osascript/JSX     ┌───────────┐
│ Photoshop │◄────────────────────►│host-bridge │ (macOS, port 9090)
└──────────┘                       └─────┬──────┘
                                         │ HTTP
                                   ┌─────▼──────┐
                                   │  FastAPI    │ (Docker, port 18080)
                                   │  container  │
                                   └─────┬──────┘
                                         │
                                    AI / Browser
```

`host-bridge.py` runs on macOS and sends ExtendScript to Photoshop via `osascript`. The FastAPI container calls the bridge, caches results, and serves a web UI + REST API.

Coding assistants: read [AI_GUIDE.md](AI_GUIDE.md) first (`GET /api/state`, then `/api/layers`), or use the 6-tool MCP in `mcp_server.py`.

## Quick start

**Requirements:** macOS, Photoshop (any recent version), Docker

```bash
git clone https://github.com/daaaqi/photoshop-ai-bridge.git
cd photoshop-ai-bridge
chmod +x start.sh

# Optional: configure Photoshop version
export PS_APP="Adobe Photoshop 2025"

# Start everything
./start.sh
```

Open `http://127.0.0.1:18080` for the web UI, or use the API directly.

The HTTP API and host-bridge bind to `127.0.0.1` by default. Official URL: `http://127.0.0.1:18080` (compose publishes `localhost:${PORT:-18080}`). There is no auth — do not publish these ports to the public internet.

If this machine uses an HTTP proxy (`HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy`), add `127.0.0.1` and `localhost` to `NO_PROXY` (and `no_proxy`). Otherwise requests to the local container go through the corporate proxy and fail.


## MCP (Cursor / other clients)

This is a thin wrapper around the REST API — **six tools**, not the 100-tool Adobe Photoshop MCP.

| Tool | REST |
|------|------|
| `get_health` | `GET /api/health` |
| `get_state` | `GET /api/state` |
| `get_layers` | `GET /api/layers` |
| `get_slices` | `GET /api/slices` |
| `get_exports` | `GET /api/exports` |
| `get_thumbnail` | `GET /api/thumbnail/{idx}` |
| `export_png` | `POST /api/export` |

`mcp_server.py` is stdio JSON-RPC, stdlib only. After `./start.sh`:

```json
{
  "mcpServers": {
    "photoshop-ai-bridge": {
      "command": "python3",
      "args": ["/ABS/PATH/photoshop-ai-bridge/mcp_server.py"],
      "env": { "BRIDGE_API": "http://127.0.0.1:18080" }
    }
  }
}
```

Layer JSON is cached. The cache is dropped when you switch documents or Photoshop history changes (edits / layer tree). Pass `refresh=true` on `get_layers` to force a live read.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | host-bridge up, Photoshop open?, active document name (or null) |
| `GET` | `/api/state` | Current document, selected layer (with id/bounds/type/text), last export |
| `GET` | `/api/layers?refresh=true` | Full layer tree with bounds, opacity, blend mode, text, font size, color |
| `GET` | `/api/slices` | Photoshop slices (index, name, bounds) |
| `GET` | `/api/exports` | Export manifest (all previously exported files) |
| `GET` | `/api/thumbnail/id/{layer_id}` | JPEG thumbnail by stable layer.id (nested OK) |
| `GET` | `/api/thumbnail/{idx}` | JPEG thumbnail of top-level layer by index (legacy) |
| `POST` | `/api/export` | Export PNG; prefer `visibilityById`, legacy `visibility` by top-level index |

### Layer data structure

```json
{
  "doc": "design.psd",
  "w": 1080, "h": 1920,
  "layers": [
    {
      "n": "Title",
      "id": 42,
      "v": true,
      "g": false,
      "op": 100,
      "bm": "NORMAL",
      "k": "t",
      "b": [100, 200, 980, 260],
      "t": "Welcome to the event",
      "fs": 36,
      "c": "#FFFFFF",
      "ff": "Helvetica-Bold",
      "fst": "Bold"
    },
    {
      "n": "Hero Section",
      "id": 38,
      "v": true,
      "g": true,
      "b": [0, 0, 1080, 800],
      "c": [ ... ]
    }
  ]
}
```

| Field | Description |
|-------|-------------|
| `n` | Layer name |
| `id` | Stable unique ID (Photoshop `layer.id`, survives reorder) |
| `v` | Visible |
| `g` | Is group (LayerSet) |
| `op` | Opacity 0–100 |
| `bm` | Blend mode (e.g. `NORMAL`, `MULTIPLY`) |
| `k` | Kind: `t` text, `i` pixel, `s` solid fill (groups omit this) |
| `b` | Bounds: `[left, top, right, bottom]` in pixels |
| `t` | Full text content (text layers only, from `TextItem.contents`) |
| `fs` | Font size (text layers only) |
| `ff` | Font PostScript name (text layers only, e.g. `Helvetica-Bold`) |
| `fst` | Faux Bold/Italic style when set (text only; omit if neither) |
| `fc` | Solid-fill hex when cheap (`k: "s"`); omit if unavailable |
| `c` | **Overloaded:** text layers = hex color (e.g. `#F3E0B0`); groups = child layer array |

### Selected layer

`GET /api/state` returns the active layer as a full object, not just a name:

```json
{
  "doc": "design.psd",
  "selected": [
    { "id": 42, "n": "Title", "k": "t", "b": [100, 200, 980, 260], "v": true, "t": "Welcome" }
  ],
  "lastExport": null
}
```

## Configuration

For large PNG exports set `HOST_EXPORT_DIR` to the **absolute Mac path** of the same folder as `EXPORT_DIR` (e.g. `EXPORT_DIR=~/Desktop` and `HOST_EXPORT_DIR=/Users/you/Desktop`). Photoshop then saves on the host and the container reads the mount — no 18MB bridge HTTP copy. Without it, export falls back to `/tmp` + bridge fetch (OK for small files).

Optional launchd sample: `launchd/com.will.psd-bridge.plist` (includes `EnvironmentVariables` for `PS_APP`).


Copy `.env.example` to `.env` and edit as needed:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `18080` | Web UI port |
| `EXPORT_DIR` | `~/Desktop` | Host directory for exported PNGs |
| `PS_APP` | `Adobe Photoshop 2025` | Photoshop application name for osascript |
| `BRIDGE_PORT` | `9090` | Host bridge port |

## Limitations

- **macOS only** — the bridge uses `osascript` to talk to Photoshop
- **Photoshop must be running** — this is a live bridge, not a PSD file parser
- Single selection only (multi-layer selection requires ActionDescriptor, not yet implemented)

## License

MIT
