# -*- coding: utf-8 -*-
"""2026-10-24 复盘取数摘要：三账户 daily_facts + state 关键字段提取"""
import json, sys

ACCTS = ["us", "cn_conservative", "cn_aggressive"]

def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

out = []
for a in ACCTS:
    facts = load(f"_facts_{a}_1024.json")
    state = load(f"_state_{a}_1024.json")
    o = []
    o.append(f"===== ACCOUNT: {a} =====")
    o.append(f"facts.date={facts.get('date')} account={facts.get('account')}")
    o.append(f"facts keys: {sorted(facts.keys())}")
    # top-level scalars
    for k in ("cash","totalValue","total","todayPnl","pnlPct","lastScan","market","positionPct","cashPct"):
        if k in facts:
            o.append(f"  facts.{k} = {facts[k]}")
    if isinstance(facts.get("market"), dict):
        o.append(f"  facts.market = {facts['market']}")
    # trades
    trades = facts.get("trades") or facts.get("todayTrades") or []
    o.append(f"  trades n={len(trades)}")
    for t in trades:
        o.append(f"    TRADE {t}")
    # holdings from state (with pct/preClose)
    hs = state.get("holdings") or []
    hlive = [h for h in hs if (h.get("shares") or 0) > 0]
    o.append(f"  state.holding total={len(hs)} live={len(hlive)}")
    for h in hlive:
        o.append(f"    HOLD {h.get('code')} {h.get('name')} sh={h.get('shares')} cost={h.get('cost')} price={h.get('price')} pnlPct={h.get('pnlPct')} pct={h.get('pct')} preClose={h.get('preClose')}")
    # pool tags for held codes
    pool = state.get("pool") or []
    tagmap = {}
    for p in pool:
        tagmap[p.get("code")] = (p.get("tags") or [])
    held_codes = {h.get("code") for h in hlive}
    for c in held_codes:
        o.append(f"    TAGS {c}: {tagmap.get(c)}")
    # pending queue
    pend = facts.get("pending") or facts.get("pendingReview") or facts.get("queue") or []
    o.append(f"  pending n={len(pend) if isinstance(pend,list) else pend}")
    if isinstance(pend, list):
        for p in pend[:20]:
            o.append(f"    PEND {p}")
    # signals
    sig = facts.get("signals") or []
    o.append(f"  signals n={len(sig) if isinstance(sig,list) else sig}")
    if isinstance(sig, list):
        for s in sig[:20]:
            o.append(f"    SIG {s}")
    # history last 12
    hist = state.get("history") or []
    o.append(f"  history n={len(hist)} last12:")
    for r in hist[-12:]:
        o.append(f"    HIST {r}")
    # log last 15
    log = state.get("log") or []
    o.append(f"  log n={len(log)} last15:")
    for r in log[-15:]:
        o.append(f"    LOG {r}")
    # reviewLog recent
    rl = state.get("reviewLog") or []
    o.append(f"  reviewLog n={len(rl)}")
    # dailyReviews count
    dr = state.get("dailyReviews") or []
    o.append(f"  dailyReviews n={len(dr)} dates={[d.get('date') for d in dr][-5:] if dr else []}")
    # realized
    rz = state.get("realized") or facts.get("realized") or []
    o.append(f"  realized n={len(rz) if isinstance(rz,list) else rz}")
    out.append("\n".join(o))

text = "\n\n".join(out)
with open("_digest_1024.txt", "w", encoding="utf-8") as f:
    f.write(text)
print("written _digest_1024.txt", len(text))
