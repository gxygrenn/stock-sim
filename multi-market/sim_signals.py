# -*- coding: utf-8 -*-
"""技术信号：K线打分、形态、RSRS择时、波动率、量价因子与行业轮动。"""

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



def tech_score(s, code):
    """技术面信号（借鉴 Sequoia-X 的形态识别：海龟突破 / 均线放量 / RPS 相对强度）。

    维度：多头排列、金叉死叉、20日新高新低、量比异动、阳线确认（防诱多）、
          相对强度 RPS、流动性过滤。返回 (分数[-3,3], 明细)。
    """
    kl = server.fetch_kline(code, 65)
    if len(kl) < 20:
        return 0, []
    closes = [k[2] for k in kl]
    opens = [k[1] for k in kl]
    highs = [k[3] for k in kl]
    vols = [k[5] for k in kl]
    score, notes = 0, []
    q = (s.get("poolQuote") or {}).get(code) or {}
    # 盘中实时价优先：日 K 最后一根是「昨天收盘」，用它判「20日新高/突破」会漏掉
    # 当天盘中创新高的强势股（实测英伟达盘中创历史新高，日 K 收盘价却未更新）。
    live = q.get("price") or 0
    c = live if live > 0 else closes[-1]
    o = opens[-1]
    live_up = (q.get("pct") or 0) >= 0  # 盘中实时是否上涨（替代「是否收阳」）

    # 1. 均线多头/空头排列（MA5 > MA20 > MA60）
    ma5 = sum(closes[-5:]) / 5.0
    ma20 = sum(closes[-20:]) / 20.0
    ma60 = sum(closes[-60:]) / 60.0 if len(closes) >= 60 else ma20
    if ma5 > ma20 > ma60:
        score += 1; notes.append("多头排列+1")
    elif ma5 < ma20 < ma60:
        score -= 1; notes.append("空头排列-1")

    # 2. 金叉 / 死叉（MA5 上穿/下穿 MA20，最近 3 日内）
    if len(closes) >= 21:
        ma5_p = sum(closes[-6:-1]) / 5.0
        ma20_p = sum(closes[-21:-1]) / 20.0
        if ma5_p <= ma20_p and ma5 > ma20:
            score += 1; notes.append("金叉+1")
        elif ma5_p >= ma20_p and ma5 < ma20:
            score -= 1; notes.append("死叉-1")

    # 3. 20 日新高 / 新低（用实时价，能捕捉盘中突破）
    if c >= max(highs[-20:]):
        score += 1; notes.append("20日新高+1")
    elif c <= min(closes[-20:]):
        score -= 1; notes.append("20日新低-1")

    # 4. 量比异动
    base = sum(vols[-6:-1]) / 5.0
    if base > 0 and vols[-1] > 2 * base:
        if q.get("pct", 0) >= 0:
            score += 1; notes.append("放量上涨+1")
        else:
            score -= 1; notes.append("放量下跌-1")

    # 5. 阳线/突破确认（防诱多）：创 20 日新高且盘中上涨，才是有效突破
    if c >= max(highs[-20:]) and (live_up if live > 0 else c > o):
        score += 1; notes.append("阳线突破+1")

    # 6. 相对强度 RPS（欧奈尔）：20 日 / 60 日涨幅（用实时价）
    r20 = (c / closes[-21] - 1) * 100 if len(closes) > 21 and closes[-21] else 0
    r60 = (c / closes[-61] - 1) * 100 if len(closes) > 61 and closes[-61] else 0
    if r20 > 15 and r60 > 5:
        score += 1; notes.append("相对强度高+1")
    elif r20 < -15:
        score -= 1; notes.append("相对强度弱-1")

    # 7. 流动性过滤：成交额不足（A股/美股均按元计，低于 1 亿视为流动性差）
    amount = q.get("amount", 0) or 0
    if amount > 0 and amount < 1e8:
        score -= 1; notes.append("流动性差-1")

    # 8. 旗形突破（Sequoia-X 旗形形态，美股/A股通用）：急涨旗杆 + 高位缩量整理 + 放量突破
    flag = flag_pattern(closes, highs, vols)
    if flag > 0:
        score += 1; notes.append("旗形突破+1")
    elif flag < 0:
        score -= 1; notes.append("旗形破位-1")

    # 9/10. A股专属形态（涨停洗盘 / 跌停反包），美股不适用
    if ACCOUNTS.get(server.current_account(), {}).get("market") == "cn":
        if limitup_washout(closes, vols):
            score += 1; notes.append("涨停洗盘+1")
        if limitdown_reversal(closes, vols):
            score += 1; notes.append("跌停反包+1")

    return max(-3, min(3, score)), notes[:8]



