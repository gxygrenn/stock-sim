# -*- coding: utf-8 -*-
"""扫描引擎：每日事实包、评审队列进出、执行撮合与当日限额。"""

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



def daily_facts(s):
    """当日事实包：供复盘官判断决策对错，避免其自行拼数据出错。"""
    today = datetime.now().strftime("%Y-%m-%d")
    q = s.get("poolQuote") or {}

    def cur_of(code):
        return (q.get(code) or {}).get("price") or 0

    def enrich(t):
        row = {k: t.get(k) for k in ("time", "side", "name", "code", "price", "qty", "amount", "reason")}
        cur = cur_of(t.get("code") or "")
        if cur and t.get("price"):
            row["last"] = round(cur, 3)
            row["chgPctSinceTrade"] = round((cur / t["price"] - 1) * 100, 2)
            sign = 1 if t.get("side") == "买入" else -1
            row["pnlIfHeld"] = round((cur - t["price"]) * t["qty"] * sign, 2)
        else:
            row["last"] = None
            row["chgPctSinceTrade"] = None
            row["pnlIfHeld"] = None
        return row

    trades = [enrich(t) for t in (s.get("trades") or [])
              if (t.get("time") or "").startswith(today)]
    holdings = [{"name": h["name"], "code": h["code"], "shares": h["shares"],
                 "cost": round(h.get("cost") or 0, 3), "price": h.get("price"),
                 "pnlPct": round((h["price"] / h["cost"] - 1) * 100, 2) if h.get("cost") else 0}
                for h in s["holdings"] if h["shares"] > 0]
    news = [n for n in (s.get("news") or []) if (n.get("time") or "").startswith(today)]
    hit_news = [{"time": n.get("time"), "title": n.get("title"), "score": n.get("score"),
                 "hits": n.get("hits"), "action": n.get("action")}
                for n in news if n.get("hits")]
    hit_news.sort(key=lambda x: -abs(x.get("score") or 0))
    idx = s.get("quote") or {}
    total = total_asset(s)
    hist = s.get("history") or []
    today_hist = [h for h in hist if (h.get("date") or "") == today]
    reviewed_today = sorted([{"code": k, "time": v}
                             for k, v in (s.get("reviewed") or {}).items()
                             if (v or "").startswith(today)], key=lambda x: x["time"])
    council_today = [{"time": r.get("time"), "name": r.get("name"), "verdict": r.get("verdict"),
                      "agree": r.get("agree"), "oppose": r.get("oppose"), "veto": r.get("veto"),
                      "proposal": r.get("proposal"), "news": r.get("news")}
                     for r in (s.get("council") or [])
                     if (r.get("time") or "").startswith(today)]
    return {
        "date": today,
        "priceMode": s.get("priceMode"),
        "account": {
            "cash": round(s["cash"], 2), "totalAsset": round(total, 2),
            "initCapital": s["initCapital"],
            "pnlTotal": round(total - s["initCapital"], 2),
            "retTotalPct": round((total / s["initCapital"] - 1) * 100, 2),
            "todayPnl": today_hist[-1].get("dayPnl") if today_hist else None,
            "realized": round(s.get("realized") or 0, 2),
            "fees": round(s.get("fees") or 0, 2),
        },
        "market": {"code": s["config"].get("indexCode"), "name": idx.get("name"),
                   "price": idx.get("price"), "pct": idx.get("pct")},
        "trades": trades,
        "holdings": holdings,
        "newsStats": {"total": len(news), "hitCount": len(hit_news), "topHits": hit_news[:10]},
        "pendingReview": [dict(p, waitH=round(age_hours(p.get("queuedAt") or p.get("time")), 1))
                          for p in (s.get("pendingReview") or [])],
        "expiredRecent": (s.get("expiredReviews") or [])[-5:],
        "reviewedToday": reviewed_today,
        "reviewed": s.get("reviewed") or {},   # 完整 code->时间戳（冷却判断用，跨天也可靠）
        "reviewLog": (s.get("reviewLog") or [])[-600:],
        "councilToday": council_today[:10],
        "marketContext": s.get("marketContext"),
        "historyLast10": hist[-10:],
        "lastScan": s.get("lastScan"),
        "scanErr": s.get("scanErr"),
    }



