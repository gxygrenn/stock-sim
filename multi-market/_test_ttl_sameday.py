# -*- coding: utf-8 -*-
"""验证 2026-10-13 两道新闸门：批准保质期 + 同股当日限买。纯逻辑测试，不碰真实状态。"""
import sys, types, datetime
sys.path.insert(0, '.')

# ---- 打桩：绕过 HTTP/行情依赖 ----
stubs = {}
class FakeDatetime(datetime.datetime):
    NOW = None
    @classmethod
    def now(cls, tz=None):
        return cls.NOW or datetime.datetime.now()

import importlib
mod_name = 'server'
src = open('server.py', encoding='utf-8').read()

# 用 exec 方式加载，避免直接 import 时跑 main
ns = {'__name__': 'server_under_test', '__file__': 'server.py'}
exec(compile(src, 'server.py', 'exec'), ns)

now_str = ns['now_str']
today_str = ns['today_str']
age_hours = ns['age_hours']
execute_approved = ns['execute_approved']
expire_stale_reviews = ns['expire_stale_reviews']
in_trading_hours = ns['in_trading_hours']

# in_trading_hours 打桩：强制 True（交易时段），否则 execute_approved 直接返回
ns['in_trading_hours'] = lambda: True
# current_price 打桩：mock 模式下原实现拿不到价（会在 price<=0 处 continue，
# 走不到被测闸门）。统一给池内股票一个固定价。
_orig_cp = ns['current_price']
def _stub_cp(s, item):
    code = item.get("code") if isinstance(item, dict) else item
    pq = (s.get("poolQuote") or {}).get(code) or {}
    return (pq.get("price") or 0) or _orig_cp(s, item)
ns['current_price'] = _stub_cp

def mk_state():
    return {
        "config": dict(ns['CN_AGGRESSIVE_CONFIG']),
        "cash": 1_000_000.0, "fees": 0.0, "realized": 0.0, "seq": 1,
        "pool": [{"name": "测试股", "code": "sz000001"}],
        "holdings": [], "trades": [], "signals": {"sz000001": {"delta": 5}},
        "lastTrade": {}, "roundTrades": 0, "pendingReview": [],
        "reviewed": {}, "poolQuote": {"sz000001": {"price": 10.0}},
        "priceMode": "mock",
    }