def flag_pattern(closes, highs, vols):
    """旗形形态识别（借鉴 Sequoia-X 旗形策略）。

    结构：旗杆 = 近 12 日内有一段急涨（单日涨幅 >8%）；
          旗面 = 随后 3~8 日高位窄幅整理（区间幅度 < 杆高的 50%，且缩量）；
          突破 = 最新价上穿旗面上沿（放量更可靠）或跌破下沿。
    返回 +1=向上突破 / -1=向下破位 / 0=无旗形或未突破。
    """
    n = len(closes)
    if n < 10:
        return 0
    # 找旗杆：从前往后扫，在 n-9 .. n-4 之间找最大单日涨幅（给旗面留至少 3 根）
    pole_i, pole_r = -1, 0.0
    for i in range(max(1, n - 9), n - 3):
        if closes[i - 1] > 0:
            r = (closes[i] / closes[i - 1] - 1) * 100
            if r > pole_r:
                pole_r, pole_i = r, i
    if pole_r < 8 or pole_i < 0:
        return 0                        # 没有足够强的旗杆
    # 旗面：旗杆之后的整理区间（3~8 日，不含最新突破日），用收盘价定义上下沿
    seg_c = closes[pole_i + 1:-1]
    if len(seg_c) < 2 or len(seg_c) > 9:
        return 0
    seg_hi = max(seg_c)
    seg_lo = min(seg_c)
    if seg_hi <= seg_lo:
        return 0
    # 旗面高度应明显小于旗杆（窄幅整理）
    pole_h = (closes[pole_i] - closes[pole_i - 1]) / closes[pole_i - 1] * 100
    flag_h = (seg_hi - seg_lo) / seg_lo * 100
    if flag_h > pole_h * 0.5:
        return 0                        # 整理太宽，不是旗形
    # 缩量确认：旗面均量 < 旗杆日量的 1.1 倍（放量突破前通常是缩量整理，留点容差）
    pole_v = vols[pole_i] if pole_i < len(vols) else 0
    flag_vols = vols[pole_i + 1:-1] if pole_i + 1 < len(vols) else []
    flag_v = sum(flag_vols) / len(flag_vols) if flag_vols else 0
    if pole_v > 0 and flag_v > pole_v * 1.1:
        return 0                        # 整理期明显放量，非旗形
    # 突破方向
    latest = closes[-1]
    if latest > seg_hi:
        return 1                        # 向上突破
    if latest < seg_lo:
        return -1                       # 向下破位
    return 0



def limitup_washout(closes, vols):
    """涨停洗盘（A股专属）：近期出现涨停后缩量回踩不破支撑，是强势洗盘。

    规则：近 12 日内有涨停（单日涨幅 >= 9.5%）；涨停后回踩（现价低于涨停收盘，
          但未跌破涨停前一日收盘的 97%）；且回踩期间缩量。
    """
    n = len(closes)
    if n < 6:
        return False
    start = max(1, n - 12)
    for i in range(start, n - 1):
        if closes[i - 1] <= 0:
            continue
        r = (closes[i] / closes[i - 1] - 1) * 100
        if r >= 9.5:
            support = closes[i - 1]     # 涨停前一日收盘做支撑
            latest = closes[-1]
            if latest < closes[i] and latest >= support * 0.97:
                # 缩量：回踩段均量 < 涨停日量的 80%
                if i < len(vols) and vols[i] > 0:
                    tail = vols[i + 1:]
                    if tail and sum(tail) / len(tail) < vols[i] * 0.8:
                        return True
            return False
    return False



