#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""A股 R1 六项强势分量（东财日线，腾讯 501 时的备用源）"""
import json, sys, urllib.request, ssl

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE


def secid(code):
    return ("1." if code.startswith("sh") else "0.") + code[2:]


def kline(code):
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
           "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt=70"
           % secid(code))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    out = []
    for row in (d.get("data") or {}).get("klines") or []:
        p = row.split(",")
        try:
            out.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                        "h": float(p[3]), "l": float(p[4]), "v": float(p[5])})
        except Exception:
            continue
    return out


def ma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else None


def pct(rows, n):
    if len(rows) < n + 1:
        return None
    a, b = rows[-n - 1]["c"], rows[-1]["c"]
    return (b - a) / a * 100 if a else None


idx = kline("sh000001")
idx20 = pct(idx, 20)
print("基准 上证20日: %s%% 今日 %s" % (("%.2f" % idx20) if idx20 is not None else "NA",
                                    idx[-1]["d"] if idx else "NA"))

for code in sys.argv[1:]:
    try:
        rows = kline(code)
    except Exception as e:
        print("%s 取数失败: %s" % (code, e)); continue
    if len(rows) < 25:
        print("%s 数据不足(%d)" % (code, len(rows))); continue
    cur, prev = rows[-1], rows[-2]
    closes = [r["c"] for r in rows]
    hist = rows[:-1]
    hits = []
    m5, m10, m20 = ma(closes, 5), ma(closes, 10), ma(closes, 20)
    if m5 and m10 and m20 and m5 > m10 > m20:
        hits.append("多头排列")
    s20 = pct(rows, 20)
    if s20 is not None and idx20 is not None and s20 > idx20:
        hits.append("相对强度高")
    if cur["h"] >= max(r["h"] for r in hist[-20:]):
        hits.append("20日新高")
    v5 = ma([r["v"] for r in hist], 5)
    if v5 and cur["v"] > 1.2 * v5 and cur["c"] > prev["c"]:
        hits.append("放量上涨")
    hi10 = max(r["c"] for r in hist[-10:])
    if cur["c"] > cur["o"] and cur["c"] > hi10:
        hits.append("阳线突破")
    print("%s %s 现价%.2f(%+.2f%%) 量比%.2fx 20日%s%% | 命中%d: %s" % (
        code, cur["d"], cur["c"], (cur["c"] - prev["c"]) / prev["c"] * 100,
        (cur["v"] / v5) if v5 else 0, ("%.2f" % s20) if s20 is not None else "NA",
        len(hits), "+".join(hits) if hits else "无"))
