# -*- coding: utf-8 -*-
"""本地 UI 预览用的假后端：只负责吐 index.html 与 /api/state（读 11-25 快照）。
不连线上、不写任何数据文件，仅用于改版视觉核对。"""
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MM = os.path.join(ROOT, "multi-market")
SNAP = os.path.join(ROOT, "snap", "2026-11-25")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(MM, "index.html"), "rb") as fh:
                self._send(200, fh.read(), "text/html; charset=utf-8")
            return
        if u.path == "/head":
            with open(os.path.join(HERE, "index_head.html"), "rb") as fh:
                self._send(200, fh.read(), "text/html; charset=utf-8")
            return
        if u.path == "/api/state":
            acc = (parse_qs(u.query).get("account") or ["cn_conservative"])[0]
            p = os.path.join(SNAP, "state_%s.json" % acc)
            with open(p, "rb") as fh:
                self._send(200, fh.read(), "application/json")
            return
        self._send(200, b'{"ok":true}', "application/json")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8931), H).serve_forever()
