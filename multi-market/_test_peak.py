# -*- coding: utf-8 -*-
"""买入后高点（holdings[].peak）在刷价时就地更新 —— 2026-11-25。

背景两层：
1) peak 原先只在 check_stops 里更新，遇到「刷价了但 check_stops 被跳过」
   （如美股非交易时段、当日笔数额度用尽）就会停在旧值。塔吉特 11-25 出现
   峰值 170.26 < 现价 176.06 的倒挂，界面上「买入后高点」比现价还低，
   移动止盈回撤线也被算到成本下方。
2) 第二版改用行情「今日最高」p[33] 抬升，覆盖盘中插针（旧实现只取每轮刷价
   的现价，日内冲高的高点会漏记）。边界：本场次内新建仓的标的不能用今日最高
   （高点可能发生在买入前），且清仓残影不参与。

本测试锁定：peak ≥ price、只升不降、插针能捕获、当日建仓不吃买入前高点、
新建仓重置峰值、high 缺失不误归零。
"""
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


def mk_state(peak=None, shares=100, price=100.0, buy_sess=None):
    server.set_account("cn_conservative")
    s = server.default_state("cn_conservative")
    s["priceMode"] = "real"
    h = {"name": "测试股", "code": "sh600000", "shares": shares,
         "price": price, "preClose": price, "cost": 90.0, "pct": 0.0}
    if peak is not None:
        h["peak"] = peak
    if buy_sess is not None:
        h["buySess"] = buy_sess
    s["holdings"] = [h]
    s["cash"] = 100000
    return s, h


def stub_quote(px, high=None, ts="2026-11-25 15:00"):
    """high 缺省＝现价（模拟「当前就是日内最高」）。"""
    hi = px if high is None else high
    server.fetch_quote = lambda code, **kw: {"name": "上证", "code": code, "price": 3000.0,
                                             "preClose": 3000.0, "chg": 0, "pct": 0.0,
                                             "high": 3010.0, "low": 2990.0,
                                             "amount": 0, "time": ts}
    server.fetch_prices = lambda codes: {c: {"name": "测试股", "code": c, "price": px,
                                             "preClose": px, "chg": 0, "pct": 0.0,
                                             "high": hi, "low": px, "amount": 0,
                                             "time": ts} for c in codes}


print("peak：刷价创新高 -> peak 抬到现价")
s, h = mk_state(peak=95.0)
stub_quote(101.5)
server.refresh_prices(s)
chk("现价已刷新", abs(h["price"] - 101.5) < 1e-9)
chk("peak 抬到 101.5", abs(h.get("peak") - 101.5) < 1e-9)

print("peak：刷价回落 -> peak 保持不动（只升不降）")
stub_quote(98.0)
server.refresh_prices(s)
chk("peak 仍 101.5", abs(h.get("peak") - 101.5) < 1e-9)

print("peak：原先缺 peak（老存档）-> 首次刷价即建立")
s, h = mk_state(peak=None, price=99.0)
stub_quote(99.0)
server.refresh_prices(s)
chk("peak 建立为 99.0", abs(h.get("peak") - 99.0) < 1e-9)

print("peak：清仓后重置，重新建仓从新建高点开始")
s, h = mk_state(peak=120.0, shares=0, price=110.0)
chk("清仓残影不参与（shares=0）", h["shares"] == 0)
stub_quote(110.0)
server.refresh_prices(s)
chk("shares=0 时峰值不被改写", abs(h.get("peak") - 120.0) < 1e-9)

print("peak：日内插针 -> 现价 99 但今日最高 103，peak 取 103")
s, h = mk_state(peak=95.0, price=99.0)
stub_quote(99.0, high=103.0)
server.refresh_prices(s)
chk("peak 捕获日内最高 103", abs(h.get("peak") - 103.0) < 1e-9)
chk("现价仍为 99", abs(h["price"] - 99.0) < 1e-9)

print("peak：本场次内建仓 -> 不吃买入前的今日最高")
s, h = mk_state(peak=None, price=99.0, buy_sess="2026-11-25")
stub_quote(99.0, high=103.0, ts="2026-11-25 15:00")
server.refresh_prices(s)
chk("peak 只到现价 99", abs(h.get("peak") - 99.0) < 1e-9)

print("peak：跨场次持仓（buySess 为昨日）-> 今日最高可用")
s, h = mk_state(peak=None, price=99.0, buy_sess="2026-11-24")
stub_quote(99.0, high=103.0, ts="2026-11-25 15:00")
server.refresh_prices(s)
chk("peak 取 103", abs(h.get("peak") - 103.0) < 1e-9)

print("peak：行情不给 high（字段缺失）-> 不把峰值归零")
s, h = mk_state(peak=95.0, price=99.0, buy_sess="2026-11-24")
server.fetch_quote = lambda code, **kw: {"name": "上证", "code": code, "price": 3000.0,
                                         "preClose": 3000.0, "chg": 0, "pct": 0.0,
                                         "amount": 0, "time": "2026-11-25 15:00"}
server.fetch_prices = lambda codes: {c: {"name": "测试股", "code": c, "price": 99.0,
                                         "preClose": 99.0, "chg": 0, "pct": 0.0,
                                         "amount": 0, "time": "2026-11-25 15:00"} for c in codes}
server.refresh_prices(s)
chk("peak 抬到现价 99，未被 high=0 覆盖", abs(h.get("peak") - 99.0) < 1e-9)

print("do_buy：新建仓重置 peak 并记建仓场次")
s, h = mk_state(peak=140.0, shares=0, price=100.0)
h["cost"] = 0.0
s["quote"] = {"time": "2026-11-25 15:00"}
s["config"]["minBuyAmount"] = 0
tr = server.do_buy(s, "测试股", "sh600000", 100.0, 100, "单测建仓")
chk("买单成立", bool(tr))
chk("peak 重置为买入价 100", abs(h.get("peak") - 100.0) < 1e-9)
chk("buySess 记为建仓场次", h.get("buySess") == "2026-11-25")

print("do_buy：加仓不重置已有峰值")
h["peak"] = 110.0
tr = server.do_buy(s, "测试股", "sh600000", 105.0, 10, "单测加仓")
chk("加仓后 peak 仍 110", abs(h.get("peak") - 110.0) < 1e-9)

print("\n结果：%d 通过，%d 失败" % (_passed, _failed))
sys.exit(0 if _failed == 0 else 1)
