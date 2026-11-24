# -*- coding: utf-8 -*-
"""止盈残仓保护回测：+8% 卖出一半后，残仓加「跌回卖价 -x% 即清」的效果。
对应报告 deliverables/auto-trade/tp-residual-guard-2026-11-24.md，改动参数后可直接重跑。

只读分析，不改线上参数。数据：三账户 live state 的 trades + 腾讯日K（server.fetch_kline）。
口径：
  - 触发日 = 收盘浮盈首次 >= 8% 的交易日，卖价 S 取当日收盘（保守：实盘多卖在盘中更高价）。
  - 触发日按规则卖出 50%（全清臂卖 100%），残仓自此日之后逐日判断。
  - 基准臂（现状）= 峰值回撤 trail（要求仍浮盈）+ 硬止损；对照臂再加「收盘 <= S*(1-x) 即清」。
  - 硬止损按 min(开盘, 止损价) 成交，其余按收盘成交，无未来函数。
"""
import json, ssl, sys, urllib.request
sys.path.insert(0, r"C:\Users\49928\WorkBuddy\2026-09-30-19-04-40\multi-market")
sys.stdout.reconfigure(encoding="utf-8")
import server  # noqa: E402

BASE = "https://stock-sim-us.app.workbuddy.host"
SC = "6R7GBTHXQI"
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
PARAM = {"us": (5, -6), "cn_conservative": (5, -6), "cn_aggressive": (6, -8)}
GUARDS = [1.0, 1.5, 2.0, 2.5, 3.0]


def get(a):
    req = urllib.request.Request(f"{BASE}/api/state?account={a}&sharecode={SC}",
                                 headers={"User-Agent": "resim/1.0"})
    with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def episodes_of(trades):
    pos, eps, out = {}, {}, []
    for t in sorted(trades, key=lambda x: x["time"]):
        c, d = t["code"].lower(), t["time"][:10]
        q, p = int(t["qty"]), float(t["price"])
        if t["side"] == "买入":
            cur = pos.setdefault(c, {"shares": 0, "cost": 0.0})
            cur["cost"] = (cur["cost"] * cur["shares"] + p * q) / (cur["shares"] + q)
            cur["shares"] += q
            e = eps.get(c)
            if e is None:
                e = {"code": c, "name": t["name"], "d0": d, "tl": [], "d1": None}
                eps[c] = e
            e["tl"].append(("B", d, p, q))
        else:
            cur = pos.get(c)
            if not cur or cur["shares"] <= 0:
                continue
            q = min(q, cur["shares"])
            cur["shares"] -= q
            e = eps[c]
            e["tl"].append(("S", d, p, q))
            if cur["shares"] <= 0:
                e["d1"] = d
                out.append(e); del eps[c]; del pos[c]
    out += list(eps.values())
    return out


def walk(e, bars, trail, stop, guard=None, allclear=False):
    bd = {b[0]: b for b in bars}
    dates = sorted(bd)
    ti = 0
    sh = cost = cash = inv = 0.0
    peak_close = 0.0
    tp = None
    exit_kind, exit_date, exit_pnl = "期末持有", None, None
    for dt in dates:
        bar = bd[dt]
        while ti < len(e["tl"]) and e["tl"][ti][1] <= dt:
            side, _, p, q = e["tl"][ti]
            if side == "B":
                cost = (cost * sh + p * q) / (sh + q) if sh > 0 else p
                sh += q; inv += p * q
            else:
                q = min(q, sh); sh -= q; cash += p * q
                if sh <= 0:
                    cost = 0.0
            ti += 1
        if sh <= 0 or cost <= 0:
            continue
        close = bar[2]
        pnl = close / cost - 1
        peak_close = max(peak_close, close)
        if tp is None:
            if pnl >= 0.08:
                tp = (dt, close)
                sell = sh if allclear else sh * 0.5
                cash += sell * close; sh -= sell
                exit_kind, exit_date, exit_pnl = ("8%全清", dt, pnl) if allclear else ("持有中", dt, pnl)
            continue
        S = tp[1]
        if guard is not None:
            gp = S * (1 - guard / 100.0)
            if bar[4] <= gp:                      # 日内最低触及保护线，成交价同止损口径
                fill = min(bar[1], gp)
                cash += sh * fill; sh = 0.0
                exit_kind, exit_date, exit_pnl = "保护线", dt, fill / cost - 1
                break
        if pnl <= stop / 100.0:
            fill = min(bar[1], cost * (1 + stop / 100.0))
            cash += sh * fill; sh = 0.0
            exit_kind, exit_date, exit_pnl = "硬止损", dt, pnl
            break
        if pnl > 0 and close <= peak_close * (1 - trail / 100.0):
            cash += sh * close; sh = 0.0
            exit_kind, exit_date, exit_pnl = "峰值回撤", dt, pnl
            break
    if sh > 0 and dates:
        last = bd[dates[-1]][2]
        cash += sh * last
        exit_kind, exit_date, exit_pnl = exit_kind, dates[-1], (last / cost - 1 if cost else 0)
    return cash - inv, (exit_kind, exit_date, exit_pnl, tp)