def expire_stale_reviews(s):
    """队列 TTL：等太久的候选作废出队，释放坑位，避免队列被永久占死。

    - 未评审（waiting）：从入队时间 queuedAt 起算，超 TTL 作废
    - 已批准（approved）：也有保质期 approvedTTLHours——超时未成交说明结论已脱离
      行情（典型：周六批准、周一才开盘，其间隔夜消息全没消化），作废出队，
      复市后的扫描会按最新信号重新入队评审，而不是拿旧结论直接下单
    - 旧格式条目（没有 queuedAt 的）一律视为已过期
    """
    cfg = s["config"]
    try:
        ttl = float(cfg.get("reviewTTLHours", 24) or 24)
    except Exception:
        ttl = 24.0
    try:
        attl = float(cfg.get("approvedTTLHours", 24) or 24)
    except Exception:
        attl = 24.0
    pend = s.get("pendingReview") or []
    if not pend:
        return 0
    keep, dropped = [], []
    for p in pend:
        st = p.get("state") or "waiting"
        if st == "approved":
            ref = p.get("reviewedAt") or p.get("queuedAt") or p.get("time")
            aged = age_hours(ref)
            # 保质期按盘中时间计（2026-10-28）：周末与夜间不计时，避免周五批的单
            # 活不过周一开盘（T15：10-26 三账户各废 5 条）
            eff = approved_age_hours(ref) if aged >= 0 else -1.0
            if aged < 0 or eff <= attl:
                keep.append(p)     # 批准未过期：交给 server.execute_approved 在交易时段成交
            else:
                dropped.append((p, eff, "批准结论盘中超 %.0fh 保质期" % attl))
            continue
        base = p.get("queuedAt") or p.get("time")
        aged = age_hours(base)
        if aged < 0 or aged > ttl:
            dropped.append((p, aged if aged >= 0 else ttl, "TTL 超时未评审"))
        else:
            keep.append(p)
    if not dropped:
        return 0
    s["pendingReview"] = keep
    reviewed = s.setdefault("reviewed", {})
    exp = s.setdefault("expiredReviews", [])
    for p, aged, why in dropped:
        exp.append({"code": p.get("code"), "name": p.get("name"),
                    "direction": p.get("direction"), "state": p.get("state") or "waiting",
                    "waitedH": round(aged, 1), "why": why, "time": now_str()})
        reviewed[(p.get("code") or "").lower()] = now_str()   # 记冷却（key 统一小写），避免同一条信号立刻又被塞回来
    s["expiredReviews"] = exp[-100:]
    log("评审队列作废 %d 只：%s"
        % (len(dropped), "、".join("%s(%s)" % (p.get("name") or p.get("code"), why)
                                    for p, _, why in dropped)))
    return len(dropped)



def _link_review_log(s, code, price, action):
    """成交后把成交价/动作回写到评审台账对应条目，实现「评审→成交→结果」可追溯。"""
    rl = s.get("reviewLog") or []
    for r in reversed(rl):
        if (r.get("code") or "").lower() == (code or "").lower() and r.get("fillPrice") is None:
            r["fillPrice"] = price
            r["fillTime"] = now_str()
            r["outcome"] = action
            return



def maybe_catchup(s):
    """来访触发补成交：交易时段内若队列里有「已批准」结论，按最新价落地。

    成交原本只靠 worker 后台线程在交易时段存活；云端沙箱无人访问时 worker 挂起，
    导致 approved 结论错过整个交易时段（09-28 已实锤 trades=0）。这里让任何一次
    面板刷新/自动化访问都成为补成交入口，摆脱对后台线程的依赖。
    """
    if not server.in_trading_hours():
        return 0
    pend = s.get("pendingReview") or []
    if not any((p.get("state") or "") == "approved" for p in pend):
        return 0
    if time.time() - server._last_catchup < 45.0:   # 节流：45 秒内最多试一次，避免每请求都拉行情
        return 0
    server._last_catchup = time.time()
    done = server.execute_approved(s)
    if done:
        save_state()
    return done



