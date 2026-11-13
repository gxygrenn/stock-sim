# -*- coding: utf-8 -*-
"""行情/新闻网络获取（http 层）。打桩面 fetch_* 保留在 server.py。"""

import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import server  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}



# ---------------------------------------------------------------- 网络
def http_get(url, encoding="utf-8", timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(encoding, "ignore")



def _f(p, i):
    try:
        return float(p[i]) if len(p) > i and p[i] not in ("", "-") else 0.0
    except Exception:
        return 0.0



def _fmt_ts(ts):
    """行情时间戳统一为 'YYYY-MM-DD HH:MM'。
    A股为紧凑格式 20260930160001，美股为 2026-09-30 16:00:01。"""
    if not ts:
        return ""
    if "-" in ts:
        return ts[:16]
    if len(ts) >= 12:
        return "%s-%s-%s %s:%s" % (ts[0:4], ts[4:6], ts[6:8], ts[8:10], ts[10:12])
    return ""



def fetch_quote(code):
    """指数或个股行情。腾讯 qt.gtimg.cn，GBK 编码。支持 A股(sh/sz)与美股(us)。"""
    txt = http_get("https://qt.gtimg.cn/q=" + code, encoding="gbk")
    m = re.search(r'v_\w+="([^"]*)"', txt)
    if not m or not m.group(1):
        raise RuntimeError("行情返回为空")
    p = m.group(1).split("~")
    if len(p) < 33:
        raise RuntimeError("行情字段不足")
    price = float(p[3] or 0)
    pre_close = float(p[4] or 0)
    ts = p[30] or ""
    is_us = code.startswith("us")
    # 成交额：A股 p[37] 单位为万元，美股为美元全额
    amount = _f(p, 37) * (1 if is_us else 1e4)
    return {
        "name": p[1], "code": code, "price": price, "preClose": pre_close,
        "chg": float(p[31] or 0), "pct": float(p[32] or 0),
        "amount": amount, "time": _fmt_ts(ts) or "--",
        "turn": _f(p, 38), "pe": _f(p, 39), "pb": _f(p, 46),
    }



def fetch_news(limit=40):
    """新浪 7x24 财经直播。"""
    url = ("https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=%d"
           "&zhibo_id=152&tag_id=0&dire=f&dpc=1" % limit)
    data = json.loads(http_get(url))
    lst = ((data.get("result") or {}).get("data") or {}).get("feed", {}).get("list") or []
    out = []
    for it in lst:
        text = (it.get("rich_text") or "").strip()
        if not text:
            continue
        mt = re.match(r"[【\[]([^】\]]+)[】\]]", text)
        title = mt.group(1) if mt else text[:30]
        out.append({
            "id": str(it.get("id")),
            "time": it.get("create_time") or "",
            "title": title,
            "text": text[:300],
            "tag": ",".join(t.get("name", "") for t in (it.get("tag") or [])),
        })
    return out
