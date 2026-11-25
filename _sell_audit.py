"""卖出端归因：已卖出的票，卖出价相对「事后最高点」差了多少。

对每一笔卖出：
  - regret5 / regret20 = 卖出后 5 / 20 个交易日内的最高价 相对 卖出价 的涨幅
    （正 = 卖飞了，负 = 卖对了）
  - sell_vs_prepeak = 卖出价 相对 卖出前 20 日最高价 的折价
    （衡量「卖出时是不是已经让出了多少涨幅」）

用法：NO_PROXY="*" python _sell_audit.py
"""
import json
import os
import ssl
import sys
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi-market"))
sys.stdout.reconfigure(encoding="utf-8")
import server  # noqa: E402

BASE = "https://stock-sim-us.app.workbuddy.host"
SC = "6R7GBTHXQI"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

_CACHE = {}


def get(acc):
    req = urllib.request.Request(
        "%s/api/state?account=%s&sharecode=%s" % (BASE, acc, SC),
        headers={"User-Agent": "sellaudit/1.0"})
    with urllib.request.urlopen(req, timeout=90, context=CTX) as f:
        return json.loads(f.read().decode("utf-8", "ignore"))


def kline(code):
    if code not in _CACHE:
        try:
            _CACHE[code] = server.fetch_kline(code, 260)
        except Exception:
            _CACHE[code] = []
    return _CACHE[code]


def classify(reason):
    r = reason or ""
    if "目标止盈" in r:
        return "目标止盈"
    if "移动止盈" in r:
        return "移动止盈"
    if "浮亏减半" in r:
        return "浮亏减半"
    if "止损" in r and "专家团" not in r:
        return "硬止损"
    if "专家团SELL" in r or "评审" in r:
        return "专家团SELL"
    if "减仓" in r or "超配" in r:
        return "超配减仓"
    return "其它"


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


_IDX = {}


def index_series(acc):
    """同期基准：A 股用上证 sh000001，美股用标普 usINX。"""
    code = "usINX" if acc == "us" else "sh000001"
    if code not in _IDX:
        try:
            _IDX[code] = server.fetch_kline(code, 260)
        except Exception:
            _IDX[code] = []
    return _IDX[code]


def idx_at(acc, d):
    v = None
    for b in index_series(acc):
        if b[0] <= d:
            v = b
        else:
            break
    return v


