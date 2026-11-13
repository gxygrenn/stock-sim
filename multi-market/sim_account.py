# -*- coding: utf-8 -*-
"""账户与交易：买卖成交、费用、节假日、结算、峰值跟踪与止盈止损。"""

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



# ---------------------------------------------------------------- 账户
def total_mv(s):
    return sum(h["price"] * h["shares"] for h in s["holdings"])



def total_asset(s):
    return s["cash"] + total_mv(s)



def buy_fee(amount):
    # 美股零佣金（对标 Robinhood 等零佣金通道），无印花税/过户费
    return 0.0



def sell_fee(amount):
    # 仅 SEC 交易费（约 22.9 美元/百万，仅卖出端收取）
    return round(amount * 0.000023, 2)



def find_holding(s, code):
    for h in s["holdings"]:
        if h["code"] == code:
            return h
    return None



def do_buy(s, name, code, price, qty, reason):
    qty = int(qty)     # 美股 1 股起，不整手
    if qty <= 0 or price <= 0:
        return None
    amount = price * qty
    # 2026-10-15 教训（激进版）：现金耗尽后仍通过 88~302 元的零股买单，无资金意义
    # 却承担个股风险。最小下单金额校验：低于门槛直接拒单，保留弹药。
    min_amt = float(s.get("config", {}).get("minBuyAmount", 0) or 0)
    if min_amt > 0 and amount < min_amt:
        log("拒单 %s：金额 %.0f 低于最小下单金额 %.0f，保留弹药" % (name, amount, min_amt))
        return None
    fee = buy_fee(amount)
    if amount + fee > s["cash"]:
        return None
    s["cash"] -= amount + fee
    s["fees"] += fee
    h = find_holding(s, code)
    if h is None:
        h = {"id": s["seq"], "name": name, "code": code, "price": price,
             "preClose": price, "shares": 0, "cost": 0.0, "beta": 1.0, "pct": 0.0}
        s["seq"] += 1
        s["holdings"].append(h)
    h["cost"] = (h["cost"] * h["shares"] + amount) / (h["shares"] + qty)
    h["shares"] += qty
    h["price"] = price
    tr = {"time": now_str(), "side": "买入", "name": name, "code": code,
          "price": round(price, 3), "qty": qty, "amount": round(amount, 2), "reason": reason}
    s["trades"].insert(0, tr)
    s["trades"] = s["trades"][:300]
    s["lastTrade"][code] = now_str()
    s["roundTrades"] = int(s.get("roundTrades", 0)) + 1
    log("买入 %s %d 股 @%.3f（%s）" % (name, qty, price, reason))
    return tr



def do_sell(s, name, code, price, qty, reason):
    h = find_holding(s, code)
    if h is None or h["shares"] <= 0:
        return None
    qty = min(int(qty), h["shares"])
    if qty <= 0:
        return None
    amount = price * qty
    fee = sell_fee(amount)
    s["cash"] += amount - fee
    s["fees"] += fee
    s["realized"] += (price - h["cost"]) * qty
    h["shares"] -= qty
    if h["shares"] <= 0:
        h["shares"] = 0
        h["cost"] = 0.0
    h["price"] = price
    tr = {"time": now_str(), "side": "卖出", "name": name, "code": code,
          "price": round(price, 3), "qty": qty, "amount": round(amount, 2), "reason": reason}
    s["trades"].insert(0, tr)
    s["trades"] = s["trades"][:300]
    s["lastTrade"][code] = now_str()
    s["roundTrades"] = int(s.get("roundTrades", 0)) + 1
    log("卖出 %s %d 股 @%.3f（%s）" % (name, qty, price, reason))
    return tr



