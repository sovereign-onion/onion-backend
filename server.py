# FILE: backend/server.py
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from config import HOST, PORT
from db import add_watch_item, delete_watch_item, list_active_items, list_watchlist, mute_item

class FeedRequestHandler(BaseHTTPRequestHandler):
    server_version = "OnionTravelFeedHTTP/0.1"

    def _send_json(self, status_code: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._send_json(200, {"ok": True})
            return

        if parsed.path == "/feed":
            items = list_active_items()
            self._send_json(200, items)
            return

        if parsed.path == "/watchlist":
            items = list_watchlist()
            self._send_json(200, items)
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path not in {"/mute", "/watchlist/add", "/watchlist/delete"}:
            self._send_json(404, {"error": "Not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        if parsed.path == "/mute":
            fingerprint = str(payload.get("fingerprint", "")).strip()
            if not fingerprint:
                self._send_json(400, {"error": "fingerprint is required"})
                return

            changed = mute_item(fingerprint)
            self._send_json(200, {"ok": True, "muted": changed, "fingerprint": fingerprint})
            return

        if parsed.path == "/watchlist/add":
            name = str(payload.get("name", "")).strip()
            watch_type = str(payload.get("watch_type", "")).strip()
            alias = str(payload.get("alias", "")).strip()

            if not name:
                self._send_json(400, {"error": "name is required"})
                return

            try:
                item = add_watch_item(name=name, watch_type=watch_type, alias=alias)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return

            self._send_json(200, {"ok": True, "item": item})
            return

        if parsed.path == "/watchlist/delete":
            try:
                watch_id = int(payload.get("id", 0))
            except (TypeError, ValueError):
                watch_id = 0

            if watch_id <= 0:
                self._send_json(400, {"error": "id is required"})
                return

            changed = delete_watch_item(watch_id)
            self._send_json(200, {"ok": True, "deleted": changed, "id": watch_id})
            return

    def log_message(self, format: str, *args) -> None:
        return


import os

def run_http_server() -> None:
    port = int(os.environ.get("PORT", PORT))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), FeedRequestHandler)
    print(f"HTTP server running on http://0.0.0.0:{port}")
    httpd.serve_forever()
