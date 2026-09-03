# Photoshop AI Bridge — coding assistant guide

## First steps after clone

Public repo: **photoshop-ai-bridge**. Compose service name is still **psd-picker**. After clone, `./start.sh` (you may need `chmod +x start.sh`) starts host-bridge plus the container.

**Any assistant should call these in order before writing layout code:**

```bash
BASE="http://127.0.0.1:18080"

# 1. current context (selected layer + last export)
curl -s $BASE/api/state

# 2. full layer tree
curl -s $BASE/api/layers

# 3. export manifest
curl -s $BASE/api/exports
```

This is a live bridge, not a PSD file parser. The app must be running. Optional extra channel: Adobe Photoshop MCP (`alisaitteke/photoshop-mcp`) can mutate the document; use this REST API for bounds, stable ids, and text.

---

## Optional channel: PS MCP

已安装：`alisaitteke/photoshop-mcp`，102 个工具，需要 Photoshop 在前台开着。

### AI 能获取的信息

| 工具 | 能拿到什么 |
|------|-----------|
| `photoshop_get_state` | 文档名/尺寸/分辨率/色彩模式，**当前选中图层**的名称、类型、不透明度、混合模式、**bounds（含坐标）** |
| `photoshop_get_layers` | 所有图层的名称、类型、可见性、不透明度、混合模式（**无坐标**） |
| `photoshop_get_document_info` | 同 get_state，文档级信息 |
| `photoshop_get_preview` | 当前文档的 JPEG 快照（视觉预览） |
| `photoshop_execute_script` | 执行自定义 JSX，可获取任意信息（切片编号、全部图层坐标等） |

### AI 无法原生获取的信息

- **所有图层的坐标**：get_layers 不含 bounds，只有 get_state 里的当前图层有
- **切片信息**（编号、边框）：无专用工具，需 execute_script 自写 JSX

---

## REST API (this repo, Compose service `psd-picker`)

**Base URL:**`http://127.0.0.1:18080`

> ⚠️ 需要 Photoshop 开着。psd-picker 通过 host-bridge 向 PS 发送 ExtendScript，不是独立解析 PSD 文件。

### AI 能获取的信息

| API | 能拿到什么 |
|-----|-----------|
| `GET /api/state` | 当前文档名、**选中图层**（含 id/bounds/类型/文案）、**上次导出记录** |
| `GET /api/layers` | Full tree: id/name/visibility/bounds; text layers also t/fs/c |
| `GET /api/layers?refresh=true` | Same, bypassing the on-disk cache |
| `GET /api/slices` | PS **切片列表**：编号、名称、坐标（稿子没用 PS 切片时返回空数组） |
| `GET /api/exports` | 桌面 `manifest.json` 中的所有导出记录（file/bounds/备注/时间） |
| `GET /api/thumbnail/{idx}` | 单个顶层图层的 JPEG 缩略图 |
| `POST /api/export` | 按指定可见性+裁剪区域导出 PNG，同时追加到桌面 `manifest.json` |

Do not invent other routes.

### 图层数据结构

```json
{
  "doc": "design.psd",
  "w": 1080,
  "h": 1920,
  "layers": [
    {
      "n": "Fab",
      "id": 214,
      "v": true,
      "g": false,
      "k": "i",
      "b": [4, 1224, 163, 1397]
    },
    {
      "n": "Hero",
      "id": 180,
      "v": true,
      "g": true,
      "b": [0, 0, 1080, 1920],
      "c": [
        { "n": "Title", "id": 175, "v": true, "k": "t", "b": [100, 200, 980, 300], "t": "Headline copy", "fs": 37, "c": "#F3E0B0" },
        { "n": "Background",   "id": 170, "v": true, "k": "i", "b": [0, 0, 1080, 1920] }
      ]
    }
  ]
}
```

Field names come from the JSX serializer in `app/main.py` (do not rename):
- `n` name, `id` Photoshop `layer.id`
- `v` visible, `g` is group
- `k` t or i (groups omit k)
- `b` [left, top, right, bottom]
- `t` text, `fs` font size (text layers only)
- **Schema collision on `c`:** on groups `c` is the children array; on text layers `c` is hex color (e.g. `#F3E0B0`). Use `g` / `k` to tell them apart.

### 选中图层数据结构

`GET /api/state` returns `selected` as an object array (not a name string). `lastExport.file` is the path on the export volume, not a laptop path.

```json
{
  "doc": "design.psd",
  "selected": [
    { "id": 214, "n": "Layer 23", "k": "i", "b": [0, 1532, 1080, 2684], "v": true }
  ],
  "lastExport": { "file": "/export/out.png", "crop": [0,0,1080,1920], "note": "hero", "at": "14:30" }
}
```

### 导出示例

```bash
curl -s -X POST http://127.0.0.1:18080/api/export \
  -H "Content-Type: application/json" \
  -d '{"filename":"out.png","visibility":{},"crop":[0,0,1080,1920]}'
```

---

## Optional comparison

| | PS MCP | photoshop-ai-bridge (`psd-picker` service) |
|---|---|---|
| 需要 PS 开着 | ✅ 需要 | ✅ 需要（通过 bridge 连 PS） |
| 所有图层 bounds | 需 execute_script | ✅ 原生支持 |
| 图层稳定 id | ❌ | ✅ layer.id |
| 文字层完整文案 | 需 execute_script | ✅ t 字段 |
| 切片信息 | 需 execute_script | ✅ /api/slices |
| 导出清单 | ❌ | ✅ /api/exports |
| 导出 PNG | ✅ | ✅ |
| 图层缩略图 | ✅ get_preview | ✅ /api/thumbnail（JPEG） |
| 执行 PS 操作 | ✅ 102 个工具 | ❌ 只读+导出 |
