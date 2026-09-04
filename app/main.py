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
CACHE = Path(os.getenv("CACHE_DIR", "/app/cache"))
CACHE.mkdir(parents=True, exist_ok=True)
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "/export"))
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR = Path(os.getenv("STATIC_DIR", "/app/static"))

app = FastAPI()

# in-memory state: last export
_last_export: dict | None = None

# ExtendScript: serialize full layer tree to /tmp/psd_layers.json on host
_GET_LAYERS_JSX = r"""
function je(s){return String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/[\n\r]/g,' ');}
function hx(n){n=Math.max(0,Math.min(255,Math.round(n)));var s=n.toString(16);return s.length<2?'0'+s:s;}
function solidFc(l){
    try{
        if(l.kind!=LayerKind.SOLIDFILL)return null;
        var ref=new ActionReference();
        ref.putIdentifier(charIDToTypeID("Lyr "), l.id);
        var desc=executeActionGet(ref);
        var adj=desc.getList(stringIDToTypeID("adjustment")).getObjectValue(0);
        var color=adj.getObjectValue(stringIDToTypeID("color"));
        var r=color.getDouble(stringIDToTypeID("red"));
        var g=color.getDouble(stringIDToTypeID("grain"));
        var b=color.getDouble(stringIDToTypeID("blue"));
        return "#"+hx(r)+hx(g)+hx(b);
    }catch(e){return null;}
}
function node(l){
    var g=l.typename=="LayerSet";
    var p=['"n":"'+je(l.name)+'"','"id":'+l.id,'"v":'+(l.visible?'true':'false'),'"g":'+(g?'true':'false')];
    try{p.push('"op":'+Math.round(l.opacity));}catch(e){}
    try{p.push('"bm":"'+je(String(l.blendMode).replace(/^BlendMode\./,''))+'"');}catch(e){}
    if(!g){
        var kind='i';
        try{if(l.kind==LayerKind.TEXT)kind='t';else if(l.kind==LayerKind.SOLIDFILL)kind='s';}catch(e){}
        p.push('"k":"'+kind+'"');
    }
    try{var b=l.bounds;p.push('"b":['+b[0].value+','+b[1].value+','+b[2].value+','+b[3].value+']');}catch(e){}
    if(!g&&l.kind==LayerKind.TEXT){
        try{
            var ti=l.textItem;
            p.push('"t":"'+je(ti.contents)+'"');
            try{p.push('"fs":'+Math.round(ti.size.value));}catch(e){}
            try{var cl=ti.color.rgb;p.push('"c":"#'+cl.hexValue+'"');}catch(e){}
            try{p.push('"ff":"'+je(ti.font)+'"');}catch(e){}
            try{
                var st=[];
                if(ti.fauxBold)st.push('Bold');
                if(ti.fauxItalic)st.push('Italic');
                if(st.length)p.push('"fst":"'+st.join(' ')+'"');
            }catch(e){}
        }catch(e){}
    }
    if(!g){try{var fc=solidFc(l);if(fc)p.push('"fc":"'+fc+'"');}catch(e){}}
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
            try:
                data = r.json()
            except Exception:
                data = {"ok": False, "error": r.text[:500], "result": ""}
            if r.is_success:
                pass
            elif not require_ok and isinstance(data, dict):
                data.setdefault("ok", False)
                return data
            else:
                r.raise_for_status()
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



# Cheap fingerprint: document identity + history (edits / tree changes).
_GET_FINGERPRINT_JSX = r"""
var d=app.activeDocument;
var fn=d.name;
try{fn=String(d.fullName);}catch(e){}
var hs="";
try{hs=d.historyStates.length+"|"+d.activeHistoryState.name;}catch(e){hs="?";}
return fn+"|"+d.width.value+"x"+d.height.value+"|"+hs;
"""

_FP_FILE = CACHE / "_fp.txt"


def clear_layer_cache() -> None:
    if not CACHE.exists():
        return
    for f in CACHE.iterdir():
        if f.is_file():
            f.unlink()


async def ensure_cache_current() -> str:
    """Drop cached layers/thumbnails when the open PSD changes or its history moves."""
    r = await call_jsx(_GET_FINGERPRINT_JSX)
    fp = r.get("result") or ""
    prev = _FP_FILE.read_text(encoding="utf-8") if _FP_FILE.exists() else None
    if prev != fp:
        clear_layer_cache()
    CACHE.mkdir(exist_ok=True)
    _FP_FILE.write_text(fp, encoding="utf-8")
    return fp


async def get_doc_name() -> str:
    r = await call_jsx("return app.activeDocument.name;")
    return r["result"]



@app.get("/api/health")
async def health():
    """Bridge + Photoshop + active document. Safe when PS is closed."""
    bridge = False
    photoshop = False
    doc = None
    detail = None
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{BRIDGE}/health")
            bridge = r.status_code == 200 and r.text.strip() == "ok"
    except Exception as e:
        detail = f"host-bridge unreachable: {e}"
        return {"ok": False, "bridge": False, "photoshop": False, "doc": None, "detail": detail}

    r = await call_jsx("return app.activeDocument.name;", require_ok=False)
    if r.get("ok") and r.get("result"):
        photoshop = True
        doc = r["result"]
    else:
        err = (r.get("error") or "").lower()
        if "running" in err or "application" in err or "-600" in err:
            photoshop = False
            detail = r.get("error") or "Photoshop does not appear to be running"
        else:
            # bridge talked to PS but no active document (or other JSX error)
            photoshop = True
            doc = None
            detail = r.get("error") or "no active document"
    return {
        "ok": bridge and photoshop and doc is not None,
        "bridge": bridge,
        "photoshop": photoshop,
        "doc": doc,
        "detail": detail,
    }


@app.get("/api/layers")
async def get_layers(refresh: bool = False):
    await ensure_cache_current()
    doc_name = await get_doc_name()
    key = hashlib.md5(doc_name.encode()).hexdigest()
    cf = CACHE / f"{key}.json"

    if refresh:
        clear_layer_cache()
        await ensure_cache_current()

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
    await ensure_cache_current()
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
    visibility: Optional[dict] = None  # top-level index -> bool (legacy)
    visibilityById: Optional[dict] = None  # stable layer.id -> bool (nested OK)
    crop: Optional[list] = None
    note: Optional[str] = None  # 给 AI 看的备注


@app.post("/api/export")
async def export_png(spec: ExportSpec):
    global _last_export

    vis_idx = {int(k): v for k, v in (spec.visibility or {}).items()}
    vis_id = {str(k): v for k, v in (spec.visibilityById or {}).items()}
    crop = json.dumps(spec.crop) if spec.crop else "null"
    safe = spec.filename.replace('"', "").replace("/", "").replace("..", "").strip() or "export"
    if not safe.endswith(".png"):
        safe += ".png"

    tmp_path = f"/tmp/psd_export_{safe}"
    jsx = f"""
