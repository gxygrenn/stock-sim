# -*- coding: utf-8 -*-
"""本周(10-18~10-24) realized 精确复算：全量 trades 加权成本 -> 本周卖出盈亏"""
import json
from datetime import datetime

def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")

W0 = datetime(2026, 10, 18, 0, 0, 0)

for a in ["us", "cn_conservative", "cn_aggressive"]:
    state = json.load(open(f"_state_{a}_1024.json", encoding="utf-8"))
    trades = state.get("trades", [])
    # 按 code 聚合买入成本（加权）与卖出
    buys = {}   # code -> [qty, cost]
    sells_week = []
    for t in sorted(trades, key=lambda x: x["time"]):
        c = t["code"]
        if t["side"] == "买入":
            q, cst = buys.get(c, [0, 0.0])
            buys[c] = [q + t["qty"], cst + t["amount"]]
        else:
            q0, cst0 = buys.get(c, [0, 0.0])
            avg = cst0 / q0 if q0 else 0
            if dt(t["time"]) >= W0:
                sells_week.append((t, avg, t["amount"] - avg * t["qty"]))
            buys[c] = [q0 - t["qty"], cst0 - avg * t["qty"]] if q0 else [0, 0.0]
    tot = sum(r for _, _, r in sells_week)
    print(f"=== {a} 本周卖出 {len(sells_week)} 笔, 周realized={tot:+.2f} (服务端累计realized={state.get('realized'):+.2f})")
    for t, avg, rl in sells_week:
        print(f"  {t['time']} {t['name']} 卖{t['qty']}股@{t['price']} 均价成本{avg:.4f} realized={rl:+.2f} reason={t['reason'][:40]}")
    print(f"  nextDate={state.get('nextDate')} lastTrade={state.get('lastTrade')} regime={state.get('regime')}")
