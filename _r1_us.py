# -*- coding: utf-8 -*-
"""R1 六项强势分量计数（美股版）。基准=标普500 us.INX。"""
import urllib.request, json, sys

CAND = ["usTSLA", "usV", "usMA", "usABBV", "usWMT", "usCOST"]
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    CAND = sys.argv[1:]
SUF = {".OQ": 0, ".N": 0}


def get(code):
    for suf in [".OQ", ".N", ""]:
        c = code + suf
        u = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,70,qfq" % c
        try:
            r = json.loads(urllib.request.urlopen(u, timeout=20).read().decode("utf-8"))
            d = r["data"][c]
            k = d.get("qfqday") or d.get("day")
            if k and len(k) >= 25:
                return c, k
        except Exception:
            continue
    return None, None


def ma(xs, n):
    return sum(xs[-n:]) / n


def analyze(code, bars, bidx):
    rows = [[b[0], float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])] for b in bars]
    dates = [r[0] for r in rows]
    o = [r[1] for r in rows]
    c = [r[2] for r in rows]
    h = [r[3] for r in rows]
    lo = [r[4] for r in rows]
    v = [r[5] for r in rows]

    today = dates[-1]
    px = c[-1]
    prev = c[-2] if len(c) > 1 else c[-1]
    hist_c = c[:-1]  # 不含今日
    hist_h = h[:-1]
    hist_v = v[:-1]

    hits = []
    detail = {}

    # 1 多头排列 MA5>MA10>MA20（用含今日的序列）
    m5, m10, m20 = ma(c, 5), ma(c, 10), ma(c, 20)
    bull = m5 > m10 > m20
    detail["MA"] = "MA5 %.2f / MA10 %.2f / MA20 %.2f" % (m5, m10, m20)
    if bull:
        hits.append("多头排列")

    # 2 相对强度高：近20日涨幅 > 指数近20日涨幅
    stk20 = (px / c[-21] - 1) * 100 if len(c) > 21 else 0.0
    idx20 = (bidx[-1] / bidx[-21] - 1) * 100 if len(bidx) > 21 else 0.0
    detail["RS20"] = "个股 %+.2f%% vs 标普 %+.2f%%" % (stk20, idx20)
    if stk20 > idx20:
        hits.append("相对强度高")

    # 3 20日新高：今日最高 >= 前20日最高
    hi20 = max(hist_h[-20:])
    detail["HI20"] = "今高 %.2f / 前20日高 %.2f" % (h[-1], hi20)
    if h[-1] >= hi20:
        hits.append("20日新高")

    # 4 放量上涨：今日量 > 1.2x 前5日均量 且 现价>昨收
    av5 = sum(hist_v[-5:]) / 5
    detail["VOL"] = "今量 %.0f / 5日均 %.0f = %.2fx，现价%s昨收" % (v[-1], av5, v[-1] / av5 if av5 else 0, ">" if px > prev else "≤")
    if av5 and v[-1] > 1.2 * av5 and px > prev:
        hits.append("放量上涨")

    # 5 阳线突破：现价>今开 且 现价>前10日最高收盘
    hi10c = max(hist_c[-10:])
    detail["BRK"] = "现价 %.2f / 今开 %.2f / 前10日最高收 %.2f" % (px, o[-1], hi10c)
    if px > o[-1] and px > hi10c:
        hits.append("阳线突破")

    # 6 龙虎榜主力净买：美股无此数据，记未命中
    return today, px, prev, hits, detail


def main():
    ic, ibars = get("us.INX")
    if not ibars:
        print("指数取数失败")
        sys.exit(1)
    bidx = [float(b[2]) for b in ibars]
    print("基准 %s 最新 %s  收 %.2f" % (ic, ibars[-1][0], bidx[-1]))
    for code in CAND:
        c, bars = get(code)
        if not bars:
            print("\n%s 取数失败" % code)
            continue
        today, px, prev, hits, detail = analyze(code, bars, bidx)
        chg = (px / prev - 1) * 100 if prev else 0
        print("\n%s (%s) %s 现价 %.2f 当日 %+.2f%%" % (code, c, today, px, chg))
        for k in ["MA", "RS20", "HI20", "VOL", "BRK"]:
            print("   %-5s %s" % (k, detail[k]))
        print("   命中 %d/6：%s" % (len(hits), "+".join(hits) if hits else "无"))


main()
