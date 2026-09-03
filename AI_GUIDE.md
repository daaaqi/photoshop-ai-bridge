# photoshop-ai-bridge AI 使用指南

仓库：https://github.com/daaaqi/photoshop-ai-bridge

## 首要步骤：先读图层 API

**任何 AI 开始工作前，按顺序调用这两个接口：**

```bash
BASE="http://127.0.0.1:18080"

# 1. 当前上下文（选中图层带完整信息、上次导出）
curl -s $BASE/api/state

# 2. 完整图层结构（含 id/bounds/文字内容/字号/颜色）
curl -s $BASE/api/layers

# 3. 导出清单（桌面 manifest.json 里的所有已导出图）
curl -s $BASE/api/exports
```

拿到结构后，再根据任务需要决定是否使用 PS MCP 执行操作。

---

## 渠道一：PS MCP（Adobe Photoshop 直连）

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

## 渠道二：photoshop-ai-bridge（本地 Docker 服务）

**地址**：`http://127.0.0.1:18080`（默认只绑本地，无鉴权，不要暴露公网）

Docker compose 服务名仍是 `psd-picker`，产品名是 photoshop-ai-bridge。

> ⚠️ 需要 Photoshop 开着。photoshop-ai-bridge 通过 host-bridge 向 PS 发送 ExtendScript，不是独立解析 PSD 文件。

### AI 能获取的信息

| API | 能拿到什么 |
|-----|-----------|
| `GET /api/state` | 当前文档名、**选中图层**（含 id/bounds/类型/文案）、**上次导出记录** |
| `GET /api/layers` | 完整图层树，每层含 **id、名称、类型、可见性、bounds、op（不透明度）、bm（混合模式）**；文字层额外含 **t（完整文案）、fs（字号）、c（颜色）** |
| `GET /api/slices` | PS **切片列表**：编号、名称、坐标（稿子没用 PS 切片时返回空数组） |
| `GET /api/exports` | 桌面 `manifest.json` 中的所有导出记录（file/bounds/备注/时间） |
| `GET /api/thumbnail/{idx}` | 单个顶层图层的 JPEG 缩略图 |
| `POST /api/export` | 按指定可见性+裁剪区域导出 PNG，同时追加到桌面 `manifest.json` |

### 图层数据结构

```json
{
  "doc": "design.psd",
  "w": 1080,
  "h": 14247,
  "layers": [
    {
      "n": "悬浮球",
      "id": 214,
      "v": true,
      "op": 100,
      "bm": "NORMAL",
      "k": "i",
      "b": [4, 1224, 163, 1397]
    },
    {
      "n": "第一屏",
      "id": 180,
      "v": true,
      "g": true,
      "b": [0, 0, 1080, 1920],
      "c": [
        { "n": "标题文字", "id": 175, "v": true, "k": "t", "b": [100, 200, 980, 300], "t": "完整文案内容", "fs": 37, "c": "#F3E0B0" },
        { "n": "背景图",   "id": 170, "v": true, "k": "i", "b": [0, 0, 1080, 1920] }
      ]
    }
  ]
}
```

字段说明：
- `n` = 名称，`id` = 稳定唯一标识（PS layer.id）
- `v` = 可见性，`g` = 是否为组
- `op` = 不透明度 0–100，`bm` = 混合模式（如 `NORMAL`）
- `k` = 类型（`i` 图片 / `t` 文字，组无此字段）
- `b` = [left, top, right, bottom]
- `t` = 完整文案（仅文字层，来自 TextItem.contents）
- `fs` = 字号（仅文字层）
- `c` = **字段复用**：文字层是 hex 颜色（如 `#F3E0B0`）；组是子图层数组。不要做效果/图层样式解析。

### 选中图层数据结构

`GET /api/state` 返回的 `selected` 是对象数组（非字符串）：

```json
{
  "doc": "design.psd",
  "selected": [
    { "id": 214, "n": "图层 23", "k": "i", "b": [0, 1532, 1080, 2684], "v": true }
  ],
  "lastExport": { "file": "/Users/you/Desktop/out.png", "crop": [0,0,1080,1920], "note": "首屏", "at": "14:30" }
}
```

### 导出示例

```bash
curl -s -X POST http://127.0.0.1:18080/api/export \
  -H "Content-Type: application/json" \
  -d '{"filename":"out.png","visibility":{},"crop":[0,0,1080,1920]}'
```

---

## 两者对比

| | PS MCP | photoshop-ai-bridge |
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