def limitdown_reversal(closes, vols):
    """跌停反包（A股专属）：跌停次日放量阳线反包，超跌反转信号。

    规则：近 10 日内有跌停（单日跌幅 <= -9.5%）；次日（或近 2 日内）收阳且
          收盘价收复跌停阴线实体一半以上，且放量。
    """
    n = len(closes)
    if n < 5:
        return False
    start = max(1, n - 10)
    for i in range(start, n - 1):
        if closes[i - 1] <= 0:
            continue
        r = (closes[i] / closes[i - 1] - 1) * 100
        if r <= -9.5:
            for j in (i + 1, i + 2):
                if j >= n:
                    break
                if closes[j - 1] <= 0:
                    continue
                if closes[j] > closes[j - 1]:           # 阳线
                    body_lo = closes[i]                 # 跌停阴线实体下沿
                    body_hi = closes[i - 1]             # 阴线实体上沿
                    if closes[j] >= (body_hi + body_lo) / 2:
                        if j < len(vols) and vols[j] > vols[i] * 1.2:   # 放量
                            return True
            return False
    return False



def _linreg(xs, ys):
    """最小二乘线性回归，返回 (斜率 beta, 截距 alpha)。数据不足返回 (None, None)。"""
    n = len(xs)
    if n < 5:
        return None, None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None, None
    beta = sxy / sxx
    alpha = my - beta * mx
    return beta, alpha



def _rsrs(kl, n=18):
    """RSRS 阻力支撑相对强度（光大证券择时系列）。

    用最近 n 根日 K 的 high 对 low 做 OLS 回归，斜率 beta 衡量「上涨时低点
    抬升 vs 高点抬升」的相对强度：beta 明显 >1 表示阻力小、趋势强；<1 表示
    下跌趋势。返回 (beta, beta 的标准化 z-score, 明细文字)。
    """
    if len(kl) < n + 5:
        return None, None, "K线不足"
    highs = [k[3] for k in kl[-n:]]
    lows = [k[4] for k in kl[-n:]]
    beta, alpha = _linreg(highs, lows)
    if beta is None:
        return None, None, "回归失败"
    # 滚动 beta 序列求 z-score（用于判断 beta 处于自身历史的高/低位）
    betas = []
    for i in range(n, len(kl)):
        hh = [k[3] for k in kl[i - n:i]]
        ll = [k[4] for k in kl[i - n:i]]
        b, _ = _linreg(hh, ll)
        if b is not None:
            betas.append(b)
    z = 0.0
    if len(betas) >= 5:
        mb = sum(betas) / len(betas)
        sd = (sum((b - mb) ** 2 for b in betas) / len(betas)) ** 0.5
        if sd > 0:
            z = (beta - mb) / sd
    return beta, z, "beta=%.3f" % beta



def _regime_rsrs_score(kl):
    """把 RSRS 转成 [-1, 1] 的择时分。beta 明显 >1 且 z 为高位 → 强；<1 → 弱。"""
    beta, z, note = _rsrs(kl)
    if beta is None:
        return 0, note
    score = 0
    if beta >= 1.03:
        score += 0.6
    elif beta >= 1.0:
        score += 0.25
    elif beta <= 0.96:
        score -= 0.6
    elif beta <= 0.99:
        score -= 0.25
    # z-score 高位/低位增强：beta 处于自身历史高位则趋势可靠，低位则趋势反转
    if z > 0.8:
        score += 0.4 if beta >= 1.0 else -0.2
    elif z < -0.8:
        score += -0.4 if beta < 1.0 else 0.2
    return max(-1.0, min(1.0, score)), "%s z=%.2f" % (note, z)



def _regime_momentum_score(kl):
    """指数均线动量：MA5/MA20/MA60 多空排列 + 20 日涨幅。返回 [-1, 1]。"""
    if len(kl) < 60:
        return 0, "K线不足"
    closes = [k[2] for k in kl]
    ma5 = sum(closes[-5:]) / 5.0
    ma20 = sum(closes[-20:]) / 20.0
    ma60 = sum(closes[-60:]) / 60.0
    c = closes[-1]
    score = 0
    if ma5 > ma20 > ma60:
        score += 0.6
    elif ma5 < ma20 < ma60:
        score -= 0.6
    r20 = (c / closes[-21] - 1) * 100 if closes[-21] else 0
    if r20 > 8:
        score += 0.4
    elif r20 < -8:
        score -= 0.4
    return max(-1.0, min(1.0, score)), "MA%s 20日%+.1f%%" % (
        "多头" if ma5 > ma20 > ma60 else ("空头" if ma5 < ma20 < ma60 else "缠绕"), r20)