# 2026 年 A 股法定节假日休市日期（上交所官方公告；周末不在此列，由 weekday 判断处理）。
# 仅覆盖 2026 年，跨年后需人工续补。
CN_HOLIDAYS_2026 = {
    # 元旦：1/1(四)~1/3(六)
    "2026-01-01", "2026-01-02", "2026-01-03",
    # 春节：2/15(日)~2/23(一)
    "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    # 清明：4/4(六)~4/6(一)
    "2026-04-04", "2026-04-05", "2026-04-06",
    # 劳动节：5/1(五)~5/5(二)
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    # 端午：6/19(五)~6/21(日)
    "2026-06-19", "2026-06-20", "2026-06-21",
    # 中秋：9/25(五)~9/27(日)
    "2026-09-25", "2026-09-26", "2026-09-27",
    # 国庆：10/1(四)~10/7(三)
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
    "2026-10-06", "2026-10-07",
}



def is_cn_holiday(dt):
    """判断某日是否为 A 股法定节假日休市日（不含周末）。"""
    return dt.strftime("%Y-%m-%d") in CN_HOLIDAYS_2026



def next_trading_day(ds):
    y, m, d = [int(x) for x in ds.split("-")]
    cur = datetime(y, m, d) + timedelta(days=1)
    while cur.weekday() >= 5 or is_cn_holiday(cur):
        cur += timedelta(days=1)
    return cur.strftime("%Y-%m-%d")



def _closed_session_label(market, now=None):
    """该市场「最近一个已收盘交易日」的日期标签。

    2026-10-28 修正：结算标签只能由它决定。此前用 s["nextDate"]，而 nextDate 会
    因漏结算而落后于真实行情日，于是把今日收盘数据写进历史日期（cn_aggressive
    自 10-19 起、cn_conservative 自 10-22 起 history 整体错位一天）。
    """
    now = now or datetime.now()
    if market == "us":
        # 美股收盘落在北京时间次日凌晨，先换算回美东当日；
        # 该日若还没到收盘（美东 <16:00），最近已收盘的交易日要再退一天
        shift = 12 if us_dst(now) else 13
        et = now - timedelta(hours=shift)
        cur = datetime(et.year, et.month, et.day)
        if et.hour < 16:
            cur -= timedelta(days=1)
        for _ in range(10):
            if cur.weekday() < 5:
                break
            cur -= timedelta(days=1)
        return cur.strftime("%Y-%m-%d")
    cur = datetime(now.year, now.month, now.day)
    for _ in range(15):
        if cur.weekday() < 5 and not is_cn_holiday(cur):
            break
        cur -= timedelta(days=1)
    return cur.strftime("%Y-%m-%d")



def settle(s, mkt_pct, date_str=None):
    """结算：记录当日快照。当日盈亏 = 总资产变化（含手续费）。

    标签来源（2026-10-28 修正）：自动结算一律用「最近一个已收盘交易日」，
    **不再用 s["nextDate"]**。nextDate 落后时，拿它当标签等于把今日数据写进
    历史日期；漏掉的交易日只能留空——宁可缺行，不可错行。

    A 股法定节假日休市日不产生结算记录（跳过，只推进 nextDate）。
    同一交易日只写一条（幂等），修复「同一天被结算两次」。
    返回本次写入的日期标签；未写入返回 None。
    """
    market = (ACCOUNTS.get(server.current_account()) or {}).get("market") or "cn"
    explicit = bool(date_str)
    base = date_str or _closed_session_label(market)
    if not explicit and s["history"] and (s["history"][-1].get("date") or "") >= base:
        return None                      # 该交易日已结算过，幂等跳过
    # A 股账户且 base 落在法定节假日 → 跳过结算，只推进到下一个交易日
    if market != "us":
        try:
            y, m, d = [int(x) for x in base.split("-")]
            if is_cn_holiday(datetime(y, m, d)):
                s["nextDate"] = next_trading_day(base)
                return None
        except Exception:
            pass
    prev = s["history"][-1]["total"] if s["history"] else s["initCapital"]
    total = total_asset(s)
    s["idx"] = s["idx"] * (1 + mkt_pct / 100.0)
    s["history"].append({
        "date": base,
        "mkt": round(mkt_pct, 4),
        "total": round(total, 2),
        "dayPnl": round(total - prev, 2),
        "ret": round((total / s["initCapital"] - 1) * 100, 4),
        "mret": round(s["idx"] - 100, 4),
    })
    s["nextDate"] = next_trading_day(base)
    return base



