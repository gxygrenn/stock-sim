# -*- coding: utf-8 -*-
"""按自查报告修正 us 10-20 复盘的表述（仅改歧义句，不动结论）。"""
import json
import ssl
import urllib.request

BASE = "https://stock-sim-us.app.workbuddy.host"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def get(p):
    return json.loads(urllib.request.urlopen(BASE + p, context=ctx, timeout=30).read().decode())


s = get("/api/state?account=us")
r = [x for x in s["dailyReviews"] if x["date"] == "2026-10-20"][0]
old = r["summary"]
new = old.replace("持仓4只浮盈3只浮亏", "7只持仓中4只浮盈、3只浮亏")
assert new != old, "未匹配到待修正句"
body = {"date": r["date"], "summary": new, "bullets": r["bullets"], "lessons": r["lessons"], "score": r["score"]}
print("summary 字数:", len(new), "/300")
for b in body["bullets"]:
    assert len(b) <= 80, b
print("修正：", old[old.index("+0.75%"):old.index("+0.75%") + 40], "→", new[new.index("+0.75%"):new.index("+0.75%") + 40])

req = urllib.request.Request(
    BASE + "/api/daily_review?account=us",
    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
print("写入:", urllib.request.urlopen(req, context=ctx, timeout=30).read().decode()[:200])
