# -*- coding: utf-8 -*-
"""收盘前补扫的 HTTP 接线测试（2026-11-24）。

验证的是「接线」而不是判定逻辑：真实起一个 ThreadingHTTPServer，打一次 GET /api/state，
确认 do_GET → maybe_close_sweep → close_sweep 这条链真的通，并且卖出结果落进 state。
判定逻辑本身由 _test_close_sweep.py 覆盖。

为什么必须测接线：云端沙箱无人访问时后台 worker 会被挂起（11-24 实测 80 分钟没推进扫描），
补扫最终靠「有访问就触发」。这条链断了，P1 等于没上线。
"""
import json
import os
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

os.environ["NO_WORKER"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

_p = _f = 0


def chk(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1
        print("  PASS  %s" % name)
    else:
        _f += 1
        print("  FAIL  %s %s" % (name, extra))


server.in_trading_hours = lambda now=None: True
server.cn_close_sweep_window = lambda now=None: True
server.fetch_prices = lambda codes: {c: {"price": 108.0, "preClose": 100.0, "pct": 8.0}
                                     for c in codes}
server.refresh_prices = lambda s: ""

server.set_account("cn_conservative")
s = server.default_state("cn_conservative")
s["config"].update({"takeProfitPct": 8, "takeProfitSellFrac": 1.0, "maxPerRound": 10, "maxPerDay": 100})
s["priceMode"] = "real"
s["cash"] = 100000.0
s["holdings"] = [{"name": "测试股", "code": "sh600000", "shares": 1000,
                  "price": 100.0, "cost": 100.0, "pct": 0.0}]
s["pendingReview"] = []
server._states["cn_conservative"] = s
server.save_state()

srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

try:
    url = "http://127.0.0.1:%d/api/state?account=cn_conservative" % port
    with urllib.request.urlopen(url, timeout=30) as r:
        body = json.loads(r.read().decode("utf-8"))
    chk("GET /api/state 正常返回", body.get("account") == "cn_conservative")
    chk("补扫在请求路径上执行（1000 股全清）", body["holdings"][0]["shares"] == 0,
        "shares=%s" % body["holdings"][0]["shares"])
    msgs = [x.get("msg", "") for x in (body.get("log") or [])]
    chk("日志留下「收盘前补扫」痕迹", any("收盘前补扫" in m for m in msgs), str(msgs[:3]))
    chk("标记 closeSweepDate", body.get("closeSweepDate") == server.today_str(),
        str(body.get("closeSweepDate")))
finally:
    srv.shutdown()
    srv.server_close()

print("\n结果：PASS %d / FAIL %d" % (_p, _f))
sys.exit(1 if _f else 0)
