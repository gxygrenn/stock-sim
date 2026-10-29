#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""A股 R1 六项强势分量计数（腾讯免费日线，含当日盘中 bar）。
用法: python _r1_cn.py sh600031 sz000157 ...
"""
import json, sys, urllib.request, ssl

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
IDX = "sh000001"


def kline(code):
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,70,qfq" % code)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    node = (d.get("data") or {}).get(code) or {}
    rows = node.get("qfqday") or node.get("day") or []
    out = []
    for x in rows:
        try:
            out.append({"d": x[0], "o": float(x[1]), "c": float(x[2]),
                        "h": float(x[3]), "l": float(x[4]), "v": float(x[5])})
        except Exception:
            continue
    return out


def ma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def pct(rows, n):
    if len(rows) < n + 1:
        return None
    a, b = rows[-n - 1]["c"], rows[-1]["c"]
    return (b - a) / a * 100 if a else None


idx = kline(IDX)
idx20 = pct(idx, 20)
print("基准 上证20日涨幅: %s%%（今日 %s）" % (
    ("%.2f" % idx20) if idx20 is not None else "NA", idx[-1]["d"] if idx else "NA"))

for code in sys.argv[1:]:
    try:
        rows = kline(code)
    except Exception as e:
        print("%s 取数失败: %s" % (code, e)); continue
    if len(rows) < 25:
        print("%s 数据不足(%d根)" % (code, len(rows))); continue
    cur = rows[-1]
    prev = rows[-2]
    closes = [r["c"] for r in rows]
    hist = rows[:-1]                     # 不含今日
    hits = []
    # 1 多头排列
    m5, m10, m20 = ma(closes, 5), ma(closes, 10), ma(closes, 20)
    if m5 and m10 and m20 and m5 > m10 > m20:
        hits.append("多头排列")
    # 2 相对强度高
    s20 = pct(rows, 20)
    if s20 is not None and idx20 is not None and s20 > idx20:
        hits.append("相对强度高")
    # 3 20日新高
    if cur["h"] >= max(r["h"] for r in hist[-20:]):
        hits.append("20日新高")
    # 4 放量上涨
    v5 = ma([r["v"] for r in hist], 5)
    if v5 and cur["v"] > 1.2 * v5 and cur["c"] > prev["c"]:
        hits.append("放量上涨")
    # 5 阳线突破
    hi10 = max(r["c"] for r in hist[-10:])
    if cur["c"] > cur["o"] and cur["c"] > hi10:
        hits.append("阳线突破")
    print("%s %s 现价%.2f(%+.2f%%) 量比5日%.2fx 个股20日%s%% | 命中%d项: %s" % (
        code, cur["d"], cur["c"], (cur["c"] - prev["c"]) / prev["c"] * 100,
        (cur["v"] / v5) if v5 else 0,
        ("%.2f" % s20) if s20 is not None else "NA",
        len(hits), "+".join(hits) if hits else "无"))
