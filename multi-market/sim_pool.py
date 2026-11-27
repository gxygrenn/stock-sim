# -*- coding: utf-8 -*-
"""动态调池：强度榜候选、剔除、再平衡、公告打分、信号综合计算。"""

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



# ---------------------------------------------------------------- 信号源
# 公告标题关键词（强词权重 2）
ANN_GOOD = [
    "回购", "增持", "中标", "重大合同", "签署.*合同", "战略合作", "预增", "扭亏",
    "分红", "利润分配", "获批", "注册证", "投产", "量产", "股权激励", "涨价",
]

ANN_BAD = [
    "减持", "预亏", "亏损", "业绩.*下滑", "立案", "警示函", "处罚", "诉讼", "仲裁",
    "冻结", "退市", "问询函", "商誉减值", "质押", "违规", "终止.*重组", "风险提示",
]

ANN_STRONG = ["减持", "回购", "增持", "立案", "预增", "预亏", "扭亏", "中标", "重大合同", "退市"]



# ---------------------------------------------------------------- 动态调池
def _cn_code_prefix(f12, f13):
    """东财 6 位代码 -> 带交易所前缀的完整代码（sh/sz）。f13: 1=沪 0=深。"""
    f12 = str(f12)
    if f12[:2] in ("60", "68", "90"):
        return "sh" + f12
    if f12[:2] in ("00", "30", "20", "12"):
        return "sz" + f12
    return ("sh" if str(f13) == "1" else "sz") + f12



_RANK_CACHE = {}      # "cn" -> (fetch_ts, cands)，候选缓存，避免 30s 循环反复撞接口

_RANK_TTL = 900       # 候选缓存 15 分钟



def _rank_get(url):
    hdrs = {"User-Agent": UA["User-Agent"],
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive"}
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=12) as resp:
        return resp.read().decode("utf-8", "ignore")



def _is_bad_name(name):
    """ST / 上市首日(N) / 次新(C) / 未盈利(-U) / 同股不同权(-W) 不纳入候选。"""
    n = (name or "").upper()
    return (n.startswith(("N", "C")) or "ST" in n or "-U" in n or "-W" in n)



def pool_remove_candidates(s):
    """分析当前自选池，找出「没前景」应淘汰的股票。

    命中任一即建议淘汰（理由按优先级）：
      - PE < 0：亏损
      - PE > 80：估值过热
      - 成交额 < 1 亿：流动性枯竭
      - 60 日跌幅 < -30%：长期走弱
      - 60 日跌幅 < -20% 且空头排列：趋势崩溃
    """
    out = []
    for item in s["pool"]:
        code = item["code"]
        if code.startswith("us"):
            continue
        q = (s.get("poolQuote") or {}).get(code) or {}
        reasons = []
        pe = q.get("pe", 0) or 0
        if pe < 0:
            reasons.append("亏损(PE为负)")
        elif pe > 80:
            reasons.append("PE%.0f 估值过热" % pe)
        amount = q.get("amount", 0) or 0
        if amount > 0 and amount < 1e8:
            reasons.append("成交额%.0f万 流动性枯竭" % (amount / 1e4))
        try:
            kl = server.fetch_kline(code, 61)
        except Exception:
            kl = []
        if len(kl) >= 61:
            closes = [k[2] for k in kl]
            r60 = (closes[-1] / closes[-61] - 1) * 100 if closes[-61] else 0
            ma5 = sum(closes[-5:]) / 5.0
            ma20 = sum(closes[-20:]) / 20.0
            ma60 = sum(closes[-60:]) / 60.0
            if r60 < -30:
                reasons.append("60日跌幅%.0f%%" % r60)
            elif r60 < -20 and ma5 < ma20 < ma60:
                reasons.append("60日跌幅%.0f%% 且空头排列" % r60)
        if not reasons:
            continue
        out.append({"code": code, "name": item["name"], "reasons": reasons[:3],
                    "pe": pe, "amount": amount})
    return out