rows, miss = [], 0
for acc in PARAM:
    s = get(acc)
    trail, stop = PARAM[acc]
    for e in episodes_of(s.get("trades") or []):
        if not e["code"].startswith(("sh", "sz", "us")):
            continue
        d1 = e["d1"] or "2026-11-24"
        try:
            bars = [b for b in server.fetch_kline(e["code"], 250) if e["d0"] <= b[0] <= d1]
        except Exception:
            miss += 1; continue
        if len(bars) < 2:
            miss += 1; continue
        pnl_b, rb = walk(e, bars, trail, stop)
        if rb[3] is None:      # 从未到过 +8%，不在规则覆盖范围
            continue
        pnl_c, _ = walk(e, bars, trail, stop, allclear=True)
        row = {"acc": acc, "name": e["name"], "d0": e["d0"], "d1": e["d1"] or "持有",
               "base": pnl_b, "end": rb, "clear": pnl_c, "G": {}, "tp": rb[3][0], "S": rb[3][1]}
        for g in GUARDS:
            pnl_g, rg = walk(e, bars, trail, stop, guard=g)
            row["G"][g] = (pnl_g, rg, pnl_g - pnl_b)
        rows.append(row)

base_tot = sum(r["base"] for r in rows)
print("样本 %d 个 episode（三账户、收盘浮盈到过 +8%%），K线缺失 %d" % (len(rows), miss))
print("基准臂（半仓落袋 + 残仓只靠 trail/硬止损）合计 %+.0f 元" % base_tot)
print("\n保护档 | 触发 | 合计改善 | 平均触发时浮盈 | 改善/恶化只数")
print("-" * 66)
for g in GUARDS:
    d = sum(r["G"][g][2] for r in rows)
    n = sum(1 for r in rows if r["G"][g][1][0] == "保护线")
    s3 = sum(r["G"][g][1][2] * 100 for r in rows if r["G"][g][1][0] == "保护线" and r["G"][g][1][2] is not None)
    w = sum(1 for r in rows if r["G"][g][2] > 1)
    l = sum(1 for r in rows if r["G"][g][2] < -1)
    print("%5.1f%% | %3d  | %+8.0f | %+7.1f%% | %d / %d" % (g, n, d, (s3 / n) if n else 0, w, l))
print("8%%全清 |  -   | %+8.0f |       -  | -" % sum(r["clear"] - r["base"] for r in rows))
print("\n明细（基准残仓损益升序）：")
for r in sorted(rows, key=lambda x: x["base"]):
    gs = " ".join("%.1f:%+.0f" % (g, r["G"][g][2]) for g in GUARDS)
    print(" %-8s %-6s 触发%s 卖价%.2f 结算%s | 基准%+6.0f 全清%+6.0f | %s" % (
        r["acc"][3:6], r["name"][:6], r["tp"][5:], r["S"], r["end"][0],
        r["base"], r["clear"], gs))
