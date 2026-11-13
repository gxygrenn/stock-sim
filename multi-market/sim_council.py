# -*- coding: utf-8 -*-
"""专家评议团：初审意见、风控复核、裁决与快讯匹配。"""

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



# ---------------------------------------------- 专家评议团
# 四位分析师各自独立出意见（不看别人），加权合议投票；风控官持一票否决权。
ANALYSTS = [
    ("技术分析师", 1.0),
    ("资金面分析师", 1.2),
    ("消息面分析师", 1.0),
    ("估值分析师", 0.8),
]



def _stance(x):
    return "看多" if x > 0 else ("看空" if x < 0 else "中性")



def _regime_tier(s):
    """当前市场环境档位：attack / neutral / defense。regime 未启用或未算过则视为 neutral。"""
    reg = s.get("regime") or {}
    if not reg.get("enabled"):
        return "neutral"
    return reg.get("tier") or "neutral"



def _val_opinion(s, code):
    """估值分析师的独立意见：低估值给买入倾向，高估值/亏损给卖出倾向。"""
    q = (s.get("poolQuote") or {}).get(code) or {}
    try:
        pe = float(q.get("pe") or 0)
    except Exception:
        pe = 0.0
    if pe < 0:
        return -2, "亏损股，PE为负"
    if pe > 80:
        return -1, "PE%.0f 明显偏贵" % pe
    if pe > 45:
        return -1, "PE%.0f 估值偏高" % pe
    if 0 < pe <= 30:
        return 1, "PE%.0f 估值合理" % pe
    return 0, "PE%.0f 中性" % pe



def _flow_opinion(s, code, sig, q):
    """资金面分析师：龙虎榜净买卖为主；无席位时看成交额是否放量，且必须与涨跌同向。"""
    l = int(sig.get("lhb", 0) or 0)
    if l > 0:
        return l, "龙虎榜净买入"
    if l < 0:
        return l, "龙虎榜净卖出"
    try:
        amt = float(q.get("amount") or 0)
    except Exception:
        amt = 0.0
    try:
        p = float(q.get("pct") or 0)
    except Exception:
        p = 0.0
    if amt >= 80e8:
        if p >= 0:
            return 1, "巨额成交%.0f亿上涨" % (amt / 1e8)
        return -1, "巨额成交%.0f亿下跌" % (amt / 1e8)
    if 0 < amt < 3e8:
        return -1, "成交清淡仅%.1f亿" % (amt / 1e8)
    return 0, "成交%.0f亿，无席位" % (amt / 1e8)



def _is_clear_sell(p):
    """该队列项是否为「将清仓」的 SELL（执行后必然释放一个席位）。

    清仓口径（2026-11-13 R15）：forced=true 的浮亏强制复核终审 SELL，或 note 带
    【R15清仓腾位】标记的置换 SELL——这两类在执行端卖全量（见 execute_approved）。
    普通 sellPct 部分卖出不清仓、不释放席位，不算数。
    """
    if ((p.get("review") or {}).get("verdict") or "").upper() != "SELL":
        return False
    if p.get("forced") or "【R15清仓腾位" in ((p.get("review") or {}).get("note") or ""):
        return True
    return False



def _approved_clear_sell_pending(s):
    """队列里是否已有「已批准且将清仓」的 SELL（换仓闸门的放行前提）。"""
    for p in s.get("pendingReview") or []:
        if (p.get("state") or "") == "approved" and _is_clear_sell(p):
            return True
    return False



