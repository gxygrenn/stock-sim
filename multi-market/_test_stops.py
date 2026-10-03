# -*- coding: utf-8 -*-
"""持仓级止损/止盈纪律（check_stops）单元测试。"""
import os, sys
os.environ["NO_WORKER"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

_passed = 0
_failed = 0


def chk(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print("  PASS  %s" % name)
    else:
        _failed += 1
        print("  FAIL  %s" % name)


def mk_state(cost, price, peak=None, shares=100):
    server.set_account("cn_conservative")
    s = server.default_state("cn_conservative")
    s["config"]["useStops"] = True
    s["config"]["stopLossPct"] = -8
    s["config"]["trailPct"] = 15
    s["config"]["takeProfitPct"] = 30
    s["config"]["maxPerRound"] = 10
    s["config"]["maxPerDay"] = 100
    h = {"name": "测试股", "code": "sh600000", "shares": shares,
         "price": price, "cost": cost, "pct": 0.0}
    if peak is not None:
        h["peak"] = peak
    s["holdings"] = [h]
    s["cash"] = 100000
    return s, h


# 让 in_trading_hours 恒为真，避免测试依赖真实时钟
server.in_trading_hours = lambda now=None: True

print("stops：固定止损（浮亏 -10% 触及 -8% 线）")
s, h = mk_state(cost=100.0, price=90.0)
n = server.check_stops(s)
chk("触发止损卖出", n == 1)
chk("持仓清空", h["shares"] == 0)
chk("成交记录写入", any("止损" in t["reason"] for t in s["trades"]))

print("stops：浮亏未触及止损线 -> 不卖")
s, h = mk_state(cost=100.0, price=95.0)  # -5%
n = server.check_stops(s)
chk("不触发", n == 0)
chk("持仓不变", h["shares"] == 100)

print("stops：移动止盈（从峰值回撤 18% 触及 15% 线，仍浮盈）")
# 峰值 150，现价 123（回撤 18%），成本 100（仍浮盈 23%）
s, h = mk_state(cost=100.0, price=123.0, peak=150.0)
n = server.check_stops(s)
chk("触发移动止盈", n == 1)
chk("清仓", h["shares"] == 0)
chk("理由含移动止盈", any("移动止盈" in t["reason"] for t in s["trades"]))

print("stops：回撤未触及移动止盈线 -> 不卖")
# 成本 100、现价 112（浮盈 12%，未到 30% 目标）、峰值 120（回撤 6.7%，未到 15%）
s, h = mk_state(cost=100.0, price=112.0, peak=120.0)
n = server.check_stops(s)
chk("不触发", n == 0)
chk("持仓不变", h["shares"] == 100)

print("stops：目标止盈（浮盈 35% 触及 30% 目标，分批卖一半）")
s, h = mk_state(cost=100.0, price=135.0)
n = server.check_stops(s)
chk("触发目标止盈", n == 1)
chk("卖一半（剩 50 股）", h["shares"] == 50)
chk("理由含目标止盈", any("目标止盈" in t["reason"] for t in s["trades"]))

print("stops：峰值自动更新（现价创历史新高）")
s, h = mk_state(cost=100.0, price=120.0)  # 无显式 peak
server.check_stops(s)
chk("peak 被更新为现价", h.get("peak") == 120.0)

print("stops：关闭时不执行")
s, h = mk_state(cost=100.0, price=90.0)
s["config"]["useStops"] = False
n = server.check_stops(s)
chk("关闭时不卖", n == 0)
chk("持仓不变", h["shares"] == 100)

print("\n结果：%d 通过，%d 失败" % (_passed, _failed))
sys.exit(1 if _failed else 0)
