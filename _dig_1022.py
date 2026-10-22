#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""深挖：log 时间线、council 原始字段、reviewLog、结算日志"""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

def load(tag):
    f = json.load(open("_facts_%s_1022.json" % tag, encoding="utf-8"))
    s = json.load(open("_state_%s_1022.json" % tag, encoding="utf-8"))
    return f, s

for tag, label in [("us","美股"), ("cnc","A股稳健"), ("cna","A股激进")]:
    f, s = load(tag)
    print("=" * 78)
    print("## %s — facts 顶层键: %s" % (label, list(f.keys())))
    log = s.get("log") or []
    print("-- log 共 %d 条；10-21/10-22 相关 %d 条 --" % (len(log), sum(1 for e in log if "2026-10-21" in str(e.get("time","")) or "2026-10-22" in str(e.get("time","")))))
    for e in log:
        t = str(e.get("time", ""))
        if t.startswith("2026-10-21") or t.startswith("2026-10-22"):
            print("  %s %s" % (t, (e.get("msg") or "")[:110]))
    settles = [e for e in log if "自动结算" in e.get("msg", "")]
    print("-- 自动结算条目 %d 条 --" % len(settles))
    for e in settles[-6:]:
        print("  %s %s" % (e.get("time"), (e.get("msg") or "")[:80]))
    # 万科 / 电信 / MRNA 专项
    for kw in (["万科"], ["中国电信"], ["莫德纳"], ["富春染织", "万丰"]):
        hits = [e for e in log if any(k in (e.get("msg") or "") for k in kw)]
        print("-- 关键词 %s 命中 %d 条，末6条 --" % (kw, len(hits)))
        for e in hits[-6:]:
            print("  %s %s" % (e.get("time"), (e.get("msg") or "")[:110]))
    # council 原始
    print("-- councilToday 原始 --")
    for x in (f.get("councilToday") or []):
        print("  ", json.dumps(x, ensure_ascii=False)[:260])
    # reviewLog 当日
    rl = [x for x in (f.get("reviewLog") or []) if str(x.get("reviewedAt", ""))[:10] == "2026-10-22"]
    print("-- reviewLog 当日 %d 条，前12条 --" % len(rl))
    for x in rl[:12]:
        print("  ", json.dumps(x, ensure_ascii=False)[:220])
    # account 原始
    print("-- account 字段 --")
    print("  ", json.dumps(f.get("account", {}), ensure_ascii=False)[:400])
print("DONE")