def _regime_breadth_score(s):
    """市场宽度（扩散指标，东北证券 NH-NL 思路）：自选池内 20 日新高占比 - 新低占比。

    用自选池（约 100 只跨行业龙头）作为全市场宽度的代理。返回 [-1, 1]。
    """
    sig = s.get("signals") or {}
    new_high = new_low = total = 0
    for item in s["pool"]:
        code = item["code"]
        notes = (sig.get(code) or {}).get("notes") or []
        joined = " ".join(notes)
        if "20日新高" in joined:
            new_high += 1
        if "20日新低" in joined:
            new_low += 1
        total += 1
    if total == 0:
        return 0, "无数据"
    nh_pct = new_high / total
    nl_pct = new_low / total
    diff = nh_pct - nl_pct
    score = max(-1.0, min(1.0, diff * 4.0))  # 差 ±25% 映射到 ±1
    return score, "新高%.0f%%/新低%.0f%%" % (nh_pct * 100, nl_pct * 100)



def market_regime(s):
    """市场环境温度计（借鉴 QuantsPlaybook 的 RSRS + 扩散指标 + 均线动量）。

    合成 [-3, 3] 的 regime 分，映射三档：>=1 进攻 / -1~1 中性 / <=-1 防守。
    结果写入 s["regime"]，供买卖决策与风控读取。
    """
    cfg = s["config"]
    if not cfg.get("useRegime"):
        s["regime"] = {"enabled": False, "score": 0, "tier": "neutral"}
        return s["regime"]
    idx = cfg.get("indexCode", "sh000001")
    detail = {}
    total = 0.0
    try:
        kl = server.fetch_kline(idx, 80)
        r, rnote = _regime_rsrs_score(kl)
        m, mnote = _regime_momentum_score(kl)
        detail["rsrs"] = rnote
        detail["momentum"] = mnote
        total += r + m
    except Exception as exc:
        detail["rsrs"] = "不可用: %s" % exc
        detail["momentum"] = "不可用"
        r = m = 0.0
    try:
        b, bnote = _regime_breadth_score(s)
        detail["breadth"] = bnote
        total += b
    except Exception as exc:
        detail["breadth"] = "不可用: %s" % exc
        b = 0.0
    score = max(-3.0, min(3.0, total))
    if score >= 1.0:
        tier = "attack"
    elif score <= -1.0:
        tier = "defense"
    else:
        tier = "neutral"
    s["regime"] = {"enabled": True, "score": round(score, 2), "tier": tier,
                   "detail": detail, "time": now_str()}
    # 波动率维度：与方向分独立计算，合成到 regime 的 vol 字段（供仓位缩放读取）
    if cfg.get("useVolTarget"):
        try:
            _vol_scale(s)
        except Exception as exc:
            (s.get("regime") or {})["vol"] = {"enabled": True, "scale": 1.0,
                                              "note": "不可用: %s" % exc}
    # 行业轮动 RRG：识别强势行业，供进攻档聚焦强势行业（独立于方向分）
    if cfg.get("useSectorRotation"):
        try:
            sector_rotation(s)
        except Exception as exc:
            log("行业轮动异常: %s" % exc)
    return s["regime"]



