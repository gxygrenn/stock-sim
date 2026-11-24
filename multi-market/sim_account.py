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



def _market_of(s):
    """当前账户所属市场（cn/us），用于整手规则与交易日历。"""
    try:
        return (server.ACCOUNTS.get(server.current_account()) or {}).get("market") or "cn"
    except Exception:
        return "cn"



def _lot_size(s):
    return 100 if _market_of(s) == "cn" else 1



def _is_trading_day(d, market):
    from datetime import date
    try:
        y, m, dd = map(int, str(d)[:10].split("-"))
        dt = date(y, m, dd)
    except Exception:
        return False
    if dt.weekday() >= 5:
        return False
    if market == "cn" and str(d)[:10] in CN_HOLIDAYS_2026:
        return False
    return True



def trading_days_between(d1, d2, market):
    """(d1, d2) 之间的交易日数，不含两端；d1 缺失返回 999（当作不连续）。"""
    from datetime import date, timedelta
    if not d1:
        return 999
    try:
        a = date(*map(int, str(d1)[:10].split("-")))
        b = date(*map(int, str(d2)[:10].split("-")))
    except Exception:
        return 999
    if b <= a:
        return 0
    n, cur = 0, a + timedelta(days=1)
    while cur < b:
        if _is_trading_day(cur.isoformat(), market):
            n += 1
        cur += timedelta(days=1)
    return n



