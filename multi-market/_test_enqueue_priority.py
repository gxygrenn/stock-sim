# -*- coding: utf-8 -*-
"""队列抢位（2026-11-27）单元测试：强候选按 delta 挤掉最弱 waiting 买入候选。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server
ns = server.__dict__

PASS = 0
FAIL = 0


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


def mk_state(deltas, pool_delta=None, direction="买入", full=True):
    """deltas: 已在队列里的 waiting 买入候选 delta 列表（顺序即入队顺序）。"""
    pool = [{"name": "强票", "code": "sz300999"}]
    signals = {"sz300999": {"delta": pool_delta if pool_delta is not None else 4.4,
                            "notes": ["均线多头"]}}
    pend = []
    for i, d in enumerate(deltas):
        code = "sz%06d" % (100 + i)
        pool.append({"name": "弱票%d" % i, "code": code})
        signals[code] = {"delta": d, "notes": ["快讯"]}
        if full:
            pend.append({"code": code, "name": "弱票%d" % i, "direction": direction,
                         "score": 1, "delta": d, "reason": "快讯: 测试",
                         "time": server.now_str(), "queuedAt": "2026-11-27 10:0%d:00" % i,
                         "px": 10.0, "state": "waiting"})
    return {
        "config": dict(ns["CN_AGGRESSIVE_CONFIG"]),
        "cash": 500000.0, "fees": 0.0, "realized": 0.0, "seq": 1,
        "pool": pool, "holdings": [], "trades": [], "signals": signals,
        "lastTrade": {}, "roundTrades": 0, "pendingReview": pend,
        "reviewed": {}, "poolQuote": {p["code"]: {"price": 10.0} for p in pool},
        "priceMode": "mock", "council": [],
    }


def fast_council(*a, **k):
    return {"verdict": "买", "score": 3, "agree": 3, "oppose": 0, "abstain": 0,
            "veto": "", "votes": []}


ns["run_council"] = fast_council

print("抢位：队列满 6 席时强候选进场")
s = mk_state([0.6, 0.6, 0.8, 1.3, -0.2, 2.4])
server.enqueue_review_candidates(s, fresh=[])
codes = [p["code"] for p in s["pendingReview"]]
chk("强票 sz300999 进入队列", "sz300999" in codes, str(codes))
chk("队列仍为 6 席（未超限）", len(codes) == 6, "len=%d" % len(codes))
chk("最弱候选 sz000104（delta 2.4 之前最低的 -0.2）被挤掉",
    "sz000104" not in codes, str(codes))
deltas = sorted(p["delta"] for p in s["pendingReview"] if p["code"] != "sz300999")
chk("留下的都是 delta 更高的票", deltas == sorted([0.6, 0.6, 0.8, 1.3, 2.4]), str(deltas))

print("抢位：不如队内最弱者就不抢")
s = mk_state([3.0, 3.0, 3.0, 3.0, 3.0, 3.0], pool_delta=2.9)
server.enqueue_review_candidates(s, fresh=[])
chk("delta 2.9 抢不过队内 3.0，未入队",
    "sz300999" not in [p["code"] for p in s["pendingReview"]])
chk("队列长度不变", len(s["pendingReview"]) == 6)

print("抢位：approved / forced 不动")
s = mk_state([0.6, 0.6, 0.8, 1.3, -0.2, 2.4])
s["pendingReview"][0]["state"] = "approved"          # delta 0.6 但是已批准，不能挤
s["pendingReview"][1]["forced"] = True               # delta 0.6 强制复核，不能挤
s["pendingReview"][1]["score"] = 0
server.enqueue_review_candidates(s, fresh=[])
codes = [p["code"] for p in s["pendingReview"]]
chk("approved 条目仍在", "sz000100" in codes, str(codes))
chk("forced 条目仍在", "sz000101" in codes)
chk("被挤掉的是普通 waiting 里最弱的（-0.2）", "sz000104" not in codes, str(codes))

print("抢位：卖出候选不参与抢位")
s = mk_state([0.6, 0.6, 0.8, 1.3, -0.2, 2.4], direction="卖出")
# 浮亏仅 -1.96%：不触发强制复核线，确保这条只验证「卖出候选不抢买入席位」
s["holdings"] = [{"code": "sz300999", "name": "强票", "shares": 100,
                  "cost": 10.2, "price": 10.0, "pct": -2.0}]
s["signals"]["sz300999"] = {"delta": -3.5, "notes": ["破位"]}
server.enqueue_review_candidates(s, fresh=[])
chk("卖出候选不挤占买入席位", len(s["pendingReview"]) == 6
    and "sz300999" not in [p["code"] for p in s["pendingReview"]])

print("抢位：开关可回退")
s = mk_state([0.6, 0.6, 0.8, 1.3, -0.2, 2.4])
s["config"] = dict(s["config"], enqueueDeltaPriority=False)
server.enqueue_review_candidates(s, fresh=[])
chk("enqueueDeltaPriority=False 时退回先到先得",
    "sz300999" not in [p["code"] for p in s["pendingReview"]]
    and len(s["pendingReview"]) == 6)

print("抢位：队列未满时正常入队、不触发挤占")
s = mk_state([0.6, 0.8])
server.enqueue_review_candidates(s, fresh=[])
codes = [p["code"] for p in s["pendingReview"]]
chk("未满队列直接追加", "sz300999" in codes and len(codes) == 3, str(codes))

print("\n结果：%d 通过，%d 失败" % (PASS, FAIL))
raise SystemExit(1 if FAIL else 0)