def scan(s, force_trade=False):
    """算信号 -> 抓新闻 -> 打分 -> 匹配个股 -> 自动交易；无新闻的强信号也独立触发。"""
    cfg = s["config"]
    s["roundTrades"] = 0           # 新的一轮，笔数额度重新计
    err = server.refresh_prices(s)
    s["lastScan"] = now_str()

    # 先算全池综合信号（技术面/龙虎榜/公告/估值），新闻决策要用
    try:
        compute_signals(s)
    except Exception as exc:
        log("信号计算异常: %s" % exc)

    # 持仓级止损/止盈纪律：基于刚刷新的现价主动离场（与新闻/信号驱动卖出互补）
    try:
        stopped = check_stops(s)
        if stopped:
            log("止损止盈触发 %d 笔" % stopped)
    except Exception as exc:
        log("止损止盈异常: %s" % exc)

    # 市场环境温度计：大盘择时（RSRS + 扩散指标 + 动量），供买卖决策/风控收紧用
    try:
        market_regime(s)
    except Exception as exc:
        log("市场择时异常: %s" % exc)

    try:
        news = fetch_news(40)
    except Exception as exc:
        s["scanErr"] = "新闻抓取失败: %s" % exc
        log(s["scanErr"])
        save_state()
        return {"error": s["scanErr"]}

    seen = set(s["seenNews"])
    before = len(s["trades"])      # 一条消息可能命中多只股票，用流水增量统计成交笔数
    fresh = []
    for n in news:
        if n["id"] in seen:
            continue
        seen.add(n["id"])
        decide(s, n, force_trade=force_trade)
        fresh.append(n)
        if len(fresh) >= 25:
            break

    # 无新闻独立触发：综合信号够强（每日每方向每只股票只触发一次）
    if cfg["autoTrade"] or force_trade:
        fired = s.setdefault("sigFired", {})
        today = today_str()
        for item in s["pool"]:
            code = item["code"]
            sig = (s.get("signals") or {}).get(code) or {}
            d0 = sig.get("delta", 0)
            if abs(d0) < cfg.get("sigScore", 4):
                continue
            mark = today + ("+" if d0 > 0 else "-")
            if fired.get(code) == mark:
                continue
            notes = " ".join(sig.get("notes", [])[:2])
            act = execute(s, {"title": ("综合信号 " + notes)[:22]}, [item["name"]],
                          d0, apply_delta=False)
            if act.startswith(("买入", "卖出")):
                fired[code] = mark
                log("综合信号触发 %s(%+d) → %s" % (item["name"], d0, act))

    # ---- 评审与执行解耦：先作废等太久的候选，再把已批准的结论在盘内按最新价落地 ----
    expire_stale_reviews(s)
    approved_done = server.execute_approved(s)
    if approved_done:
        log("本轮落地 %d 笔专家团已批准的订单" % approved_done)

    # ---- 专家团门控：自动交易关闭时服务端不下单，把够格的候选放进待评审队列，
    #      等外部 LLM 专家团（定时自动化）裁决；结论写回队列后由 execute_approved 成交 ----
    # ---- 专家团门控：自动交易关闭时服务端不下单，把够格的候选放进待评审队列，
    #      等外部 LLM 专家团（定时自动化）裁决；结论写回队列后由 execute_approved 成交 ----
    if not (cfg["autoTrade"] or force_trade):
        enqueue_review_candidates(s, fresh)

    done = len(s["trades"]) - before
    s["seenNews"] = list(seen)[-2000:]
    s["news"] = (fresh + s["news"])[:60]
    s["scanErr"] = err
    log("扫描完成：新增 %d 条消息，成交 %d 笔%s" % (len(fresh), done, ("（%s）" % err) if err else ""))
    save_state()
    return {"new": len(fresh), "traded": done, "err": err}