def _risk_check(s, item, code, direction, price, advisory=False):
    """风控官审查。返回否决理由，放行返回空串。

    advisory=True 表示这是人工召集的「研判会」（只出意见、不下单）：
    跳过「本轮笔数上限 / 冷却期」这类**执行额度**约束——那是回答「现在能不能
    下单」，不是「这笔交易该不该做」。实质性风险项（估值过滤、仓位上限、
    浮亏禁加仓、现金不足、未持仓）在研判模式下依然照拦。
    """
    cfg = s["config"]
    sig = (s.get("signals") or {}).get(code) or {}
    if not direction:
        return ""
    h = find_holding(s, code)
    # 执行额度约束：只在真要下单时生效
    if not advisory:
        stop = quota_block(s)
        if stop:
            return stop
        last = s["lastTrade"].get(code)
        if last:
            try:
                cool = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
                if cool < cfg["cooldownMin"] * 60:
                    return "距上次交易不足%d分钟" % cfg["cooldownMin"]
            except Exception:
                pass
    if direction == "买入":
        if price <= 0:
            return "无有效报价"
        if sig.get("block"):
            return "估值过滤未通过（高PE/亏损/换手过热）"
        if h and h["shares"] > 0 and h["cost"] > 0:
            pnl = (price - h["cost"]) / h["cost"] * 100.0
            if pnl <= cfg.get("stopLossPct", -8):
                return "该股浮亏%.1f%%，禁止加仓摊平" % pnl
        alive = [x for x in s["holdings"] if x["shares"] > 0]
        if len(alive) >= cfg["maxHold"] and (h is None or h["shares"] <= 0):
            # 2026-11-13 新增：换仓闸门（swapGate）。满席不再一票否决——队列里已有
            # 一笔「将清仓」的已批准 SELL（强制复核终审 / R15 清仓指令）时按
            # 「先卖后买」放行：执行端同轮先卖腾位、再买入回补（execute_approved
            # 已改为 SELL 先于 BUY）。部分卖出不释放席位，不放行。
            # 背景（T10/T14/T28）：cnc 东方环宇 delta5.9 连续 4 日因只数上限 HOLD，
            # 11-10 @21.79 → 11-13 @27.99（+28.5%），而账户现金 25.2% 与满 8 席
            # 并存；11-13 三一置换 SELL 仅成交 100/326 股、席位未释放。
            if not (cfg.get("swapGate") and _approved_clear_sell_pending(s)):
                return "持仓只数已达上限%d" % cfg["maxHold"]
        total = total_asset(s) or 1.0
        if h and h["shares"] > 0 and (h["price"] * h["shares"]) / total * 100.0 >= cfg["maxPosPct"]:
            return "单票仓位已达%.0f%%上限" % cfg["maxPosPct"]
        # 2026-11-10 新增：单票剩余额度连一手都不够时，加仓必然在执行端
        # 弃单（qty=int(room/price)=0），但此前初审不拦，评审团照批——
        # us 礼来 11-10 单日 4 轮 BUY 全批、0 股成交（权重 19.0%，剩余额度
        # 919 元 < 股价 1412）。在入队初审就否决，评审不再收到买不了的候选。
        if h and h["shares"] > 0:
            room = total * cfg["maxPosPct"] / 100.0 - h["price"] * h["shares"]
            if room < price * 1:
                return "单票剩余额度%.0f元不足一手，无法加仓" % max(room, 0.0)
        mv = sum(x["price"] * x["shares"] for x in s["holdings"])
        # 大盘择时：防守档收紧总仓位上限，保留弹药
        pos_cap = cfg.get("maxTotalPos", 92)
        if _regime_tier(s) == "defense":
            pos_cap = min(pos_cap, cfg.get("regimePosCap", 70))
        # 波动率自适应：高波动降仓位上限、低波动可放宽（乘 volScale）
        if cfg.get("useVolTarget"):
            vs = (s.get("regime") or {}).get("vol") or {}
            vol_scale = float(vs.get("scale", 1.0) or 1.0)
            if vol_scale < 1.0:
                pos_cap = min(pos_cap, max(40.0, pos_cap * vol_scale))
            elif vol_scale > 1.0:
                pos_cap = min(cfg.get("maxTotalPos", 92) + 6, pos_cap * vol_scale)
        if mv / total * 100.0 >= pos_cap:
            return "总仓位超%.0f%%（防守档收紧），保留弹药" % pos_cap
        if s["cash"] < price * 1:
            return "现金不足一手"
    else:
        if not h or h["shares"] <= 0:
            return "未持仓，无可卖"
    return ""