def _volatility_profile(kl):
    """波动率画像（借鉴 QuantsPlaybook 的「时变夏普 / 波动率目标」思想）。

    用指数日 K 计算：
      - 年化波动率：最近 20 日日收益率标准差 × sqrt(252)
      - 时变夏普：最近 60 日日收益均值 / 标准差（短期风险调整后收益）
    返回 (ann_vol_pct, sharpe, note)。数据不足返回 (0, 0, "K线不足")。
    """
    if len(kl) < 22:
        return 0.0, 0.0, "K线不足"
    closes = [k[2] for k in kl]
    rets = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 20:
        return 0.0, 0.0, "收益率不足"
    r20 = rets[-20:]
    mean = sum(r20) / len(r20)
    var = sum((r - mean) ** 2 for r in r20) / len(r20)
    std = var ** 0.5
    ann_vol = std * (252 ** 0.5) * 100.0
    # 时变夏普：用更长窗口（60 日）均值 / 20 日波动，捕捉「短期风险调整后收益」
    if len(rets) >= 60:
        m60 = sum(rets[-60:]) / len(rets[-60:])
    else:
        m60 = sum(rets) / len(rets)
    sharpe = (m60 / std) if std > 0 else 0.0
    return ann_vol, sharpe, "年化波动%.0f%% 夏普%.2f" % (ann_vol, sharpe)



def _vol_scale(s):
    """波动率自适应仓位系数（0.5 ~ 1.2）。

    年化波动越高、时变夏普越低 → 系数越小（降仓）；低波动 + 正夏普 → 放大仓位。
    结果写入 s["regime"]["vol"]，供买入仓位与总仓位上限读取。
    """
    cfg = s["config"]
    if not cfg.get("useVolTarget"):
        return 1.0
    idx = cfg.get("indexCode", "sh000001")
    try:
        kl = server.fetch_kline(idx, 80)
        ann_vol, sharpe, note = _volatility_profile(kl)
    except Exception as exc:
        note = "不可用: %s" % exc
        ann_vol = sharpe = 0.0
    # 映射：年化波动 15% 以下低波、15~30% 中波、30% 以上高波
    if ann_vol <= 0:
        scale = 1.0
        tier = "neutral"
    elif ann_vol < 15:
        scale = 1.0 + min(0.2, sharpe * 0.1 if sharpe > 0 else 0.0)
        tier = "low"      # 低波动：可小幅加仓（正夏普时）
    elif ann_vol <= 30:
        scale = 1.0
        tier = "mid"
    else:
        # 高波动：线性收缩，最高降 50%；时变夏普为负进一步收紧
        scale = max(0.5, 1.0 - (ann_vol - 30) / 60.0 - (0.1 if sharpe < 0 else 0.0))
        tier = "high"
    scale = round(max(0.5, min(1.2, scale)), 2)
    reg = s.get("regime") or {}
    reg["vol"] = {"enabled": True, "annVol": round(ann_vol, 1),
                  "sharpe": round(sharpe, 2), "scale": scale, "tier": tier,
                  "note": note, "time": now_str()}
    s["regime"] = reg
    return scale



def lhb_score(net):
    """龙虎榜净买额打分。返回 (分数[-2,2], 明细或空串)。"""
    yi = net / 1e8
    if net > 5e7:
        return 2, "龙虎榜净买%.2f亿+2" % yi
    if net > 1e7:
        return 1, "龙虎榜净买%.2f亿+1" % yi
    if net < -5e7:
        return -2, "龙虎榜净卖%.2f亿-2" % abs(yi)
    if net < -1e7:
        return -1, "龙虎榜净卖%.2f亿-1" % abs(yi)
    return 0, ""



