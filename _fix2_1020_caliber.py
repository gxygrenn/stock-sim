# -*- coding: utf-8 -*-
"""按 /api/state 的 pct(当日涨跌) 与 pool[].tags(行业) 修正 10-20 三份复盘的口径。"""
import json, subprocess

HOST = "https://stock-sim-us.app.workbuddy.host"

# (账户, [(查找, 替换)...], 追加bullet 或 None)
PATCH = [
 ("us", [
   ("「医疗30%/科技28%/支付9%」三支柱", "「医药30%/电商22%/支付9%」三支柱"),
   ("结构换血见效：医疗30%（莫德纳19%+安进10.9%）、科技28%、支付9%，告别77%科技单押。",
    "结构换血见效：医药30%（莫德纳19%+安进10.9%）、电商22%、支付9.1%，告别77%科技单押。"),
 ], None),
 ("cn_conservative", [
   ("国轩-2.08%较10-16的-2.71%继续修复，电信+1.18%转正。",
    "国轩累计-2.08%（今日+1.64%）、电信累计+1.18%，两只持仓同步修复。"),
   ("国轩-2.08%较10-16的-2.71%继续修复、电信+1.18%转正，两只持仓同步转好，68.9%现金仍未动。",
    "国轩累计-2.08%（今日+1.64%）、电信累计+1.18%（今日+1.18%），68.9%现金仍未动。"),
 ], None),
 ("cn_aggressive", [
   ("中间6pp无任何主动减仓", "中间5.6pp无任何主动减仓"),
   ("缓冲垫继续工作：风语筑+8.21%、跨境通+3.25%、神华+3.24%、农行+2.35%，对冲万科与中远海控-2.81%。",
    "累计浮盈垫：风语筑+8.21%、跨境通+3.25%、神华+3.24%；但今日银行两只与神华齐跌，没对冲住万科。"),
 ], "账户约-0.4%，跑输上证约0.5pp：万科-1.43%、农行-1.07%拖累，缓冲垫今日失效。"),
]

for acct, pairs, add_bullet in PATCH:
    st = json.load(open(f"_state_{acct}_now.json", encoding="utf-8"))
    r = [x for x in st["dailyReviews"] if x["date"] == "2026-10-20"][0]
    payload = {"date": "2026-10-20", "summary": r["summary"],
               "bullets": list(r["bullets"]), "lessons": r["lessons"], "score": r["score"]}
    hits = 0
    def sub(t):
        global hits
        for old, new in pairs:
            if old in t:
                t = t.replace(old, new); hits += 1
        return t
    payload["summary"] = sub(payload["summary"])
    payload["bullets"] = [sub(b) for b in payload["bullets"]]
    if add_bullet and len(payload["bullets"]) < 6:
        payload["bullets"].append(add_bullet)
    # 长度校验
    assert len(payload["summary"]) <= 300, f"{acct} summary {len(payload['summary'])}"
    for b in payload["bullets"]:
        assert len(b) <= 80, f"{acct} bullet too long {len(b)}: {b}"
    fn = f"_fix2_{acct}_1020.json"
    json.dump(payload, open(fn, "w", encoding="utf-8"), ensure_ascii=False)
    out = subprocess.run(["curl", "-s", "--ssl-no-revoke", "-X", "POST",
                          f"{HOST}/api/daily_review?account={acct}",
                          "-H", "Content-Type: application/json", "-d", f"@{fn}"],
                         capture_output=True, text=True).stdout.strip()
    print(f"{acct:16s} repl={hits} bullets={len(payload['bullets'])} summary_len={len(payload['summary'])} -> {out}")
