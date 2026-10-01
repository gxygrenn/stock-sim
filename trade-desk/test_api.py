#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易工作台 API 回归测试。
A 部分：纯函数（不依赖网络/当天行情，结果可精确断言）
B 部分：HTTP API（需要服务已在 127.0.0.1:8123 运行）
"""
import json
import os
import sys
import urllib.request

BASE = "http://127.0.0.1:8123"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, detail))


def req(path, body=None, method=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


# ============ A. 纯函数 ============
print("== A. 纯函数（确定性断言） ==")
q = {"code": "sh600519", "name": "贵州茅台", "price": 100.0, "prevClose": 95.0,
     "changePct": 5.26, "market": "A股"}

check("above 命中", server.evaluate_alert({"type": "above", "value": 99}, q) == (True, server.evaluate_alert({"type": "above", "value": 99}, q)[1]))
check("above 未命中", server.evaluate_alert({"type": "above", "value": 101}, q)[0] is False)
check("below 命中", server.evaluate_alert({"type": "below", "value": 101}, q)[0] is True)
check("below 未命中", server.evaluate_alert({"type": "below", "value": 99}, q)[0] is False)
check("pct_up 命中", server.evaluate_alert({"type": "pct_up", "value": 5.0}, q)[0] is True)
check("pct_down 命中", server.evaluate_alert({"type": "pct_down", "value": -1}, q)[0] is False)
check("行情不可用不触发", server.evaluate_alert({"type": "above", "value": 1}, {"unavailable": "x"})[0] is False)
check("无效阈值不触发", server.evaluate_alert({"type": "above", "value": None}, q)[0] is False)

# 持仓计算（买入摊薄成本、卖出扣减、清零）
trades = [
    {"side": "buy", "code": "sh600519", "name": "贵州茅台", "price": 10.0, "qty": 100, "date": "2026-01-01"},
    {"side": "buy", "code": "sh600519", "name": "贵州茅台", "price": 20.0, "qty": 100, "date": "2026-01-02"},
    {"side": "sell", "code": "sh600519", "name": "贵州茅台", "price": 25.0, "qty": 150, "date": "2026-01-03"},
]
r = server.compute_positions(trades)
p = r["positions"][0]
check("持仓数量 = 50", abs(p["qty"] - 50) < 1e-9, "got %s" % p["qty"])
check("移动平均成本 = 15", abs(p["cost"] - 15.0) < 1e-9, "got %s" % p["cost"])
check("市值 = 盈亏 + 成本市值",
      abs((p["value"] - p["pnl"]) - p["cost"] * p["qty"]) < 0.11, str(p))
check("总市值 = 各持仓市值和", abs(r["totalValue"] - sum(x["value"] for x in r["positions"])) < 0.01)
# 全部卖出 -> 持仓清零
r2 = server.compute_positions(trades + [{"side": "sell", "code": "sh600519", "name": "x", "price": 25, "qty": 50, "date": "2026-01-04"}])
check("清仓后无持仓", len(r2["positions"]) == 0)

# 回测：先跌后涨应产生金叉买入并盈利
kline_up = []
for i in range(30):   # 前段下跌
    kline_up.append({"date": "d%02d" % i, "open": 20 - i * 0.1, "close": 20 - i * 0.2,
                     "high": 20.4 - i * 0.2, "low": 19.7 - i * 0.2, "volume": 100})
for i in range(30):   # 后段上涨
    kline_up.append({"date": "e%02d" % i, "open": 14 + i * 0.1, "close": 14 + i * 0.2,
                     "high": 14.5 + i * 0.2, "low": 13.8 + i * 0.2, "volume": 100})
closes_up = [b["close"] for b in kline_up]
sig = server.ma_cross_signals(closes_up, 5, 10)
bt = server.run_backtest(kline_up, sig)
check("上涨行情期末资产为正", bt["stats"]["finalEquity"] > 0)
check("equity 与 K线等长", len(bt["equity"]) == len(kline_up))
check("上涨行情收益为正", bt["stats"]["totalReturnPct"] > 0, str(bt["stats"]))
# 信号合法性：信号只在均线就绪后出现
check("信号不超前于长均线就绪", all(s == 0 for s in sig[:9]))
check("下穿必在上穿之后", sig.count(-1) <= sig.count(1))

# 每日复盘：经验洞察纯函数
print("== A2. 复盘洞察（确定性断言） ==")
reviews = [
    {"date": "2026-09-01", "tags": ["追高", "满仓"], "pnl": -1500},
    {"date": "2026-09-02", "tags": ["追高"], "pnl": -800},
    {"date": "2026-09-03", "tags": ["止损", "纪律好"], "pnl": -200},
    {"date": "2026-09-04", "tags": ["拿住", "纪律好"], "pnl": 1200},
    {"date": "2026-09-05", "tags": ["拿住"], "pnl": 900},
]
ins = server.compute_insights(reviews)
check("洞察统计复盘篇数", ins["reviewCount"] == 5)
check("最差模式=追高(两次均值-1150)", ins["worstTag"] and ins["worstTag"]["tag"] == "追高"
      and ins["worstTag"]["avgPnl"] == -1150.0, str(ins["worstTag"]))
check("最佳模式=拿住", ins["bestTag"] and ins["bestTag"]["tag"] == "拿住", str(ins["bestTag"]))
check("单次标签不算模式", server.compute_insights(
    [{"date": "d", "tags": ["抄底"], "pnl": 999}])["worstTag"] is None)
check("空复盘不报错", server.compute_insights([])["reviewCount"] == 0)

# ============ B. HTTP API ============
print("== B. HTTP API ==")
st, d = req("/api/health")
check("health 200", st == 200 and d.get("ok") is True)

# 清空自选
st, d = req("/api/watchlist", {"action": "remove", "code": "sh600519"}, "POST")
st, d = req("/api/watchlist")
check("初始自选为空", d.get("codes") == [])

st, d = req("/api/watchlist", {"action": "add", "code": "abc"}, "POST")
check("非法代码返回400", st == 400, str(d))
st, d = req("/api/watchlist", {"action": "add", "code": "sh600519"}, "POST")
check("添加A股自选", st == 200 and "sh600519" in d.get("codes", []))
st, d = req("/api/watchlist", {"action": "add", "code": "sh600519"}, "POST")
check("重复添加不报错", st == 200 and d.get("codes", []).count("sh600519") == 1)
check("自选带行情快照", st == 200 and len(d.get("quotes", [])) == 1
      and ("price" in d["quotes"][0] or "unavailable" in d["quotes"][0]))

st, d = req("/api/quote?codes=")
check("quote 缺参返回400", st == 400)
st, d = req("/api/quote?codes=sh600519,nf_RB0")
check("quote 多市场批量", st == 200 and len(d.get("quotes", [])) == 2)

st, d = req("/api/alerts", {"code": "sh600519"}, "POST")
check("提醒缺参返回400", st == 400)
st, d = req("/api/alerts", {"code": "sh600519", "type": "above", "value": 9999999}, "POST")
check("创建提醒(不可能触发的阈值)", st == 200 and d.get("alert", {}).get("triggered") is False)
aid = d["alert"]["id"]
st, d = req("/api/check_alerts")
check("检查提醒返回结构", st == 200 and isinstance(d.get("results"), list)
      and all("hit" in r and "detail" in r for r in d["results"]))
row = [r for r in d["results"] if r.get("id") == aid]
check("不可能触发的阈值保持监控中", row and row[0]["hit"] is False and row[0]["triggered"] is False)
st, d = req("/api/alerts", {"action": "delete", "id": aid}, "POST")
check("删除提醒", st == 200 and all(a["id"] != aid for a in d.get("alerts", [])))

# 交易流水
st, d = req("/api/trades", {"side": "buy", "code": "sh600519"}, "POST")
check("交易缺参返回400", st == 400)
st, d = req("/api/trades", {"side": "buy", "code": "sh600519", "price": 10, "qty": 100, "date": "2026-01-01"}, "POST")
check("买入成功", st == 200 and d.get("trade", {}).get("amount") == 1000.0)
st, d = req("/api/positions")
check("持仓 100 股", any(abs(p["qty"] - 100) < 1e-9 for p in d.get("positions", [])))
st, d = req("/api/trades", {"side": "sell", "code": "sh600519", "price": 12, "qty": 100, "date": "2026-01-02"}, "POST")
check("卖出成功", st == 200)
st, d = req("/api/positions")
check("清仓后无持仓", d.get("positions") == [] and d.get("totalValue") == 0)
# 删除全部流水 -> 总资产精确还原为 0
st, d = req("/api/trades")
for t in list(d.get("trades", [])):
    st, d = req("/api/trades", {"action": "delete", "id": t["id"]}, "POST")
check("删除流水后总资产归零", d.get("totalValue") == 0 and d.get("positions") == [])

# 每日复盘 API
st, d = req("/api/reviews", {"mood": "中性"}, "POST")
check("复盘缺日期返回400", st == 400)
st, d = req("/api/reviews", {"date": "2026-09-13", "mood": "谨慎", "tags": ["止损", "纪律好"],
                             "note": "按计划执行", "pnl": -200}, "POST")
check("保存复盘", st == 200 and d.get("review", {}).get("date") == "2026-09-13")
st, d = req("/api/reviews", {"date": "2026-09-13", "mood": "乐观", "tags": ["拿住"], "note": "覆盖测试", "pnl": 500}, "POST")
check("同日复盘覆盖为一条", st == 200 and len([r for r in d["reviews"] if r["date"] == "2026-09-13"]) == 1
      and d["reviews"][0]["mood"] == "乐观")
st, d = req("/api/reviews", {"date": "2026-09-12", "tags": ["追高"], "pnl": -800}, "POST")
st, d = req("/api/reviews")
check("复盘列表与洞察同返", st == 200 and {"reviews", "insights"} <= set(d.keys())
      and d["insights"]["reviewCount"] == 2)
st, d = req("/api/trades", {"side": "buy", "code": "sh600519", "price": 10, "qty": 100, "date": "2026-09-13 10:00"}, "POST")
st, d = req("/api/review_digest?date=2026-09-13")
check("复盘底稿含当日交易", st == 200 and d.get("tradeCount") == 1 and len(d.get("quotes", [])) >= 1)
st, d = req("/api/insights")
check("洞察接口独立可用", st == 200 and "lessons" in d)
st, d = req("/api/reviews")
for r in list(d.get("reviews", [])):
    st, d = req("/api/reviews", {"action": "delete", "id": r["id"]}, "POST")
st, d = req("/api/trades")
for t in list(d.get("trades", [])):
    st, d = req("/api/trades", {"action": "delete", "id": t["id"]}, "POST")
check("复盘清理完成", d.get("totalValue") == 0)

# 回测
st, d = req("/api/backtest?code=")
check("回测缺参返回400", st == 400)
st, d = req("/api/backtest?code=sh600519&strategy=ma_cross&short=5&long=20&n=120")
check("回测结构完整", st == 200 and {"kline", "signals", "equity", "stats"} <= set(d.keys()))
if st == 200:
    check("回测各序列等长", len(d["kline"]) == len(d["signals"]) == len(d["equity"]))
    check("回测统计字段齐全", {"totalReturnPct", "maxDrawdownPct", "tradeCount", "finalEquity"} <= set(d["stats"].keys()))
    check("期末资产为正", d["stats"]["finalEquity"] > 0)
st, d = req("/api/backtest?code=sh600519&strategy=ma_breakout&period=20&n=120")
check("均线突破策略可用", st == 200 and d.get("strategy") == "ma_breakout")

st, d = req("/api/nonexist")
check("未知路由404", st == 404)

print("\n结果: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
