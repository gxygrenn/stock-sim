# -*- coding: utf-8 -*-
"""2026-10-24 第二轮解析：log/reviewLog 头部、周度数据、误杀候选"""
import json
from datetime import datetime

ACCTS = ["us", "cn_conservative", "cn_aggressive"]

def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

out = []
for a in ACCTS:
    facts = load(f"_facts_{a}_1024.json")
    state = load(f"_state_{a}_1024.json")
    o = [f"===== {a} ====="]
    o.append(f"facts.priceMode={facts.get('priceMode')} scanErr={facts.get('scanErr')}")
    o.append(f"facts.newsStats={facts.get('newsStats')}")
    o.append(f"facts.councilToday={facts.get('councilToday')}")
    o.append(f"facts.reviewedToday={facts.get('reviewedToday')}")
    o.append(f"facts.expiredRecent={facts.get('expiredRecent')}")
    mc = facts.get("marketContext")
    if mc:
        o.append(f"facts.marketContext keys={list(mc.keys()) if isinstance(mc,dict) else mc}")
        o.append(f"  marketContext={json.dumps(mc, ensure_ascii=False)[:800]}")
    # historyLast10 from facts
    o.append(f"facts.historyLast10={facts.get('historyLast10')}")
    # state keys
    o.append(f"state keys: {sorted(state.keys())}")
    # log HEAD (newest)
    log = state.get("log") or []
    o.append(f"--- log head 25 (n={len(log)}) ---")
    for r in log[:25]:
        o.append(f"  LOG {r.get('time')} | {r.get('msg')}")
    # settlement entries anywhere
    o.append("--- log 结算/作废/复盘 相关（全部日志里筛） ---")
    for r in log:
        m = r.get("msg") or ""
        if "结算" in m or "作废" in m or "复盘" in m:
            o.append(f"  SETTLE {r.get('time')} | {m}")
    # reviewLog structure
    rl = state.get("reviewLog") or []
    o.append(f"--- reviewLog n={len(rl)}; first entry fields ---")
    if rl:
        o.append(f"  keys={sorted(rl[0].keys())}")
        o.append(f"  first={json.dumps(rl[0], ensure_ascii=False)[:400]}")
        o.append(f"  last={json.dumps(rl[-1], ensure_ascii=False)[:400]}")
        # time span
        o.append(f"  newest reviewedAt={rl[0].get('reviewedAt')} ; oldest={rl[-1].get('reviewedAt')}")
    # dailyReviews head
    dr = state.get("dailyReviews") or []
    if dr:
        o.append(f"dailyReviews n={len(dr)} newest5={[d.get('date') for d in dr[:5]]}")
    # state trades / closedTrades for weekly audit
    for k in ("trades","tradeLog","closedTrades","realizedTrades","realizedLog"):
        if k in state:
            v = state[k]
            o.append(f"state.{k}: type={type(v).__name__} len={len(v) if isinstance(v,(list,dict)) else v}")
    out.append("\n".join(o))

with open("_digest2_1024.txt", "w", encoding="utf-8") as f:
    f.write("\n\n".join(out))
print("ok", sum(len(x) for x in out))
