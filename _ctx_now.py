#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""紧凑上下文：持仓累计/当日/权重 + waiting 候选 reason/council 摘要。"""
import json, ssl, urllib.request, sys

BASE = "https://stock-sim-us.app.workbuddy.host"
SHARECODE = "6R7GBTHXQI"
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE


def get(acc):
    req = urllib.request.Request(BASE + "/api/state?account=" + acc + "&sharecode=" + SHARECODE,
                                 headers={"User-Agent": "ctx/1.0"})
    with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def f(x, d=2):
    try:
        return round(float(x), d)
    except Exception:
        return None


for acc in sys.argv[1:]:
    s = get(acc)
    hold = [h for h in (s.get("holdings") or []) if (h.get("shares") or 0) > 0]
    mv = sum((h.get("price") or 0) * (h.get("shares") or 0) for h in hold)
    cash = s.get("cash") or 0
    tot = mv + cash
    print("=== %s === 持仓%d 现金%.0f(%.1f%%) 总值%.0f" % (acc, len(hold), cash, 100 * cash / tot if tot else 0, tot))
    for h in hold:
        cost = h.get("cost") or 0
        px = h.get("price") or 0
        pnl = 100 * (px / cost - 1) if cost else 0
        w = 100 * px * (h.get("shares") or 0) / tot if tot else 0
        print("  %-9s %-6s 股%-5s 成本%-8s 现%-8s 累%+6.2f%% 日%+6.2f%% 权重%5.2f%%" % (
            h.get("code"), (h.get("name") or "")[:6], h.get("shares"), cost, px, pnl, f(h.get("pct")) or 0, w))
    print("  lastTrade:", json.dumps(s.get("lastTrade") or {}, ensure_ascii=False)[:300])
    tr = s.get("trades") or []
    today = "2026-11-19"
    for t in tr[-12:]:
        if today in str(t.get("time") or ""):
            print("  TODAY TRADE:", json.dumps(t, ensure_ascii=False)[:200])
    for p in (s.get("pendingReview") or []):
        if (p.get("state") or "waiting") != "waiting":
            continue
        print("\n  -- %s %s δ=%s %s | %s | px=%s | 上次审 %s" % (
            p.get("code"), p.get("name"), p.get("delta"), p.get("direction"), p.get("time"), p.get("px"),
            p.get("lastReview")))
        r = p.get("reason")
        if r:
            print("     reason:", json.dumps(r, ensure_ascii=False)[:600] if not isinstance(r, str) else r[:600])
        c = p.get("council")
        if c:
            print("     council:", json.dumps(c, ensure_ascii=False)[:700])
