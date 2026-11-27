# -*- coding: utf-8 -*-
"""本地 UI 视觉核对用假后端（成交流水「盈亏」列）。

只服务 index.html 与 /api/state，state 读 snap 快照，并在内存里给卖出流水补上
pnl/pnlPct/costPrice **假数据**（仅用于看版式，绝不写回任何文件、不连线上）。
"""
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MM = os.path.join(ROOT, "multi-market")
SNAP = os.path.join(ROOT, "snap", "2026-11-26")

FAKE = [(268.4, 3.12), (-431.6, -4.85), (1024.9, 8.41), (-88.2, -1.03),
        (56.0, 0.62), (-1205.3, -6.72), (312.75, 2.44), (-19.9, -0.31)]


def load(acc):
    with open(os.path.join(SNAP, "state_%s.json" % acc), "rb") as fh:
        s = json.loads(fh.read().decode("utf-8"))
    i = 0
    for t in s.get("trades") or []:
        if t.get("side") == "卖出":
            pnl, pc = FAKE[i % len(FAKE)]
            i += 1
            t["pnl"] = pnl
            t["pnlPct"] = pc
            t["costPrice"] = round(t.get("price", 0) / (1 + pc / 100), 3)
    s.setdefault("realized", -4820.35)
    s.setdefault("fees", 126.4)
    return json.dumps(s, ensure_ascii=False).encode("utf-8")


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
        if u.path in ("/", "/index.html", "/preview"):
            with open(os.path.join(MM, "index.html"), "rb") as fh:
                html = fh.read().decode("utf-8")
            if u.path == "/preview":
                inject = ("<script>setTimeout(function(){"
                          "document.querySelectorAll('.panel').forEach(function(p){"
                          "if(!p.querySelector('#tblTr')) p.style.display='none';});"
                          "document.querySelectorAll('.cards,.status,.hd,.topbar').forEach(function(e){"
                          "e.style.display='none';});},50);</script></body>")
                html = html.replace("</body>", inject)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if u.path == "/api/state":
            acc = (parse_qs(u.query).get("account") or ["cn_conservative"])[0]
            self._send(200, load(acc), "application/json")
            return
        self._send(200, b'{"ok":true}', "application/json")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8932), H).serve_forever()