# ---------------------------------------------------------------- 量价因子 + 行业轮动
def price_volume_factors(code):
    """量价微观结构因子（借鉴 QuantsPlaybook 的买卖压力/振幅/凸显性因子）。

    在 tech_score 的「形态」之上，补足「资金质量」维度，用于让动态调池候选更挑剔。
    返回 (质量分[-3,3], 明细[], 因子dict)。三个因子：
      1. 买卖压力（Money Flow）：上涨日量能 vs 下跌日量能失衡度。上涨日放量、下跌日缩量
         = 资金持续净流入（压力为正）；反之资金在出逃。
      2. 振幅因子（Amplitude）：近20日 (high-low)/prev_close 均值。适度振幅(2%~6%)+上涨
         才是健康换手；过低(死水)或过高(剧烈博弈)都扣分。
      3. 凸显性因子（Salience/特质）：20日超额收益的「稳定性」——用日均收益/波动(类信息比)，
         衡量这只票在大盘之外「独立走强」的质量，剔掉靠单日脉冲撑起来的伪强势。
    """
    kl = server.fetch_kline(code, 65)
    if len(kl) < 22:
        return 0, ["K线不足"], {}
    closes = [k[2] for k in kl]
    opens = [k[1] for k in kl]
    highs = [k[3] for k in kl]
    lows = [k[4] for k in kl]
    vols = [k[5] for k in kl]
    score, notes = 0, []

    # ---- 因子1：买卖压力（近20日） ----
    up_vol = 0.0
    down_vol = 0.0
    for i in range(-20, 0):
        chg = closes[i] - closes[i - 1]
        v = vols[i] or 0
        if chg >= 0:
            up_vol += v
        else:
            down_vol += v
    if (up_vol + down_vol) > 0:
        # 买卖压力比：上涨日量能占比，>0.5 表示资金偏多
        pressure = up_vol / (up_vol + down_vol)
    else:
        pressure = 0.5
    if pressure >= 0.60:
        score += 1; notes.append("买压偏强+1")
    elif pressure <= 0.42:
        score -= 1; notes.append("卖压偏强-1")

    # ---- 因子2：振幅因子（近20日平均日内振幅） ----
    amps = []
    for i in range(-20, 0):
        if closes[i - 1] > 0:
            amps.append((highs[i] - lows[i]) / closes[i - 1] * 100)
    avg_amp = sum(amps) / len(amps) if amps else 0
    if 2.0 <= avg_amp <= 6.0:
        score += 1; notes.append("振幅健康+1")
    elif avg_amp < 1.2:
        score -= 1; notes.append("振幅过低(死水)-1")
    elif avg_amp > 9.0:
        score -= 1; notes.append("振幅过高(剧烈博弈)-1")

    # ---- 因子3：凸显性（20日信息比：日均收益/收益波动） ----
    rets = []
    for i in range(-20, 0):
        if closes[i - 1] > 0:
            rets.append(closes[i] / closes[i - 1] - 1)
    if len(rets) >= 5:
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / len(rets)
        sd = var_r ** 0.5
        ir = mean_r / sd if sd > 0 else 0
        # 信息比(日频)年化放大约 sqrt(252)，用阈值判断：稳且强 > 0.3 算强
        if mean_r > 0 and ir > 0.3:
            score += 1; notes.append("凸显性强(稳定跑赢)+1")
        elif mean_r > 0 and ir <= 0.1:
            # 涨但波动极大 = 靠脉冲，扣分
            score -= 1; notes.append("脉冲式上涨(不稳)-1")
        elif mean_r < 0:
            score -= 1; notes.append("超额收益为负-1")

    return max(-3, min(3, score)), notes[:6], {
        "pressure": round(pressure, 3), "avgAmp": round(avg_amp, 2),
        "infoRatio": round(ir, 3), "meanRet": round(mean_r * 100, 2)}



# 行业归类：tags 首个关键词 -> 一级行业（用于 RRG 行业轮动）
_SECTOR_MAP = {
    "银行": "银行", "保险": "非银金融", "券商": "非银金融",
    "白酒": "食品饮料", "调味品": "食品饮料", "乳制品": "食品饮料", "粮油": "食品饮料",
    "啤酒": "食品饮料", "食品": "食品饮料", "饮料": "食品饮料",
    "半导体": "电子", "消费电子": "电子", "面板": "电子", "安防": "电子",
    "军工": "国防军工", "光伏": "电力设备", "锂电池": "电力设备", "新能源车": "电力设备",
    "风电": "电力设备", "储能": "电力设备", "电力": "公用事业",
    "医药": "医药生物", "中药": "医药生物", "创新药": "医药生物", "医疗": "医药生物",
    "地产": "房地产", "房地产": "房地产",
    "服务器": "计算机", "软件": "计算机", "云计算": "计算机", "算力": "计算机",
    "传媒": "传媒", "游戏": "传媒",
    "煤炭": "煤炭", "化工": "基础化工", "建材": "建筑材料", "水泥": "建筑材料",
    "工程机械": "机械设备", "汽车零部件": "汽车", "汽车": "汽车",
    "家电": "家用电器", "航空": "交通运输", "机场": "交通运输", "物流": "交通运输",
    "通信": "通信", "电信": "通信", "运营商": "通信", "5G": "通信",
    "养殖": "农林牧渔", "农业": "农林牧渔", "钢铁": "钢铁", "有色": "有色金属",
    "机械": "机械设备", "机器人": "机械设备",
}



