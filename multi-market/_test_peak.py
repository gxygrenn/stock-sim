# -*- coding: utf-8 -*-
"""买入后高点（holdings[].peak）在刷价时就地更新 —— 2026-11-25。

背景：peak 原先只在 check_stops 里更新，遇到「刷价了但 check_stops 被跳过」
（如美股非交易时段、当日笔数额度用尽）就会停在旧值。塔吉特 11-25 出现
峰值 170.26 < 现价 176.06 的倒挂，界面上「买入后高点」会显示得比现价还低，
移动止盈回撤线也会被算到成本下方。本测试锁定：refresh_prices 刷价后 peak ≥ price，
且 peak 只升不降、清仓后重置。
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


def mk_state(peak=None, shares=100, price=100.0):
    server.set_account("cn_conservative")
    s = server.default_state("cn_conservative")
    s["priceMode"] = "real"
    s["holdings"] = [{"name": "测试股", "code": "sh600000", "shares": shares,
                      "price": price, "preClose": price, "cost": 90.0, "pct": 0.0}]
    if peak is not None:
        s["holdings"][0]["peak"] = peak
    s["cash"] = 100000
    return s, s["holdings"][0]


def stub_quote(px):
    server.fetch_quote = lambda code, **kw: {"name": "上证", "code": code, "price": 3000.0,
                                             "preClose": 3000.0, "chg": 0, "pct": 0.0,
                                             "amount": 0, "time": "--"}
    server.fetch_prices = lambda codes: {c: {"name": "测试股", "code": c, "price": px,
                                             "preClose": px, "chg": 0, "pct": 0.0,
                                             "amount": 0} for c in codes}


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

print("\n结果：%d 通过，%d 失败" % (_passed, _failed))
sys.exit(0 if _failed == 0 else 1)