def enqueue_review_candidates(s, fresh):
    """把本轮够格的候选写入 s["pendingReview"]，供 LLM 专家团评审。

    入队规则：
      - 新闻触发的「待执行」消息：正分 -> 买入候选；负分且已持仓 -> 卖出候选
      - 无新闻但 |综合信号 delta| >= sigScore：同上按方向入队
      - 估值过滤被 block 的不入队；同股 60 分钟内评审过（reviewed 冷却）的不重复入队
      - 队列上限 6 只，避免一轮评审拖太久
      - 超时（reviewTTLHours）未评审的候选由 expire_stale_reviews() 作废出队，释放坑位
    """
    cfg = s["config"]
    pend = s.setdefault("pendingReview", [])
    reviewed = s.setdefault("reviewed", {})
    queued = {p["code"] for p in pend}
    cool = int(cfg.get("reviewCooldownMin", 60)) * 60

    def recently(code):
        ts = reviewed.get((code or "").lower())   # reviewed key 统一小写（美股 code 为大写）
        if not ts:
            return False
        try:
            return (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() < cool
        except Exception:
            return False

    def enqueue(item, direction, score, reason, is_news=False):
        code = item["code"]
        if code in queued or recently(code) or len(pend) >= 6:
            return
        sig = (s.get("signals") or {}).get(code) or {}
        if sig.get("block"):
            return
        if direction == "卖出":
            h = find_holding(s, code)
            if not h or h["shares"] <= 0:
                return
        else:
            floor = int(cfg.get("buySigFloor", -6))
            # 大盘择时：防守档上浮买入信号分下限，减少弱市里的低质候选进评审
            if _regime_tier(s) == "defense":
                floor += int(cfg.get("regimeBuyFloor", 0))
            if int(sig.get("delta", 0) or 0) < floor:
                return   # 信号分低于买入下限，不入队（同 execute 的买入门槛）
            # 2026-11-13：当日限买已用的票不再入队——入队必被执行侧拦截，评审只是
            # 空转（11-06 us 30 条评审 11 条结论是「限买已用」），还把操作限制混进
            # HOLD 污染误杀归因。卖出/强制复核候选不受此条影响。
            if buys_today(s, code) >= int(cfg.get("sameDayBuyMax", 1) or 1):
                return
        entry = {"code": code, "name": item["name"], "direction": direction,
                 "score": score, "delta": sig.get("delta", 0),
                 "reason": reason[:60], "time": now_str(),
                 "queuedAt": now_str(), "px": round(cached_price(s, code), 3),
                 "state": "waiting"}
        # ---- 内置评议团初审：门控模式下内置评议会不再随 execute() 自动召开，
        #      导致前端「专家评议团」卡片恒空。这里让它在入队时以「初审」身份复活：
        #      合议记录写进 s["council"]（卡片有真实数据），意见附在队列条目上，
        #      LLM 专家团经 /api/state 可见，作为第二意见参考。
        #      不拦截入队——终审权仍在 LLM 专家团。----
        try:
            prev = server.run_council(s, item, code,
                               news_score=(score if is_news else 0),
                               news_title=(reason if is_news else ""),
                               record=True, session="门控初审",
                               advisory=True, price_hint=entry["px"])
            entry["council"] = {"verdict": prev["verdict"], "score": prev["score"],
                                "agree": prev["agree"], "oppose": prev["oppose"],
                                "abstain": prev["abstain"], "veto": prev["veto"],
                                "notes": ["%s:%s" % (v["role"], v["stance"])
                                          for v in prev["votes"]]}
        except Exception as exc:
            log("初审评议异常 %s: %s" % (item.get("name"), exc))
        pend.append(entry)
        queued.add(code)

    by_name = {p["name"]: p for p in s["pool"]}
    for n in fresh:
        if n.get("action") != "待执行":
            continue
        sc = int(n.get("score") or 0)
        direction = "买入" if sc > 0 else "卖出"
        for nm in n.get("hits") or []:
            it = by_name.get(nm)
            if it:
                enqueue(it, direction, sc, "快讯: " + (n.get("title") or "")[:24], True)
    for item in s["pool"]:
        sig = (s.get("signals") or {}).get(item["code"]) or {}
        d0 = int(sig.get("delta", 0) or 0)
        if abs(d0) >= cfg.get("sigScore", 4):
            direction = "买入" if d0 > 0 else "卖出"
            enqueue(item, direction, d0,
                    "综合信号: " + " ".join(sig.get("notes", [])[:2])[:24])

    # ---- 2026-10-15 教训（国轩/万科/AMD）：浮亏持仓长期失血却无任何动作——
    #      止损线太远时中间地带无人管。浮亏触及 reReviewPct 时主动入队一个
    #      「卖出」方向候选，交 LLM 专家团裁决 HOLD/SELL；不自动砍仓，终审权在评审。
    #      同一持仓 reReviewGapH 小时内不重复强制复核。----
    rr = float(cfg.get("reReviewPct", 0) or 0)
    if rr < 0:
        rr_gap = float(cfg.get("reReviewGapH", 72) or 72) * 3600.0
        rr_book = s.setdefault("reReviewed", {})
        # 2026-10-30 新增：记录「上次复核时的浮亏」，用于判断冷却期内是否继续恶化
        rr_pnl = s.setdefault("reReviewedPnl", {})
        for h in s["holdings"]:
            code = h.get("code") or ""
            if h.get("shares", 0) <= 0 or not code or code in queued:
                continue
            if (h.get("price") or 0) <= 0 or (h.get("cost") or 0) <= 0:
                continue
            pnl = (h["price"] - h["cost"]) / h["cost"] * 100.0
            if pnl > rr:
                continue
            ts = rr_book.get(code.lower())
            if ts:
                in_gap = False
                try:
                    in_gap = (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() < rr_gap
                except Exception:
                    in_gap = False
                if in_gap:
                    # 2026-10-30 修正：reReviewGapH 的本意是「别让同一只票反复刷评审」，
                    # 但 72h 冷却也把止损变成了盲区——10-30 cna 大洋电机 09:38 复核时浮亏 -5.0%
                    # 判 HOLD，收盘 -10.46%，冷却期内没有第二次复核，单日拖累 774 元。
                    # 改为：冷却期内若浮亏比上次复核时再恶化 ≥ reReviewWorsenPp（默认 3pp），
                    # 允许提前重触发。只影响「要不要送复核」，不自动下单，终审权仍在评审。
                    worsen = abs(float(cfg.get("reReviewWorsenPp", 3) or 3))
                    base_pnl = rr_pnl.get(code.lower())
                    if base_pnl is None or (float(base_pnl) - pnl) < worsen:
                        continue
            if len(pend) >= 6:
                # 2026-10-31 修正：队列满时风控项优先。此前这里是直接 break，
                # 10-30 cna 收盘队列被 1 条 approved + 5 条普通买入候选（score 2~12）占满，
                # 大洋电机跌到累计 -10.46%、越过 -8% 复核线也挤不进来，
                # 全天只有 09:38 那一次评审（当时浮亏 -5.0% 判 HOLD），单日拖累 774 元。
                # 改为挤掉分数最低的普通 waiting 候选；approved 与 forced 一律不动。
                evictable = [p for p in pend
                             if not p.get("forced") and p.get("state") == "waiting"]
                if not evictable:
                    break
                victim = min(evictable, key=lambda p: (p.get("score") or 0, p.get("queuedAt") or ""))
                pend.remove(victim)
                queued.discard(victim.get("code"))
                log("强制复核挤占队列：移除低分候选 %s（score %s）"
                    % (victim.get("name"), victim.get("score")))
            entry = {"code": code, "name": h.get("name") or code, "direction": "卖出",
                     "score": 0, "delta": 0,
                     "reason": "浮亏%.1f%%触及强制复核线%.0f%%" % (pnl, rr),
                     "time": now_str(), "queuedAt": now_str(),
                     "px": round(h.get("price") or 0, 3), "state": "waiting",
                     "forced": True}
            try:
                item = next((p for p in s["pool"] if p["code"] == code), None)
                if item:
                    prev = server.run_council(s, item, code, news_score=0,
                                       record=True, session="浮亏复核初审",
                                       advisory=True, price_hint=entry["px"])
                    entry["council"] = {"verdict": prev["verdict"], "score": prev["score"],
                                        "agree": prev["agree"], "oppose": prev["oppose"],
                                        "abstain": prev["abstain"], "veto": prev["veto"],
                                        "notes": ["%s:%s" % (v["role"], v["stance"])
                                                  for v in prev["votes"]]}
            except Exception as exc:
                log("浮亏复核初审异常 %s: %s" % (h.get("name"), exc))
            pend.append(entry)
            queued.add(code)
            rr_book[code.lower()] = now_str()
            rr_pnl[code.lower()] = round(pnl, 2)
            log("浮亏强制复核入队 %s（现亏%.1f%%）" % (h.get("name"), pnl))



def execute(s, n, hits, score, apply_delta=True):
    """按命中逐只尝试成交。apply_delta=True 时叠加综合信号修正分并应用估值硬过滤。"""
    cfg = s["config"]
    acts = []
    for name in hits:
        stop = quota_block(s)
        if stop:
            acts.append(stop)
            break
        item = None
        for p in s["pool"]:
            if p["name"] == name:
                item = p
                break
        if item is None:
            continue
        code = item["code"]
        sig = (s.get("signals") or {}).get(code) or {}
        sd = sig.get("delta", 0) if apply_delta else 0
        eff = score + sd                       # 新闻分 + 信号修正 = 决策分
        use_council = bool(cfg.get("useCouncil"))
        # 冷却：评议团模式下交给风控官判定，以便留下完整的合议记录
        if not use_council:
            last = s["lastTrade"].get(code)
            if last:
                try:
                    cool = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
                    if cool < cfg["cooldownMin"] * 60:
                        acts.append("冷却中")
                        continue
                except Exception:
                    pass
        price = server.current_price(s, item)
        if price <= 0:
            acts.append("无报价")
            continue

        tag = ""
        if use_council:
            # 评议团：四位分析师独立研判 -> 加权合议投票 -> 风控官一票否决
            dec = server.run_council(s, item, code,
                              news_score=(score if apply_delta else 0),
                              news_title=n.get("title", ""))
            if dec["verdict"] == "否决":
                acts.append("%s评议否决(%s)" % (name, dec["veto"][:16]))
                continue
            if dec["verdict"] == "观望":
                acts.append("%s评议观望%d:%d" % (name, dec["agree"], dec["total"]))
                continue
            direction = dec["verdict"]
            tag = "|评议%d:%d" % (dec["agree"], dec["oppose"])
        else:
            if abs(eff) < cfg["minScore"]:
                acts.append("%s信号抵消" % name)
                continue
            direction = "买入" if eff > 0 else "卖出"

        if apply_delta:
            base = "消息%+d信号%+d%s" % (score, sd, tag)
        else:
            base = "综合信号%+d%s" % (score, tag)
        reason = "%s:%s(%s)" % ("利好" if direction == "买入" else "利空",
                                n["title"][:12], base)
        if direction == "买入":
            if sig.get("block"):
                acts.append("%s被估值过滤" % name)
                continue
            floor = cfg.get("buySigFloor", -6)
            # 大盘择时：防守档上浮买入信号分下限（宁缺毋滥）
            if _regime_tier(s) == "defense":
                floor += int(cfg.get("regimeBuyFloor", 0))
            if int(sd) < floor:
                acts.append("%s信号分%+d<下限%+d" % (name, int(sd), floor))
                continue
            if len([h for h in s["holdings"] if h["shares"] > 0]) >= cfg["maxHold"] \
                    and find_holding(s, code) is None:
                acts.append("持仓已满")
                continue
            total = total_asset(s)
            room = total * cfg["maxPosPct"] / 100.0
            h = find_holding(s, code)
            if h:
                room -= h["price"] * h["shares"]
            # 大盘择时：防守档缩小单笔买入比例
            buy_pct = cfg["buyPct"]
            if _regime_tier(s) == "defense":
                buy_pct = buy_pct * float(cfg.get("regimeBuyPctScale", 0.5))
            # 波动率自适应：高波动缩小单笔、低波动放大单笔（乘 volScale）
            if cfg.get("useVolTarget"):
                vs = (s.get("regime") or {}).get("vol") or {}
                buy_pct = buy_pct * float(vs.get("scale", 1.0) or 1.0)
            budget = min(s["cash"] * buy_pct / 100.0, room, s["cash"] * 0.98)
            qty = int(budget / price) if price > 0 else 0
            qty = int(qty)          # 美股 1 股起
            if qty < 1:
                acts.append("%s资金不足" % name)
                continue
            tr = do_buy(s, name, code, price, qty, reason)
            acts.append(("买入%d股" % tr["qty"]) if tr else "未成交")
        else:
            h = find_holding(s, code)
            if not h or h["shares"] <= 0:
                acts.append("%s未持仓" % name)
                continue
            qty = h["shares"] * cfg["sellPct"] / 100.0
            qty = max(1, int(qty))
            qty = min(qty, h["shares"])
            tr = do_sell(s, name, code, price, qty, reason)
            acts.append(("卖出%d股" % tr["qty"]) if tr else "未成交")
    if not acts:
        return "忽略"
    ok = [a for a in acts if a.startswith(("买入", "卖出"))]
    if ok:
        return ok[0] if len(ok) == 1 else "%s等%d笔" % (ok[0], len(ok))
    return acts[0]



def done_today_count(s):
    today = today_str()
    return sum(1 for t in s["trades"] if t["time"].startswith(today))



def buys_today(s, code):
    """同股当日已成交买入次数（sameDayBuyMax 的统一计数口径，入队与执行两处共用）。"""
    ckey = (code or "").lower()
    return sum(1 for t in s.get("trades") or []
               if (t.get("code") or "").lower() == ckey
               and t.get("side") == "买入"
               and (t.get("time") or "").startswith(today_str()))



def quota_block(s):
    """执行额度检查：本轮笔数 + 当日笔数。返回拦截理由，放行返回空串。

    这两个是**执行额度**，只在真要下单时才算数：它回答「现在还能不能下单」，
    不是「这笔交易该不该做」。所以人工召集的研判会不走这里（advisory）。
    """
    cfg = s["config"]
    if int(s.get("roundTrades", 0)) >= cfg["maxPerRound"]:
        return "本轮已达%d笔上限" % cfg["maxPerRound"]
    day_cap = int(cfg.get("maxPerDay", 12))
    if done_today_count(s) >= day_cap:
        return "今日成交已达%d笔上限" % day_cap
    return ""