def sector_of(item):
    """从股票 tags 推断一级行业。返回行业名，无法归类返回 '其他'。"""
    tags = (item.get("tags") or "").split(",")
    for t in tags:
        t = t.strip()
        if t in _SECTOR_MAP:
            return _SECTOR_MAP[t]
    return "其他"



def sector_rotation(s):
    """行业轮动 RRG（相对旋转图）：算各行业相对大盘的强度与动能，识别强势行业。

    思路（借鉴 QuantsPlaybook 的 RRG / 行业量价因子）：
      - 对自选池按行业归类，取行业内股票的平均 20日/60日涨幅作为行业表现。
      - 大盘用 indexCode 的同期涨幅做基准。
      - RS-Ratio（横轴）= 行业20日涨幅 - 大盘20日涨幅（相对强度）
      - RS-Momentum（纵轴）= 行业20日涨幅 - 行业60日涨幅（动能，加速/减速）
      - 四象限：领先(强+加速) / 转强(弱但加速) / 转弱(强但减速) / 落后(弱+减速)
    返回 {sectors: [{name, ratio, momentum, tier, n}], leading: [行业名...], time}。
    """
    cfg = s["config"]
    idx_code = cfg.get("indexCode", "sh000001")
    try:
        idx_kl = server.fetch_kline(idx_code, 65)
    except Exception:
        idx_kl = []
    if len(idx_kl) < 62:
        return None
    idx_c = [k[2] for k in idx_kl]
    idx20 = (idx_c[-1] / idx_c[-21] - 1) * 100 if idx_c[-21] else 0
    idx60 = (idx_c[-1] / idx_c[-61] - 1) * 100 if idx_c[-61] else 0

    groups = {}
    for item in s["pool"]:
        sec = sector_of(item)
        groups.setdefault(sec, []).append(item["code"])

    sectors = []
    for sec, codes in groups.items():
        r20s, r_prev20 = [], []   # r20s=近20日涨幅，r_prev20=前20日(21-40日)涨幅，同窗口比"加速度"
        for code in codes:
            try:
                kl = server.fetch_kline(code, 65)
            except Exception:
                kl = []
            if len(kl) < 41:
                continue
            c = [k[2] for k in kl]
            if c[-21]:
                r20s.append((c[-1] / c[-21] - 1) * 100)
            if c[-41] and c[-21]:
                # 前20日区间：从 -41 到 -21 的涨幅（同20天窗口，比近期是否加速）
                r_prev20.append((c[-21] / c[-41] - 1) * 100)
        if not r20s:
            continue
        sec20 = sum(r20s) / len(r20s)
        sec_prev = sum(r_prev20) / len(r_prev20) if r_prev20 else 0.0
        ratio = round(sec20 - idx20, 2)          # 相对强度（近20日 vs 大盘近20日）
        momentum = round(sec20 - sec_prev, 2)    # 动能：近20日涨幅 - 前20日涨幅（加速为正）
        if ratio >= 0 and momentum >= 0:
            tier = "leading"      # 领先：强且加速
        elif ratio < 0 and momentum >= 0:
            tier = "improving"    # 转强：弱但加速
        elif ratio >= 0 and momentum < 0:
            tier = "weakening"    # 转弱：强但减速
        else:
            tier = "lagging"      # 落后：弱且减速
        sectors.append({"name": sec, "ratio": ratio, "momentum": momentum,
                        "tier": tier, "n": len(codes)})

    sectors.sort(key=lambda x: (x["ratio"] + x["momentum"]), reverse=True)
    leading = [x["name"] for x in sectors if x["tier"] == "leading"]
    improving = [x["name"] for x in sectors if x["tier"] == "improving"]
    result = {"sectors": sectors, "leading": leading, "improving": improving,
              "time": now_str()}
    s["sectors"] = result
    return result
