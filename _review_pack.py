#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""复盘事实包生成器 —— 把三账户的 /api/daily_facts + /api/state 压成一页纸。

为什么要有它：
  收盘复盘原来对每个账户分别 GET daily_facts(~86KB) + state(~330KB)，
  三个账户就是 ~1.2MB 原始 JSON 灌进上下文，单次运行烧 3.5M tokens。
  真正的复盘只需要其中几十个字段，所以先在本机压成 ~6KB 的事实包。

用法:
  python _review_pack.py                 # 生成今日事实包 -> logs/<date>-复盘事实包.md
  python _review_pack.py --date 2026-10-25
  python _review_pack.py --account us    # 只生成一个账户
  python _review_pack.py --print         # 同时打印到标准输出

产物:
  logs/<date>-复盘事实包.md          压缩后的事实包（读这个，别读 raw）
  snap/<date>/raw_<account>.json     原始 JSON 备份（需要查细节时才读）
"""
import argparse
import datetime
import json
import os
import ssl
import sys
import urllib.request

BASE = "https://stock-sim-us.app.workbuddy.host"
ACCOUNTS = [("us", "美股"), ("cn_conservative", "A股稳健"), ("cn_aggressive", "A股激进")]
ROOT = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.path.join(ROOT, "logs")
SNAPDIR = os.path.join(ROOT, "snap")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "review-pack/1.0"})
    with urllib.request.urlopen(req, timeout=45, context=CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def num(x, nd=2):
    if x is None:
        return "-"
    try:
        return ("%." + str(nd) + "f") % float(x)
    except Exception:
        return str(x)


def pct(x, nd=2):
    if x is None:
        return "-"
    return ("%." + str(nd) + "f%%") % float(x)


def hours_since(ts):
    if not ts:
        return -1.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            t = datetime.datetime.strptime(ts, fmt)
            return (datetime.datetime.now() - t).total_seconds() / 3600.0
        except Exception:
            continue
    return -1.0


def build(acc, label, day):
    facts = get("/api/daily_facts?account=%s" % acc)
    state = get("/api/state?account=%s" % acc)
    os.makedirs(os.path.join(SNAPDIR, day), exist_ok=True)
    with open(os.path.join(SNAPDIR, day, "raw_%s.json" % acc), "w", encoding="utf-8") as f:
        json.dump({"facts": facts, "state": state}, f, ensure_ascii=False)
    # 同时写自查器认的格式，省掉一次 _self_check.py --save（写前快照，口径与 --save 一致）
    with open(os.path.join(SNAPDIR, day, "facts_%s.json" % acc), "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False)
    with open(os.path.join(SNAPDIR, day, "state_%s.json" % acc), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

    a = facts.get("account") or {}
    total = a.get("totalAsset") or 0
    cash = a.get("cash") or 0
    cash_pct = (cash / total * 100) if total else 0

    # 持仓：以 state 为准（带 pct/preClose），过滤 shares>0；pnlPct 补自 facts
    hold = [h for h in (state.get("holdings") or []) if (h.get("shares") or 0) > 0]
    fp = {h.get("code"): h.get("pnlPct") for h in (facts.get("holdings") or [])}
    for h in hold:
        if h.get("pnlPct") is None:
            h["pnlPct"] = fp.get(h.get("code"))
    tags = {p["code"]: (p.get("tags") or "").split(",")[0].strip() for p in (state.get("pool") or [])}

    lines = []
    lines.append("## %s（%s）" % (acc, label))
    # 当日成交
    trades = facts.get("trades") or []
    today_pnl = a.get("todayPnl")
    if today_pnl is None:
        if trades:
            hist = state.get("history") or []
            prev = hist[-1].get("total") if hist else None
            today_pnl = (total - prev) if prev else None
            pnl_src = "复算=总资产−上一结算日总值"
        else:
            today_pnl = sum((h.get("price") or 0) * (h.get("shares") or 0)
                            - (h.get("preClose") or h.get("price") or 0) * (h.get("shares") or 0)
                            for h in hold)
            pnl_src = "复算=Σ股数×(现价−昨收)"
    else:
        pnl_src = "服务端 todayPnl"
    lines.append("- 总资产 %s｜现金 %s（%s）｜累计 %s｜已实现 %s｜今日盈亏 %s（%s）" % (
        num(total), num(cash), pct(cash_pct, 1), pct(a.get("retTotalPct")),
        num(a.get("realized")), ("约 " + num(today_pnl)) if today_pnl is not None else "-", pnl_src))
    mk = facts.get("market") or {}
    lines.append("- 大盘：%s %s（%s）" % (mk.get("name") or "-", num(mk.get("price")), pct(mk.get("pct"))))
    mc = facts.get("marketContext") or {}
    if mc.get("intl") or mc.get("sentiment"):
        it, se = mc.get("intl") or {}, mc.get("sentiment") or {}
        lines.append("- 盘前播报（%s）：国际 %s %s｜情绪 %s(%s) %s" % (
            mc.get("date") or "-", it.get("impact") or "-", (it.get("summary") or "-")[:60],
            se.get("label") or "-", num(se.get("score")), (se.get("summary") or "")[:50]))

    # 持仓明细
    if hold:
        lines.append("- 持仓 %d 只（shares>0）：" % len(hold))
        lines.append("  | 名称 | 代码 | 股数 | 成本 | 现价 | 当日 | 累计 | 权重 |")
        lines.append("  |---|---|---|---|---|---|---|---|")
        wt = {}
        for h in sorted(hold, key=lambda x: -((x.get("price") or 0) * (x.get("shares") or 0))):
            v = (h.get("price") or 0) * (h.get("shares") or 0)
            w = (v / total * 100) if total else 0
            tg = tags.get(h["code"]) or "未分类"
            wt[tg] = wt.get(tg, 0) + w
            lines.append("  | %s | %s | %d | %s | %s | %s | %s | %s |" % (
                h.get("name"), h["code"], h.get("shares") or 0, num(h.get("cost"), 3),
                num(h.get("price")), pct(h.get("pct")), pct(h.get("pnlPct")), pct(w, 1)))
        agg = sorted(wt.items(), key=lambda x: -x[1])
        lines.append("- 行业集中度（按池标签首项）：%s" % "｜".join("%s %s" % (k, pct(v, 0)) for k, v in agg))
    else:
        lines.append("- 持仓 0 只（空仓）")

    # 今日成交
    if trades:
        lines.append("- 今日成交 %d 笔：" % len(trades))
        for t in trades:
            lines.append("  - %s %s %s %s 股 @%s，金额 %s，成交后涨跌 %s（%s）" % (
                (t.get("time") or "")[11:16], t.get("side"), t.get("name"), t.get("qty") or t.get("shares"),
                num(t.get("price"), 3), num(t.get("amount"), 0), pct(t.get("chgPctSinceTrade")),
                (t.get("reason") or "")[:40]))
    else:
        lines.append("- 今日成交 0 笔")

    # 待评审 / 已批准
    pend = state.get("pendingReview") or []
    waiting = [p for p in pend if (p.get("state") or "waiting") == "waiting"]
    approved = [p for p in pend if p.get("state") == "approved"]
    if waiting:
        lines.append("- 待评审 %d 条：" % len(waiting))
        for p in waiting:
            c = p.get("council") or {}
            lines.append("  - %s %s｜%s｜delta %s｜已等 %sh｜初审 %s%s" % (
                p.get("name"), p.get("code"), p.get("direction"), num(p.get("delta"), 1),
                num(hours_since(p.get("queuedAt") or p.get("time")), 1),
                c.get("verdict") or "无", ("（" + c.get("veto") + "）") if c.get("veto") else ""))
    else:
        lines.append("- 待评审 0 条")
    if approved:
        lines.append("- 已批准待成交 %d 条（保质期 24h，剩余不足会作废重评）：" % len(approved))
        for p in approved:
            age = hours_since(p.get("reviewedAt") or p.get("time"))
            lines.append("  - %s %s｜%s｜已等 %sh，剩 %sh" % (
                p.get("name"), p.get("code"), p.get("direction"), num(age, 1), num(max(0, 24 - age), 1)))

    # 信号 Top
    sig = state.get("signals") or {}
    nm = {p["code"]: p.get("name") for p in (state.get("pool") or [])}
    top = sorted([(k, v) for k, v in sig.items() if isinstance(v, dict)],
                 key=lambda kv: -(kv[1].get("delta") or 0))[:5]
    if top:
        lines.append("- 信号 Top5（delta）：%s" % "｜".join(
            "%s %s%s" % (nm.get(k, k), num(v.get("delta"), 1), "（估值过滤）" if v.get("block") else "")
            for k, v in top))

    # 今日评审统计（判断放行/拦截是否同一把尺子）
    rt = facts.get("reviewedToday") or []
    if rt:
        cnt = {}
        for r in rt:
            cnt[r.get("verdict") or "?"] = cnt.get(r.get("verdict") or "?", 0) + 1
        lines.append("- 今日已评审 %d 条：%s" % (
            len(rt), "｜".join("%s %d" % (k, v) for k, v in cnt.items())))
    else:
        lines.append("- 今日已评审 0 条")

    # 最近评审
    rl = [r for r in (state.get("reviewLog") or []) if r.get("reviewedAt")][-3:]
    if rl:
        lines.append("- 最近 3 条评审：")
        for r in rl:
            lines.append("  - %s %s｜%s｜信心%s｜%s｜%s" % (
                r.get("reviewedAt"), r.get("name"), r.get("verdict"), r.get("confidence"),
                (r.get("note") or "")[:50], r.get("outcome") or ""))

    # 净值
    hist = (state.get("history") or [])[-8:]
    if hist:
        lines.append("- 净值近 %d 日：%s" % (len(hist), "；".join(
            "%s %s(日%s/累%s)" % (h.get("date", "")[5:], num(h.get("total"), 0),
                                  num(h.get("dayPnl"), 0), pct(h.get("ret")))
            for h in hist)))

    # 系统状态
    reg = state.get("regime") or {}
    notes = []
    if facts.get("scanErr"):
        notes.append("scanErr=%s" % facts["scanErr"])
    if hist and hist[-1].get("date") != day:
        notes.append("history 末行为 %s，与今日 %s 不符（结算错位，当日盈亏须复算）" % (hist[-1].get("date"), day))
    lines.append("- 系统：lastScan %s｜regime %s(%s)｜冷却表 %d 条｜%s" % (
        facts.get("lastScan") or "-", reg.get("tier") or "-", num(reg.get("score")), len(state.get("reviewed") or {}),
        "；".join(notes) if notes else "无异常"))

    # 被否归因候选（48h 内 HOLD/否决，回算被否后涨跌 ≥ +8%）
    pq = state.get("poolQuote") or {}
    hp = {h["code"]: h.get("price") for h in hold}
    killed = []
    for r in (state.get("reviewLog") or []):
        if (r.get("verdict") or "") not in ("HOLD", "SELL"):
            continue
        if r.get("verdict") == "SELL":
            continue
        p0 = r.get("priceAtReview")
        if not p0:
            continue
        age = hours_since(r.get("reviewedAt"))
        if age < 0 or age > 48:
            continue
        cur = (pq.get(r.get("code")) or {}).get("price") or hp.get(r.get("code"))
        if not cur or not p0:
            continue
        chg = (cur - p0) / p0 * 100
        if chg >= 8:
            killed.append((chg, r, cur))
    if killed:
        killed.sort(key=lambda x: -x[0])
        lines.append("- 被否后涨超 8%%（归因候选 %d 条）：" % len(killed))
        for chg, r, cur in killed:
            lines.append("  - %s｜%s｜评审 %s @%s → 现 %s｜+%s｜理由：%s" % (
                r.get("name"), r.get("reviewedAt"), r.get("verdict"), num(r.get("priceAtReview"), 2),
                num(cur, 2), num(chg, 1), (r.get("note") or "")[:60]))
    else:
        lines.append("- 被否后涨超 8%：无")

    return "\n".join(lines)


def push_reviews(path):
    """批量写回三账户复盘：省掉 3 次 POST。"""
    items = json.load(open(path, encoding="utf-8"))
    if isinstance(items, dict):
        items = [items]
    bad = 0
    for it in items:
        acc = it.pop("account", None)
        if acc not in [a for a, _ in ACCOUNTS]:
            print("  跳过：account=%s 不是全名（us/cn_conservative/cn_aggressive）" % acc)
            bad += 1
            continue
        if not (it.get("summary") or it.get("bullets")):
            print("  %-16s 跳过：summary 与 bullets 都为空（接口会 400）" % acc)
            bad += 1
            continue
        req = urllib.request.Request(
            "%s/api/daily_review?account=%s" % (BASE, acc),
            data=json.dumps(it, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
                body = r.read().decode("utf-8", "ignore")
            ok = '"ok": true' in body.replace('"ok":true', '"ok": true')
            print("  %-16s HTTP %s %s" % (acc, r.status, "ok" if ok else body[:100]))
            bad += 0 if ok else 1
        except Exception as e:
            print("  %-16s 失败：%s" % (acc, str(e)[:120]))
            bad += 1
    print("复盘写回完成：%d 成功 / %d 失败" % (len(items) - bad, bad))
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--account", default=None, help="只跑单个账户（us/cn_conservative/cn_aggressive）")
    ap.add_argument("--print", action="store_true", help="同时打印到标准输出")
    ap.add_argument("--push", default=None, help="批量写回复盘：--push _reviews.json")
    args = ap.parse_args()

    if args.push:
        return push_reviews(args.push)

    os.makedirs(LOGDIR, exist_ok=True)
    blocks = []
    hdr = ["# 复盘事实包 %s（生成于 %s）" % (args.date, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
           "> 直接读本文件，不要去读 API 原始 JSON（snap/%s/raw_*.json 只在需要查细节时读）。" % args.date,
           "> 当日盈亏若标注「约/复算」，正文就要写「约」；不要把它当服务端精确值。"]

    for acc, label in ACCOUNTS:
        if args.account and acc != args.account:
            continue
        try:
            blocks.append(build(acc, label, args.date))
        except Exception as e:
            blocks.append("## %s（%s）\n- 取数失败：%s" % (acc, label, e))

    text = "\n\n".join(hdr + [""] + blocks) + "\n"
    out = os.path.join(LOGDIR, "%s-复盘事实包.md" % args.date)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    if args.print:
        print(text)
    print("事实包已生成：%s（%d 字符，约 %.1f KB）" % (out, len(text), len(text.encode("utf-8")) / 1024))


if __name__ == "__main__":
    main()
