# -*- coding: utf-8 -*-
"""专家团门控（pendingReview 队列）单元测试 —— 进程内直测，不依赖当天新闻。"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("srv", os.path.join(HERE, "server.py"))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

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


def fresh_state():
    srv.set_account("us")
    s = srv.default_state("us")
    s["config"]["autoTrade"] = False
    return s


def news_item(name, score):
    return {"id": "t1", "time": srv.now_str(), "title": "测试快讯",
            "text": "测试", "action": "待执行", "score": score, "hits": [name]}


print("门控：pendingReview 队列")

# 字段存在
s = fresh_state()
chk("default_state 含 pendingReview/reviewed",
    isinstance(s.get("pendingReview"), list) and isinstance(s.get("reviewed"), dict))

# 1. 正分新闻 -> 买入候选
s = fresh_state()
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
pend = s["pendingReview"]
chk("正分新闻入队为买入候选", len(pend) == 1 and pend[0]["direction"] == "买入",
    str(pend[:1]))
chk("候选字段完整",
    all(k in pend[0] for k in ("code", "name", "direction", "score", "delta", "reason", "time")))

# 2. 同一只不重复入队
srv.enqueue_review_candidates(s, [news_item("苹果", 6)])
chk("同一只不重复入队", len(s["pendingReview"]) == 1)

# 3. 负分新闻未持仓 -> 不入队；持仓后 -> 卖出候选
s = fresh_state()
srv.enqueue_review_candidates(s, [news_item("英伟达", -5)])
chk("负分但未持仓不入队", len(s["pendingReview"]) == 0)
srv.do_buy(s, "英伟达", "usNVDA", 100.0, 100, "测试")
srv.enqueue_review_candidates(s, [news_item("英伟达", -5)])
chk("负分且持仓入队为卖出候选",
    len(s["pendingReview"]) == 1 and s["pendingReview"][0]["direction"] == "卖出")

# 4. 估值 block 不入队
s = fresh_state()
s["signals"]["usAAPL"] = {"tech": 0, "lhb": 0, "ann": 0, "delta": 0,
                            "block": True, "notes": []}
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
chk("估值被 block 的不入队", len(s["pendingReview"]) == 0)

# 5. reviewed 冷却
s = fresh_state()
s["reviewed"]["usaapl"] = srv.now_str()
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
chk("评审冷却期内不入队", len(s["pendingReview"]) == 0)
s["reviewed"]["usaapl"] = "2020-01-01 00:00:00"
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
chk("冷却期外正常入队", len(s["pendingReview"]) == 1)

# 6. 综合信号 |delta| >= sigScore 独立入队
s = fresh_state()
s["signals"]["usTSLA"] = {"tech": 3, "lhb": 2, "ann": 0, "delta": 5,
                            "block": False, "notes": ["均线多头+1", "龙虎榜净买+2"]}
srv.enqueue_review_candidates(s, [])
pend = s["pendingReview"]
chk("强信号无新闻也入队", len(pend) == 1 and pend[0]["name"] == "特斯拉",
    str(pend[:1]))
chk("信号候选带方向与分值", pend[0]["direction"] == "买入" and pend[0]["score"] == 5)

# 7. 弱信号不入队
s = fresh_state()
s["signals"]["usTSLA"] = {"tech": 1, "lhb": 0, "ann": 0, "delta": 2,
                            "block": False, "notes": []}
srv.enqueue_review_candidates(s, [])
chk("低于 sigScore 门槛不入队", len(s["pendingReview"]) == 0)

# 8. 队列上限 6
s = fresh_state()
for it in s["pool"]:
    s["signals"][it["code"]] = {"tech": 3, "lhb": 2, "ann": 0, "delta": 5,
                                "block": False, "notes": []}
srv.enqueue_review_candidates(s, [])
chk("队列上限 6 只", len(s["pendingReview"]) == 6, "实际 %d" % len(s["pendingReview"]))

# 9. 买入信号分下限（buySigFloor）
s = fresh_state()
s["config"]["buySigFloor"] = 0
s["signals"]["usAAPL"] = {"tech": -1, "lhb": -1, "ann": 0, "delta": -2,
                            "block": False, "notes": []}
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
chk("买入候选信号分低于下限不入队", len(s["pendingReview"]) == 0)
s["config"]["buySigFloor"] = -6
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
chk("放宽下限后恢复入队", len(s["pendingReview"]) == 1)

s = fresh_state()
s["config"]["buySigFloor"] = 0
s["signals"]["usAAPL"] = {"tech": 0, "lhb": 0, "ann": 0, "delta": 0,
                            "block": False, "notes": []}
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
chk("信号分恰等于下限时放行", len(s["pendingReview"]) == 1)

# 卖出候选不受买入下限影响
s = fresh_state()
s["config"]["buySigFloor"] = 3
s["signals"]["usNVDA"] = {"tech": -3, "lhb": -2, "ann": 0, "delta": -5,
                            "block": False, "notes": []}
srv.do_buy(s, "英伟达", "usNVDA", 100.0, 100, "测试")
srv.enqueue_review_candidates(s, [news_item("英伟达", -5)])
chk("卖出候选不受买入下限影响",
    len(s["pendingReview"]) == 1 and s["pendingReview"][0]["direction"] == "卖出")
# ------------------------------------------------ 10. 队列 TTL：等太久自动作废
print("\n队列 TTL：超时作废")
import datetime as _dt


def ago(h):
    return (_dt.datetime.now() - _dt.timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")


s = fresh_state()
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
p0 = s["pendingReview"][0]
chk("入队带 queuedAt / px / state",
    all(k in p0 for k in ("queuedAt", "px", "state")) and p0["state"] == "waiting",
    str(p0)[:90])
chk("刚入队不会被作废", srv.expire_stale_reviews(s) == 0 and len(s["pendingReview"]) == 1)

s["pendingReview"][0]["queuedAt"] = ago(30)
n = srv.expire_stale_reviews(s)
chk("滞留 30h 超 TTL 作废出队", n == 1 and len(s["pendingReview"]) == 0, "n=%d" % n)
chk("过期原因写入 expiredReviews（供复盘归因）",
    len(s.get("expiredReviews") or []) == 1 and
    s["expiredReviews"][0]["why"].startswith("TTL") and
    s["expiredReviews"][0]["waitedH"] >= 24,
    str((s.get("expiredReviews") or [{}])[0])[:90])
chk("过期后写冷却，避免同一条信号立刻被塞回来", "usaapl" in (s.get("reviewed") or {}))
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
chk("过期冷却期内不会重新入队", len(s["pendingReview"]) == 0)

s = fresh_state()
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
s["pendingReview"][0]["queuedAt"] = ago(10)
chk("滞留 10h 未超时则保留", srv.expire_stale_reviews(s) == 0 and len(s["pendingReview"]) == 1)

# 线上那种旧格式积压候选：没有 queuedAt，必须能自动清掉（解死锁的关键）
s = fresh_state()
s["pendingReview"] = [{"code": "usAAPL", "name": "苹果", "direction": "买入",
                       "score": 5, "delta": -1, "reason": "快讯", "time": "2026-09-15 10:14:41"}]
chk("旧格式积压候选（无 queuedAt）一律作废", srv.expire_stale_reviews(s) == 1)
s = fresh_state()
s["pendingReview"] = [{"code": "usAAPL", "name": "苹果", "direction": "买入", "score": 5}]
chk("时间缺失的候选也视为过期", srv.expire_stale_reviews(s) == 1)

s = fresh_state()
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
s["pendingReview"][0]["state"] = "approved"
s["pendingReview"][0]["queuedAt"] = ago(1)
s["pendingReview"][0]["reviewedAt"] = ago(30)
chk("已批准的项不因 TTL 作废（保留待交易时段成交）",
    srv.expire_stale_reviews(s) == 0 and len(s["pendingReview"]) == 1 and
    s["pendingReview"][0]["state"] == "approved")

# ------------------------------------------------ 11. 评审与执行解耦
print("\n评审与执行解耦：结论落队列，交易时段才成交")
_orig_price, _orig_ith = srv.current_price, srv.in_trading_hours
srv.current_price = lambda st, item: 10.0        # 免网络


def approve(s, verdict, conf=7):
    p = s["pendingReview"][0]
    p["state"] = "approved"
    p["reviewedAt"] = srv.now_str()
    p["review"] = {"verdict": verdict, "confidence": conf, "note": "测试",
                   "reviewedAt": p["reviewedAt"]}


s = fresh_state()
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
approve(s, "BUY")
srv.in_trading_hours = lambda *a: False
chk("非交易时段不成交", srv.execute_approved(s) == 0 and len(s["trades"]) == 0)
chk("非交易时段候选留在队列里等盘中", len(s["pendingReview"]) == 1)

srv.in_trading_hours = lambda *a: True
n = srv.execute_approved(s)
chk("盘中按最新价成交并出队",
    n == 1 and len(s["trades"]) == 1 and len(s["pendingReview"]) == 0, "n=%d" % n)
tr = s["trades"][0]
chk("成交价取当时最新价", abs(tr["price"] - 10.0) < 1e-9, str(tr.get("price")))
chk("成交量守整手约束", tr["qty"] >= 1, str(tr.get("qty")))
chk("成交理由标注专家团结论与滞后",
    "专家团BUY" in tr["reason"] and "按最新价" in tr["reason"], tr["reason"])

# 已批准买入但信号已跌破下限 -> 作废，不拿过期结论下单
s = fresh_state()
s["config"]["buySigFloor"] = 0   # 固定门槛，避免依赖各版默认值
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
approve(s, "BUY")
s["signals"]["usAAPL"] = {"tech": -3, "lhb": 0, "ann": 0, "delta": -3,
                            "block": False, "notes": []}
chk("已批准买入但信号跌破下限 -> 作废不成交",
    srv.execute_approved(s) == 0 and len(s["trades"]) == 0 and len(s["pendingReview"]) == 0)
chk("作废原因可追溯",
    any((r.get("why") or "").startswith("信号分") for r in (s.get("expiredReviews") or [])),
    str((s.get("expiredReviews") or [{}])[0])[:90])

# 已批准卖出 -> 按 sellPct 减仓
s = fresh_state()
srv.do_buy(s, "英伟达", "usNVDA", 10.0, 500, "测试")
srv.enqueue_review_candidates(s, [news_item("英伟达", -5)])
approve(s, "SELL", 8)
chk("已批准卖出 -> 按 sellPct 减仓",
    srv.execute_approved(s) == 1 and s["holdings"][0]["shares"] < 500,
    "剩 %d 股" % s["holdings"][0]["shares"])

# 未批准的候选不会被误执行
s = fresh_state()
srv.enqueue_review_candidates(s, [news_item("苹果", 5)])
chk("waiting 状态（还没评审）不被执行",
    srv.execute_approved(s) == 0 and len(s["trades"]) == 0 and len(s["pendingReview"]) == 1)

srv.current_price, srv.in_trading_hours = _orig_price, _orig_ith

print("\n%d passed, %d failed" % (PASS, FAIL))
raise SystemExit(1 if FAIL else 0)
