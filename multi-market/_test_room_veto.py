# -*- coding: utf-8 -*-
"""单票剩余额度不足一手 → 入队初审即否决（2026-11-10 T26 修复）。

背景：us 礼来 11-10 单日 4 轮 BUY 全部批准、0 股成交——权重 19.0%，
剩余单票额度 919 元 < 股价 1412，执行端 qty=int(919/1412)=0 每轮弃单，
但初审不拦，评审团照批。
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


def mk_state(hold_shares, hold_price, cash):
    server.set_account("cn_conservative")
    s = server.default_state("cn_conservative")
    s["config"]["maxPosPct"] = 20
    s["config"]["maxHold"] = 8
    s["holdings"] = [{"name": "测试股", "code": "sh600000", "shares": hold_shares,
                      "price": hold_price, "cost": hold_price * 0.9, "pct": 0.0}]
    s["cash"] = cash
    return s


item = {"name": "测试股", "code": "sh600000"}

print("room：剩余额度不足一手 → 初审即否决（advisory，模拟入队初审）")
# 总资产 100,000；持仓市值 19,000（19%）；剩余额度 = 20,000-19,000 = 1,000 < 现价 1,500
s = mk_state(950, 20.0, 81000.0)
v = server._risk_check(s, item, "sh600000", "买入", 1500.0, advisory=True)
chk("返回否决", bool(v))
chk("理由含「不足一手」", "不足一手" in v)
print("     （否决理由：%s）" % v)

print("room：剩余额度够一手 → 放行")
# 同上但现价 900 ≤ 剩余额度 1,000
s = mk_state(950, 20.0, 81000.0)
v = server._risk_check(s, item, "sh600000", "买入", 900.0, advisory=True)
chk("放行（空串）", v == "")

print("room：非加仓（未持仓新票）不受该条约束")
s = mk_state(0, 20.0, 100000.0)
s["holdings"] = []
v = server._risk_check(s, item, "sh600000", "买入", 1500.0, advisory=True)
chk("新票不受 room 约束（走到别的检查或放行）", "不足一手" not in v)

print("room：执行端（非 advisory）同样拦截")
s = mk_state(950, 20.0, 81000.0)
v = server._risk_check(s, item, "sh600000", "买入", 1500.0, advisory=False)
chk("执行端也否决", "不足一手" in v)

print("room：卖出方向不受该条约束")
s = mk_state(950, 20.0, 81000.0)
v = server._risk_check(s, item, "sh600000", "卖出", 1500.0, advisory=True)
chk("卖出不触发 room 否决", "不足一手" not in (v or ""))

print("\n结果：%d 通过，%d 失败" % (_passed, _failed))
sys.exit(1 if _failed else 0)
