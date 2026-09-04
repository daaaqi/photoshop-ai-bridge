"""Tiny host-bridge stand-in for CI. No Photoshop."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


SAMPLE_LAYERS = {
    "doc": "design.psd",
    "w": 1080,
    "h": 1920,
    "layers": [
        {
            "n": "Title",
            "id": 42,
            "v": True,
            "g": False,
            "op": 100,
            "bm": "NORMAL",
            "k": "t",
            "b": [100, 200, 980, 260],
            "t": "Hello",
            "fs": 36,
            "c": "#FFFFFF",
            "ff": "Helvetica-Bold",
            "fst": "Bold",
        },
        {
            "n": "Hero",
            "id": 10,
            "v": True,
            "g": True,
            "op": 100,
            "bm": "NORMAL",
            "b": [0, 0, 1080, 800],
            "c": [
                {
                    "n": "Fill",
                    "id": 11,
                    "v": True,
                    "g": False,
                    "op": 100,
                    "bm": "NORMAL",
                    "k": "s",
                    "b": [0, 0, 1080, 800],
                    "fc": "#3366FF",
                }
            ],
        },
    ],
}


class MockBridge(BaseHTTPRequestHandler):
    last_jsx: str = ""
    mode: str = "ok"  # ok | no_doc | ps_down

    def log_message(self, fmt, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if path == "/file":
            qs = parse_qs(urlparse(self.path).query)
            fpath = qs.get("path", [""])[0]
            if "psd_layers" in fpath:
                data = json.dumps(SAMPLE_LAYERS).encode()
            elif "psd_slices" in fpath:
                data = json.dumps({"doc": "design.psd", "w": 1080, "h": 1920, "slices": []}).encode()
            elif "psd_thumb" in fpath:
                # minimal JPEG
                data = (
                    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
                    b"\xff\xd9"
                )
            elif "psd_export" in fpath or fpath.endswith(".png"):
                # minimal 1x1 PNG (export path /tmp/psd_export_*.png)
                data = bytes.fromhex(
                    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
                    "0000000c49444154789c63f80f00000101000518d84e0000000049454e44ae426082"
                )
            else:
                return self._json({"ok": False, "error": "missing"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if urlparse(self.path).path != "/jsx":
            return self._json({"ok": False, "error": "not found"}, 404)
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        code = body.get("code", "")
        MockBridge.last_jsx = code

        if MockBridge.mode == "ps_down":
            return self._json(
                {"ok": False, "result": "", "error": "Photoshop does not appear to be running"},
                502,
            )
        if MockBridge.mode == "no_doc":
            if "activeDocument.name" in code and "historyStates" not in code and "psd_layers" not in code:
                return self._json(
                    {"ok": False, "result": "", "error": "Error: No such element"},
                    502,
                )

        # fingerprint
        if "historyStates" in code:
            return self._json({"ok": True, "result": "design.psd|1080x1920|1|Open", "error": ""})

        if "activeDocument.name" in code and "File(" not in code and "duplicate" not in code:
            return self._json({"ok": True, "result": "design.psd", "error": ""})

        # layers / slices writers
        if "psd_layers.json" in code:
            return self._json({"ok": True, "result": "design.psd", "error": ""})
        if "psd_slices.json" in code:
            return self._json({"ok": True, "result": "0", "error": ""})
        if "psd_thumb" in code or "_psd_thumb" in code:
            return self._json({"ok": True, "result": "ok", "error": ""})
        if "_psd_pick_tmp" in code or "PNGSaveOptions" in code:
            return self._json({"ok": True, "result": "ok", "error": ""})

        # selection
        if "activeLayer" in code:
            return self._json(
                {
                    "ok": True,
                    "result": '[{"id":42,"n":"Title","k":"t","b":[100,200,980,260],"v":true,"t":"Hello"}]',
                    "error": "",
                }
            )

        return self._json({"ok": True, "result": "ok", "error": ""})


def start_mock_bridge(mode: str = "ok"):
    MockBridge.mode = mode
    MockBridge.last_jsx = ""
    server = HTTPServer(("127.0.0.1", 0), MockBridge)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"
