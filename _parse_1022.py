#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""2026-10-22 复盘取数解析：三账户 facts+state 关键事实提取"""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DATE = "2026-10-22"

def load(acct, tag):
    f = json.load(open("_facts_%s_1022.json" % tag, encoding="utf-8"))
    s = json.load(open("_state_%s_1022.json" % tag, encoding="utf-8"))
    return f, s

for acct, tag, label in [("us","us","美股"), ("cn_conservative","cnc","A股稳健"), ("cn_aggressive","cna","A股激进")]:
    f, s = load(acct, tag)
    a = f.get("account", {})
    print("=" * 70)
    print("## %s (%s)" % (label, acct))
    print("totalAsset=%s cash=%s todayPnl=%s" % (a.get("totalAsset"), a.get("cash"), a.get("todayPnl")))
    total = a.get("totalAsset") or 0
    cash = a.get("cash") or 0
    print("cash_pct=%.2f%%" % (cash/total*100 if total else 0))
    # holdings
    fmap = {h["code"]: h for h in f.get("holdings", [])}
    tags = {p["code"]: (p.get("tags") or "") for p in s.get("pool", [])}
    holds = [h for h in s.get("holdings", []) if h.get("shares", 0) > 0]
    print("\n-- 持仓 %d 只（shares>0）--" % len(holds))
    ind = {}
    for h in holds:
        fh = fmap.get(h["code"], {})
        w = h["shares"] * h["price"] / total * 100
        t = tags.get(h["code"], "")
        first = t.split(",")[0].strip() if t else "未分类"
        ind[first] = ind.get(first, 0) + w
        print("  %-12s %s shares=%-6s price=%-9.2f preClose=%-9.2f pct=%-8s pnlPct=%-8s cost=%-9.2f w=%.1f%% tag=%s"
              % (h["name"], h["code"], h["shares"], h["price"], h.get("preClose") or -1,
                 h.get("pct"), fh.get("pnlPct"), fh.get("cost") or h.get("cost") or -1, w, first or "未分类"))
    print("  行业集中度(tags首标签):", " ".join("%s %.1f%%" % (k, v) for k, v in sorted(ind.items(), key=lambda x: -x[1])))
    n_up = sum(1 for h in holds if (h.get("pct") or 0) > 0)
    n_dn = sum(1 for h in holds if (h.get("pct") or 0) < 0)
    n_pu = sum(1 for h in holds if (fmap.get(h["code"], {}).get("pnlPct") or 0) > 0)
    n_pd = sum(1 for h in holds if (fmap.get(h["code"], {}).get("pnlPct") or 0) < 0)
    print("  当日涨/跌: %d/%d  浮盈/浮亏: %d/%d" % (n_up, n_dn, n_pu, n_pd))
    # trades
    print("\n-- 当日成交 %d 笔 --" % len(f.get("trades") or []))
    for t in f.get("trades") or []:
        print("  RAW:", json.dumps(t, ensure_ascii=False)[:300])
        print("  %s %s %s股 @%.2f chgPctSinceTrade=%s reason=%s"
              % (t.get("side") or t.get("direction"), t.get("name"), t.get("shares"), t.get("price"),
                 t.get("chgPctSinceTrade"), (t.get("reason") or "")[:60]))
    # 复算当日
    fpnl = sum(h["shares"] * (h["price"] - h["preClose"]) for h in holds if h.get("preClose"))
    rows = sorted(s.get("history") or [], key=lambda r: r["date"])
    print("\n-- history 末4行 --")
    for r in rows[-4:]:
        print("  %s total=%.2f mkt=%s" % (r["date"], r.get("total"), r.get("mkt")))
    prev = [r for r in rows if r["date"] < DATE]
    prev_total = prev[-1]["total"] if prev else None
    print("  复算 float_pnl=%.2f prev_total=%s day_pct=%s" % (
        fpnl, prev_total, "%.2f%%" % (fpnl/prev_total*100) if prev_total else None))
    mkt = None
    for r in rows:
        if r["date"] == DATE: mkt = r.get("mkt")
    if mkt is None and prev: mkt = prev[-1].get("mkt")
    print("  mkt(当日或最近)=%s" % mkt)
    # log 结算
    settles = [e for e in s.get("log", []) if "自动结算" in e.get("msg", "")]
    print("\n-- log 末8条 --")
    for e in (s.get("log") or [])[-8:]:
        print("  %s %s" % (e.get("time"), (e.get("msg") or "")[:80]))
    # council / review
    ct = f.get("councilToday") or []
    print("\n-- councilToday %d 条 --" % len(ct))
    for x in ct:
        print("  %s proposal=%s verdict=%s | %s" % (x.get("name"), x.get("proposal"), x.get("verdict"), (x.get("reason") or "")[:70]))
    rt = f.get("reviewedToday") or []
    rl = f.get("reviewLog") or []
    n_rl_today = sum(1 for x in rl if str(x.get("reviewedAt", ""))[:10] == DATE)
    print("reviewedToday=%d reviewLog(当日)=%d" % (len(rt), n_rl_today))
    # pending
    print("\n-- pendingReview --")
    for p in f.get("pendingReview") or []:
        print("  %s state=%s queuedAt=%s %s" % (p.get("name"), p.get("state"), p.get("queuedAt") or p.get("time"), (p.get("reason") or "")[:60]))
    # signals
    print("\n-- signals --")
    for g in (f.get("signals") or [])[:12]:
        print("  ", json.dumps(g, ensure_ascii=False)[:150])
    # dailyReviews
    dr = s.get("dailyReviews") or []
    print("\n-- dailyReviews 已写 %d 份: %s --" % (len(dr), [x.get("date") for x in dr]))
print("\nDONE")
