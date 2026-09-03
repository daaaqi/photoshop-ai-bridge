# Photoshop AI Bridge

A lightweight bridge that exposes Photoshop document structure (layer tree, bounds, text content, slices) as a REST API — designed for AI coding assistants to read PSD layouts and generate pixel-accurate frontend code.

## Why

Photoshop MCP gives you 100+ tools but `get_layers` **doesn't include bounds**. Without coordinates, AI can't lay out a page — it can only guess spacing from screenshots.

This bridge fills that gap: one `GET /api/layers` call returns every layer's position, dimensions, text content, font size, and color. AI reads the JSON, writes CSS. No guessing.

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

## Quick start

**Requirements:** macOS, Photoshop (any recent version), Docker

```bash
git clone https://github.com/user/photoshop-ai-bridge.git
cd photoshop-ai-bridge

# Optional: configure Photoshop version
export PS_APP="Adobe Photoshop 2025"

# Start everything
./start.sh
```

Open `http://localhost:18080` for the web UI, or use the API directly.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/state` | Current document, selected layer (with id/bounds/type/text), last export |
| `GET` | `/api/layers?refresh=true` | Full layer tree with bounds, text content, font size, color |
| `GET` | `/api/slices` | Photoshop slices (index, name, bounds) |
| `GET` | `/api/exports` | Export manifest (all previously exported files) |
| `GET` | `/api/thumbnail/{idx}` | JPEG thumbnail of top-level layer by index |
| `POST` | `/api/export` | Export PNG with visibility/crop control, appends to manifest |

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
      "k": "t",
      "b": [100, 200, 980, 260],
      "t": "Welcome to the event",
      "fs": 36,
      "c": "#FFFFFF"
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
| `k` | Kind: `t` = text, `i` = pixel (groups omit this) |
| `b` | Bounds: `[left, top, right, bottom]` in pixels |
| `t` | Full text content (text layers only, from `TextItem.contents`) |
| `fs` | Font size (text layers only) |
| `c` | Text color as hex (text layers only, e.g. `#F3E0B0`) |

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
