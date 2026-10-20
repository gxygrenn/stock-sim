# -*- coding: utf-8 -*-
"""修正 2026-10-20 三份复盘中被自查发现的事实/计数错误，保留其余内容不变。"""
import json, subprocess

HOST = "https://stock-sim-us.app.workbuddy.host"
FIX = {
 "us": [("7只持仓6只红盘", "持仓4只浮盈3只浮亏")],
 "cn_conservative": [
    ("国轩-2.08%连续第三日修复", "国轩-2.08%较10-16的-2.71%继续修复"),
    ("国轩-2.08%连续三日修复", "国轩-2.08%较10-16的-2.71%继续修复"),
 ],
 "cn_aggressive": [
    ("6个买入信号全被", "7个买入信号全被"),
    ("从-2.5%到-8.11%的6pp里", "从-2.5%到-8.11%的5.6pp里"),
 ],
}

for acct, pairs in FIX.items():
    st = json.load(open(f"_state_{acct}_after.json", encoding="utf-8"))
    rev = [r for r in st["dailyReviews"] if r["date"] == "2026-10-20"][0]
    payload = {"date": "2026-10-20", "summary": rev["summary"], "bullets": rev["bullets"],
               "lessons": rev["lessons"], "score": rev["score"]}
    hits = 0
    def sub(t):
        global hits
        for old, new in pairs:
            if old in t:
                t = t.replace(old, new); hits += 1
        return t
    payload["summary"] = sub(payload["summary"])
    payload["bullets"] = [sub(b) for b in payload["bullets"]]
    payload["lessons"] = [sub(l) for l in payload["lessons"]]
    fn = f"_fix_{acct}_1020.json"
    json.dump(payload, open(fn, "w", encoding="utf-8"), ensure_ascii=False)
    out = subprocess.run(["curl", "-s", "--ssl-no-revoke", "-X", "POST",
                          f"{HOST}/api/daily_review?account={acct}",
                          "-H", "Content-Type: application/json", "-d", f"@{fn}"],
                         capture_output=True, text=True).stdout.strip()
    print(f"{acct:16s} replacements={hits} -> {out}")
