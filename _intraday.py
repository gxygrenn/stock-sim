#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""盘中引擎一次到位：refresh + scan + 待审清单 压缩输出，或批量写回评审结论。

为什么要有它：
  原先每轮要对 3 个账户分别 refresh → scan → GET state，9 次工具调用，
  而 state 原始 JSON 有 330KB，稍微打印多一点就吃掉几万 token。
  这里把整轮压成 1 次调用 + 约 3KB 的摘要输出。

用法:
  python _intraday.py --cycle                      # 三账户全跑
  python _intraday.py --cycle --accounts us        # 只跑美股
  python _intraday.py --cycle --accounts cn_conservative,cn_aggressive
  python _intraday.py --review _reviews.json       # 批量写回评审结论
  python _intraday.py --cycle --dry-run            # 只看不写（不调 refresh/scan）

_reviews.json 格式:
  [{"account":"us","code":"usPLTR","verdict":"HOLD","confidence":60,"note":"..."},
   {"account":"cn_aggressive","code":"sz000002","verdict":"SELL","confidence":72,"note":"..."}]
"""
import argparse
import datetime
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

BASE = "https://stock-sim-us.app.workbuddy.host"
SHARECODE = "6R7GBTHXQI"  # 2026-11-12 平台改版：所有请求必须带 sharecode，否则网关 403


def _url(path):
    return BASE + path + ("&" if "?" in path else "?") + "sharecode=" + SHARECODE


ALL = ["us", "cn_conservative", "cn_aggressive"]
LABEL = {"us": "美股", "cn_conservative": "A股稳健", "cn_aggressive": "A股激进"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def call(path, payload=None, timeout=120):
    """返回 (http_status, parsed_json_or_text)"""
    data = None
    hdr = {"User-Agent": "intraday/2.0"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdr["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(_url(path), data=data, headers=hdr,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            body = r.read().decode("utf-8", "ignore")
            try:
                return r.status, json.loads(body)
            except Exception:
                return r.status, body[:200]
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode("utf-8", "ignore")[:160] or "")
    except Exception as e:
        return 0, str(e)[:160]


def retry(path, payload=None, tries=2, timeout=120):
    last = (0, "")
    for i in range(tries):
        st, body = call(path, payload, timeout)
        if st == 200:
            return st, body
        last = (st, body)
        if st in (502, 503, 504, 524):
            time.sleep(5)
            continue
        break
    return last


def hours_since(ts):
    if not ts:
        return -1.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return (datetime.datetime.now() - datetime.datetime.strptime(ts, fmt)).total_seconds() / 3600
        except Exception:
            continue
    return -1.0


def cycle(accounts, dry):
    print("== 盘中引擎 %s（%s）" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "只看不写" if dry else "执行"))
    for acc in accounts:
        head = []
        if not dry:
            st1, _ = retry("/api/refresh?account=%s" % acc, {})
            st2, b2 = retry("/api/scan?account=%s" % acc, {}, tries=2, timeout=150)
            head.append("refresh %s｜scan %s" % (st1, st2 if st2 == 200 else "%s(%s)" % (st2, str(b2)[:60])))
        st3, s = call("/api/state?account=%s" % acc)
        if st3 != 200 or not isinstance(s, dict):
            print("[%s] state 取数失败 %s %s" % (acc, st3, str(s)[:80]))
            continue
        hold = [h for h in (s.get("holdings") or []) if (h.get("shares") or 0) > 0]
        total = s.get("cash", 0) + sum((h.get("price") or 0) * (h.get("shares") or 0) for h in hold)
        cash_pct = (s.get("cash", 0) / total * 100) if total else 0
        reg = s.get("regime") or {}
        head.append("lastScan %s｜scanErr %s｜持仓 %d｜现金 %.1f%%｜regime %s(%s)" % (
            s.get("lastScan") or "-", s.get("scanErr") or "空", len(hold), cash_pct,
            reg.get("tier") or "-", reg.get("score")))
        print("\n[%s] %s" % (acc, "｜".join(head)))

        pend = s.get("pendingReview") or []
        rev = s.get("reviewed") or {}
        waiting = [p for p in pend if (p.get("state") or "waiting") == "waiting"]
        appr = [p for p in pend if p.get("state") == "approved"]
        print("  waiting %d%s" % (len(waiting), "：" if waiting else ""))
        for p in waiting:
            c = p.get("council") or {}
            last = rev.get((p.get("code") or "").lower())
            age = hours_since(last) if last else -1
            cooldown = ("上次审 %.1fh前→冷却中" % age) if 0 <= age < 1 else \
                       (("上次审 %.1fh前" % age) if age >= 1 else "从未评审")
            print("   %-10s %-8s %s｜delta %s｜等 %sh｜初审 %s%s｜%s" % (
                p.get("code"), (p.get("name") or "")[:8], p.get("direction"),
                ("%.1f" % (p.get("delta") or 0)),
                ("%.1f" % hours_since(p.get("queuedAt") or p.get("time"))),
                c.get("verdict") or "无",
                ("（%s）" % c.get("veto")) if c.get("veto") else "", cooldown))
        if appr:
            print("  approved 待成交 %d：%s" % (len(appr), "、".join(
                "%s(等%.1fh)" % (p.get("name"), hours_since(p.get("reviewedAt") or p.get("time")))
                for p in appr)))
        # 当日成交（R17 依据：同日止盈卖出后回买须过价差/量比门槛）
        _td = datetime.date.today().strftime("%Y-%m-%d")
        _todays = [t for t in (s.get("trades") or []) if (t.get("time") or "").startswith(_td)]
        if _todays:
            print("  当日成交 %d：%s" % (len(_todays), "、".join(
                "%s%s %s股@%s" % (t.get("side"), (t.get("name") or "")[:6], t.get("qty"), t.get("price"))
                for t in _todays[:12])))
    print("\n提示：评审结论写进 _reviews.json 后用 --review 一次批量写回，别逐条 curl。")


def review(path):
    items = json.load(open(path, encoding="utf-8"))
    if isinstance(items, dict):
        items = [items]
    ok = bad = 0
    for it in items:
        acc = it.get("account")
        if acc not in ALL:
            print("  跳过：account=%s 不是全名" % acc)
            bad += 1
            continue
        body = {k: it.get(k) for k in ("code", "verdict", "confidence", "note") if it.get(k) is not None}
        st, resp = call("/api/review_done?account=%s" % acc, body, timeout=45)
        good = st == 200 and isinstance(resp, dict) and resp.get("ok")
        print("  %-16s %-10s %-6s → HTTP %s %s" % (
            acc, body.get("code"), body.get("verdict"), st, "ok" if good else str(resp)[:80]))
        ok += 1 if good else 0
        bad += 0 if good else 1
    print("写回完成：%d 成功 / %d 失败" % (ok, bad))
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", action="store_true")
    ap.add_argument("--accounts", default=",".join(ALL))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--review", default=None)
    args = ap.parse_args()
    if args.review:
        return review(args.review)
    if args.cycle:
        accs = [a.strip() for a in args.accounts.split(",") if a.strip() in ALL]
        if not accs:
            print("账户名必须是 us / cn_conservative / cn_aggressive")
            return 1
        cycle(accs, args.dry_run)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