var visIdx={json.dumps(vis_idx)}, visId={json.dumps(vis_id)}, crop={crop};
function hasKeys(o){{for(var k in o){{if(o.hasOwnProperty(k))return true;}}return false;}}
function setVisById(layers){{
    for(var i=0;i<layers.length;i++){{
        var l=layers[i];
        var key=String(l.id);
        if(key in visId)l.visible=!!visId[key];
        if(l.typename=="LayerSet"&&l.layers.length)setVisById(l.layers);
    }}
}}
app.displayDialogs=DialogModes.NO;
var doc=app.activeDocument;
var dup=doc.duplicate("_psd_pick_tmp");
try{{
if(hasKeys(visId)){{
    setVisById(dup.layers);
}}else if(hasKeys(visIdx)){{
    for(var i=0;i<dup.layers.length;i++){{
        if(String(i) in visIdx||i in visIdx)dup.layers[i].visible=visIdx[i];
    }}
}}
if(crop)dup.crop([crop[0],crop[1],crop[2],crop[3]]);
dup.flatten();
var f=new File("{tmp_path}");
var o=new PNGSaveOptions();o.compression=6;
dup.saveAs(f,o,true);
}}finally{{
dup.close(SaveOptions.DONOTSAVECHANGES);
app.displayDialogs=DialogModes.ALL;
}}
return "ok";
"""
    await call_jsx(jsx)
    fr = await bridge_file(tmp_path)
    saved_path = str(EXPORT_DIR / safe)
    (EXPORT_DIR / safe).write_bytes(fr.content)
    now = datetime.now().strftime("%H:%M")

    _last_export = {
        "file": saved_path,
        "crop": spec.crop,
        "visibility": spec.visibility,
        "visibilityById": spec.visibilityById,
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
        "visibilityById": spec.visibilityById,
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


@app.get("/api/thumbnail/id/{layer_id}")
async def get_thumbnail_by_id(layer_id: int):
    """JPEG thumbnail of any layer by stable Photoshop layer.id (nested OK)."""
    await ensure_cache_current()
    doc_name = await get_doc_name()
    key = hashlib.md5(doc_name.encode()).hexdigest()
    cf = CACHE / f"thumb_{key}_id{layer_id}.jpg"

    if not cf.exists():
        tmp = f"/tmp/psd_thumb_{key}_id{layer_id}.jpg"
        jsx = f"""
app.displayDialogs=DialogModes.NO;
function findById(layers, id){{
    for(var i=0;i<layers.length;i++){{
        var l=layers[i];
        if(l.id===id)return l;
        if(l.typename=="LayerSet"&&l.layers.length){{
            var f=findById(l.layers,id);
            if(f)return f;
        }}
    }}
    return null;
}}
function hideAll(layers){{
    for(var i=0;i<layers.length;i++){{
        var l=layers[i];
        try{{if(!l.isBackgroundLayer)l.visible=false;}}catch(e){{}}
        if(l.typename=="LayerSet"&&l.layers.length)hideAll(l.layers);
    }}
}}
function showChain(l){{
    while(l){{
        try{{l.visible=true;}}catch(e){{}}
        try{{l=l.parent; if(l.typename=="Document")l=null;}}catch(e){{l=null;}}
    }}
}}
var doc=app.activeDocument;
var dup=doc.duplicate("_psd_thumb_tmp");
try{{
    var target=findById(dup.layers, {layer_id});
    if(!target)throw new Error("layer id not found");
    hideAll(dup.layers);
    showChain(target);
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


@app.get("/api/thumbnail/{idx}")
async def get_thumbnail(idx: int):
    """JPEG thumbnail of a top-level layer by index (legacy). Prefer /api/thumbnail/id/{{layer_id}}."""
    await ensure_cache_current()
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


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
