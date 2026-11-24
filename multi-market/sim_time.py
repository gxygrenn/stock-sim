# -*- coding: utf-8 -*-
"""交易时段与时间工具：A股/美股开市判定、批单时效、价格缓存。"""

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



def age_hours(ts):
    """时间戳距今多少小时；无法解析返回 -1。"""
    try:
        return (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600.0
    except Exception:
        return -1.0



def us_dst(now=None):
    """美国夏令时（DST）判断：3月第二个周日 02:00 开始，11月第一个周日 02:00 结束。"""
    d = now or datetime.now()
    # 3月第二个周日
    mar1 = datetime(d.year, 3, 1)
    second_sun_mar = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    # 11月第一个周日
    nov1 = datetime(d.year, 11, 1)
    first_sun_nov = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    return second_sun_mar <= d < first_sun_nov



def cn_trading(now):
    """A 股连续竞价时段（工作日 09:30-11:30 / 13:00-15:00），法定节假日休市。"""
    if now.weekday() >= 5 or is_cn_holiday(now):
        return False
    hm = now.hour * 60 + now.minute
    return (570 <= hm <= 690) or (780 <= hm <= 900)



def cn_close_sweep_window(now):
    """A 股收盘前补扫窗口：工作日 14:55-14:59（法定节假日休市）。

    背景（2026-11-24）：check_stops 只在每小时 :40 的扫描时刻取价，14:40 到 15:00
    收盘之间是 20 分钟盲区。建设银行 11-20 收盘 +8.75%、盘中最高 +8.92%，越过 8%
    止盈线却一股未卖（tpDate 为空），11-23 盘中再到 +8.41% 同样漏掉，两日回吐约 374 元。
    这不是阈值问题，是「根本没机会触发」。补扫只做纯代码判定，不取新闻、不算信号。
    """
    if now.weekday() >= 5 or is_cn_holiday(now):
        return False
    hm = now.hour * 60 + now.minute
    return 14 * 60 + 55 <= hm < 15 * 60



def us_trading(now):
    """美股常规交易时段（按北京时间换算）。夏令时 21:30-次日04:00 / 冬令时 22:30-次日05:00。"""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    if us_dst(now):
        return (hm >= 21 * 60 + 30) or (hm < 4 * 60)      # 21:30 - 次日 04:00
    return (hm >= 22 * 60 + 30) or (hm < 5 * 60)          # 22:30 - 次日 05:00



def _next_session_open(dt, market=None):
    """dt 之后（含）的第一个交易时段开始时刻；dt 本身在盘中则原样返回。"""
    market = market or ((ACCOUNTS.get(server.current_account()) or {}).get("market") or "cn")
    cur = dt
    for _ in range(20):
        if market == "us":
            hm = cur.hour * 60 + cur.minute
            early_cut = 4 * 60 if us_dst(cur) else 5 * 60
            # 凌晨时段承接前一交易日晚间开盘，故周六凌晨仍算盘中（美股周五场）
            if hm < early_cut and 1 <= cur.weekday() <= 5:
                return cur
            if cur.weekday() < 5:
                if hm >= (21 * 60 + 30 if us_dst(cur) else 22 * 60 + 30):
                    return cur
                return cur.replace(hour=(21 if us_dst(cur) else 22), minute=30,
                                   second=0, microsecond=0)
        else:
            if cur.weekday() < 5 and not is_cn_holiday(cur):
                hm = cur.hour * 60 + cur.minute
                if 570 <= hm < 690 or 780 <= hm < 900:
                    return cur
                if hm < 570:
                    return cur.replace(hour=9, minute=30, second=0, microsecond=0)
                if hm < 780:
                    return cur.replace(hour=13, minute=0, second=0, microsecond=0)
        cur = (cur + timedelta(days=1)).replace(
            hour=21 if market == "us" else 9, minute=30, second=0, microsecond=0)
    return dt



def approved_age_hours(ts, market=None):
    """已批准结论的「有效年龄」：从 max(评审时刻, 下一个交易时段开盘) 起算。

    2026-10-28 修正：周末/深夜批出来的单，计时起点顺延到下一个交易时段开盘，
    避免在根本没有成交机会的时段里空转过期（T15：10-26 三账户各废 5 条，
    15 条判断零成本沉没）。盘中批的单仍按原 8h/12h 计时，隔夜陈旧结论照样作废
    ——「A股只当日成交、隔夜一律重评」的防线不变。时间戳解析失败退回墙钟口径。
    """
    market = market or ((ACCOUNTS.get(server.current_account()) or {}).get("market") or "cn")
    try:
        ref = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return age_hours(ts)
    start = _next_session_open(ref, market)
    return (datetime.now() - start).total_seconds() / 3600.0



def cached_price(s, code):
    """取上一次刷新缓存下来的现价（不发网络请求），用于入队时留档信号价。"""
    q = (s.get("poolQuote") or {}).get(code) or {}
    try:
        return float(q.get("price") or 0)
    except Exception:
        return 0.0
