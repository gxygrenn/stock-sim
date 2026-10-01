#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拉取 A 股指数历史日线，按收盘价算出每日涨跌幅，输出 CSV 供「模拟炒股」工具导入。

数据源默认腾讯财经（web.ifzq.gtimg.cn），东财作为备用。
选腾讯的原因：它响应头带 Access-Control-Allow-Origin:*、不校验 Referer，
而东方财富 push2 / push2his 会直接断开非自家来源的请求，浏览器端无法使用。

用法:
    python fetch_index.py                          # 默认抓上证指数近一年 -> market.csv
    python fetch_index.py --code sz399006 --days 250
    python fetch_index.py --beg 2024-01-01 --end 2026-06-30 --out hs300.csv
    python fetch_index.py --code sh000001 --stdout # 直接打印到屏幕

输出 CSV 每行: 日期,涨跌幅   例如: 2026-01-05,1.24
"""

import argparse
import csv
import datetime
import json
import sys
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

INDEXES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sh000688": "科创50",
}

# 备用源（东财）需要的 secid：沪市 1.xxx，深市 0.xxx
EM_SECID = {
    "sh000001": "1.000001",
    "sz399001": "0.399001",
    "sz399006": "0.399006",
    "sh000300": "1.000300",
    "sh000905": "1.000905",
    "sh000688": "1.000688",
}


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", "ignore")


def fetch_tencent(code, beg, end):
    """腾讯日K。返回 [(日期, 收盘价), ...] 升序。"""
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           "?param={code},day,{beg},{end},640,qfq").format(code=code, beg=beg, end=end)
    payload = json.loads(_get(url))
    if payload.get("code") != 0:
        raise RuntimeError("腾讯接口返回错误: %s" % payload.get("msg"))
    node = ((payload.get("data") or {}).get(code)) or {}
    klines = node.get("day") or node.get("qfqday") or []
    rows = []
    for item in klines:
        if len(item) < 3:
            continue
        try:
            rows.append((item[0].strip(), float(item[2])))  # 日期, 收盘
        except (ValueError, IndexError):
            continue
    return rows


def fetch_eastmoney(code, beg, end):
    """备用源：东财日K。"""
    secid = EM_SECID.get(code)
    if not secid:
        return []
    b, e = beg.replace("-", ""), end.replace("-", "")
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
           "&fields1=f1,f2&fields2=f51,f53&klt=101&fqt=1"
           "&beg={beg}&end={end}&lmt=100000").format(secid=secid, beg=b, end=e)
    payload = json.loads(_get(url))
    klines = ((payload.get("data") or {}).get("klines")) or []
    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            rows.append((parts[0].strip(), float(parts[1])))
        except ValueError:
            continue
    return rows


def to_pct(rows):
    """由收盘价序列计算每日涨跌幅(%)，首日填 0。"""
    out, prev = [], None
    for date, close in rows:
        out.append((date, 0.0 if prev is None or prev == 0 else round((close / prev - 1) * 100, 4)))
        prev = close
    return out


def main():
    ap = argparse.ArgumentParser(description="拉取指数历史日涨跌幅，输出可导入模拟炒股工具的 CSV")
    ap.add_argument("--code", default="sh000001",
                    help="指数代码，可选: " + ", ".join(sorted(INDEXES)))
    ap.add_argument("--days", type=int, default=0, help="抓最近 N 个自然日")
    ap.add_argument("--beg", default="", help="开始日期 YYYY-MM-DD")
    ap.add_argument("--end", default="", help="结束日期 YYYY-MM-DD")
    ap.add_argument("--out", default="market.csv", help="输出 CSV 文件名")
    ap.add_argument("--stdout", action="store_true", help="打印到屏幕而不写文件")
    args = ap.parse_args()

    code = args.code.lower()
    if code not in INDEXES:
        print("不支持的指数代码: {}\n可选: {}".format(code, ", ".join(sorted(INDEXES))), file=sys.stderr)
        return 2

    today = datetime.date.today()
    if args.days:
        beg = (today - datetime.timedelta(days=args.days)).isoformat()
        end = today.isoformat()
    else:
        beg = args.beg or (today - datetime.timedelta(days=365)).isoformat()
        end = args.end or today.isoformat()

    print("正在拉取 {} {} ({} ~ {}) ...".format(INDEXES[code], code, beg, end))
    rows, src = [], ""
    try:
        rows = fetch_tencent(code, beg, end)
        src = "腾讯财经"
    except Exception as exc:
        print("  主源失败: {}，改用东财备用源 ...".format(exc))
        try:
            rows = fetch_eastmoney(code, beg, end)
            src = "东方财富(备用)"
        except Exception as exc2:
            print("拉取失败: {}".format(exc2), file=sys.stderr)
            print("网络不通时，可在工具里手动录入每日涨跌幅，或使用「随机模拟」功能。", file=sys.stderr)
            return 1

    rows = to_pct(rows)
    if not rows:
        print("没有取到任何数据", file=sys.stderr)
        return 1

    ups = sum(1 for _, p in rows if p > 0)
    downs = sum(1 for _, p in rows if p < 0)
    cum = 1.0
    for _, p in rows:
        cum *= (1 + p / 100)
    print("数据源: {} | 共 {} 个交易日，涨 {} 天 / 跌 {} 天，区间 {} ~ {}，累计 {:+.2f}%".format(
        src, len(rows), ups, downs, rows[0][0], rows[-1][0], (cum - 1) * 100))

    if args.stdout:
        writer = csv.writer(sys.stdout)
        writer.writerow(["日期", "涨跌幅"])
        writer.writerows(rows)
    else:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
        print("已写入 {}，在工具里点「导入真实行情 CSV」选择它即可。".format(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