def run_council(s, item, code, news_score=0, news_title="", skip_risk=False, record=True,
                session="", advisory=False, price_hint=0.0):
    """开一次专家会：四位分析师独立研判 -> 加权合议 -> 风控官否决。

    advisory=True 时按「研判会」处理：只出意见不下单，风控官不查执行额度。
    price_hint>0 时用该价研判（入队初审用缓存价，省一次实时拉价）。
    返回 {verdict(买入/卖出/观望/否决), score, agree, total, veto, votes[], time}。
    """
    cfg = s["config"]
    sig = (s.get("signals") or {}).get(code) or {}
    q = (s.get("poolQuote") or {}).get(code) or {}
    price = float(price_hint or 0) or server.current_price(s, item) or 0

    t = int(sig.get("tech", 0) or 0) if cfg.get("useTech") else 0
    tnotes = [x for x in (sig.get("notes") or []) if x.startswith(("均线", "20日", "放量"))][:3]
    tnote = " ".join(tnotes)[:28] or "形态平淡"
    tech_op = (t, tnote)

    flow_op = _flow_opinion(s, code, sig, q) if cfg.get("useLhb") else (0, "资金面未启用")
    ns = max(-3, min(3, int(news_score or 0)))
    news_note = ("利好快讯 %+d" % news_score) if news_score > 0 else \
                (("利空快讯 %+d" % news_score) if news_score < 0 else "无相关快讯")
    news_op = (ns, news_note)
    val_op = _val_opinion(s, code) if cfg.get("useVal") else (0, "估值未启用")

    votes, wsum, wscore = [], 0.0, 0.0
    for (role, w), (sc, note) in zip(ANALYSTS, [tech_op, flow_op, news_op, val_op]):
        sc = max(-3, min(3, int(sc)))
        votes.append({"role": role, "stance": _stance(sc), "score": sc, "note": note[:30]})
        wsum += w
        wscore += sc * w
    wscore = (wscore / wsum) if wsum else 0.0

    if wscore > 0.2:
        direction = "买入"
    elif wscore < -0.2:
        direction = "卖出"
    else:
        direction = ""
    # ---- 合议表决：弃权者不计入票池，同意票需达门槛且严格多于反对票 ----
    voters = [v for v in votes if v["stance"] != "中性"]
    need = int(cfg.get("minAgree", 2))
    n_agree = n_oppose = 0
    if direction:
        want = "看多" if direction == "买入" else "看空"
        hate = "看空" if direction == "买入" else "看多"
        n_agree = sum(1 for v in voters if v["stance"] == want)
        n_oppose = sum(1 for v in voters if v["stance"] == hate)

    # 议案落地性：卖出但并未持仓 -> 议案自然失效（属于「无需表决」，不算风控否决）
    if direction == "卖出":
        h0 = find_holding(s, code)
        if not h0 or h0["shares"] <= 0:
            direction = ""
            n_agree = n_oppose = 0

    risk_on = bool(cfg.get("useRisk", True)) and not skip_risk
    veto = _risk_check(s, item, code, direction, price, advisory=advisory) if risk_on else ""
    if not direction:
        verdict = "观望"
    elif veto:
        verdict = "否决"
    elif n_agree >= need and n_agree > n_oppose:
        verdict = direction
    else:
        verdict = "观望"

    if not cfg.get("useRisk", True):
        risk_note = "风控官已关闭"
    elif skip_risk:
        risk_note = "预览未审查"
    elif veto:
        risk_note = veto
    else:
        risk_note = "无需表决" if not direction else ("研判放行" if advisory else "风控指标正常")
    votes.append({"role": "风控官", "stance": ("否决" if veto else "放行"), "score": 0,
                  "note": risk_note[:30]})
    rec = {
        "time": now_str(), "code": code, "name": item.get("name", ""),
        "verdict": verdict, "score": round(wscore, 2),
        "agree": n_agree, "oppose": n_oppose, "total": len(voters),
        "abstain": len(ANALYSTS) - len(voters), "proposal": direction,
        "veto": veto, "news": (news_title or "")[:26], "votes": votes,
        "session": session, "advisory": bool(advisory),
    }
    if record:
        s["council"] = ([rec] + list(s.get("council") or []))[:24]
    return rec



def decide(s, n, force_trade=False, min_score=None):
    """对**一条**消息走完整决策：打分 -> 命中 -> 阈值 -> 下单。

    抽出来是为了让「自动扫描」与「手动投喂演练」走完全同一条链路，
    避免两边逻辑漂移。返回执行结果字符串（写入 n["action"]）。
    """
    cfg = s["config"]
    score, good, bad = score_text(n["text"])
    if n.get("forceScore") is not None:      # 演练用：显式指定分值
        score = int(n["forceScore"])
    hits, hitkeys = match_pool(n["text"], s["pool"], n.get("title"))
    n["score"] = score
    n["good"] = good[:6]
    n["bad"] = bad[:6]
    n["hits"] = hits
    n["hitKeys"] = hitkeys[:6]
    gate = cfg["minScore"] if min_score is None else min_score
    if not hits or abs(score) < gate:
        n["action"] = "忽略"
    elif not (cfg["autoTrade"] or force_trade):
        n["action"] = "待执行"
    else:
        n["action"] = execute(s, n, hits, score)
    return n["action"]
