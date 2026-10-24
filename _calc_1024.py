# -*- coding: utf-8 -*-
"""2026-10-24 复盘核心计算：权重/行业/漂移/复算/误杀候选/周度裁决统计"""
import json, re
from datetime import datetime

ACCTS = ["us", "cn_conservative", "cn_aggressive"]
NOW = datetime(2026, 10, 24, 16, 10, 0)

def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")

lines = []
P = lines.append

for a in ACCTS:
    facts = load(f"_facts_{a}_1024.json")
    state = load(f"_state_{a}_1024.json")
    total = facts["account"]["totalAsset"]
    cash = facts["account"]["cash"]
    hs = [h for h in state["holdings"] if (h.get("shares") or 0) > 0]
    P(f"\n########## {a} ##########")
    P(f"total={total} cash={cash} cashPct={cash/total*100:.1f}%")
    # 权重与行业
    pool = {p.get("code"): p for p in state.get("pool", [])}
    sec_sum = {}
    mv_sum = 0
    for h in hs:
        mv = h["shares"] * h["price"]
        mv_sum += mv
        tags = (pool.get(h["code"], {}).get("tags") or [])
        t0 = (tags[0] if tags and tags[0].strip() else "未分类")
        sec_sum[t0] = sec_sum.get(t0, 0) + mv
        P(f"  {h['name']} mv={mv:.0f} w={mv/total*100:.1f}% pnlCum={(h['price']/h['cost']-1)*100:+.2f}% pct={h.get('pct')} preClose={h.get('preClose')}")
    P(f"  持仓市值={mv_sum:.0f} ({mv_sum/total*100:.1f}%)")
    P("  行业集中度(首标签): " + ", ".join(f"{k} {v/total*100:.1f}%" for k, v in sorted(sec_sum.items(), key=lambda x: -x[1])))
    # 当日复算（个股法，price 为最新快照）
    day = sum(h["shares"] * (h["price"] - h["preClose"]) for h in hs if h.get("preClose"))
    P(f"  个股法快照漂移/当日变动 = {day:+.2f}")
    # history 对比
    hist = state.get("history") or []
    if hist:
        P(f"  history末行: {hist[-1]}")
    P(f"  快照total与history末行差 = {total - hist[-1]['total']:+.2f}" if hist else "  无history")

# ===== 误杀候选（48h HOLD）=====
P("\n\n########## 误杀候选（48h 内 HOLD） ##########")
for a in ACCTS:
    state = load(f"_state_{a}_1024.json")
    facts = load(f"_facts_{a}_1024.json")
    # 当前价字典：holdings + poolQuote
    px = {}
    for h in state["holdings"]:
        px[h["code"]] = h["price"]
    pq = state.get("poolQuote") or {}
    if isinstance(pq, dict):
        for k, v in pq.items():
            if isinstance(v, dict) and v.get("price"):
                px[k] = v["price"]
            elif isinstance(v, (int, float)):
                px[k] = v
    P(f"\n--- {a} (poolQuote样例: {list(pq.items())[:2] if isinstance(pq,dict) else pq}) ---")
    cands = []
    for r in state.get("reviewLog", []):
        if r.get("verdict") != "HOLD":
            continue
        if not r.get("priceAtReview"):
            continue
        ra = r.get("reviewedAt")
        try:
            t = dt(ra)
        except Exception:
            continue
        if (NOW - t).total_seconds() > 48 * 3600:
            continue
        cur = px.get(r["code"])
        chg = (cur - r["priceAtReview"]) / r["priceAtReview"] * 100 if cur else None
        cands.append((chg if chg is not None else -999, r, cur))
    cands.sort(key=lambda x: -x[0])
    P(f"  48h内HOLD记录 n={len(cands)}")
    for chg, r, cur in cands[:15]:
        mark = " ★≥8%" if chg >= 8 else ""
        P(f"  {r['name']}({r['code']}) {r['reviewedAt']} pAtRev={r['priceAtReview']} cur={cur} chg={chg if chg==-999 else round(chg,2)}%{mark}")
        P(f"    note: {r['note'][:80]}")

# ===== 周度评审裁决统计（10-18 ~ 10-24）=====
P("\n\n########## 周度评审对账（2026-10-18 ~ 10-24） ########==")
W0 = datetime(2026, 10, 18, 0, 0, 0)
for a in ACCTS:
    state = load(f"_state_{a}_1024.json")
    facts = load(f"_facts_{a}_1024.json")
    px = {}
    for h in state["holdings"]:
        px[h["code"]] = h["price"]
    pq = state.get("poolQuote") or {}
    if isinstance(pq, dict):
        for k, v in pq.items():
            if isinstance(v, dict) and v.get("price"):
                px[k] = v["price"]
            elif isinstance(v, (int, float)):
                px[k] = v
    P(f"\n--- {a} ---")
    buy_hits, buy_tot, sell_right, sell_tot, hold_n = 0, 0, 0, 0, 0
    buy_rows, sell_rows = [], []
    for r in state.get("reviewLog", []):
        ra = r.get("reviewedAt")
        try:
            t = dt(ra)
        except Exception:
            continue
        if t < W0 or t > NOW:
            continue
        v = r.get("verdict")
        if v == "BUY":
            buy_tot += 1
            fp = r.get("fillPrice")
            base = fp if fp else r.get("priceAtReview")
            cur = px.get(r["code"]) or r.get("priceAtReview")
            chg = (cur - base) / base * 100 if base and cur else None
            hit = (chg or 0) > 0
            buy_hits += 1 if hit else 0
            buy_rows.append((r, base, cur, chg, hit, fp))
        elif v == "SELL":
            sell_tot += 1
            base = r.get("fillPrice") or r.get("priceAtReview")
            cur = px.get(r["code"])
            chg = (cur - base) / base * 100 if base and cur else None
            right = (chg or 0) < 0
            sell_right += 1 if right else 0
            sell_rows.append((r, base, cur, chg, right))
        elif v == "HOLD":
            hold_n += 1
    P(f"  BUY裁决 {buy_tot} 条，命中(现价>成交/评审价) {buy_hits}，命中率 {buy_hits}/{buy_tot}={buy_hits/buy_tot*100 if buy_tot else 0:.0f}%")
    for r, base, cur, chg, hit, fp in buy_rows:
        P(f"    BUY {r['name']} {r['reviewedAt']} fill={fp} base={base} cur={cur} chg={chg and round(chg,2)}% {'✅' if hit else '❌'} outcome={r['outcome']}")
    P(f"  SELL裁决 {sell_tot} 条，卖对(卖后跌) {sell_right}")
    for r, base, cur, chg, right in sell_rows:
        P(f"    SELL {r['name']} {r['reviewedAt']} base={base} cur={cur} chg={chg and round(chg,2)}% {'✅卖对' if right else '❌卖飞'} outcome={r['outcome']}")
    P(f"  HOLD裁决 {hold_n} 条")
    # 本周成交
    P("  本周成交 (state.trades 10-18起):")
    wkreal = 0
    for t in state.get("trades", []):
        tt = t.get("time") or t.get("fillTime") or ""
        try:
            if dt(tt) < W0:
                continue
        except Exception:
            continue
        P(f"    {t}")
    P(f"  累计realized={state.get('realized')}")

with open("_calc_1024.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("ok")
