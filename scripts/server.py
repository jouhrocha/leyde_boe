#!/usr/bin/env python3
"""
server.py — Local HTTPS-equivalent server for Chrome TOCTOU PoC
Serves the PoC with the required COOP/COEP headers that Chrome
needs to enable SharedArrayBuffer (required for the race condition demo).

Run: python3 server.py
Then open: http://localhost:8887
"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
import os

class CORPHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # Required for SharedArrayBuffer in Chrome
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def log_message(self, format, *args):
        print(f"  [server] {args[0]} {args[1]}")

PORT = 8887
os.chdir(os.path.dirname(os.path.abspath(__file__)))
print(f"\n  TOCTOU PoC Server")
print(f"  =================")
print(f"  Open in Chrome: http://localhost:{PORT}")
print(f"  (SharedArrayBuffer requires these COOP/COEP headers)\n")
HTTPServer(("", PORT), CORPHandler).serve_forever()
