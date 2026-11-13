# -*- coding: utf-8 -*-
"""动态调池（rebalance）单元测试 —— 进程内直测，monkeypatch 网络，不依赖真实行情。"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
import server
srv = server

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


def kline_flat(n=61, close=10.0):
    return [("2026-01-01", close, close, close, close, 1000.0)] * n


def kline_ramp(n=61, start=10.0, end=10.0):
    step = (end - start) / (n - 1)
    return [("2026-01-01", start + step * i, start + step * i, start + step * i,
             start + step * i, 1000.0) for i in range(n)]


def fresh_cn():
    srv.set_account("cn_conservative")
    s = srv.default_state("cn_conservative")
    s["pool"] = [
        {"name": "亏损股", "code": "sh600001", "alias": "亏损", "beta": 1.0, "tags": "测试"},
        {"name": "流动性差", "code": "sh600002", "alias": "差", "beta": 1.0, "tags": "测试"},
        {"name": "暴跌股", "code": "sh600003", "alias": "暴跌", "beta": 1.0, "tags": "测试"},
        {"name": "健康股", "code": "sh600004", "alias": "健康", "beta": 1.0, "tags": "测试"},
    ]
    s["poolQuote"] = {
        "sh600001": {"pe": -5.0, "amount": 5e8, "price": 10.0},
        "sh600002": {"pe": 15.0, "amount": 5e7, "price": 10.0},
        "sh600003": {"pe": 15.0, "amount": 5e8, "price": 10.0},
        "sh600004": {"pe": 15.0, "amount": 5e8, "price": 10.0},
    }
    return s


def fake_kline(code, n=61):
    if code == "sh600003":
        return kline_ramp(61, 10.0, 6.0)      # -40%
    if code == "sz300888":
        return kline_ramp(61, 10.0, 11.0)      # +10%
    return kline_flat(61, 10.0)


print("调池：字段与淘汰")

s = fresh_cn()
chk("default_state 含 poolChanges/poolChangeLog/lastRebalance",
    isinstance(s.get("poolChanges"), list) and isinstance(s.get("poolChangeLog"), list)
    and "lastRebalance" in s)

srv.fetch_kline = fake_kline
removed = srv.pool_remove_candidates(s)
removed_codes = {r["code"]: r for r in removed}
chk("亏损股被建议淘汰", "sh600001" in removed_codes,
    str(removed_codes.get("sh600001", {}).get("reasons")))
chk("流动性差被建议淘汰", "sh600002" in removed_codes)
chk("60日暴跌被建议淘汰", "sh600003" in removed_codes)
chk("健康股不被淘汰", "sh600004" not in removed_codes)

print("调池：build_rebalance 生成建议")
s = fresh_cn()
srv.fetch_kline = fake_kline
srv.refresh_prices = lambda st: None
srv.fetch_strength_candidates = lambda limit=40: [{
    "code": "sz300888", "name": "强势股", "industry": "半导体",
    "pct": 5.0, "amount": 5e8, "turn": 8.0, "pe": 20.0, "netIn": 3e8, "volRatio": 2.0,
}]
res = srv.build_rebalance(s)
pend = s["poolChanges"]
chk("生成建议返回 remove/add 计数", res["remove"] >= 1 and res["add"] == 1, str(res))
chk("淘汰建议含暴跌股", any(p["direction"] == "remove" and p["code"] == "sh600003" for p in pend))
adds = [p for p in pend if p["direction"] == "add"]
chk("选入建议含强势股", len(adds) == 1 and adds[0]["code"] == "sz300888", str(adds[:1]))
chk("选入建议状态 waiting", adds and adds[0]["state"] == "waiting")
chk("lastRebalance 已更新", s["lastRebalance"] == srv.today_str())

print("调池：execute_approved_pool_changes 落地")
s = fresh_cn()
srv.fetch_kline = fake_kline
srv.refresh_prices = lambda st: None
srv.fetch_strength_candidates = lambda limit=40: [{
    "code": "sz300888", "name": "强势股", "industry": "半导体",
    "pct": 5.0, "amount": 5e8, "turn": 8.0, "pe": 20.0, "netIn": 3e8, "volRatio": 2.0,
}]
srv.build_rebalance(s)
# 批准全部建议（3 淘汰 + 1 选入）
for p in s["poolChanges"]:
    p["state"] = "approved"
done = srv.execute_approved_pool_changes(s)
pool_codes = {p["code"] for p in s["pool"]}
chk("批准项被落地", done == 4)
chk("暴跌股已移出监控池", "sh600003" not in pool_codes)
chk("强势股已选入监控池", "sz300888" in pool_codes)
added = next(p for p in s["pool"] if p["code"] == "sz300888")
chk("选入股字段补全(alias/beta/tags)", added["alias"] and added["beta"] == 1.0 and "半导体" in added["tags"])
chk("落地后队列清空", len(s["poolChanges"]) == 0)
chk("台账留痕", len(s["poolChangeLog"]) >= 2)

print("调池：门控否决不落地")
s = fresh_cn()
srv.fetch_kline = fake_kline
srv.refresh_prices = lambda st: None
srv.fetch_strength_candidates = lambda limit=40: [{
    "code": "sz300888", "name": "强势股", "industry": "半导体",
    "pct": 5.0, "amount": 5e8, "turn": 8.0, "pe": 20.0, "netIn": 3e8, "volRatio": 2.0,
}]
srv.build_rebalance(s)
for p in s["poolChanges"]:
    p["state"] = "rejected"
srv.execute_approved_pool_changes(s)
chk("否决项不落地", all(p["code"] == "sh600003" or p["code"] == "sz300888" for p in s["pool"]) is False
    and "sz300888" not in {p["code"] for p in s["pool"]})

print("调池：冷却期内不重复建议")
s = fresh_cn()
srv.fetch_kline = fake_kline
srv.refresh_prices = lambda st: None
srv.fetch_strength_candidates = lambda limit=40: []
srv.build_rebalance(s)
# 模拟已落地并留痕
s["poolChangeLog"] = [{"code": "sh600003", "time": srv.now_str(), "direction": "remove"}]
s["poolChanges"] = []
s["lastRebalance"] = ""
srv.build_rebalance(s)
chk("冷却期内暴跌股不重复建议",
    not any(p["code"] == "sh600003" for p in s["poolChanges"]))

print("\n结果：%d 通过，%d 失败" % (PASS, FAIL))
raise SystemExit(1 if FAIL else 0)
