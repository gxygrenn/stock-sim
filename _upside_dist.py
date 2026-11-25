"""持仓「能涨多少」的实证分布：用三账户真实成交 + 日K 回算。

回答两个问题：
  1) 买入之后，未来 1/3/5/10/20 个交易日内，最高能摸到多少浮盈？（上行空间的经验分布）
  2) 当前持仓未来 5 个交易日的波动区间有多宽？（波动率可估，方向不可估）

用法：NO_PROXY="*" python _upside_dist.py
"""
import json
import os
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi-market"))
sys.stdout.reconfigure(encoding="utf-8")
import server  # noqa: E402

BASE = "https://stock-sim-us.app.workbuddy.host"
SC = "6R7GBTHXQI"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

HORIZONS = (1, 3, 5, 10, 20)


def get(acc):
    req = urllib.request.Request(
        "%s/api/state?account=%s&sharecode=%s" % (BASE, acc, SC),
        headers={"User-Agent": "upside/1.0"})
    with urllib.request.urlopen(req, timeout=90, context=CTX) as f:
        return json.loads(f.read().decode("utf-8", "ignore"))


def episodes_of(trades):
    """按标的聚合买入-清仓周期；未清仓的记为 open。"""
    pos, eps, out = {}, {}, []
    for t in sorted(trades, key=lambda x: x.get("time") or ""):
        code = (t.get("code") or "").lower()
        d = (t.get("time") or "")[:10]
        try:
            qty, px = int(t.get("qty") or 0), float(t.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or px <= 0:
            continue
        if t.get("side") == "买入":
            cur = pos.setdefault(code, {"shares": 0, "cost": 0.0})
            cur["cost"] = (cur["cost"] * cur["shares"] + px * qty) / (cur["shares"] + qty)
            cur["shares"] += qty
            e = eps.setdefault(code, {"code": code, "name": t.get("name"),
                                      "d0": d, "cost": px, "qty": 0, "d1": None})
            e["cost"] = cur["cost"]
            e["qty"] += qty
        else:
            cur = pos.get(code)
            if not cur or cur["shares"] <= 0:
                continue
            cur["shares"] -= min(qty, cur["shares"])
            if cur["shares"] <= 0:
                eps[code]["d1"] = d
                out.append(eps.pop(code))
                pos.pop(code, None)
    out.extend(eps.values())
    return out


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def main():
    rows = []
    missing = 0
    for acc in ("cn_conservative", "cn_aggressive", "us"):
        st = get(acc)
        for e in episodes_of(st.get("trades") or []):
            bars = [b for b in server.fetch_kline(e["code"], 160) if b[0] >= e["d0"]]
            if len(bars) < 2:
                missing += 1
                continue
            highs = [b[3] for b in bars]
            row = {"acc": acc, "code": e["code"], "name": e["name"],
                   "d0": e["d0"], "cost": e["cost"], "open": e["d1"] is None}
            for h in HORIZONS:
                if len(highs) >= 1:
                    n = min(h, len(highs)) - 1
                    row["h%d" % h] = max(highs[:n + 1]) / e["cost"] * 100 - 100
            row["final"] = bars[-1][2] / e["cost"] * 100 - 100
            row["maxto_end"] = max(highs) / e["cost"] * 100 - 100
            rows.append(row)

    print("episode 样本 %d 个（K线缺失 %d）\n" % (len(rows), missing))

    print("一、买入后 N 个交易日内「最高摸到多少浮盈」的经验分布（%%）")
    print("%-10s%6s%9s%9s%9s%9s%9s%9s" % ("口径", "样本", "P10", "P25", "中位", "P75", "P90", "均值"))
    for h in HORIZONS:
        xs = [r["h%d" % h] for r in rows if r.get("h%d" % h) is not None]
        if not xs:
            continue
        print("%-10s%6d%9.1f%9.1f%9.1f%9.1f%9.1f%9.1f" % (
            "第%d日" % h, len(xs), pct(xs, 10), pct(xs, 25), pct(xs, 50),
            pct(xs, 75), pct(xs, 90), sum(xs) / len(xs)))

    for grp, label in ((("cn_conservative", "cn_aggressive"), "A股"),
                       (("us",), "美股")):
        print("\n  【%s】5 日 / 10 日最高浮盈分位" % label)
        for h in (5, 10):
            xs = [r["h%d" % h] for r in rows if r["acc"] in grp and r.get("h%d" % h) is not None]
            if not xs:
                continue
            print("    第%-3d日  P25 %+.1f%%  中位 %+.1f%%  P75 %+.1f%%  P90 %+.1f%%" % (
                h, pct(xs, 25), pct(xs, 50), pct(xs, 75), pct(xs, 90)))

    print("\n二、最终结算收益（已清仓）/ 当前浮动（未清仓）")
    xs = [r["final"] for r in rows]
    print("  全部 %d 笔：P25 %+.1f%%  中位 %+.1f%%  P75 %+.1f%%  均值 %+.1f%%" % (
        len(xs), pct(xs, 25), pct(xs, 50), pct(xs, 75), sum(xs) / len(xs)))
    win = sum(1 for x in xs if x > 0)
    print("  正收益 %d 笔（%.0f%%）" % (win, win * 100.0 / len(xs)))

    print("\n三、当前持仓：未来 5 日波动区间（近 20 日日收益 σ）")
    print("%-8s%-8s%9s%9s%9s%9s%9s" % ("账户", "名称", "现价", "-1σ", "+1σ", "-2σ", "+2σ"))
    for acc in ("cn_conservative", "cn_aggressive", "us"):
        st = get(acc)
        for h in st.get("holdings") or []:
            if (h.get("shares") or 0) <= 0:
                continue
            bars = server.fetch_kline(h["code"], 40)
            cl = [b[2] for b in bars][-21:]
            if len(cl) < 10:
                continue
            rets = [(cl[i] / cl[i - 1] - 1) for i in range(1, len(cl))]
            mu = sum(rets) / len(rets)
            sd = (sum((x - mu) ** 2 for x in rets) / max(1, len(rets) - 1)) ** 0.5
            s5 = sd * (5 ** 0.5)
            px = h["price"]
            print("%-8s%-8s%9.2f%9.2f%9.2f%9.2f%9.2f" % (
                {"us": "us", "cn_conservative": "cnc", "cn_aggressive": "cna"}[acc],
                (h.get("name") or "")[:6], px,
                px * (1 - s5), px * (1 + s5), px * (1 - 2 * s5), px * (1 + 2 * s5)))


if __name__ == "__main__":
    main()
