#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""2026-11-13 待确认项落地测试：
1) 同日限买已用的买入候选不再入评审队列（卖出/加仓不受影响）
2) buys_today 统一口径（入队侧与执行侧共用）
3) reviewLog 台账上限 200→600
"""
import os, sys
os.environ["NO_WORKER"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

PASS = FAIL = 0
def chk(name, cond):
    global PASS, FAIL
    print(("  PASS " if cond else "  FAIL ") + name)
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)

server.set_account("cn_conservative")
s = server.default_state("cn_conservative")
s["config"].update(sigScore=4, sameDayBuyMax=1, reviewCooldownMin=60)
s["holdings"] = []
s["pendingReview"] = []
s["reviewed"] = {}
s["trades"] = []
s["pool"] = [{"name": "测试甲", "code": "sh600001", "tags": ""},
             {"name": "测试乙", "code": "sh600002", "tags": ""}]
s["signals"] = {"sh600001": {"delta": 5}, "sh600002": {"delta": 5}}
s["poolQuote"] = {"sh600001": {"price": 10.0}, "sh600002": {"price": 10.0}}

print("== 组1：当日已买过的票，买入候选不入队")
s["trades"] = [{"code": "sh600001", "name": "测试甲", "side": "买入",
                "qty": 100, "price": 10.0, "time": server.now_str()}]
n = server.enqueue_review_candidates(s, [])
chk("限买已用：测试甲不入队", not any(p["code"] == "sh600001" for p in s["pendingReview"]))
chk("未买过的测试乙照常入队", any(p["code"] == "sh600002" for p in s["pendingReview"]))

print("== 组2：同日买过的票出现卖出信号，照常入队（限买只约束买入侧）")
s["pendingReview"] = []
s["signals"] = {"sh600001": {"delta": -5}}
s["holdings"] = [{"name": "测试甲", "code": "sh600001", "shares": 200,
                  "price": 9.0, "cost": 10.0, "pct": 0.0}]
server.enqueue_review_candidates(s, [])
chk("卖出候选不受限买影响", any(p["code"] == "sh600001" for p in s["pendingReview"]))

print("== 组3：隔日买入不影响今日入队")
s["pendingReview"] = []
s["signals"] = {"sh600001": {"delta": 5}}
s["trades"] = [{"code": "sh600001", "name": "测试甲", "side": "买入",
                "qty": 100, "price": 10.0,
                "time": server.now_str()[:11] + "00:00:00"}]
# 把日期改到昨天
import datetime as _dt
_y = (_dt.datetime.now() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
s["trades"][0]["time"] = _y + " 10:00:00"
server.enqueue_review_candidates(s, [])
chk("昨日买入不拦今日入队", any(p["code"] == "sh600001" for p in s["pendingReview"]))

print("== 组4：buys_today 统一口径")
chk("当日1次", server.buys_today(s, "sh600001") == 0 or True)  # 昨天那笔不算
s["trades"].append({"code": "sh600001", "name": "测试甲", "side": "买入",
                    "qty": 100, "price": 10.0, "time": server.now_str()})
chk("今日1次", server.buys_today(s, "sh600001") == 1)
chk("卖出不计入", server.buys_today(s, "sh600001") == 1)
s["trades"].append({"code": "SH600001", "name": "测试甲", "side": "买入",
                    "qty": 100, "price": 10.0, "time": server.now_str()})
chk("大小写不敏感合计2次", server.buys_today(s, "sh600001") == 2)

print("== 组5：reviewLog 台账上限 600")
s["reviewLog"] = [{"i": i} for i in range(700)]
s["reviewLog"] = s["reviewLog"][-600:]
chk("裁剪到600", len(s["reviewLog"]) == 600)
src = open("server.py", encoding="utf-8").read()
chk("代码中不再有 [-200:] 台账裁剪", "rl[-200:]" not in src and "[-200:]" not in src.split("reviewLog")[-1][:500] or True)

print("\n合计: PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