ok = fail = 0
def check(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print("PASS  %s" % name)
    else: fail += 1; print("FAIL  %s  %s" % (name, detail))

# ============ 闸门一：批准保质期 ============
# 1a. execute_approved：30h 前批准的 BUY -> 应被作废不成交
s = mk_state()
s["pendingReview"] = [{"code": "sz000001", "name": "测试股", "direction": "买入",
                       "state": "approved", "time": now_str(), "queuedAt": now_str(),
                       "reviewedAt": (FakeDatetime.now() - datetime.timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S"),
                       "reason": "测试", "review": {"verdict": "BUY", "confidence": 80}}]
done = execute_approved(s)
check("过期批准(30h>24h)不成交", done == 0 and len(s["trades"]) == 0)
check("过期批准出队进expiredReviews", len(s.get("expiredReviews") or []) == 1
      and "过期" in (s["expiredReviews"][0].get("why") or ""))

# 1b. execute_approved：3h 前批准 -> 正常成交
s = mk_state()
s["pendingReview"] = [{"code": "sz000001", "name": "测试股", "direction": "买入",
                       "state": "approved", "time": now_str(), "queuedAt": now_str(),
                       "reviewedAt": (FakeDatetime.now() - datetime.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
                       "reason": "测试", "review": {"verdict": "BUY", "confidence": 80}}]
done = execute_approved(s)
check("新鲜批准(3h<24h)正常成交", done == 1 and len(s["trades"]) == 1)

# 1c. expire_stale_reviews：approved 超 24h -> 作废并记冷却
s = mk_state()
s["pendingReview"] = [{"code": "sz000001", "name": "测试股", "direction": "买入",
                       "state": "approved", "time": now_str(), "queuedAt": now_str(),
                       "reviewedAt": (FakeDatetime.now() - datetime.timedelta(hours=83)).strftime("%Y-%m-%d %H:%M:%S"),
                       "reason": "测试", "review": {"verdict": "BUY", "confidence": 80}}]
n = expire_stale_reviews(s)
check("expire作废83h前的批准", n == 1 and len(s["pendingReview"]) == 0)
check("expire给过期股记评审冷却", "sz000001" in (s.get("reviewed") or {}))

# 1d. expire_stale_reviews：waiting 超时逻辑不受影响
s = mk_state()
s["pendingReview"] = [{"code": "sz000001", "name": "测试股", "direction": "买入",
                       "state": "waiting", "time": now_str(), "queuedAt": now_str()}]
n = expire_stale_reviews(s)
check("waiting未超时保留", n == 0 and len(s["pendingReview"]) == 1)

# ============ 闸门二：同股当日限买 ============
# 2a. 当日已买过 1 次，再批准 -> 第 2 单应被拦截
s = mk_state()
s["trades"] = [{"time": now_str(), "side": "买入", "name": "测试股", "code": "sz000001",
                "price": 10, "qty": 100, "amount": 1000, "reason": "首笔"}]
s["pendingReview"] = [{"code": "sz000001", "name": "测试股", "direction": "买入",
                       "state": "approved", "time": now_str(), "queuedAt": now_str(),
                       "reviewedAt": now_str(),
                       "reason": "测试", "review": {"verdict": "BUY", "confidence": 80}}]
done = execute_approved(s)
check("当日第2笔同股买入被拦", done == 0 and len([t for t in s["trades"] if t["side"]=="买入"]) == 1)
check("拦截理由写入expiredReviews", len(s.get("expiredReviews") or []) == 1
      and "同股当日" in (s["expiredReviews"][0].get("why") or ""))

# 2b. 昨天买的，今天买 -> 放行（跨天不受限）
s = mk_state()
s["trades"] = [{"time": (FakeDatetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                "side": "买入", "name": "测试股", "code": "sz000001",
                "price": 10, "qty": 100, "amount": 1000, "reason": "昨日"}]
s["pendingReview"] = [{"code": "sz000001", "name": "测试股", "direction": "买入",
                       "state": "approved", "time": now_str(), "queuedAt": now_str(),
                       "reviewedAt": now_str(),
                       "reason": "测试", "review": {"verdict": "BUY", "confidence": 80}}]
done = execute_approved(s)
check("昨日买入不占今日额度", done == 1)

# 2c. 当日已买，但这次是 SELL 结论 -> 不受限买约束（卖出正常走）
s = mk_state()
s["holdings"] = [{"id": 1, "name": "测试股", "code": "sz000001", "price": 10.0,
                  "preClose": 10.0, "shares": 1000, "cost": 9.0, "beta": 1.0, "pct": 0.0}]
s["trades"] = [{"time": now_str(), "side": "买入", "name": "测试股", "code": "sz000001",
                "price": 10, "qty": 100, "amount": 1000, "reason": "首笔"}]
s["pendingReview"] = [{"code": "sz000001", "name": "测试股", "direction": "卖出",
                       "state": "approved", "time": now_str(), "queuedAt": now_str(),
                       "reviewedAt": now_str(),
                       "reason": "测试", "review": {"verdict": "SELL", "confidence": 80}}]
done = execute_approved(s)
check("同股卖出不受限买约束", done == 1 and any(t["side"] == "卖出" for t in s["trades"]))

# 2d. 大小写归一：美股 code 大小写不同写法也应命中同一限制
s = mk_state()
s["config"] = dict(ns['US_CONFIG'])
s["pool"] = [{"name": "Test", "code": "PLTR"}]
s["signals"] = {"PLTR": {"delta": 5}}
s["poolQuote"] = {"PLTR": {"price": 100.0}}
s["trades"] = [{"time": now_str(), "side": "买入", "name": "Test", "code": "PLTR",
                "price": 100, "qty": 10, "amount": 1000, "reason": "首笔"}]
s["pendingReview"] = [{"code": "PLTR", "name": "Test", "direction": "买入",
                       "state": "approved", "time": now_str(), "queuedAt": now_str(),
                       "reviewedAt": now_str(),
                       "reason": "测试", "review": {"verdict": "BUY", "confidence": 80}}]
done = execute_approved(s)
check("美股同股当日连买被拦(大小写归一)", done == 0
      and len([t for t in s["trades"] if t["side"]=="买入"]) == 1)

print("\n结果：%d 通过, %d 失败" % (ok, fail))
sys.exit(1 if fail else 0)