def _update_peak(h, price):
    """更新持仓历史最高价（用于移动止盈回撤计算）。"""
    if price > (h.get("peak") or 0):
        h["peak"] = price



def check_stops(s):
    """持仓级止损/止盈纪律（借鉴 QuantsPlaybook 的组合风控思想）。

    这是对「新闻/信号驱动卖出」的补强：新闻没有利空、信号没转弱时，持仓也可能
    已经浮亏过深或回吐过多，需要**主动**离场，而不是被动等消息。三类规则：

      1. 固定止损：现价相对成本跌破 stopLossPct → 全仓清掉（砍掉深套）
      2. 移动止盈：从历史峰值回撤超过 trailPct → 卖出，锁住利润（让利润奔跑，
         但防止「坐电梯」回吐）
      3. 目标止盈：盈利达到 takeProfitPct → 分批止盈（先落袋一部分）

    返回卖出笔数。只在交易时段内执行（与 execute_approved 一致）。
    """
    cfg = s["config"]
    if not cfg.get("useStops", True):
        return 0
    if not server.in_trading_hours():
        return 0
    done = 0
    stop_loss = float(cfg.get("stopLossPct", -8))
    trail = float(cfg.get("trailPct", 15))
    take = float(cfg.get("takeProfitPct", 30))
    for h in list(s["holdings"]):
        if h["shares"] <= 0 or h["cost"] <= 0 or h["price"] <= 0:
            continue
        code = h["code"]
        name = h["name"]
        price = h["price"]
        cost = h["cost"]
        pnl_pct = (price - cost) / cost * 100.0

        _update_peak(h, price)
        peak = h.get("peak") or cost
        drawdown = (price - peak) / peak * 100.0  # 相对峰值回撤（负值）

        reason = ""
        qty = 0
        mark_tp = False
        # 1. 固定止损：深套清仓
        if pnl_pct <= stop_loss:
            reason = "止损：浮亏%.1f%% 触及%.0f%% 线" % (pnl_pct, stop_loss)
            qty = h["shares"]
        # 2. 移动止盈：从峰值回撤过大，锁利润
        elif pnl_pct > 0 and drawdown <= -trail:
            reason = "移动止盈：从峰值回撤%.1f%% 触及%.0f%% 线（仍浮盈%.1f%%）" % (
                drawdown, trail, pnl_pct)
            qty = h["shares"]
        # 3. 目标止盈：达到目标位，分批落袋（先卖一半）
        elif pnl_pct >= take:
            # 2026-11-10 修复：同一持仓当日只触发一次。此前交易时段每轮都把
            # 剩余仓位再折半，313 股被拆成 7 笔（最小 31 元）、最终卖掉 99%，
            # 与「先卖一半」的设计意图相悖（风语筑 2026-11-10 实证）。
            if h.get("tpDate") == today_str():
                continue
            reason = "目标止盈：浮盈%.1f%% 触及%.0f%% 目标，分批落袋" % (pnl_pct, take)
            qty = max(1, int(h["shares"] * 0.5))
            # 折半后单笔金额过小（残股），且整仓本就不大 → 一次清掉，不留给
            # 下一轮再拆（伊利 12 股折成 6/3/1/1、风语筑余 3 股 46 元实证）
            min_amt = float(cfg.get("minSellAmount", 1000) or 0)
            if min_amt > 0 and qty * price < min_amt \
                    and h["shares"] * price < min_amt * 2:
                qty = h["shares"]
            mark_tp = True

        if qty <= 0:
            continue
        if quota_block(s):
            # 达到当日笔数上限，本笔跳过（不卖），下轮再试
            log("止损止盈暂停（%s）" % quota_block(s))
            break
        tr = do_sell(s, name, code, price, qty, reason)
        if tr:
            done += 1
            if mark_tp:
                h["tpDate"] = today_str()
            # 止损止盈清仓后，重置峰值，避免残留影响未来重新建仓
            if h["shares"] <= 0:
                h.pop("peak", None)
                h.pop("tpDate", None)
            log("止损止盈卖出 %s %d 股 @%.3f（%s）" % (name, qty, price, reason))
    return done
