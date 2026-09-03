#!/usr/bin/env python3
"""macOS host bridge: HTTP → Photoshop via osascript. No deps."""
import http.server, json, os, socket, subprocess, tempfile, urllib.parse

PORT = int(os.environ.get("BRIDGE_PORT", "9090"))
PS_APP = os.environ.get("PS_APP", "Adobe Photoshop 2025")


class DualStackServer(http.server.HTTPServer):
    address_family = socket.AF_INET6


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
            r = subprocess.run(
                ["osascript", as_path], capture_output=True, text=True, timeout=120
            )
            self._json({"ok": r.returncode == 0, "result": r.stdout.strip(), "error": r.stderr.strip()})
        except Exception as e:
            self._json({"ok": False, "result": "", "error": str(e)})
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
    server = DualStackServer(("::", PORT), Bridge)
    print(f"host-bridge on [::]:{PORT} (IPv4+IPv6)", flush=True)
    server.serve_forever()
