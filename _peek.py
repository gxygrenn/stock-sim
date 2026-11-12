#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""压缩查看 waiting 候选的评分细节（不打印整份 state）。"""
import json, ssl, urllib.request, sys

BASE = "https://stock-sim-us.app.workbuddy.host"
SHARECODE = "6R7GBTHXQI"  # 2026-11-12 起平台网关要求所有请求必须带 sharecode
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE

def get(acc):
    req = urllib.request.Request(BASE + "/api/state?account=" + acc + "&sharecode=" + SHARECODE, headers={"User-Agent": "peek/1.0"})
    with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))

def cut(x, n=160):
    s = json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x
    return s[:n]

for acc in sys.argv[1:]:
    s = get(acc)
    print("=== %s ===" % acc)
    hold = [h for h in (s.get("holdings") or []) if (h.get("shares") or 0) > 0]
    for h in hold:
        print("  HOLD %-9s %-8s 股数%-6s 成本%-9s 现价%-9s 盈亏%s%%" % (
            h.get("code"), (h.get("name") or "")[:8], h.get("shares"), h.get("cost"),
            h.get("price"), ("%.2f" % (h.get("pnlPct") or 0))))
    print("  cash %s" % s.get("cash"))
    for p in (s.get("pendingReview") or []):
        if (p.get("state") or "waiting") != "waiting":
            continue
        print("\n  -- %s %s delta=%s dir=%s" % (p.get("code"), p.get("name"), p.get("delta"), p.get("direction")))
        keys = [k for k in p.keys() if k not in ("code", "name", "delta", "direction", "state")]
        print("     keys:", ",".join(keys))
        for k in ("reason", "reasons", "signals", "why", "note", "tags", "tech", "news", "headlines", "summary", "detail"):
            if k in p:
                print("     %s: %s" % (k, cut(p.get(k), 700)))
        c = p.get("council") or {}
        if c:
            print("     council:", cut(c, 400))
