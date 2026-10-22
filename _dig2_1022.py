#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""补充挖掘：今日大盘、expiredRecent、国轩强制复核全文、cnc国轩残影、marketContext"""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

for tag, label in [("us","美股"), ("cnc","A股稳健"), ("cna","A股激进")]:
    f = json.load(open("_facts_%s_1022.json" % tag, encoding="utf-8"))
    s = json.load(open("_state_%s_1022.json" % tag, encoding="utf-8"))
    print("=" * 78)
    print("## %s" % label)
    print("-- facts.market:", json.dumps(f.get("market"), ensure_ascii=False)[:300])
    print("-- marketContext:", json.dumps(f.get("marketContext"), ensure_ascii=False)[:400])
    print("-- newsStats:", json.dumps(f.get("newsStats"), ensure_ascii=False)[:200])
    print("-- expiredRecent:", json.dumps(f.get("expiredRecent"), ensure_ascii=False)[:500])
    print("-- lastScan:", f.get("lastScan"), " priceMode:", f.get("priceMode"))
    # state 顶层键
    print("-- state 顶层键:", list(s.keys()))
    # 0 股残影
    for h in s.get("holdings", []):
        if h.get("shares", 0) <= 0:
            print("-- 残影: %s %s shares=%s cost=%s pnlPct字段=%s" % (
                h.get("name"), h.get("code"), h.get("shares"), h.get("cost"), h.get("pnlPct")))
    # 国轩相关 reviewLog 全文
    for x in f.get("reviewLog") or []:
        if "国轩" in (x.get("name") or ""):
            print("-- 国轩评审(%s): " % x.get("reviewedAt"), json.dumps(x, ensure_ascii=False)[:600])
    # 万科相关 reviewLog 全文
    for x in f.get("reviewLog") or []:
        if "万科" in (x.get("name") or ""):
            print("-- 万科评审(%s): " % x.get("reviewedAt"), json.dumps(x, ensure_ascii=False)[:600])
    # us: trades 更早记录? state 里找 trades/ledger
    for k in s.keys():
        if "trade" in k.lower() or "ledger" in k.lower() or "realized" in k.lower():
            v = s[k]
            print("-- state[%s]:" % k, json.dumps(v, ensure_ascii=False)[:600])
print("DONE")