def main():
    rows = []
    miss = 0
    for acc in ("cn_conservative", "cn_aggressive", "us"):
        st = get(acc)
        for t in st.get("trades") or []:
            if "卖" not in str(t.get("side")):
                continue
            code = (t.get("code") or "").lower()
            d = (t.get("time") or "")[:10]
            px = float(t.get("price") or 0)
            qty = int(t.get("qty") or 0)
            if px <= 0 or qty <= 0:
                continue
            bars = kline(code)
            idx = None
            for i, b in enumerate(bars):
                if b[0] <= d:
                    idx = i
                else:
                    break
            if idx is None or idx < 5:
                miss += 1
                continue
            pre = bars[max(0, idx - 20):idx]
            post = bars[idx + 1:]
            pre_high = max(b[3] for b in pre) if pre else None
            r5 = (max(b[3] for b in post[:5]) / px - 1) * 100 if len(post) >= 1 else None
            # 最高价口径只作理论上限；不足 10 个交易日的不进 20 日统计
            r20 = (max(b[3] for b in post[:20]) / px - 1) * 100 if len(post) >= 10 else None
            npost = len(post)
            # 收盘口径 = 真实可实现的持有结果
            close_ret = (post[min(20, npost) - 1][2] / px - 1) * 100 if npost >= 5 else None
            # 同期基准（卖出日至对应日）
            ib0 = idx_at(acc, d)
            after = [b for b in index_series(acc) if b[0] > d][:min(20, max(npost, 1))]
            idx_ret = (after[-1][2] / ib0[2] - 1) * 100 if (ib0 and after) else None
            rows.append({
                "acc": acc, "code": code, "name": t.get("name"), "d": d,
                "px": px, "qty": qty, "amt": px * qty,
                "kind": classify(t.get("reason")),
                "r5": r5, "r20": r20, "close_ret": close_ret, "idx_ret": idx_ret,
                "vs_pre": (px / pre_high - 1) * 100 if pre_high else None,
                "npost": len(post),
            })

    print("卖出样本 %d 笔（K线缺失 %d）\n" % (len(rows), miss))

    print("一、按卖出原因分组：卖出后 5 / 20 日最高价 vs 卖出价（正 = 卖飞）")
    print("%-12s%6s%10s%10s%10s%11s%9s" % ("原因", "笔数", "5日均值", "5日中位", "20日均值", "20日中位", "继续涨%"))
    g = defaultdict(list)
    for r in rows:
        g[r["kind"]].append(r)
    order = ["目标止盈", "移动止盈", "浮亏减半", "硬止损", "专家团SELL", "超配减仓", "其它"]
    for k in order:
        v = g.get(k) or []
        if not v:
            continue
        a5 = [x["r5"] for x in v if x["r5"] is not None]
        a20 = [x["r20"] for x in v if x["r20"] is not None]
        up = sum(1 for x in a20 if x > 0) * 100.0 / len(a20) if a20 else 0
        print("%-12s%6d%9.2f%%%9.2f%%%9.2f%%%10.2f%%%8.0f%%" % (
            k, len(v), sum(a5) / len(a5) if a5 else 0, pct(a5, 50) or 0,
            sum(a20) / len(a20) if a20 else 0, pct(a20, 50) or 0, up))

    all20 = [r["r20"] for r in rows if r["r20"] is not None]
    print("\n  全部：20 日最高价平均 %+.2f%%、中位 %+.2f%%（理论上限，不可实现）" % (
        sum(all20) / len(all20), pct(all20, 50)))

    print("\n一之二、扣掉同期大盘后的真实机会成本（收盘口径，持有 5~20 个交易日）")
    print("%-12s%6s%11s%11s%11s%9s" % ("原因", "笔数", "卖后收盘", "同期基准", "超额", "跑输基准%"))
    for k in order:
        v = [x for x in (g.get(k) or []) if x["close_ret"] is not None]
        if not v:
            continue
        cr = [x["close_ret"] for x in v]
        ir = [x["idx_ret"] for x in v if x["idx_ret"] is not None]
        ex = [x["close_ret"] - x["idx_ret"] for x in v if x["idx_ret"] is not None]
        print("%-12s%6d%10.2f%%%10.2f%%%10.2f%%%8.0f%%" % (
            k, len(v), sum(cr) / len(cr), sum(ir) / len(ir) if ir else 0,
            sum(ex) / len(ex) if ex else 0,
            sum(1 for x in ex if x < 0) * 100.0 / len(ex) if ex else 0))

    vv = [x for x in rows if x["close_ret"] is not None and x["idx_ret"] is not None]
    if vv:
        ex = [x["close_ret"] - x["idx_ret"] for x in vv]
        print("  %-10s%6d%10.2f%%%10.2f%%%10.2f%%%8.0f%%" % (
            "合计", len(vv), sum(x["close_ret"] for x in vv) / len(vv),
            sum(x["idx_ret"] for x in vv) / len(vv), sum(ex) / len(ex),
            sum(1 for x in ex if x < 0) * 100.0 / len(ex)))
        print("  卖后 20 日收盘相对基准的累计超额：%+.0f 元（正 = 卖出后跑赢大盘，即卖早了）" % sum(
            x["amt"] * (x["close_ret"] - x["idx_ret"]) / 100 for x in vv))

    print("\n二、卖出价相对「卖出前 20 日最高价」的折价（负 = 已经从高点让出）")
    for k in order:
        v = [x["vs_pre"] for x in (g.get(k) or []) if x["vs_pre"] is not None]
        if not v:
            continue
        print("  %-10s 中位 %+6.2f%%   平均 %+6.2f%%" % (k, pct(v, 50), sum(v) / len(v)))

    print("\n三、卖飞金额最大的 10 笔（按 20 日最高价计的机会成本）")
    print("%-6s%-9s%-8s%-6s%8s%8s%9s%10s" % ("账户", "名称", "日期", "原因", "卖出价", "20日高", "涨幅", "少赚"))
    for r in sorted([x for x in rows if x["r20"] is not None], key=lambda x: -x["r20"])[:10]:
        print("%-6s%-9s%-8s%-6s%8.2f%8.2f%+8.1f%%%9.0f" % (
            {"us": "us", "cn_conservative": "cnc", "cn_aggressive": "cna"}[r["acc"]],
            (r["name"] or "")[:6], r["d"][5:], r["kind"][:4], r["px"],
            r["px"] * (1 + r["r20"] / 100), r["r20"], r["amt"] * r["r20"] / 100))

    print("\n四、卖得最对的 5 笔（卖出后继续跌，躲过损失）")
    print("%-6s%-9s%-8s%-6s%8s%8s%9s%10s" % ("账户", "名称", "日期", "原因", "卖出价", "20日高", "涨幅", "避损"))
    for r in sorted([x for x in rows if x["r20"] is not None], key=lambda x: x["r20"])[:5]:
        print("%-6s%-9s%-8s%-6s%8.2f%8.2f%+8.1f%%%9.0f" % (
            {"us": "us", "cn_conservative": "cnc", "cn_aggressive": "cna"}[r["acc"]],
            (r["name"] or "")[:6], r["d"][5:], r["kind"][:4], r["px"],
            r["px"] * (1 + r["r20"] / 100), r["r20"], r["amt"] * r["r20"] / 100))

    net = sum(r["amt"] * r["r20"] / 100 for r in rows if r["r20"] is not None)
    print("\n  按事后最高价计的机会成本上限 %+.0f 元（正 = 卖飞，但事后最高点不可实现）" % net)


if __name__ == "__main__":
    main()
