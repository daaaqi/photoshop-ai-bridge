import hashlib, json, os
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BRIDGE = os.getenv("BRIDGE_URL", "http://host.docker.internal:9090")
CACHE = Path("/app/cache")
CACHE.mkdir(exist_ok=True)
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "/export"))

app = FastAPI()

# in-memory state: last export
_last_export: dict | None = None

# ExtendScript: serialize full layer tree to /tmp/psd_layers.json on host
_GET_LAYERS_JSX = r"""
function je(s){return String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/[\n\r]/g,' ');}
function node(l){
    var g=l.typename=="LayerSet";
    var p=['"n":"'+je(l.name)+'"','"id":'+l.id,'"v":'+(l.visible?'true':'false'),'"g":'+(g?'true':'false')];
    try{p.push('"op":'+Math.round(l.opacity));}catch(e){}
    try{p.push('"bm":"'+je(String(l.blendMode).replace(/BlendMode\./,''))+'"');}catch(e){}
    if(!g)p.push('"k":"'+(l.kind==LayerKind.TEXT?'t':'i')+'"');
    try{var b=l.bounds;p.push('"b":['+b[0].value+','+b[1].value+','+b[2].value+','+b[3].value+']');}catch(e){}
    if(!g&&l.kind==LayerKind.TEXT){try{var ti=l.textItem;p.push('"t":"'+je(ti.contents)+'"');try{p.push('"fs":'+Math.round(ti.size.value));}catch(e){}try{var cl=ti.color.rgb;p.push('"c":"#'+cl.hexValue+'"');}catch(e){}}catch(e){}}
    if(g&&l.layers.length){var ch=[];for(var i=0;i<l.layers.length;i++)ch.push('{'+node(l.layers[i])+'}');p.push('"c":['+ch.join(',')+']');}
    return p.join(',');
}
var doc=app.activeDocument;
var top=[];for(var i=0;i<doc.layers.length;i++)top.push('{'+node(doc.layers[i])+'}');
var out='{"doc":"'+je(doc.name)+'","w":'+doc.width.value+',"h":'+doc.height.value+',"layers":['+top.join(',')+']}';
var f=new File("/tmp/psd_layers.json");f.encoding="UTF-8";f.open("w");f.write(out);f.close();
return doc.name;
"""

# ExtendScript: get PS slices
_GET_SLICES_JSX = r"""
function je(s){return String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/[\n\r]/g,' ');}
var doc=app.activeDocument;
var items=[];
try{
    var sl=doc.slices;
    for(var i=0;i<sl.length;i++){
        var s=sl[i];
        var b=s.bounds;
        items.push('{"i":'+s.index+',"name":"'+je(s.name)+'","b":['+b.left.value+','+b.top.value+','+b.right.value+','+b.bottom.value+']}');
    }
}catch(e){}
var out='{"doc":"'+je(doc.name)+'","w":'+doc.width.value+',"h":'+doc.height.value+',"slices":['+items.join(',')+']}';
var f=new File("/tmp/psd_slices.json");f.encoding="UTF-8";f.open("w");f.write(out);f.close();
return String(items.length);
"""

# ExtendScript: get current selection info
_GET_SELECTION_JSX = r"""
function je(s){return String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/[\n\r]/g,' ');}
var doc=app.activeDocument;
var items=[];
try{
    var l=doc.activeLayer;
    var g=l.typename=="LayerSet";
    var p='"id":'+l.id+',"n":"'+je(l.name)+'","v":'+(l.visible?'true':'false');
    if(!g)p+=',"k":"'+(l.kind==LayerKind.TEXT?'t':'i')+'"';
    try{var b=l.bounds;p+=',"b":['+b[0].value+','+b[1].value+','+b[2].value+','+b[3].value+']';}catch(e){}
    if(!g&&l.kind==LayerKind.TEXT){try{p+=',"t":"'+je(l.textItem.contents)+'"';}catch(e){}}
    items.push('{'+p+'}');
}catch(e){}
return '['+items.join(',')+']';
"""


async def call_jsx(code: str, *, require_ok: bool = True) -> dict:
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{BRIDGE}/jsx", json={"code": code})
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError as e:
        raise HTTPException(
            503,
            "host-bridge unreachable. On the Mac run ./start.sh and keep Photoshop open.",
        ) from e
    except httpx.TimeoutException as e:
        raise HTTPException(
            504,
            "host-bridge timed out (120s). Photoshop may be busy, or the layer tree is too large.",
        ) from e
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:500] if e.response is not None else str(e)
        raise HTTPException(502, f"host-bridge HTTP {e.response.status_code}: {detail}") from e
    if require_ok and not data.get("ok"):
        raise HTTPException(502, data.get("error") or "Photoshop JSX failed")
    return data


async def bridge_file(path: str) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            fr = await c.get(f"{BRIDGE}/file", params={"path": path})
            fr.raise_for_status()
            return fr
    except httpx.ConnectError as e:
        raise HTTPException(503, "host-bridge unreachable. On the Mac run ./start.sh.") from e
    except httpx.TimeoutException as e:
        raise HTTPException(504, "host-bridge timed out reading a temp file.") from e
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"host-bridge file HTTP {e.response.status_code}") from e


