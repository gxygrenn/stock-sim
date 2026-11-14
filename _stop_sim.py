# -*- coding: utf-8 -*-
"""止损线反事实分析：模拟 stopLossPct 不同档位对三账户的实际损益影响。
只读分析。数据源：backup/2026-11-13-2130 三账户 state + 腾讯日K（server.fetch_kline）。
方法：每个持仓 episode 逐日重放（当日先查止损线、未触发再处理当日交易），
某档位触发 → 剩余持仓按止损价清仓（跳空低开按开盘价）、后续该episode买卖取消。"""
import json, os, sys
sys.path.insert(0, r"C:\Users\49928\WorkBuddy\2026-09-30-19-04-40\multi-market")
sys.stdout.reconfigure(encoding="utf-8")
import server  # noqa: E402

BK = r"C:\Users\49928\WorkBuddy\2026-09-30-19-04-40\backup\2026-11-13-2130"
ACC = [("us", -8, -5), ("cn_conservative", -8, -5), ("cn_aggressive", -15, -8)]
X_SET = [-4, -5, -6, -7, -8, -10, -12, -15]


def episodes_of(trades):
    """按 code 聚合连续持仓 episode。旧->新重放。"""
    pos, eps, out = {}, {}, []
    for t in sorted(trades, key=lambda x: x["time"]):
        c, d = t["code"].lower(), t["time"][:10]
        amt, p, q = float(t["amount"]), float(t["price"]), int(t["qty"])
        if t["side"] == "买入":
            cur = pos.setdefault(c, {"shares": 0, "cost": 0.0})
            cur["shares"] += q
            cur["cost"] = (cur["cost"] * (cur["shares"] - q) + p * q) / cur["shares"]
            e = eps.get(c)
            if e is None:
                e = {"code": c, "name": t["name"], "d0": d, "tl": [], "shares": 0,
                     "cost": 0.0, "invested": 0.0, "cash": 0.0, "d1": None}
                eps[c] = e
            e["tl"].append(("B", d, p, q, amt))
        else:
            cur = pos.get(c)
            if not cur or cur["shares"] <= 0:
                continue
            q = min(q, cur["shares"])
            cur["shares"] -= q
            e = eps[c]
            e["tl"].append(("S", d, p, q, p * q))
            if cur["shares"] <= 0:
                e["d1"] = d
                out.append(e)
                del eps[c]
                del pos[c]
    out += list(eps.values())
    return out


def replay(e, bars, X):
    """返回 (pnl_amount, stop_date|None)。X=None 表示实际结局（不设止损）。"""
    bd = {b[0]: b for b in bars}
    dates = sorted(bd)
    tl = e["tl"]
    ti = 0
    sh = cost = inv = cash = 0.0
    stop_date = None
    for dt in dates:
        bar = bd[dt]
        if X is not None and sh > 0 and cost > 0:
            sp = cost * (1 + X / 100.0)
            if bar[4] <= sp:  # 当日最低触及止损线
                fill = min(bar[1], sp)  # 低开按开盘价
                cash += sh * fill
                sh = 0.0
                stop_date = dt
                break  # 后续买卖全部取消
        while ti < len(tl) and tl[ti][1] <= dt:
            side, _, p, q, amt = tl[ti]
            if sh <= 0:
                cost = 0.0
            if side == "B":
                cost = (cost * sh + p * q) / (sh + q) if sh > 0 else p
                sh += q
                inv += amt
            else:
                q = min(q, sh)
                sh -= q
                cash += p * q
                if sh <= 0:
                    cost = 0.0
            ti += 1
        if X is None and e["d1"] and dt >= e["d1"]:
            break
    if sh > 0:  # 窗口结束仍持有（episode 未闭合）按最后一根 close 估值
        cash += sh * bd[dates[-1]][2]
    return cash - inv, stop_date


for acc, cur_stop, cur_rr in ACC:
    s = json.load(open(os.path.join(BK, "state_%s.json" % acc), encoding="utf-8"))
    eps = episodes_of(s.get("trades", []))
    hold = {h["code"].lower(): h for h in s["holdings"] if h.get("shares", 0) > 0}
    rows, miss = [], 0
    for e in eps:
        d1 = e["d1"] or "2026-11-13"
        try:
            bars = [b for b in server.fetch_kline(e["code"], 90)
                    if e["d0"] <= b[0] <= d1]
        except Exception:
            miss += 1
            continue
        if len(bars) < 2:
            miss += 1
            continue
        h = hold.get(e["code"])
        # 实际结局
        act, _ = replay(e, bars, None)
        if e["d1"] is None and h:  # 未闭合按现价校正
            act = act + (h["price"] - bars[-1][2]) * 0  # bars[-1] 即 11-13，与 state 同源
        cost0 = None
        for t in e["tl"]:
            if t[0] == "B":
                pass
        # 期初成本（首笔买入价，报告用）
        first_buy = next(t for t in e["tl"] if t[0] == "B")
        row = {"name": e["name"], "d0": e["d0"], "d1": e["d1"] or "持有",
               "cost": first_buy[2], "act": act, "inv": 0.0, "X": {}}
        for _, _, p, q, amt in [t for t in e["tl"] if t[0] == "B"]:
            row["inv"] += amt
        mae = min(b[4] for b in bars)
        row["mae_pct"] = (mae / first_buy[2] - 1) * 100
        row["act_pct"] = act / row["inv"] * 100 if row["inv"] else 0
        for X in X_SET:
            pnl, sd = replay(e, bars, X)
            row["X"][X] = (pnl, sd)
        rows.append(row)
    print("\n===== %s（episode %d，K线缺失 %d）当前硬止损 %.0f%% / 复核线 %.0f%%" % (
        acc, len(eps), miss, cur_stop, cur_rr))
    act_total = sum(r["act"] for r in rows)
    print("  实际已实现+浮动合计: %+.0f 元（%d 个 episode）" % (act_total, len(rows)))
    print("  档位 | 触发数 | 合计损益 | 相对实际 | 仍触发episode平均少亏/多亏")
    for X in X_SET:
        hits = [(r, r["X"][X]) for r in rows if r["X"][X][1]]
        tot = sum(v[0] for r, v in [(r, r["X"][X]) for r in rows])
        delta = tot - act_total
        nh = len(hits)
        per = (delta / nh) if nh else 0.0
        tag = " ←现状" if X == cur_stop else ""
        print("   %3d%% | %3d    | %+8.0f | %+8.0f | %+6.0f 元%s" % (X, nh, tot, delta, per, tag))
    # 触发明细（只列 MAE≤-5 或实际亏损的）
    print("  关键 episode 明细：")
    for r in sorted(rows, key=lambda x: x["act_pct"]):
        if r["act_pct"] > -2 and r["mae_pct"] > -4.5:
            continue
        xs = " ".join("%d%%:%s" % (X, ("%+d" % round(r["X"][X][0])) if r["X"][X][1] else "-")
                      for X in [-5, -6, -7, -8])
        print("   %-8s %s→%s 买%.2f 实际%+.1f%%(MAE%.1f%%) | %s" % (
            r["name"], r["d0"][5:], r["d1"][5:] if r["d1"] != "持有" else "持有",
            r["cost"], r["act_pct"], r["mae_pct"], xs))
