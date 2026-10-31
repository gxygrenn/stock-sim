# -*- coding: utf-8 -*-
"""强制复核冷却期内的「恶化重触发」单元测试（2026-10-30 新增机制）。

背景：reReviewGapH=72 的本意是防止同一只票反复刷评审，但它同时让止损在冷却期内变成盲区——
2026-10-30 cna 大洋电机 09:38 复核时浮亏 -5.0% 判 HOLD，收盘 -10.46%，中间没有第二次复核，
单日拖累 774 元。现改为：冷却期内若浮亏比上次复核时再恶化 ≥ reReviewWorsenPp（默认 3pp），
允许提前重触发。
"""
import os, sys, datetime
os.environ["NO_WORKER"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

_passed = 0
_failed = 0


def chk(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print("  PASS  %s" % name)
    else:
        _failed += 1
        print("  FAIL  %s %s" % (name, detail))


def ago(hours):
    t = datetime.datetime.now() - datetime.timedelta(hours=hours)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def mk_state(price, cost=100.0, last_review_h=1.0, base_pnl=None, code="sh600001"):
    server.set_account("cn_conservative")
    s = server.default_state("cn_conservative")
    s["config"]["reReviewPct"] = -5
    s["config"]["reReviewGapH"] = 72
    s["holdings"] = [{"name": "测试股", "code": code, "shares": 100,
                      "price": price, "cost": cost, "pct": 0.0}]
    s["cash"] = 100000
    s["pool"] = []
    s["pendingReview"] = []
    s["reReviewed"] = {code.lower(): ago(last_review_h)}
    s["reReviewedPnl"] = {}
    if base_pnl is not None:
        s["reReviewedPnl"][code.lower()] = base_pnl
    return s


def queued(s):
    return [p for p in s.get("pendingReview") or [] if p.get("forced")]


print("== 恶化重触发 ==")

# 1) 冷却期内（1h 前复核过），浮亏 -5.0 -> -8.5，恶化 3.5pp >= 3 -> 应重触发
s = mk_state(price=91.5, base_pnl=-5.0, last_review_h=1.0)
server.enqueue_review_candidates(s, [])
q = queued(s)
chk("冷却期内再恶化 3.5pp 提前重触发", len(q) == 1, "实际入队 %d 条" % len(q))
chk("基线更新为新浮亏 -8.5", abs(s["reReviewedPnl"]["sh600001"] - (-8.5)) < 0.01,
    "基线=%r" % s["reReviewedPnl"].get("sh600001"))

# 2) 冷却期内，浮亏 -5.0 -> -6.5，恶化 1.5pp < 3 -> 仍按冷却跳过
s = mk_state(price=93.5, base_pnl=-5.0, last_review_h=1.0)
server.enqueue_review_candidates(s, [])
chk("冷却期内仅恶化 1.5pp 不重触发", len(queued(s)) == 0)

# 3) 老存档没有基线（只有时间戳）-> 保持旧行为，不重触发
s = mk_state(price=91.5, base_pnl=None, last_review_h=1.0)
server.enqueue_review_candidates(s, [])
chk("缺基线时保持旧行为（不重触发）", len(queued(s)) == 0)

# 4) 冷却期已过（80h）-> 正常触发，与恶化幅度无关
s = mk_state(price=94.0, base_pnl=-5.0, last_review_h=80.0)
server.enqueue_review_candidates(s, [])
chk("冷却期外正常触发", len(queued(s)) == 1)

# 5) 未触及复核线（-3%，高于 -5%）-> 入队与否都不应出现 forced 项
s = mk_state(price=97.0, base_pnl=-5.0, last_review_h=1.0)
server.enqueue_review_candidates(s, [])
chk("未触及复核线不入队", len(queued(s)) == 0)

# 6) 自定义阈值 reReviewWorsenPp=5：恶化 3.5pp < 5 -> 不触发
s = mk_state(price=91.5, base_pnl=-5.0, last_review_h=1.0)
s["config"]["reReviewWorsenPp"] = 5
server.enqueue_review_candidates(s, [])
chk("阈值可配置（5pp 时 3.5pp 不触发）", len(queued(s)) == 0)

# 7) 队列已被 6 条普通候选占满 -> 强制复核仍应挤进去，并移除分数最低的那条等待项
s = mk_state(price=91.5, base_pnl=None, last_review_h=200.0)
s["config"]["reReviewPct"] = -8
s["reReviewed"] = {}
s["pendingReview"] = [
    {"code": "sh600002", "name": "低分", "direction": "买入", "score": 1, "state": "waiting",
     "queuedAt": "2026-10-30 09:00:01"},
    {"code": "sh600003", "name": "中分", "direction": "买入", "score": 2, "state": "waiting",
     "queuedAt": "2026-10-30 09:00:02"},
    {"code": "sh600004", "name": "高分", "direction": "买入", "score": 12, "state": "waiting",
     "queuedAt": "2026-10-30 09:00:03"},
    {"code": "sh600005", "name": "核准", "direction": "买入", "score": 3, "state": "approved",
     "queuedAt": "2026-10-30 09:00:04"},
    {"code": "sh600006", "name": "另一个", "direction": "买入", "score": 5, "state": "waiting",
     "queuedAt": "2026-10-30 09:00:05"},
    {"code": "sh600007", "name": "再一个", "direction": "买入", "score": 6, "state": "waiting",
     "queuedAt": "2026-10-30 09:00:06"},
]
server.enqueue_review_candidates(s, [])
chk("队列满 6 条时强制复核仍能入队", len(queued(s)) == 1, "实际 %d" % len(queued(s)))
names = [p.get("name") for p in s["pendingReview"]]
chk("被挤掉的是最低分 waiting 候选", "低分" not in names, str(names))
chk("approved 与高分候选保留", "核准" in names and "高分" in names, str(names))
chk("队列长度仍为 6", len(s["pendingReview"]) == 6, "实际 %d" % len(s["pendingReview"]))

print("\n结果：%d 通过，%d 失败" % (_passed, _failed))
sys.exit(1 if _failed else 0)
