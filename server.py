#!/usr/bin/env python3
"""Ship-it Stats server (stdlib). Serves web/index.html and data/stats.json; /api/refresh re-runs the collector."""
import http.server, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("SHIPIT_PORT", "4260"))

class H(http.server.BaseHTTPRequestHandler):
    def send(self, code, body, ctype):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self.send(200, open(os.path.join(HERE, "web", "index.html"), "rb").read(), "text/html; charset=utf-8")
        if p == "/data/stats.json":
            return self.send(200, open(os.path.join(HERE, "data", "stats.json"), "rb").read(), "application/json")
        if p == "/api/health":
            return self.send(200, b'{"ok":true}', "application/json")
        if p == "/api/refresh":
            r = subprocess.run([sys.executable, os.path.join(HERE, "collect.py")], capture_output=True, text=True)
            return self.send(200 if r.returncode == 0 else 500, json.dumps({"ok": r.returncode == 0, "out": r.stdout[-400:]}).encode(), "application/json")
        self.send(404, b"not found", "text/plain")
    def log_message(self, *a): pass

if __name__ == "__main__":
    os.chdir(HERE)
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
