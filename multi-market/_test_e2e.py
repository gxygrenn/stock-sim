# -*- coding: utf-8 -*-
"""端到端压测：模拟「扫描 → 候选入队 → 评审写回 → 交易时段成交」完整链路。

目的：在周末休市、无法自然成交的背景下，用 mock 数据验证链路本身没有 bug，
排除「策略/代码有问题导致一直不交易」的疑虑。

覆盖场景：
1. 强信号候选能进 pendingReview 队列（门控模式）
2. 评审写回 approved 后，交易时段能被 execute_approved 成交
3. 非交易时段不成交（正确行为）
4. 门槛过滤：信号分低于下限不入队、估值 block 不入队
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["NO_WORKER"] = "1"
import server

_passed = 0
_failed = 0

def chk(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print("  ✓ %s" % name)
    else:
        _failed += 1
        print("  ✗ %s  %s" % (name, detail))

print("== 链路压测开始 ==")

# ---------- 场景 0：准备状态 ----------
server.set_account("cn_conservative")
s = server.default_state("cn_conservative")
cfg = s["config"]
cfg["autoTrade"] = False           # 门控模式
cfg["useStops"] = True
cfg["stopLossPct"] = -8
cfg["minScore"] = 4
cfg["sigScore"] = 4
cfg["buySigFloor"] = -6
s["cash"] = 100000.0
s["holdings"] = []
s["pendingReview"] = []
s["trades"] = []
s["signals"] = {}
s["roundTrades"] = 0

# 构造一个股票池（含一只强信号股）
s["pool"] = [
    {"name": "测试强势股", "code": "sh600000", "tags": "半导体,芯片",
     "price": 10.0, "pe": 20, "turn": 3.0},
    {"name": "测试弱信号股", "code": "sh600001", "tags": "白酒,消费",
     "price": 50.0, "pe": 30, "turn": 1.0},
]

# mock 现价与行情
s["poolQuote"] = {
    "sh600000": {"price": 10.0, "pct": 2.0},
    "sh600001": {"price": 50.0, "pct": -1.0},
}

# ---------- 场景 1：信号分达到 sigScore 能入队 ----------
print("\n[场景1] 强信号候选入队（门控模式）")
# 给强势股一个 delta=6（>= sigScore=4）的强信号
s["signals"] = {
    "sh600000": {"delta": 6, "block": False, "notes": ["突破新高", "主力净流入"]},
    "sh600001": {"delta": 1, "block": False, "notes": ["平淡"]},
}
# 直接调用 enqueue（绕过新闻抓取，聚焦核心入队逻辑）
fresh = []  # 无新闻，走"综合信号"入队分支
s["pendingReview"] = []
server.enqueue_review_candidates(s, fresh)
pend = s["pendingReview"]
chk("强信号股 sh600000 入队", any(p["code"] == "sh600000" for p in pend),
    "实际队列: %s" % [(p["code"], p["direction"]) for p in pend])
chk("弱信号股 sh600001 不入队", not any(p["code"] == "sh600001" for p in pend))
chk("入队 state=waiting", all(p["state"] == "waiting" for p in pend))

# ---------- 场景 2：信号分低于下限不入队 ----------
print("\n[场景2] 信号分低于买入下限不入队")
s["signals"] = {
    "sh600000": {"delta": -7, "block": False, "notes": []},  # 低于 buySigFloor=-6
}
s["pendingReview"] = []
server.enqueue_review_candidates(s, fresh)
chk("delta=-7 < 下限-6 不入队", len(s["pendingReview"]) == 0,
    "队列: %s" % s["pendingReview"])

# ---------- 场景 3：估值 block 不入队 ----------
print("\n[场景3] 估值过滤 block 不入队")
s["signals"] = {
    "sh600000": {"delta": 6, "block": True, "notes": ["高PE"]},
}
s["pendingReview"] = []
server.enqueue_review_candidates(s, fresh)
chk("block=True 不入队", len(s["pendingReview"]) == 0)

# ---------- 场景 4：评审写回 approved 后，交易时段成交 ----------
print("\n[场景4] 评审 approved + 交易时段 → 成交")
s["signals"] = {
    "sh600000": {"delta": 6, "block": False, "notes": ["突破新高"]},
}
s["pendingReview"] = []
server.enqueue_review_candidates(s, fresh)
chk("候选已入队", len(s["pendingReview"]) == 1)

# 模拟评审写回：把 waiting 改成 approved + review 字段
p = s["pendingReview"][0]
p["state"] = "approved"
p["review"] = {"verdict": "BUY", "confidence": 75}
p["reviewedAt"] = server.now_str()

# mock 交易时段为 True
server.in_trading_hours = lambda now=None: True
# mock current_price 返回现价
server.current_price = lambda s, item: 10.0

n = server.execute_approved(s)
chk("execute_approved 成交 1 笔", n == 1, "实际成交 %d" % n)
chk("持仓增加", len(s["holdings"]) == 1 and s["holdings"][0]["shares"] > 0,
    "持仓: %s" % [(h["code"], h["shares"]) for h in s["holdings"]])
chk("队列已清空", len(s["pendingReview"]) == 0)
chk("产生成交记录", len(s["trades"]) == 1, "trades: %s" % len(s["trades"]))
chk("cash 减少", s["cash"] < 100000.0, "cash=%s" % s["cash"])

# ---------- 场景 5：非交易时段不成交 ----------
print("\n[场景5] 非交易时段不成交（正确行为）")
s2 = server.default_state("cn_conservative")
s2["config"]["autoTrade"] = False
s2["pool"] = [{"name": "测试股", "code": "sh600000", "tags": "半导体",
               "price": 10.0, "pe": 20, "turn": 3.0}]
s2["poolQuote"] = {"sh600000": {"price": 10.0, "pct": 1.0}}
s2["signals"] = {"sh600000": {"delta": 6, "block": False, "notes": []}}
s2["pendingReview"] = [{"code": "sh600000", "name": "测试股", "direction": "买入",
                        "score": 6, "delta": 6, "reason": "测试", "time": server.now_str(),
                        "queuedAt": server.now_str(), "px": 10.0, "state": "approved",
                        "review": {"verdict": "BUY", "confidence": 70},
                        "reviewedAt": server.now_str()}]
server.in_trading_hours = lambda now=None: False   # 休市
n = server.execute_approved(s2)
chk("非交易时段 execute_approved 返回 0", n == 0)
chk("非交易时段无成交", len(s2["holdings"]) == 0)

# ---------- 场景 6：卖出方向（已持仓 + 评审 SELL） ----------
print("\n[场景6] 评审 SELL 卖出已持仓")
s3 = server.default_state("cn_conservative")
s3["config"]["autoTrade"] = False
s3["pool"] = [{"name": "测试股", "code": "sh600000", "tags": "半导体",
               "price": 10.0, "pe": 20, "turn": 3.0}]
s3["poolQuote"] = {"sh600000": {"price": 10.0, "pct": -3.0}}
s3["signals"] = {"sh600000": {"delta": -5, "block": False, "notes": []}}
s3["holdings"] = [{"code": "sh600000", "name": "测试股", "shares": 500,
                   "price": 10.0, "cost": 12.0, "pct": -3.0, "peak": 12.0}]
s3["cash"] = 50000.0
s3["pendingReview"] = [{"code": "sh600000", "name": "测试股", "direction": "卖出",
                        "score": -5, "delta": -5, "reason": "利空", "time": server.now_str(),
                        "queuedAt": server.now_str(), "px": 10.0, "state": "approved",
                        "review": {"verdict": "SELL", "confidence": 70},
                        "reviewedAt": server.now_str()}]
server.in_trading_hours = lambda now=None: True
server.current_price = lambda s, item: 10.0
n = server.execute_approved(s3)
chk("SELL 成交 1 笔", n == 1, "实际 %d" % n)
chk("持仓减少", s3["holdings"][0]["shares"] < 500,
    "剩余: %s" % s3["holdings"][0]["shares"])

# ---------- 场景 7：止损止盈链路（check_stops 主动离场） ----------
print("\n[场景7] 止损止盈主动离场链路")
s4 = server.default_state("cn_conservative")
s4["config"]["useStops"] = True
s4["config"]["stopLossPct"] = -8
s4["holdings"] = [{"code": "sh600000", "name": "测试股", "shares": 100,
                   "price": 9.0, "cost": 10.0, "pct": -10.0, "peak": 10.0}]  # 浮亏10%触发止损
s4["cash"] = 90000.0
s4["roundTrades"] = 0
server.in_trading_hours = lambda now=None: True
n = server.check_stops(s4)
chk("check_stops 触发止损 1 笔", n == 1, "实际 %d" % n)
chk("止损后清仓", len(s4["holdings"]) == 0 or s4["holdings"][0]["shares"] == 0,
    "持仓: %s" % [(h["code"], h["shares"]) for h in s4["holdings"]])

print("\n结果：%d 通过，%d 失败" % (_passed, _failed))
sys.exit(1 if _failed else 0)