def build_rebalance(s):
    """生成一轮调池建议写入 s['poolChanges']（门控：等 LLM 评审确认后落地）。"""
    cfg = s["config"]
    if not cfg.get("useRebalance"):
        return {"generated": 0, "msg": "调池未启用"}
    if not (s.get("poolQuote")):
        try:
            server.refresh_prices(s)
        except Exception:
            pass
    existing = {p["code"] for p in s["pool"]}
    now = datetime.now()

    # 2026-11-27（用户拍板「扩池」）：被否的调池建议此前永久滞留 poolChanges，
    # 既把列表堆成几十条死数据，又因 pend_codes 命中而把该 code 永久挡在「已建议」
    # 之外——线上 cnc 23 条、cna 30 条全是 rejected，池子实际停止扩张。
    # 处置：超过 poolRejectTTLHours 的 rejected 条目清出队列，允许重新提议
    # （冷却仍由 poolChangeLog 管，落地过的票不会立刻回流）。
    ttl_h = float(cfg.get("poolRejectTTLHours", 72) or 0)
    if ttl_h > 0:
        kept = []
        for p in (s.get("poolChanges") or []):
            if (p.get("state") or "") == "rejected":
                ts = p.get("queuedAt") or p.get("time") or ""
                try:
                    age_h = (now - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600.0
                except Exception:
                    age_h = 0.0
                if age_h >= ttl_h:
                    continue
            kept.append(p)
        s["poolChanges"] = kept

    pend = s.setdefault("poolChanges", [])
    pend_codes = {p["code"] for p in pend}
    logbook = {r.get("code"): r for r in (s.get("poolChangeLog") or [])}
    cool_days = int(cfg.get("rebalanceCooldownDays", 21))

    def cooled(code):
        r = logbook.get(code)
        if not r or not r.get("time"):
            return True
        try:
            return (now - datetime.strptime(r["time"][:10], "%Y-%m-%d")).days >= cool_days
        except Exception:
            return True

    max_add = int(cfg.get("rebalanceMaxAdd", 6))
    max_remove = int(cfg.get("rebalanceMaxRemove", 6))
    # 池子上限：扩池不是无限扩张，到顶后只做「换血」（先淘汰再选入），防止信号计算越拖越慢
    cap = int(cfg.get("rebalancePoolCap", 0) or 0)
    if cap and len(s["pool"]) >= cap:
        max_add = 0

    # ---- 淘汰建议 ----
    n_remove = 0
    for r in pool_remove_candidates(s):
        if r["code"] in pend_codes or not cooled(r["code"]):
            continue
        if n_remove >= max_remove:
            break
        pend.append({"code": r["code"], "name": r["name"], "direction": "remove",
                     "reason": "；".join(r["reasons"]), "score": 0,
                     "queuedAt": now_str(), "state": "waiting"})
        pend_codes.add(r["code"])
        n_remove += 1

    # ---- 选入建议 ----
    try:
        # 强势榜取样数随扩池放宽（原写死 40，一轮最多选 6~8 只，样本太少）
        cands = server.fetch_strength_candidates(int(cfg.get("rebalanceStrengthLimit", 40)))
    except Exception as exc:
        log("强势榜抓取失败: %s" % exc)
        cands = []
    n_add = 0
    # 2026-11-27（用户拍板「扩池」）：门槛原本写死 amount≥1亿 / 换手 3~20% / PE 0~80 /
    # 60日涨幅≥5%，一轮最多 6 只、冷却 21 天。实测把大量「60日刚转强、成交额 6-10 亿」
    # 的中盘强势票挡在池外，池子 84 只里 delta≥3 的常年只有 1-3 只。改为配置化并放宽：
    # 成交额 ≥rebalanceMinAmount、换手 rebalanceTurnLo~Hi、60日 ≥rebalanceMinR60。
    min_amount = float(cfg.get("rebalanceMinAmount", 1e8) or 0)
    turn_lo = float(cfg.get("rebalanceTurnLo", 3) or 0)
    turn_hi = float(cfg.get("rebalanceTurnHi", 20) or 100)
    max_pe = float(cfg.get("rebalanceMaxPE", 80) or 80)
    min_r60 = float(cfg.get("rebalanceMinR60", 5) or 0)
    for c in cands:
        if c["code"] in existing or c["code"] in pend_codes:
            continue
        if not cooled(c["code"]) or _is_bad_name(c["name"]):
            continue
        if c["amount"] < min_amount:          # 流动性
            continue
        if not (turn_lo <= c["turn"] <= turn_hi):   # 活跃但不爆炒
            continue
        if c["pe"] <= 0 or c["pe"] > max_pe:  # 盈利且不过热
            continue
        try:
            kl = server.fetch_kline(c["code"], 61)
        except Exception:
            kl = []
        if len(kl) < 61:
            continue                          # 上市不足 60 日，剔除次新
        closes = [k[2] for k in kl]
        r60 = (closes[-1] / closes[-61] - 1) * 100 if closes[-61] else 0
        if r60 < min_r60:
            continue                          # 60 日趋势未走强
        if n_add >= max_add:
            break
        # ---- 量价因子增强：候选除资金流向外，量价微观结构也必须健康 ----
        try:
            pvf, pvf_notes, pvf_meta = price_volume_factors(c["code"])
        except Exception:
            pvf, pvf_notes, pvf_meta = 0, [], {}
        # 行业轮动：进攻档聚焦强势行业，弱势行业候选降权（可选）
        sector_bonus = 0.0
        if cfg.get("useSectorRotation"):
            sec = c.get("industry") or "?"
            if sec and sec in (s.get("sectors") or {}).get("leading", []):
                sector_bonus = 0.6
            elif sec and sec in (s.get("sectors") or {}).get("improving", []):
                sector_bonus = 0.3
        # 量价质量分作为硬门槛：pvf < 0 的候选（微观结构差）直接剔除
        if pvf < 0:
            continue
        score = (c["netIn"] / 1e8 * 1.0 + c["pct"] * 0.3
                 + pvf * 0.5 + sector_bonus)
        pend.append({"code": c["code"], "name": c["name"], "direction": "add",
                     "reason": "主力净流入%.2f亿 60日%.0f%% 量价%d分%s"
                               % (c["netIn"] / 1e8, r60, pvf,
                                  (" 行业%s" % c["industry"]) if c.get("industry") else ""),
                     "score": round(score, 2),
                     "meta": {"industry": c["industry"], "pct": c["pct"],
                              "turn": c["turn"], "pe": c["pe"],
                              "pvf": pvf, "pvfNotes": pvf_notes},
                     "queuedAt": now_str(), "state": "waiting"})
        pend_codes.add(c["code"])
        n_add += 1

    s["lastRebalance"] = today_str()
    save_state()
    log("生成调池建议：淘汰 %d 只 / 选入 %d 只（待 LLM 门控确认）" % (n_remove, n_add))
    return {"generated": n_remove + n_add, "remove": n_remove, "add": n_add}



def execute_approved_pool_changes(s):
    """落地 LLM 已批准的调池建议：remove 移出监控池（不强制平仓）、add 补全字段入池。"""
    pend = s.get("poolChanges") or []
    if not pend:
        return 0
    logbook = s.setdefault("poolChangeLog", [])
    done = 0
    keep = []
    for p in pend:
        if (p.get("state") or "") != "approved":
            keep.append(p)
            continue
        code, name, direction = p["code"], p["name"], p["direction"]
        if direction == "remove":
            before = len(s["pool"])
            s["pool"] = [x for x in s["pool"] if x["code"] != code]
            removed = len(s["pool"]) < before
            logbook.append({"code": code, "name": name, "direction": "remove",
                            "result": "removed" if removed else "not_in_pool",
                            "time": now_str(), "note": p.get("reason", "")})
            if removed:
                log("调池落地：淘汰 %s(%s)（%s）" % (name, code, p.get("reason", "")))
        else:
            if any(x["code"] == code for x in s["pool"]):
                logbook.append({"code": code, "name": name, "direction": "add",
                                "result": "already_in_pool", "time": now_str(),
                                "note": p.get("reason", "")})
            else:
                meta = p.get("meta") or {}
                s["pool"].append({"name": name, "code": code, "alias": name,
                                  "beta": 1.0,
                                  "tags": "%s,热点,强势" % (meta.get("industry") or "")})
                # 入池即补拉现价进 poolQuote：否则新票要等下一次 refresh_prices 才有价，
                # 若赶上休市/扫描停摆，评审时 priceAtReview 会落空（2026-10-10 已实锤 A 股误杀归因缺价）
                try:
                    _q = server.fetch_prices([code]).get(code)
                    if _q and (_q.get("price") or 0) > 0:
                        s.setdefault("poolQuote", {})[code] = _q
                except Exception:
                    pass
                logbook.append({"code": code, "name": name, "direction": "add",
                                "result": "added", "time": now_str(),
                                "note": p.get("reason", "")})
                log("调池落地：选入 %s(%s)（%s）" % (name, code, p.get("reason", "")))
        done += 1
    s["poolChanges"] = keep
    s["poolChangeLog"] = logbook[-100:]
    return done



def ann_score(titles):
    """公告标题打分。返回 (分数[-3,3], 明细)。"""
    score, notes = 0, []
    for t in titles:
        hit_g = [w for w in ANN_GOOD if re.search(w, t)]
        hit_b = [w for w in ANN_BAD if re.search(w, t)]
        for w in hit_g:
            wgt = 2 if w in ANN_STRONG else 1
            score += wgt
            notes.append("公告[%s]%s%+d" % (w, t[:14], wgt))
        for w in hit_b:
            wgt = 2 if w in ANN_STRONG else 1
            score -= wgt
            notes.append("公告[%s]%s-%d" % (w, t[:14], wgt))
    return max(-3, min(3, score)), notes[:4]



def val_filter(s, cfg, code):
    """估值/换手过滤。返回 (是否禁买, 明细, 修正分)。"""
    if not cfg.get("useVal"):
        return False, [], 0
    q = (s.get("poolQuote") or {}).get(code) or {}
    pe, turn = q.get("pe", 0), q.get("turn", 0)
    block, notes, delta = False, [], 0
    if pe < 0:
        block = True; delta -= 1; notes.append("亏损股禁买-1")
    elif pe > 80:
        block = True; delta -= 1; notes.append("PE%.0f过高禁买-1" % pe)
    if turn > 12:
        block = True; delta -= 1; notes.append("换手%.1f%%过热禁买-1" % turn)
    return block, notes, delta



def compute_signals(s):
    """为自选池每只股票计算综合信号，写入 s['signals']。"""
    cfg = s["config"]
    lhb = {}
    if cfg.get("useLhb"):
        try:
            lhb = server.fetch_lhb()
        except Exception as exc:
            log("龙虎榜获取失败: %s" % exc)
    ann_by_code = {}
    if cfg.get("useAnn"):
        try:
            for a in server.fetch_announcements():
                ann_by_code.setdefault(a["code"], []).append(a["title"])
        except Exception as exc:
            log("公告获取失败: %s" % exc)
    signals = {}
    s["signals"] = signals          # 提前挂上：评议团预览要读同一份最新数据
    # 行业轮动：先算一次 RRG，供 delta 合成里的行业加分用（scan 里 market_regime 也会再算，幂等）
    if cfg.get("useSectorRotation"):
        try:
            sector_rotation(s)
        except Exception as exc:
            log("行业轮动异常: %s" % exc)
    sec_leading = set((s.get("sectors") or {}).get("leading") or [])
    sec_improving = set((s.get("sectors") or {}).get("improving") or [])
    _sec_list = (s.get("sectors") or {}).get("sectors") or []
    sec_weakening = {x["name"] for x in _sec_list if x.get("tier") == "weakening"}
    sec_lagging = {x["name"] for x in _sec_list if x.get("tier") == "lagging"}
    tech_fail = 0
    pvf_fail = 0
    for item in s["pool"]:
        code = item["code"]
        num = re.sub(r"\D", "", code)
        tech, tnotes = 0, []
        if cfg.get("useTech"):
            try:
                tech, tnotes = tech_score(s, code)
            except Exception as exc:
                # 不能静默吞掉：数据源挂了必须看得见，否则信号分悄悄变 0
                tech_fail += 1
                tnotes = ["技术面数据不可用"]
                if tech_fail == 1:
                    log("技术面获取失败(%s): %s" % (code, exc))
        ls, lnote = (lhb_score(lhb[num]) if (cfg.get("useLhb") and num in lhb) else (0, ""))
        ascore, anotes = (ann_score(ann_by_code.get(num, [])) if cfg.get("useAnn") else (0, []))
        block, vnotes, vdelta = val_filter(s, cfg, code)
        # 量价微观结构因子：直接进 delta（折半叠加，[-3,3]→[-1.5,1.5]，避免喧宾夺主）
        pvf = 0.0
        pvf_notes = []
        if cfg.get("usePriceVolume", True):
            try:
                pvf_raw, pvf_notes, _ = price_volume_factors(code)
                pvf = round(pvf_raw * 0.5, 2)
            except Exception as exc:
                pvf_fail += 1
                if pvf_fail == 1:
                    log("量价因子获取失败(%s): %s" % (code, exc))
        # 行业轮动加分：领先/转强行业加分、转弱/落后行业减分，直接进 delta（小权重）
        sec = sector_of(item)
        sector_bonus = 0.0
        if cfg.get("useSectorRotation"):
            if sec in sec_leading:
                sector_bonus = 0.4
                pvf_notes.append("领先行业+0.4")
            elif sec in sec_improving:
                sector_bonus = 0.2
                pvf_notes.append("转强行业+0.2")
            elif sec in sec_weakening:
                sector_bonus = -0.2
                pvf_notes.append("转弱行业-0.2")
            elif sec in sec_lagging:
                sector_bonus = -0.4
                pvf_notes.append("落后行业-0.4")
        delta = max(-6, min(6, tech + ls + ascore + vdelta + pvf + sector_bonus))
        notes = tnotes + ([lnote] if lnote else []) + anotes + vnotes + pvf_notes
        signals[code] = {"tech": tech, "lhb": ls, "ann": ascore, "delta": delta,
                         "pvf": pvf, "sectorBonus": sector_bonus,
                         "block": block, "notes": notes[:6], "time": now_str()}
        # 评议团预览：四位分析师此刻对该股的倾向（不记录、不否决）
        try:
            prev = server.run_council(s, item, code, news_score=0, skip_risk=True, record=False,
                               advisory=True)
            signals[code]["council"] = {"score": prev["score"], "agree": prev["agree"],
                                        "total": prev["total"], "verdict": prev["verdict"],
                                        "votes": prev["votes"][:4]}
        except Exception:
            pass
    if tech_fail:
        log("技术面数据获取失败 %d/%d 只，本轮该维度记 0 分" % (tech_fail, len(s["pool"])))
    s["signals"] = signals
    return signals




def score_text(text):
    """返回 (分数, 利好命中, 利空命中)。标题权重 x2。"""
    mt = re.match(r"[【\[]([^】\]]+)[】\]]", text)
    head = mt.group(1) if mt else ""
    body = text
    good, bad = [], []
    for w in GOOD:
        n = (2 if w in head else 0) + body.count(w)
        if n:
            good.append("%s×%d" % (w, n))
    for w in BAD:
        n = (2 if w in head else 0) + body.count(w)
        if n:
            bad.append("%s×%d" % (w, n))
    score = sum(int(x.split("×")[1]) for x in good) - sum(int(x.split("×")[1]) for x in bad)
    return score, good, bad



def match_pool(text, pool, title=None):
    """返回 (命中的股票名列表, 命中关键词列表)。

    名称/简称/代码在**全文**里匹配——股票自己的名字出现在哪都是强信号；
    板块主题词只在**标题**里认。否则像「金融」这种常见词，在一篇毫不相关
    的稿件正文里被顺带提一句就会误命中（实测：讲加拿大加入欧盟的稿子因为
    写了「一批来自金融与政界的非正式顾问」，一次命中招行+平安+东财）。

    美股账户特殊处理：主题词（tags）过于宽泛（AI/半导体/消费/通信…），
    中文快讯里 A股/港股/日股新闻动辄命中导致「张冠李戴」，故美股只认
    名称/别名/代码，不认主题词，宁可少抓也要抓准。
    """
    hits, keys = [], []
    head = text if title is None else title
    is_us = bool(pool) and str(pool[0].get("code", "")).startswith("us")
    for item in pool:
        name = item.get("name", "")
        num = re.sub(r"\D", "", item.get("code", ""))
        strong = [name] + [a for a in (item.get("alias") or "").split(",") if a]
        if num:
            strong.append(num)
        tags = [t for t in (item.get("tags") or "").split(",") if t]
        for k in strong:
            if k and k in text:
                hits.append(name)
                keys.append(k)
                break
        else:
            if is_us:
                continue          # 美股不靠主题词匹配，杜绝 A股/港股/日股新闻误命中
            for k in tags:
                if k and k in head:
                    hits.append(name)
                    keys.append(k)
                    break
    return hits, keys



def pool_keys(item):
    """某只股票的全部匹配词，用于前端展示。"""
    ks = [item.get("name", "")]
    ks += [a for a in (item.get("alias") or "").split(",") if a]
    return [k for k in ks if k]
