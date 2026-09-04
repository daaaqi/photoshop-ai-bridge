"""API tests against a mock host-bridge (no Photoshop)."""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from mock_bridge import SAMPLE_LAYERS, MockBridge, start_mock_bridge  # noqa: E402


@pytest.fixture()
def env_dirs(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    export = tmp_path / "export"
    static = ROOT / "app" / "static"
    monkeypatch.setenv("CACHE_DIR", str(cache))
    monkeypatch.setenv("EXPORT_DIR", str(export))
    monkeypatch.setenv("STATIC_DIR", str(static))
    return cache, export


@pytest.fixture()
def client(env_dirs, monkeypatch):
    server, url = start_mock_bridge("ok")
    monkeypatch.setenv("BRIDGE_URL", url)
    # re-import main so module-level paths/BRIDGE pick up env
    if "main" in sys.modules:
        del sys.modules["main"]
    import main

    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c, main
    server.shutdown()


@pytest.fixture()
def client_bridge_down(env_dirs, monkeypatch):
    monkeypatch.setenv("BRIDGE_URL", "http://127.0.0.1:1")  # nothing listening
    if "main" in sys.modules:
        del sys.modules["main"]
    import main

    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_layers_include_font_and_fill_fields(client):
    c, _main = client
    r = c.get("/api/layers?refresh=true")
    assert r.status_code == 200, r.text
    data = r.json()
    title = data["layers"][0]
    assert title["ff"] == "Helvetica-Bold"
    assert title["fst"] == "Bold"
    fill = data["layers"][1]["c"][0]
    assert fill["k"] == "s"
    assert fill["fc"] == "#3366FF"
    # schema markers present in JSX source
    assert '"ff"' in _main._GET_LAYERS_JSX
    assert '"fc"' in _main._GET_LAYERS_JSX
    assert "solidFc" in _main._GET_LAYERS_JSX


def test_export_by_layer_id_sends_visibilityById(client):
    c, _main = client
    r = c.post(
        "/api/export",
        json={
            "filename": "nest.png",
            "visibilityById": {"11": True, "42": False},
            "crop": [0, 0, 100, 100],
            "note": "nested",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "setVisById" in MockBridge.last_jsx
    assert '"11"' in MockBridge.last_jsx or "11" in MockBridge.last_jsx
    assert "visId" in MockBridge.last_jsx


def test_thumbnail_by_id_route(client):
    c, _main = client
    r = c.get("/api/thumbnail/id/11")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/jpeg")
    assert "findById" in MockBridge.last_jsx
    assert "11" in MockBridge.last_jsx


def test_health_ok(client):
    c, _main = client
    r = c.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["bridge"] is True
    assert data["photoshop"] is True
    assert data["doc"] == "design.psd"
    assert data["ok"] is True


def test_health_bridge_unreachable(client_bridge_down):
    c = client_bridge_down
    r = c.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["bridge"] is False
    assert data["photoshop"] is False
    assert data["doc"] is None
    assert "unreachable" in (data.get("detail") or "").lower() or data["bridge"] is False


def test_health_ps_down(env_dirs, monkeypatch):
    server, url = start_mock_bridge("ps_down")
    monkeypatch.setenv("BRIDGE_URL", url)
    if "main" in sys.modules:
        del sys.modules["main"]
    import main

    importlib.reload(main)
    with TestClient(main.app) as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["bridge"] is True
        assert data["photoshop"] is False
        assert data["ok"] is False
    server.shutdown()


def test_sample_layers_contract():
    """Document the field contract the mock (and real JSX) aim for."""
    title = SAMPLE_LAYERS["layers"][0]
    assert set(title) >= {"n", "id", "k", "ff", "fst", "fs", "c", "t"}
    fill = SAMPLE_LAYERS["layers"][1]["c"][0]
    assert fill["k"] == "s" and "fc" in fill


def test_export_host_export_dir_skips_bridge_copy(env_dirs, monkeypatch):
    """When HOST_EXPORT_DIR is set, JSX targets the host path; no /tmp bridge fetch."""
    cache, export = env_dirs
    server, url = start_mock_bridge("ok")
    monkeypatch.setenv("BRIDGE_URL", url)
    monkeypatch.setenv("HOST_EXPORT_DIR", str(export))
    if "main" in sys.modules:
        del sys.modules["main"]
    import main
    importlib.reload(main)

    async def fake_jsx(code: str, *, require_ok: bool = True):
        MockBridge.last_jsx = code
        # Simulate Photoshop writing onto the mounted folder
        name = "host.png"
        (export / name).write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
                "0000000c49444154789c63f80f00000101000518d84e0000000049454e44ae426082"
            )
        )
        return {"ok": True, "result": "ok", "error": ""}

    monkeypatch.setattr(main, "call_jsx", fake_jsx)
    with TestClient(main.app) as c:
        r = c.post(
            "/api/export",
            json={"filename": "host.png", "visibilityById": {"11": True}},
        )
        assert r.status_code == 200, r.text
        assert (export / "host.png").exists()
        assert str(export) in MockBridge.last_jsx
        assert "/tmp/psd_export_" not in MockBridge.last_jsx
    server.shutdown()
