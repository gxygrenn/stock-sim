# -*- coding: utf-8 -*-
"""2026-11-14 止损线收紧回归测试：
- us/cnc stopLossPct -8→-6、reReviewPct -5→-4
- cna stopLossPct -15→-8、reReviewPct -8→-5
验证默认值 + check_stops 行为 + 加仓否决线联动。"""
import os, sys
os.environ["NO_WORKER"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

_p = _f = 0


def chk(name, cond):
    global _p, _f
    if cond:
        _p += 1
        print("  PASS  %s" % name)
    else:
        _f += 1
        print("  FAIL  %s" % name)


print("默认值：三账户止损/复核线（2026-11-14 拍板）")
exp = {"us": (-6, -4), "cn_conservative": (-6, -4), "cn_aggressive": (-8, -5)}
for acc, (sl, rr) in exp.items():
    server.set_account(acc)
    cfg = server.default_state(acc)["config"]
    chk("%s stopLossPct=%d" % (acc, sl), cfg["stopLossPct"] == sl)
    chk("%s reReviewPct=%d" % (acc, rr), cfg["reReviewPct"] == rr)

server.in_trading_hours = lambda now=None: True


def mk_state(acc, cost, price, shares=100):
    server.set_account(acc)
    s = server.default_state(acc)
    s["config"]["maxPerRound"] = 10
    s["config"]["maxPerDay"] = 100
    s["holdings"] = [{"name": "测试股", "code": "sh600000", "shares": shares,
                      "price": price, "cost": cost, "pct": 0.0}]
    s["cash"] = 100000.0
    return s, s["holdings"][0]


print("check_stops：cnc 浮亏 -6.5% 触及新线 -6（旧线 -8 下不会卖）")
s, h = mk_state("cn_conservative", 100.0, 93.5)
chk("触发硬止损清仓", server.check_stops(s) == 1 and h["shares"] == 0)

print("check_stops：cnc 浮亏 -5.2% 未及 -6 线 -> 不卖")
s, h = mk_state("cn_conservative", 100.0, 94.8)
chk("不触发", server.check_stops(s) == 0 and h["shares"] == 100)

print("check_stops：cna 浮亏 -7.5% 未及激进档 -8 线 -> 不卖")
s, h = mk_state("cn_aggressive", 100.0, 92.5)
chk("不触发（激进档保持宽 2pp）", server.check_stops(s) == 0)

print("check_stops：cna 浮亏 -9% 触及 -8 线 -> 清仓（旧线 -15 下会扛到 -15）")
s, h = mk_state("cn_aggressive", 100.0, 91.0)
chk("触发硬止损清仓", server.check_stops(s) == 1 and h["shares"] == 0)

print("加仓否决联动：cnc 持仓浮亏 -6.5% 已过禁加仓线 -> 初审否决")
s, h = mk_state("cn_conservative", 100.0, 93.5)
veto = server._risk_check(s, {"code": "sh600000", "name": "测试股"},
                           "sh600000", "买入", 93.5)
chk("初审被否决", isinstance(veto, str) and "禁止加仓" in veto)

print("汇总: %d 通过, %d 失败" % (_p, _f))
sys.exit(1 if _f else 0)