def sell_floor_qty(s, price, shares, qty):
    """统一卖出股数下限（2026-11-18 起，B4）。

    1) 单笔卖出金额 ≥ sellFloorRatio×minSellAmount（默认 2 倍）——低于此既费通道
       也解决不了仓位问题；
    2) 卖后残值 < clearRemainderRatio×minSellAmount（默认 1.5 倍）→ 一次清仓，
       不留残股占席位（cnc 伊利 1 股、cna 大洋电机余 244 股跨 12 天才清完）。
    """
    cfg = s.get("config") or {}
    shares = int(shares)
    qty = max(0, min(int(qty), shares))
    msa = float(cfg.get("minSellAmount", 0) or 0)
    if qty <= 0 or price <= 0 or msa <= 0:
        return qty
    lot = _lot_size(s)
    floor_amt = msa * float(cfg.get("sellFloorRatio", 2.0) or 2.0)
    if qty * price < floor_amt:
        import math
        need = int(math.ceil(floor_amt / price))
        qty = min(shares, max(qty, ((need + lot - 1) // lot) * lot))
    remain = shares - qty
    if remain > 0 and remain * price < msa * float(cfg.get("clearRemainderRatio", 1.5) or 1.5):
        qty = shares
    return qty



def check_trims(s):
    """单票超配定期减仓（2026-11-18 起，B3）。

    反事实依据：25 天快照里，权重 20%~22% 的持仓 5 日超额 +2.92%（胜率 67%，
    属赢家漂移区），22%~25% 为 -5.26%（胜率 13%），>25% -1.66%。所以不在 20%
    砍，而在 22% 砍，且要连续 3 个交易日确认。

    返回卖出笔数。只读 s，卖单走 do_sell（与 check_stops 同一执行路径）。
    """
    cfg = s["config"]
    if not cfg.get("useTrim", True):
        return 0
    if not server.in_trading_hours():
        return 0
    market = _market_of(s)
    trig = float(cfg.get("trimPct", 22) or 22)
    tgt = float(cfg.get("trimTargetPct", 20) or 20)
    need_days = int(cfg.get("trimConfirmDays", 3) or 3)
    cool = int(cfg.get("trimCooldownDays", 10) or 10)
    total = total_asset(s)
    if total <= 0:
        return 0
    lot = _lot_size(s)
    streaks = s.setdefault("trimStreak", {})
    today = server.today_str()
    done = 0
    for h in list(s["holdings"]):
        if h["shares"] <= 0 or h["price"] <= 0:
            continue
        code, px = h["code"], h["price"]
        val = px * h["shares"]
        weight = val / total * 100.0
        st = streaks.get(code) or {}
        if weight < trig:
            if st.get("days"):
                streaks[code] = {"days": 0, "last": today}
            continue
        # 连续交易日确认：同一交易日重复扫描不重复计数；间隔 ≤1 个交易日才算连续
        if st.get("last") == today:
            days = int(st.get("days") or 0)
        else:
            gap = trading_days_between(st.get("last"), today, market)
            days = int(st.get("days") or 0) + 1 if gap <= 1 else 1
            streaks[code] = {"days": days, "last": today}
        if days < need_days:
            continue
        last_trim = h.get("trimDate")
        if last_trim and trading_days_between(last_trim, today, market) < cool:
            continue
        pct_now = float(h.get("pct") or 0)
        pnl_pct = (px - h["cost"]) / h["cost"] * 100.0 if h.get("cost") else 0.0
        if pct_now >= 5:
            continue          # 当日大涨：强者恒强，次日再看（R19 同源证据：急涨日放行=接刀）
        if pnl_pct >= float(cfg.get("takeProfitPct", 8) or 8):
            continue          # 已进止盈区，交给 check_stops/R10
        if pct_now <= -3:
            log("超配减仓顺延 %s：当日跌 %.1f%%，不在下跌中砸盘" % (h["name"], pct_now))
            continue
        sell_val = val - total * tgt / 100.0
        if sell_val <= 0:
            continue
        # 按整手取「减仓后权重最接近目标、且不低于 20% 太多」的股数：
        # 1000 股 @23 元、卖到 20% 需 130 股，取 100 股(22%)或 200 股(18.4%)，
        # 选离 20% 更近的一档，避免向下取整后仍高于触发线、冷却期一过又砍一刀。
        base_lots = int((sell_val / px) // lot)
        cands = [base_lots * lot, (base_lots + 1) * lot]
        cands = [q for q in cands if 0 < q < h["shares"]]
        if not cands:
            continue
        qty = min(cands, key=lambda q: abs((val - q * px) / total * 100.0 - tgt))
        qty = sell_floor_qty(s, px, h["shares"], qty)
        if qty <= 0:
            continue
        if quota_block(s):
            log("超配减仓暂停（%s）" % quota_block(s))
            break
        reason = "超配减仓：权重%.1f%%超%.0f%%线连续%d日，减至%.0f%%" % (weight, trig, days, tgt)
        tr = do_sell(s, h["name"], code, px, qty, reason)
        if tr:
            done += 1
            h["trimDate"] = today
            streaks[code] = {"days": 0, "last": today}
            log("超配减仓卖出 %s %d 股 @%.3f（%s）" % (h["name"], qty, px, reason))
    return done



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
    # 2026-11-23 新增（用户拍板：很多票到不了止盈线就回落）
    #   loseCutPct : 浮亏达到该线先减半仓（<0 生效，0/None 关闭），每个持仓只触发一次。
    #                美股回测（23 episode）净改善 +1998：救 超威/Palantir/新东方/爱彼迎/Visa
    #                等「买入即跌」的票；A股同类规则会砍错慢牛（工行 -750、农行 -414），故 A 股关闭。
    #   trailArmPct: 峰值浮盈曾达该值后，移动止盈不再要求「当前仍浮盈」（0/None 关闭）。
    #                修的是「涨到 6% 没到 8%，跌回成本以下移动止盈永久失效，只能等 -6%」。
    lose_cut = float(cfg.get("loseCutPct", 0) or 0)
    lose_frac = float(cfg.get("loseCutFrac", 0.5) or 0.5)
    trail_arm = float(cfg.get("trailArmPct", 0) or 0)
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
        peak_pnl = (peak - cost) / cost * 100.0   # 峰值时刻的浮盈（用于 trail_arm）

        reason = ""
        qty = 0
        mark_tp = False
        mark_lose = False
        # 1. 固定止损：深套清仓
        if pnl_pct <= stop_loss:
            reason = "止损：浮亏%.1f%% 触及%.0f%% 线" % (pnl_pct, stop_loss)
            qty = h["shares"]
        # 1.5 移动止盈：从峰值回撤过大，锁利润
        #    trail_arm 生效后，只要「峰值曾赚到 arm%」就不再要求当前仍浮盈——
        #    否则跌回成本那一刻本条永久失效，+6%→-6% 一路坐电梯（礼来 11-21 实证）。
        #    注意：本条排在「浮亏减半」之前。若一只票既曾赚过 arm%、又已回撤超线，
        #    说明涨幅基本回吐完，直接清仓优于先减半再补一刀（礼来同时满足两条，
        #    旧顺序会拆成两笔、分两轮执行、多占一次当日额度）。
        elif (pnl_pct > 0 or (trail_arm > 0 and peak_pnl >= trail_arm)) and drawdown <= -trail:
            reason = "移动止盈：从峰值回撤%.1f%% 触及%.0f%% 线（%s%.1f%%）" % (
                drawdown, trail, "峰值曾达%+.1f%%，当前" % peak_pnl if pnl_pct <= 0 else "仍浮盈", pnl_pct)
            qty = h["shares"]
        # 1.6 浮亏减半（2026-11-23）：跌破 loseCutPct 先砍一半，不等 -4% 送评/ -6% 硬清
        elif lose_cut < 0 and pnl_pct <= lose_cut and not h.get("loseCutDate"):
            qty = max(1, int(h["shares"] * lose_frac))
            qty = sell_floor_qty(s, price, h["shares"], qty)
            reason = "浮亏减半：浮亏%.1f%% 触及%.0f%% 线，先减%.0f%%仓位" % (
                pnl_pct, lose_cut, qty * 100.0 / max(1, h["shares"]))
            mark_lose = True
        # 3. 目标止盈：达到目标位落袋。卖出比例由 takeProfitSellFrac 决定：
        #    0.5 = 分批（先卖一半，旧行为）；1.0 = 全清（2026-11-24 用户选 B 档）。
        #    依据 12 个 episode 回测：全清相对半仓 +2,728 元，优于残仓保护 1.5% 的
        #    +1,998（deliverables/auto-trade/tp-residual-guard-2026-11-24.md）。
        elif pnl_pct >= take:
            # 2026-11-10 修复：同一持仓当日只触发一次。此前交易时段每轮都把
            # 剩余仓位再折半，313 股被拆成 7 笔（最小 31 元）、最终卖掉 99%，
            # 与「先卖一半」的设计意图相悖（风语筑 2026-11-10 实证）。
            if h.get("tpDate") == today_str():
                continue
            frac = float(cfg.get("takeProfitSellFrac", 0.5) or 0.5)
            if frac >= 0.999:
                qty = h["shares"]
                reason = "目标止盈：浮盈%.1f%% 触及%.0f%% 目标，全仓落袋" % (pnl_pct, take)
            else:
                reason = "目标止盈：浮盈%.1f%% 触及%.0f%% 目标，分批落袋" % (pnl_pct, take)
                qty = max(1, int(h["shares"] * min(max(frac, 0.05), 1.0)))
                # 2026-11-18：改用统一卖出下限（单笔 ≥2×minSellAmount；卖后残值
                # <1.5×minSellAmount 直接清仓），替代原来只判「qty<min_amt 且
                # 整仓<2×min_amt」的窄口径（伊利 1 股、风语筑 2 股碎单实证）。
                qty = sell_floor_qty(s, price, h["shares"], qty)
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
            if mark_lose:
                h["loseCutDate"] = today_str()
            # 止损止盈清仓后，重置峰值，避免残留影响未来重新建仓
            if h["shares"] <= 0:
                h.pop("peak", None)
                h.pop("tpDate", None)
                h.pop("loseCutDate", None)
            log("止损止盈卖出 %s %d 股 @%.3f（%s）" % (name, qty, price, reason))
    return done
