#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""盘前准备一次到位：推播报（三账户）+ 列调池待审 + 批量回写调池结论。

用法:
  python _premarket.py --push _ctx.json         # 把市场环境播报一次推给三个账户
  python _premarket.py --push _ctx.json --dry-run
  python _premarket.py --pool                   # 列 A股两账户的调池待审条目（压缩输出）
  python _premarket.py --pool-done _pool.json   # 批量回写调池结论

_pool.json 格式:
  [{"account":"cn_conservative","code":"sz301149","action":"approve","note":"..."},
   {"account":"cn_aggressive","code":"sh600000","action":"reject","note":"..."}]
"""
import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request

BASE = "https://stock-sim-us.app.workbuddy.host"
SHARECODE = "6R7GBTHXQI"  # 2026-11-12 平台改版：所有请求必须带 sharecode，否则网关 403


def _url(path):
    return BASE + path + ("&" if "?" in path else "?") + "sharecode=" + SHARECODE


ALL = ["us", "cn_conservative", "cn_aggressive"]
CN = ["cn_conservative", "cn_aggressive"]
LABEL = {"us": "美股", "cn_conservative": "A股稳健", "cn_aggressive": "A股激进"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def call(path, payload=None, timeout=60):
    data = None
    hdr = {"User-Agent": "premarket/2.0"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdr["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(_url(path), data=data, headers=hdr,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")[:160]
    except Exception as e:
        return 0, str(e)[:160]


def push(path, dry):
    payload = json.load(open(path, encoding="utf-8"))
    errs = []
    for k in ("intl", "sentiment"):
        blk = payload.get(k) or {}
        if not (blk.get("summary") or blk.get("items")):
            errs.append("%s 缺 summary 且缺 items（接口会 400）" % k)
    if not payload.get("date"):
        errs.append("缺 date")
    if errs:
        print("结构校验失败：" + "；".join(errs))
        return 1
    it, se = payload.get("intl") or {}, payload.get("sentiment") or {}
    print("结构 OK：date=%s｜intl %s(%s) %d 条｜sentiment %s(%s) %d 条" % (
        payload.get("date"), it.get("impact"), it.get("strength"), len(it.get("items") or []),
        se.get("label"), se.get("score"), len(se.get("items") or [])))
    if dry:
        print("dry-run，未发送")
        return 0
    fail = 0
    for acc in ALL:
        st, body = call("/api/market_context?account=%s" % acc, payload)
        ok = st == 200 and '"ok": true' in body.replace('"ok":true', '"ok": true')
        print("  %-16s HTTP %s %s" % (acc, st, "ok" if ok else body[:100]))
        fail += 0 if ok else 1
    print("推送完成：%d/%d 成功" % (len(ALL) - fail, len(ALL)))
    return 0 if fail == 0 else 1


def pool():
    total = 0
    for acc in CN:
        st, body = call("/api/state?account=%s" % acc)
        if st != 200:
            print("[%s] state 取数失败 %s" % (acc, st))
            continue
        s = json.loads(body)
        waiting = [c for c in (s.get("poolChanges") or []) if (c.get("state") or "waiting") == "waiting"]
        print("\n[%s] 调池待审 %d 条" % (acc, len(waiting)))
        for c in waiting:
            m = c.get("meta") or {}
            print("   %-10s %-8s %-6s %s｜score %s｜行业 %s" % (
                c.get("code"), (c.get("name") or "")[:8], c.get("direction"),
                (c.get("reason") or "")[:60], c.get("score"), m.get("industry") or "-"))
        total += len(waiting)
    if total == 0:
        print("\n两账户均无 waiting 条目，按规则跳过（调池建议每周才生成一轮）。")
    else:
        print("\n共 %d 条。结论写进 _pool.json 后用 --pool-done 批量回写。" % total)
    return 0


def pool_done(path):
    items = json.load(open(path, encoding="utf-8"))
    if isinstance(items, dict):
        items = [items]
    ok = bad = 0
    for it in items:
        acc = it.get("account")
        if acc not in CN:
            print("  跳过：account=%s 不是 A股全名" % acc)
            bad += 1
            continue
        body = {"code": it.get("code"), "action": it.get("action"), "note": it.get("note") or ""}
        st, resp = call("/api/pool_change_done?account=%s" % acc, body, timeout=30)
        good = st == 200 and '"ok": true' in resp.replace('"ok":true', '"ok": true')
        print("  %-16s %-10s %-8s → HTTP %s %s" % (acc, body["code"], body["action"], st,
                                                    "ok" if good else resp[:80]))
        ok += 1 if good else 0
        bad += 0 if good else 1
    print("调池回写完成：%d 成功 / %d 失败" % (ok, bad))
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pool", action="store_true")
    ap.add_argument("--pool-done", default=None)
    args = ap.parse_args()
    if args.push:
        return push(args.push, args.dry_run)
    if args.pool:
        return pool()
    if args.pool_done:
        return pool_done(args.pool_done)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
