# -*- coding: utf-8 -*-
"""专项验证 maybe_catchup 的补成交分支逻辑（进程内，不碰真实网络/存档）。

覆盖 5 个分支：交易时段触发 / 节流抑制 / 节流后重触发 / 无 approved 不触发 / 非交易时段不触发。
execute_approved 与 in_trading_hours 均打桩，只验证 maybe_catchup 自己的调度逻辑。
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("server", os.path.join(HERE, "server.py"))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

calls = {"n": 0}


def fake_execute(s):
    calls["n"] += 1
    return 1


srv.execute_approved = fake_execute
srv.in_trading_hours = lambda now=None: True
srv._last_catchup = 0.0

# 1. 交易时段 + 有 approved -> 触发一次
s = {"pendingReview": [{"code": "sh600519", "state": "approved"}]}
r = srv.maybe_catchup(s)
assert r == 1, "期望触发成交，实际 %r" % r
assert calls["n"] == 1, "期望 execute_approved 调用 1 次，实际 %d" % calls["n"]

# 2. 节流 45s 内 -> 不重复触发
r = srv.maybe_catchup(s)
assert r == 0 and calls["n"] == 1, "节流内不应重复触发"

# 3. 节流窗口过后 + 有 approved -> 再次触发
srv._last_catchup = 0.0
r = srv.maybe_catchup(s)
assert r == 1 and calls["n"] == 2, "节流过后应再次触发"

# 4. 队列无 approved（waiting）-> 不触发
srv._last_catchup = 0.0
s2 = {"pendingReview": [{"code": "sz000858", "state": "waiting"}]}
r = srv.maybe_catchup(s2)
assert r == 0 and calls["n"] == 2, "无 approved 不应触发"

# 5. 非交易时段 -> 不触发
srv._last_catchup = 0.0
srv.in_trading_hours = lambda now=None: False
s3 = {"pendingReview": [{"code": "sz000977", "state": "approved"}]}
r = srv.maybe_catchup(s3)
assert r == 0 and calls["n"] == 2, "非交易时段不应触发"

print("maybe_catchup 分支逻辑 5 项全部通过")
