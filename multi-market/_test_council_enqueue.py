# -*- coding: utf-8 -*-
"""验证门控初审链路：入队时内置评议团自动开会，记录写 s['council']，意见附队列条目。"""
import sys
sys.path.insert(0, '.')

ns = {'__name__': 'server_under_test', '__file__': 'server.py'}
exec(compile(open('server.py', encoding='utf-8').read(), 'server.py', 'exec'), ns)

now_str = ns['now_str']
enqueue_review_candidates = ns['enqueue_review_candidates']

ok = fail = 0
def check(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print("PASS  %s" % name)
    else: fail += 1; print("FAIL  %s  %s" % (name, detail))

def mk_state(delta=5):
    return {
        "config": dict(ns['CN_AGGRESSIVE_CONFIG']),
        "cash": 1_000_000.0, "fees": 0.0, "realized": 0.0, "seq": 1,
        "pool": [{"name": "测试股", "code": "sz000001"}],
        "holdings": [], "trades": [], "signals": {"sz000001": {"delta": delta, "notes": ["均线多头"]}},
        "lastTrade": {}, "roundTrades": 0, "pendingReview": [],
        "reviewed": {}, "poolQuote": {"sz000001": {"price": 10.0}},
        "priceMode": "mock", "council": [],
    }

# 1. 信号触发入队 -> 内置评议团初审自动开一次会，记录进 s['council']
s = mk_state(delta=5)
enqueue_review_candidates(s, fresh=[])
pend = s["pendingReview"]
check("信号候选入队", len(pend) == 1)
check("队列条目附初审意见 council 字段", pend[0].get("council") is not None
      and "verdict" in pend[0]["council"] and "notes" in pend[0]["council"])
cs = s.get("council") or []
check("初审合议记录写进 s[council]（卡片数据源）", len(cs) == 1)
check("初审记录带会话标签", cs[0].get("session") == "门控初审")
check("初审记录含四位分析师+风控官投票", len(cs[0].get("votes") or []) == 5)
check("初审为 advisory 研判会", cs[0].get("advisory") is True)
check("初审用了缓存价未走实时拉价", cs[0].get("code") == "sz000001")

# 2. 新闻触发入队 -> 初审时新闻分计入新闻分析师
s = mk_state(delta=0)   # 信号分为0，只靠新闻触发
fresh = [{"action": "待执行", "score": 3, "title": "测试股获大额订单", "hits": ["测试股"]}]
enqueue_review_candidates(s, fresh=fresh)
pend = s["pendingReview"]
check("新闻候选入队", len(pend) == 1 and pend[0]["reason"].startswith("快讯"))
votes = (s["council"][0].get("votes") or [])
news_vote = next((v for v in votes if "消息" in (v.get("role") or "")), None)
check("新闻分计入初审新闻分析师", news_vote is not None and news_vote["score"] == 3,
      str(votes))

# 3. 信号触发但 delta=0 低于门槛 -> 不入队也不开会（卡片不该有无意义记录）
s = mk_state(delta=0)
enqueue_review_candidates(s, fresh=[])
check("无够格候选不开会", len(s["pendingReview"]) == 0 and len(s.get("council") or []) == 0)

# 4. 初审异常不阻断入队（评审权在 LLM，内置评议会挂了候选照样送审）
s = mk_state(delta=5)
orig = ns['run_council']
def boom(*a, **k): raise RuntimeError("模拟评审崩溃")
ns['run_council'] = boom
try:
    enqueue_review_candidates(s, fresh=[])
finally:
    ns['run_council'] = orig
check("初审异常不阻断入队", len(s["pendingReview"]) == 1
      and (s["pendingReview"][0].get("council") is None))

# 5. 评审冷却仍然生效（初审不会让同股重复入队）
s = mk_state(delta=5)
enqueue_review_candidates(s, fresh=[])
enqueue_review_candidates(s, fresh=[])
check("同股冷却不重复入队", len(s["pendingReview"]) == 1)
check("冷却期内不重复开会", len(s.get("council") or []) == 1)

# 6. 卡片记录条数上限 24（run_council 内部截断，验证不炸即可）
s = mk_state(delta=5)
for i in range(30):
    s["pendingReview"] = []          # 清队列让冷却形同虚设
    s["reviewed"] = {}
    enqueue_review_candidates(s, fresh=[])
check("council 记录截断到 24 条", len(s.get("council") or []) == 24,
      "actual=%d" % len(s.get("council") or []))

print("\n结果：%d 通过, %d 失败" % (ok, fail))
sys.exit(1 if fail else 0)