async def get_doc_name() -> str:
    r = await call_jsx("return app.activeDocument.name;")
    return r["result"]


@app.get("/api/layers")
async def get_layers(refresh: bool = False):
    doc_name = await get_doc_name()
    key = hashlib.md5(doc_name.encode()).hexdigest()
    cf = CACHE / f"{key}.json"

    if not refresh and cf.exists():
        return json.loads(cf.read_text(encoding="utf-8"))

    await call_jsx(_GET_LAYERS_JSX)
    fr = await bridge_file("/tmp/psd_layers.json")

    cf.write_bytes(fr.content)
    return fr.json()


@app.get("/api/slices")
async def get_slices():
    """返回 PS 切片列表（编号、名称、坐标）。无切片时 slices 为空数组。"""
    await call_jsx(_GET_SLICES_JSX)
    fr = await bridge_file("/tmp/psd_slices.json")

    return fr.json()


@app.get("/api/state")
async def get_state():
    """交接状态：当前文档、选中图层、上次导出记录。AI 首先调用此接口了解当前上下文。"""
    doc_name = await get_doc_name()

    r = await call_jsx(_GET_SELECTION_JSX, require_ok=False)
    selected = json.loads(r["result"]) if r.get("ok") and r.get("result") else []

    return {
        "doc": doc_name,
        "selected": selected,
        "lastExport": _last_export,
    }


class ExportSpec(BaseModel):
    filename: str
    visibility: dict
    crop: Optional[list] = None
    note: Optional[str] = None  # 给 AI 看的备注


@app.post("/api/export")
async def export_png(spec: ExportSpec):
    global _last_export

    vis = json.dumps({int(k): v for k, v in spec.visibility.items()})
    crop = json.dumps(spec.crop) if spec.crop else "null"
    safe = spec.filename.replace('"', "").replace("/", "").replace("..", "").strip() or "export"
    if not safe.endswith(".png"):
        safe += ".png"

    jsx = f"""
var vis={vis}, crop={crop};
var doc=app.activeDocument;
var dup=doc.duplicate("_psd_pick_tmp");
for(var i=0;i<dup.layers.length;i++){{if(String(i) in vis||i in vis)dup.layers[i].visible=vis[i];}}
if(crop)dup.crop([crop[0],crop[1],crop[2],crop[3]]);
dup.flatten();
var f=new File("{EXPORT_DIR}/{safe}");
var o=new PNGSaveOptions();o.compression=6;
dup.saveAs(f,o,true);
dup.close(SaveOptions.DONOTSAVECHANGES);
return "ok";
"""
    await call_jsx(jsx)

    saved_path = str(EXPORT_DIR / safe)
    now = datetime.now().strftime("%H:%M")

    _last_export = {
        "file": saved_path,
        "crop": spec.crop,
        "visibility": spec.visibility,
        "note": spec.note,
        "at": now,
    }

    # write manifest.json alongside exported file
    manifest_path = EXPORT_DIR / "manifest.json"
    manifest: list = []
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = []

    manifest.append({
        "file": safe,
        "b": spec.crop,
        "note": spec.note,
        "at": now,
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True, "saved": saved_path, "manifest": str(manifest_path)}


@app.get("/api/exports")
async def get_exports():
    """读取桌面 manifest.json，返回所有导出记录。"""
    manifest_path = EXPORT_DIR / "manifest.json"
    if not manifest_path.exists():
        return {"items": []}
    try:
        items = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {"items": []}
    return {"items": items}


@app.get("/api/thumbnail/{idx}")
async def get_thumbnail(idx: int):
    doc_name = await get_doc_name()
    key = hashlib.md5(doc_name.encode()).hexdigest()
    cf = CACHE / f"thumb_{key}_{idx}.jpg"

    if not cf.exists():
        tmp = f"/tmp/psd_thumb_{key}_{idx}.jpg"
        jsx = f"""
app.displayDialogs=DialogModes.NO;
var doc=app.activeDocument;
var dup=doc.duplicate("_psd_thumb_tmp");
try{{
    dup.layers[{idx}].visible=true;
    for(var i=0;i<dup.layers.length;i++){{
        if(i!=={idx}){{try{{if(!dup.layers[i].isBackgroundLayer)dup.layers[i].visible=false;}}catch(e){{}}}}
    }}
    dup.flatten();
    var ratio=Math.min(1,280/dup.width.value,400/dup.height.value);
    dup.resizeImage(UnitValue(Math.round(dup.width.value*ratio),"px"),UnitValue(Math.round(dup.height.value*ratio),"px"),72,ResampleMethod.BICUBIC);
    var f=new File("{tmp}");
    var o=new JPEGSaveOptions();o.quality=7;
    dup.saveAs(f,o,true);
}}finally{{
    dup.close(SaveOptions.DONOTSAVECHANGES);
    app.displayDialogs=DialogModes.ALL;
}}
return "ok";
"""
        await call_jsx(jsx)
        fr = await bridge_file(tmp)
        cf.write_bytes(fr.content)

    return Response(content=cf.read_bytes(), media_type="image/jpeg")


app.mount("/", StaticFiles(directory="/app/static", html=True), name="static")
