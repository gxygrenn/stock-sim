# -*- coding: utf-8 -*-
"""一次性校验：用「从当前持仓倒推」重建历史卖出的单笔成本，并与账户 realized 对账。

倒推依据：
  1) do_sell 不改 h["cost"]，所以卖点成本 = 卖后成本；
  2) do_buy 会重算成本：cost_new = (cost_old*qty_old + amount + fee) / (qty_old + q)，
     倒推即 cost_old = (cost_new*(qty_old+q) - amount - fee) / qty_old。
从最新流水往回走，维护 code -> (qty, cost)，遇到卖出就地记下当时成本。
只读，不写任何存档。
"""
import json
import os
import sys

sys.path.insert(0, '.')
import _intraday as I


def backfill(trades, holdings):
    """trades 为新的在前；返回 {id(tr): (cost_price, pnl, pnl_pct)}"""
    qty = {}
    cost = {}
    for h in holdings:
        if h.get("shares", 0) > 0:
            qty[h["code"]] = h["shares"]
            cost[h["code"]] = h["cost"]
    out = {}
    broken = set()
    for tr in reversed(trades):          # 从最老往最新 —— 实际需从最新往回，见下
        pass
    # 正序倒推：trades 新→旧，直接按列表顺序遍历即为「从最新往回」
    for tr in trades:
        code = tr["code"]
        side = tr["side"]
        q = int(tr["qty"])
        px = float(tr["price"])
        fee = float(tr.get("fee") or 0)
        if side == "卖出":
            c = cost.get(code)
            if c and code not in broken:
                gross = (px - c) * q
                out[id(tr)] = (round(c, 3), round(gross - fee, 2),
                               round((gross - fee) / (c * q) * 100, 2) if c * q else 0.0)
            qty[code] = qty.get(code, 0) + q     # 倒推：卖前股数更多
            # 成本不变
        else:                                  # 买入
            q0 = qty.get(code, 0) - q          # 买前股数
            c1 = cost.get(code)
            if c1 is None:
                broken.add(code)
                continue
            if q0 <= 0:
                # 这一笔就是建仓，再往前没有该票持仓
                qty[code] = 0
                cost.pop(code, None)
                continue
            c0 = (c1 * (q0 + q) - px * q - fee) / q0
            qty[code] = q0
            cost[code] = c0
    return out, broken


def main():
    accounts = ["us", "cn_conservative", "cn_aggressive"]
    for acc in accounts:
        st, s = I.call('/api/state?account=' + acc)
        trades = s.get("trades") or []
        holdings = s.get("holdings") or []
        res, broken = backfill(trades, holdings)
        sells = [t for t in trades if t["side"] == "卖出"]
        got = sum(res[id(t)][1] for t in sells if id(t) in res)
        total = sum(t["pnl"] for t in sells if "pnl" in t)
        print("== %s 卖出 %d 笔，可重建 %d 笔｜重建合计 %.2f｜已有 pnl 合计 %.2f｜realized %.2f"
              % (acc, len(sells), len(res), got, total, s.get("realized") or 0))
        miss = [t for t in sells if id(t) not in res]
        if miss:
            print("   无法重建：", [(t["time"], t["name"]) for t in miss[:6]])
        if broken:
            print("   成本链断裂的票：", sorted(broken)[:6])


if __name__ == "__main__":
    main()
