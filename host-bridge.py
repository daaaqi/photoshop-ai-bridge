#!/usr/bin/env python3
"""macOS host bridge: HTTP → Photoshop via osascript. No deps. Localhost only."""
import http.server, json, os, subprocess, tempfile, urllib.parse

PORT = int(os.environ.get("BRIDGE_PORT", "9090"))
PS_APP = os.environ.get("PS_APP", "Adobe Photoshop 2026")


BIND = os.environ.get("BRIDGE_BIND", "127.0.0.1")

def friendly_error(stderr, returncode, timed_out=False):
    if timed_out:
        return (
            "Photoshop timed out after 120s. The document may be too large, "
            "or Photoshop is busy. Keep it open in the foreground with a PSD."
        )
    s = (stderr or "").strip()
    low = s.lower()
    if ("running" in low and "isn" in low) or "-600" in s:
        return (f"Photoshop does not appear to be running (app name: {PS_APP}). Open it, then retry. {s}").strip()
    if "get application" in low:
        return f"Cannot talk to {PS_APP}. Open that version, or set PS_APP in .env. {s}".strip()
    if "activedocument" in low or "no such element" in low:
        return "Photoshop is open but has no active document. Open a PSD and retry."
    return s or f"script failed (exit {returncode})"

class Bridge(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/jsx":
            return self._json({"ok": False, "error": "not found"}, 404)
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))

        jsx_fd, jsx_path = tempfile.mkstemp(suffix=".jsx")
        as_fd, as_path = tempfile.mkstemp(suffix=".applescript")
        try:
            code = body.get("code", "")
            wrapped = f"(function(){{\n{code}\n}})();"
            with os.fdopen(jsx_fd, "w", encoding="utf-8") as f:
                f.write(wrapped)
            applescript = (
                f'tell application "{PS_APP}"\n'
                f'    return do javascript (read POSIX file "{jsx_path}" as string)\n'
                "end tell"
            )
            with os.fdopen(as_fd, "w") as f:
                f.write(applescript)
            try:
                r = subprocess.run(
                    ["osascript", as_path], capture_output=True, text=True, timeout=120
                )
            except subprocess.TimeoutExpired:
                return self._json(
                    {"ok": False, "result": "", "error": friendly_error("", -1, timed_out=True)},
                    504,
                )
            err = friendly_error(r.stderr, r.returncode) if r.returncode != 0 else r.stderr.strip()
            status = 200 if r.returncode == 0 else 502
            self._json({"ok": r.returncode == 0, "result": r.stdout.strip(), "error": err}, status)
        except Exception as e:
            self._json({"ok": False, "result": "", "error": str(e)}, 500)
        finally:
            for p in (jsx_path, as_path):
                try:
                    os.unlink(p)
                except Exception:
                    pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if self.path.startswith("/file"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            path = qs.get("path", [""])[0]
            try:
                with open(path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 404)
            return
        self._json({"ok": False, "error": "not found"}, 404)


if __name__ == "__main__":
    server = http.server.HTTPServer((BIND, PORT), Bridge)
    print(f"host-bridge on {BIND}:{PORT} (localhost only)", flush=True)
    server.serve_forever()
