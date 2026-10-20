#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日复盘自助审校器 —— 把 logs/ 里犯过的错变成每日强制检查。

核心原则：**用写复盘那一刻的数据核对，而不是用几小时后的实时价**。
因此取数分两步：
  1) 复盘的同一轮里先 `--save` 存快照到 snap/<date>/（写正文前的数据）
  2) 写完后跑 `--date <date>`，用快照核数字、用实时读回正文

用法:
  python _self_check.py --save                 # 存当日快照（写复盘前跑）
  python _self_check.py                        # 检查今天
  python _self_check.py --date 2026-10-20
  python _self_check.py --live                 # 强制用实时价核对（美股盘中会漂移）
  python _self_check.py --offline              # 全用本地缓存，不联网
  python _self_check.py --json                 # JSON 输出（供自动化读取）

退出码: 0 = 无 FAIL; 1 = 有 FAIL
"""
import argparse
import datetime
import glob
import json
import os
import re
import ssl
import sys
import urllib.request

BASE = "https://stock-sim-us.app.workbuddy.host"
ACCOUNTS = [("us", "美股", "us"), ("cn_conservative", "A股稳健", "cnc"), ("cn_aggressive", "A股激进", "cna")]
SHELF_H = {"us": 12.0, "cn_conservative": 8.0, "cn_aggressive": 8.0}
ROOT = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.path.join(ROOT, "logs")
SNAPDIR = os.path.join(ROOT, "snap")

BANNED = ["全线回暖", "集体上涨", "全线上涨", "普遍上涨", "普涨", "无一例外", "全面飘红", "全面上涨"]
EXIST_MECH = re.compile(r"(增设|建议增加|应增加|补上|加上|加一道).{0,24}(单票|上限|保质期|强制复核|强制线|限买|每日限)")
# 主题 -> (首次提出日期, 关键词)。首次当天不提示重复。
CLUSTERS = {
    "批单保质期空转": ("2026-10-13", ["保质期", "过期", "批单", "排队"]),
    "满仓锁死": ("2026-10-13", ["满仓", "现金不足", "仓位锁死", "没钱"]),
    "强制复核线/止损滞后": ("2026-10-15", ["强制复核", "止损", "触发线", "滑点"]),
    "延迟成交接刀": ("2026-10-13", ["延迟", "接刀", "过期信号", "成交时点"]),
    "单票超限": ("2026-10-15", ["单票", "超限", "集中度"]),
    "零股塞单": ("2026-10-15", ["零股", "最小下单", "88元"]),
    "组合层目标": ("2026-10-20", ["总仓位", "仓位区间", "组合层"]),
    "追强衰减": ("2026-10-16", ["追强", "动量", "量能确认"]),
}
TODAY_WORDS = ["今日", "今天", "当天", "盘中"]
HEDGE_WORDS = ["抗跌", "对冲", "缓冲", "垫"]
PP_CONTEXT = ["止损", "线", "滑点", "修复", "扩大", "偏离", "拉开"]


# ---------------- 取数 ----------------
def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def fetch(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "selfcheck/2.0"})
    with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_any(path):
    """优先实时，失败回退本地缓存。"""
    try:
        return fetch(path)
    except Exception:
        return None


def latest(pattern):
    files = sorted(glob.glob(os.path.join(ROOT, pattern)), key=os.path.getmtime)
    return files[-1] if files else None


def save_snapshot(date):
    d = os.path.join(SNAPDIR, date)
    os.makedirs(d, exist_ok=True)
    ok = []
    for acct, label, tag in ACCOUNTS:
        f, s = fetch_any("/api/daily_facts?account=" + acct), fetch_any("/api/state?account=" + acct)
        if f and s:
            json.dump(f, open(os.path.join(d, "facts_%s.json" % acct), "w", encoding="utf-8"), ensure_ascii=False)
            json.dump(s, open(os.path.join(d, "state_%s.json" % acct), "w", encoding="utf-8"), ensure_ascii=False)
            ok.append(acct)
    print("快照已保存 %s：%s" % (d, ", ".join(ok) if ok else "全部失败"))
    return 0 if ok else 1


def load_sources(acct, tag, date, mode):
    """返回 (facts_truth, state_truth, review_state, src_note)"""
    snap_f = os.path.join(SNAPDIR, date, "facts_%s.json" % acct)
    snap_s = os.path.join(SNAPDIR, date, "state_%s.json" % acct)
    live_f = live_s = None
    if mode != "offline":
        live_f, live_s = fetch_any("/api/daily_facts?account=" + acct), fetch_any("/api/state?account=" + acct)
    if mode == "live":
        return live_f, live_s, live_s, "实时"
    if os.path.exists(snap_f) and os.path.exists(snap_s):
        tf = json.load(open(snap_f, encoding="utf-8"))
        ts = json.load(open(snap_s, encoding="utf-8"))
        return tf, ts, (live_s or ts), "快照 %s" % date
    if live_f and live_s:
        return live_f, live_s, live_s, "无快照·按实时核对"
    # 离线兜底
    ff, ss = latest("_facts_%s_*.json" % tag), latest("_state_%s_*.json" % acct)
    if ff and ss:
        return json.load(open(ff, encoding="utf-8")), json.load(open(ss, encoding="utf-8")), json.load(open(ss, encoding="utf-8")), "本地缓存 " + os.path.basename(ss)
    return None, None, None, "取数失败"


# ---------------- 真值 ----------------
def build_truth(acct, facts, state, today):
    a = facts.get("account", {})
    total = a.get("totalAsset") or (state["cash"] + sum(h["shares"] * h["price"] for h in state["holdings"]))
    cash = a.get("cash", state.get("cash", 0))
    fmap = {h["code"]: h for h in facts.get("holdings", [])}
    holds = []
    for h in state.get("holdings", []):
        if h.get("shares", 0) <= 0:      # 过滤 0 股残影
            continue
        fh = fmap.get(h["code"], {})
        holds.append({
            "name": h["name"], "code": h["code"], "shares": h["shares"],
            "price": h["price"], "preClose": h.get("preClose"),
            "pct": h.get("pct"), "pnlPct": fh.get("pnlPct"),
            "weight": h["shares"] * h["price"] / total * 100,
        })
    tags = {p["code"]: (p.get("tags") or "") for p in state.get("pool", [])}
    ind = {}
    for h in holds:
        t = tags.get(h["code"], "")
        first = t.split(",")[0].strip() if t else ""
        key = first if first else "未分类"
        ind[key] = ind.get(key, 0.0) + h["weight"]

    rows = sorted(state.get("history") or facts.get("historyLast10") or [], key=lambda r: r["date"])
    last_row = rows[-1] if rows else None
    prev = [r for r in rows if r["date"] < today]
    prev_total = prev[-1]["total"] if prev else None
    mkt_today = None
    for r in rows:
        if r["date"] == today:
            mkt_today = r.get("mkt")
    mkt = mkt_today if mkt_today is not None else (prev[-1].get("mkt") if prev else None)

    today_pnl = a.get("todayPnl")
    float_pnl = sum(h["shares"] * (h["price"] - h["preClose"]) for h in holds if h.get("preClose"))
    computed = today_pnl is None
    day_pnl = float_pnl if computed else today_pnl
    day_pct = (day_pnl / prev_total * 100) if prev_total else None
    excess = (day_pct - mkt) if (day_pct is not None and mkt is not None) else None

    council = facts.get("councilToday") or []
    reviewed_today = facts.get("reviewedToday") or []
    review_log = facts.get("reviewLog") or []
    trades = facts.get("trades") or []
    pend = [p for p in (facts.get("pendingReview") or []) if p.get("state") == "approved"]

    return {
        "acct": acct, "total": total, "cash": cash, "cash_pct": cash / total * 100 if total else None,
        "holds": holds, "n_holds": len(holds),
        "n_pct_up": sum(1 for h in holds if (h.get("pct") or 0) > 0),
        "n_pct_down": sum(1 for h in holds if (h.get("pct") or 0) < 0),
        "n_pnl_up": sum(1 for h in holds if (h.get("pnlPct") or 0) > 0),
        "n_pnl_down": sum(1 for h in holds if (h.get("pnlPct") or 0) < 0),
        "ind": ind, "tags": tags,
        "n_council": len(council),
        "n_prop_buy": sum(1 for x in council if x.get("proposal") == "买入"),
        "n_veto": sum(1 for x in council if x.get("verdict") == "否决"),
        "n_watch": sum(1 for x in council if x.get("verdict") in ("观望", "HOLD")),
        "n_reviewed": len(reviewed_today),
        "n_reviewlog_today": sum(1 for x in review_log if str(x.get("reviewedAt", ""))[:10] == today),
        "trades": trades, "pend": pend,
        "hist_last": last_row["date"] if last_row else None,
        "n_hist": len(rows), "prev_total": prev_total, "mkt": mkt,
        "day_pnl": day_pnl, "float_pnl": float_pnl, "day_pct": day_pct, "excess": excess,
        "computed": computed, "today_pnl_raw": today_pnl,
        "shelf": SHELF_H.get(acct, 8.0),
    }


# ---------------- 检查 ----------------
def add(res, code, level, msg):
    if any(r["code"] == code and r["msg"] == msg for r in res):
        return
    res.append({"code": code, "level": level, "msg": msg})


def clauses(text):
    return [c for c in re.split(r"[，。；、\n（）()「」]", text) if c.strip()]


def check(tr, review, today, res):
    summary = review.get("summary") or ""
    bullets = review.get("bullets") or []
    lessons = review.get("lessons") or []
    score = review.get("score")
    full = summary + "\n" + "\n".join(bullets)
    names = [h["name"] for h in tr["holds"]]

    # C01 结构
    if not summary and not bullets:
        add(res, "C01", "FAIL", "summary 与 bullets 均为空（接口会拒）")
    if len(summary) > 300:
        add(res, "C01", "FAIL", "summary %d 字 > 300" % len(summary))
    if not (3 <= len(bullets) <= 6):
        add(res, "C01", "FAIL", "bullets %d 条，应为 3~6" % len(bullets))
    for i, b in enumerate(bullets, 1):
        if len(b) > 80:
            add(res, "C01", "FAIL", "bullet%d %d 字 > 80" % (i, len(b)))
    if len(lessons) > 3:
        add(res, "C01", "FAIL", "lessons %d 条 > 3" % len(lessons))
    for i, l in enumerate(lessons, 1):
        if len(l) > 120:
            add(res, "C01", "FAIL", "lesson%d %d 字 > 120" % (i, len(l)))
    if not isinstance(score, int) or not (-5 <= score <= 5):
        add(res, "C01", "FAIL", "score=%r 不是 -5~5 整数" % (score,))

    # C02 持仓只数
    for m in re.finditer(r"(?:仅持|持有|持仓|仅剩|共持)\s*(\d+)\s*只", full):
        n = int(m.group(1))
        if n != tr["n_holds"]:
            add(res, "C02", "FAIL", "『%s…』说 %d 只，实际 %d 只（shares>0）；若想说「其中N只」，请写成「%d只持仓中N只…」"
                % (full[max(0, m.start() - 2):m.start() + 12], n, tr["n_holds"], tr["n_holds"]))
    for m in re.finditer(r"(\d+)\s*只(?:持仓|个股|股票)", full):
        n = int(m.group(1))
        if n != tr["n_holds"]:
            add(res, "C02", "FAIL", "『%s』= %d 只，实际 %d 只" % (m.group(0), n, tr["n_holds"]))

    # C03 红绿 / 浮盈浮亏
    for m in re.finditer(r"(\d+)\s*只中\s*(\d+)\s*只(?:红盘|上涨|飘红)", full):
        n = int(m.group(2))
        if n != tr["n_pct_up"]:
            add(res, "C03", "FAIL", "『%s』说 %d 只红盘，按 pct 实际 %d 只" % (m.group(0), n, tr["n_pct_up"]))
    for pat, key, label, field in [
        (r"(\d+)\s*只(?:红盘|上涨|飘红)", "n_pct_up", "红盘", "pct"),
        (r"(\d+)\s*只(?:绿盘|下跌|收跌)", "n_pct_down", "绿盘", "pct"),
        (r"(\d+)\s*只(?:浮盈)", "n_pnl_up", "浮盈", "pnlPct"),
        (r"(\d+)\s*只(?:浮亏)", "n_pnl_down", "浮亏", "pnlPct"),
    ]:
        for m in re.finditer(pat, full):
            n = int(m.group(1))
            if n != tr[key]:
                add(res, "C03", "FAIL", "『%s』说 %d 只%s，按 %s 实际 %d 只" % (m.group(0), n, label, field, tr[key]))

    # C06 现金比例（排除「释放/腾出」类，那是卖出释放的仓位比例，不是现金占比）
    for pat in [r"现金(?:占比|仓位|比例)?[^0-9\n]{0,3}?(\d+(?:\.\d+)?)\s*%",
                r"(\d+(?:\.\d+)?)\s*%\s*(?:的)?现金",
                r"现金(?:约|为)?\s*(\d+(?:\.\d+)?)\s*%"]:
        for m in re.finditer(pat, full):
            window = full[max(0, m.start() - 14):m.start()]
            if any(k in window for k in ("释放", "腾出", "回笼", "腾挪")):
                continue
            v = float(m.group(1))
            if abs(v - tr["cash_pct"]) > 1.0:
                add(res, "C06", "FAIL", "『%s』= %.1f%%，实际现金占比 %.2f%%" % (m.group(0), v, tr["cash_pct"]))

    # C07 账户当日涨跌（排除个股/累计口径）
    if tr["day_pct"] is not None:
        accepts = [("当日涨跌", tr["day_pct"])]
        if tr["mkt"] is not None:
            accepts.append(("大盘", tr["mkt"]))
        if tr["excess"] is not None:
            accepts.append(("超额", tr["excess"]))
        tol = 0.15 if tr["computed"] else 0.1
        cands = []
        for pat in [r"(?:账户|当日|单日)[^0-9+\-−，。；\n]{0,10}?([+\-−]?\d+(?:\.\d+)?)\s*%",
                    r"([+\-−]\d+(?:\.\d+)?)\s*%[^，。；\n]{0,6}(?:跑赢|跑输|超额|领先|落后)"]:
            cands += [m.group(1) for m in re.finditer(pat, full)]
        for c in cands:
            v = float(c.replace("−", "-").lstrip("+"))
            if any(abs(v - x[1]) <= tol for x in accepts):
                continue
            add(res, "C07", "WARN" if tr["computed"] else "FAIL",
                "文本 %s%% 对不上：%s（复算值未含当日已实现盈亏，容差 %.2fpp）" % (
                    c, "、".join("%s %.2f%%" % (n, x) for n, x in accepts), tol))

    # C04 单股权重
    for c in clauses(full):
        pcts = [float(x) for x in re.findall(r"占\s*(\d+(?:\.\d+)?)\s*%", c)]
        if not pcts:
            continue
        hit = [h for h in tr["holds"] if h["name"] in c or h["code"] in c]
        if len(hit) == 1 and abs(pcts[0] - hit[0]["weight"]) > 1.0:
            add(res, "C04", "WARN", "『%s』称 %s 占 %.1f%%，实际 %.2f%%" % (c.strip()[:26], hit[0]["name"], pcts[0], hit[0]["weight"]))

    # C05 行业集中度
    tag_names = {k for k in tr["ind"] if k != "未分类"}
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,6})\s*(\d+(?:\.\d+)?)\s*%", full):
        name, v = m.group(1), float(m.group(2))
        if name in tag_names:
            actual = tr["ind"][name]
            if abs(v - actual) > 2.0:
                add(res, "C05", "WARN", "行业『%s %.1f%%』与 tags 聚合值 %.1f%% 差 %.1fpp" % (name, v, actual, abs(v - actual)))
        elif name in ("医疗", "科技", "地产", "银行", "煤炭", "电信", "金融"):
            if name not in tag_names:
                add(res, "C05", "WARN", "行业『%s』不在当日 tags 首标签中（疑似手工归类）；可用标签：%s"
                    % (name, "、".join(sorted(tag_names)[:8]) or "无"))

    # C10 禁用词
    for w in BANNED:
        if w in full:
            add(res, "C10", "FAIL", "出现未逐只核对的措辞『%s』" % w)

    # C08 pp 溯源
    cand = ([abs(tr["excess"])] if tr["excess"] is not None else []) + [h["weight"] for h in tr["holds"]]
    for t in tr["trades"]:
        for num in re.findall(r"(\d+(?:\.\d+)?)\s*%", str(t.get("reason", ""))):
            for th in (8, 15, 20, 25):
                cand.append(abs(float(num) - th))
        cand.append(abs(t.get("chgPctSinceTrade") or 0))
    for c in clauses(full):
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:pp|个百分点)", c):
            v = float(m.group(1))
            if any(abs(v - x) <= 0.25 for x in cand if x is not None):
                continue
            if any(k in c for k in PP_CONTEXT) or any(n in c for n in names):
                add(res, "C08", "INFO", "『%s』中的 %.2fpp 属止损线/两日差值类，脚本无法自动溯源，需人工确认" % (c.strip()[:30], v))
            else:
                add(res, "C08", "WARN", "『%s』中的 %.2fpp 无法对应任何计数值，疑似手算取整" % (c.strip()[:30], v))

    # C11 pnlPct 冒充当日
    for c in clauses(full):
        for h in tr["holds"]:
            if h["name"] not in c and h["code"] not in c:
                continue
            pcts = [float(x.replace("−", "-")) for x in re.findall(r"([+\-−]?\d+(?:\.\d+)?)\s*%", c)]
            if not pcts:
                continue
            pct = h.get("pct") or 0.0
            if any(w in c for w in HEDGE_WORDS) and any(p > 0 for p in pcts) and pct <= 0:
                add(res, "C11", "FAIL", "『%s』：%s 今日 pct=%.2f%%（跌），不能用累计浮盈说成抗跌/对冲" % (c.strip()[:30], h["name"], pct))
            if any(w in c for w in TODAY_WORDS):
                for p in pcts:
                    if (p > 0 and pct < 0) or (p < 0 and pct > 0):
                        add(res, "C11", "FAIL", "『%s』：文中 %+.2f%% 与 %s 今日 pct=%+.2f%% 符号相反（疑似把累计浮盈亏当当日涨跌）"
                            % (c.strip()[:30], p, h["name"], pct))
                        break

    # C13 净值缺失 / todayPnl 不实
    if tr["hist_last"] != today:
        if re.search(r"复算|推算|估算|结算|约", full):
            add(res, "C13", "WARN", "history 缺 %s 行（服务端结算问题），正文已按复算/约值表述，可接受" % today)
        else:
            add(res, "C13", "FAIL", "history 末行为 %s（缺 %s），且正文未说明为复算值" % (tr["hist_last"], today))
    if tr["today_pnl_raw"] is not None and abs(tr["today_pnl_raw"]) < 1e-9 and abs(tr["float_pnl"]) > tr["total"] * 0.001:
        add(res, "C13", "FAIL", "服务端 todayPnl=0 但持仓当日浮动 %.0f 元，净值行疑似被覆盖（参见审计日志 §11）" % tr["float_pnl"])

    # C09 信号/评审计数
    oknums = {tr["n_council"], tr["n_prop_buy"], tr["n_veto"], tr["n_watch"], tr["n_reviewed"], tr["n_reviewlog_today"]}
    for pat in [r"(\d+)\s*个(?:买入)?信号", r"(\d+)\s*条(?:评审|信号)", r"(\d+)\s*条全\s*HOLD"]:
        for m in re.finditer(pat, full):
            n = int(m.group(1))
            if n not in oknums:
                add(res, "C09", "WARN", "『%s』= %d，当日实际：评审 %d / 提案买入 %d / 否决 %d / 观望 %d / reviewedToday %d"
                    % (m.group(0), n, tr["n_council"], tr["n_prop_buy"], tr["n_veto"], tr["n_watch"], tr["n_reviewed"]))

    # C15 成交解读
    for t in tr["trades"]:
        d, chg = t.get("side") or t.get("direction"), t.get("chgPctSinceTrade")
        if chg is None:
            continue
        if d == "买入" and chg < -1 and not re.search(r"追高|买高|浮亏|回撤|高开", full):
            add(res, "C15", "WARN", "买入 %s 后 %.2f%%，正文未提『追高/浮亏』" % (t.get("name"), chg))
        if d == "卖出" and chg > 1 and not re.search(r"卖飞|反弹|少赚|卖早", full):
            add(res, "C15", "WARN", "卖出 %s 后 +%.2f%%，正文未提『卖飞』" % (t.get("name"), chg))

    # C16 连续第N日
    for m in re.finditer(r"连续第[二三四五六七八九十]+日", full):
        add(res, "C16", "WARN", "『%s』无个股逐日数据支撑，建议改为『较 X 日继续修复』" % m.group(0))

    # C12 lessons
    for i, l in enumerate(lessons, 1):
        if EXIST_MECH.search(l):
            add(res, "C12", "FAIL", "lesson%d 建议了系统中已存在的机制：%s" % (i, l[:36]))
        for theme, (first, kws) in CLUSTERS.items():
            if today <= first:
                continue
            if sum(1 for k in kws if k in l) >= 2:
                add(res, "C12", "WARN", "lesson%d 命中既有主题『%s』（首提 %s），确认是否为重复" % (i, theme, first))
                break

    # C14 待执行队列临近保质期
    now = datetime.datetime.now()
    for p in tr["pend"]:
        q = p.get("queuedAt") or p.get("time")
        try:
            qt = datetime.datetime.strptime(q, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        wait = (now - qt).total_seconds() / 3600
        if wait >= tr["shelf"] * 0.75 and p.get("name") not in full:
            add(res, "C14", "WARN", "%s 已排队 %.1fh（保质期 %.0fh）却未在正文提示" % (p.get("name"), wait, tr["shelf"]))


def drift(tr, live_state, date):
    """快照 vs 实时 漂移提示（不算错误）"""
    if not live_state:
        return None
    lh = [h for h in live_state.get("holdings", []) if h.get("shares", 0) > 0]
    lv = sum(h["shares"] * h["price"] for h in lh) + live_state.get("cash", 0)
    if tr["total"] and abs(lv - tr["total"]) / tr["total"] > 0.003:
        return "按实时价重估总资产 %.0f（快照 %.0f，漂移 %+.2f%%），美股盘中价格已在变动" % (
            lv, tr["total"], (lv - tr["total"]) / tr["total"] * 100)
    return None


def integrity(date):
    """服务端数据体检：只看账户/净值/结算，不看复盘正文。用于每天确认数据可信。"""
    out = []
    for acct, label, tag in ACCOUNTS:
        f, s = fetch_any("/api/daily_facts?account=" + acct), fetch_any("/api/state?account=" + acct)
        if not f or not s:
            out.append((label, "FAIL", "取数失败"))
            continue
        a = f["account"]
        holds = [h for h in s.get("holdings", []) if h.get("shares", 0) > 0]
        float_pnl = sum(h["shares"] * (h["price"] - h["preClose"]) for h in holds if h.get("preClose"))
        total = a["totalAsset"]
        rows = sorted(s.get("history") or [], key=lambda r: r["date"])
        last = rows[-1] if rows else None
        prev = [r for r in rows if r["date"] < date]
        prev_total = prev[-1]["total"] if prev else None
        settles = sorted([e for e in s.get("log", []) if "自动结算" in e.get("msg", "")], key=lambda e: e.get("time", ""))
        last_settle = settles[-1] if settles else None
        issues = []
        if not last or last["date"] != date:
            issues.append(("FAIL", "history 末行 %s，缺当日行" % (last["date"] if last else "无")))
        if last_settle and date not in last_settle.get("msg", ""):
            issues.append(("FAIL", "末条结算日志为『%s』，日期与当日不符" % last_settle.get("msg", "")[:40]))
        if a.get("todayPnl") is not None and abs(a["todayPnl"]) < 1e-9 and abs(float_pnl) > total * 0.001:
            issues.append(("FAIL", "todayPnl=0 但持仓当日浮动 %.0f 元 → 净值行被覆盖" % float_pnl))
        if prev_total and abs(prev_total - total) < 1e-6:
            issues.append(("WARN", "前一行 total 与当日完全相等（疑似重复归档）"))
        if a.get("todayPnl") is not None and prev_total and not acct.startswith("us") and abs(a["todayPnl"] - float_pnl) > max(total * 0.002, 50):
            issues.append(("WARN", "todayPnl=%.0f 与盘面浮动 %.0f 差 %.0f 元（可能含已实现/结算错位）" % (a["todayPnl"], float_pnl, a["todayPnl"] - float_pnl)))
        out.append((label, "OK" if not issues else issues[0][0], "; ".join("%s:%s" % (lv, msg) for lv, msg in issues) or "账户/净值/结算一致"))
    if not any(True for _ in out):
        return 1
    print("== 服务端数据体检 %s ==" % date)
    for label, lv, msg in out:
        print("  [%s] %s  %s" % (lv, label, msg))
    return 0 if all(lv != "FAIL" for _, lv, _ in out) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--integrity", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    today = args.date

    if args.save:
        return save_snapshot(today)
    if args.integrity:
        return integrity(today)

    mode = "live" if args.live else ("offline" if args.offline else "auto")
    report = []
    for acct, label, tag in ACCOUNTS:
        facts, tstate, lstate, note = load_sources(acct, tag, today, mode)
        if not facts:
            report.append({"account": acct, "label": label, "error": "取数失败", "n_fail": 1, "results": []})
            continue
        tr = build_truth(acct, facts, tstate, today)
        rv = [x for x in (lstate or tstate).get("dailyReviews", []) if x.get("date") == today]
        res = []
        if not rv:
            add(res, "C00", "FAIL", "当日复盘未写入（%s 无记录）" % today)
        else:
            check(tr, rv[0], today, res)
        dr = drift(tr, lstate, today)
        if dr:
            add(res, "C17", "INFO", dr)
        report.append({
            "account": acct, "label": label, "src": note,
            "truth": {
                "total": round(tr["total"], 2), "cash_pct": round(tr["cash_pct"], 2),
                "n_holds": tr["n_holds"], "n_pct_up": tr["n_pct_up"], "n_pct_down": tr["n_pct_down"],
                "n_pnl_up": tr["n_pnl_up"], "n_pnl_down": tr["n_pnl_down"],
                "day_pct": None if tr["day_pct"] is None else round(tr["day_pct"], 3),
                "excess": None if tr["excess"] is None else round(tr["excess"], 3),
                "hist_last": tr["hist_last"], "n_council": tr["n_council"], "n_veto": tr["n_veto"],
                "n_reviewed": tr["n_reviewed"],
                "ind": {k: round(v, 1) for k, v in sorted(tr["ind"].items(), key=lambda x: -x[1])},
            },
            "results": res,
            "n_fail": sum(1 for r in res if r["level"] == "FAIL"),
            "n_warn": sum(1 for r in res if r["level"] == "WARN"),
        })

    total_fail = sum(r.get("n_fail", 1) for r in report)
    if args.json:
        print(json.dumps({"date": today, "total_fail": total_fail, "accounts": report}, ensure_ascii=False, indent=1))
        return 0 if total_fail == 0 else 1

    if not args.quiet:
        print("自查日期 %s   FAIL=%d" % (today, total_fail))
        for r in report:
            if "error" in r:
                print("\n== %s：%s" % (r["label"], r["error"]))
                continue
            print("\n== %s（%s） FAIL=%d WARN=%d  数据源：%s" % (r["label"], r["account"], r["n_fail"], r["n_warn"], r["src"]))
            t = r["truth"]
            print("   真值 持仓%d只(涨%d跌%d) 浮盈%d浮亏%d 现金%.2f%% 当日%.2f%% 超额%.2fpp 评审%d条/否决%d hist末行%s"
                  % (t["n_holds"], t["n_pct_up"], t["n_pct_down"], t["n_pnl_up"], t["n_pnl_down"],
                     t["cash_pct"], t["day_pct"] if t["day_pct"] is not None else 0,
                     t["excess"] if t["excess"] is not None else 0,
                     t["n_council"], t["n_veto"], t["hist_last"]))
            for x in r["results"]:
                print("   [%s] %s %s" % (x["level"], x["code"], x["msg"]))

    os.makedirs(LOGDIR, exist_ok=True)
    path = os.path.join(LOGDIR, "%s-自查报告.md" % today)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# %s 复盘自查报告（机器生成 %s）\n\n" % (today, datetime.datetime.now().strftime("%H:%M")))
        f.write("口径：`pct`=当日涨跌，`pnlPct`=累计浮盈亏；持仓只数已过滤 `shares>0`；数字按**写复盘时的快照**核对。\n\n")
        f.write("**合计 FAIL = %d**\n\n" % total_fail)
        for r in report:
            f.write("## %s（%s）\n\n" % (r["label"], r["account"]))
            if "error" in r:
                f.write("- 取数失败：%s\n\n" % r["error"])
                continue
            t = r["truth"]
            f.write("数据源：%s\n\n| 真值项 | 值 |\n|---|---|\n" % r["src"])
            f.write("| 总资产 | %.2f |\n| 现金占比 | %.2f%% |\n| 持仓只数 | %d |\n" % (t["total"], t["cash_pct"], t["n_holds"]))
            f.write("| 当日涨/跌只数（pct） | %d / %d |\n| 浮盈/浮亏只数（pnlPct） | %d / %d |\n" % (t["n_pct_up"], t["n_pct_down"], t["n_pnl_up"], t["n_pnl_down"]))
            f.write("| 当日涨跌 | %s |\n| 超额 | %s |\n" % (
                "%.2f%%" % t["day_pct"] if t["day_pct"] is not None else "—",
                "%.2fpp" % t["excess"] if t["excess"] is not None else "—"))
            f.write("| 评审/否决/reviewed | %d / %d / %d |\n| history 末行 | %s |\n" % (t["n_council"], t["n_veto"], t["n_reviewed"], t["hist_last"]))
            f.write("| 行业集中度（tags 首标签） | %s |\n\n" % "、".join("%s %.1f%%" % (k, v) for k, v in t["ind"].items()))
            if not r["results"]:
                f.write("**全部检查通过。**\n\n")
            else:
                f.write("| 级别 | 检查项 | 问题 |\n|---|---|---|\n")
                for x in r["results"]:
                    f.write("| %s | %s | %s |\n" % (x["level"], x["code"], x["msg"].replace("|", "\\|")))
                f.write("\n")
        f.write("---\n检查项定义见 `.workbuddy/memory/self-audit-playbook.md`。\n")
    if not args.quiet:
        print("\n报告已写入 %s" % path)
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
