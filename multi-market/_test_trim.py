# -*- coding: utf-8 -*-
"""单票超配减仓（check_trims）与卖出股数下限（sell_floor_qty）单元测试。

对应 2026-11-18 B 类参数落地：
  B3 触发 22% / 减至 20% / 连续 3 个交易日 / 冷却 10 个交易日
  B4 单笔 ≥2×minSellAmount；残值 <1.5×minSellAmount 直接清仓
"""
import os
import sys

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


# 交易日可控：测试里手动推进（2026-11-18 周三 ~ 11-24 周二）
_box = {"d": "2026-11-18"}
server.today_str = lambda: _box["d"]
server.in_trading_hours = lambda now=None: True


def mk(acct, cash, price, shares, cost=None, pct=0.0, code="sh600001"):
    server.set_account(acct)
    s = server.default_state(acct)
    s["config"]["maxPerRound"] = 10
    s["config"]["maxPerDay"] = 100
    s["config"]["takeProfitPct"] = 8     # 与线上 config 一致（代码默认仍是 40）
    s["cash"] = cash
    h = {"name": "测试股", "code": code, "shares": shares, "price": price,
         "preClose": price, "cost": cost if cost is not None else price * 0.95,
         "pct": pct, "beta": 1.0}
    s["holdings"] = [h]
    return s, h


print("trim：权重 23%，连续 3 个交易日才触发")
s, h = mk("cn_aggressive", 77000, 23.0, 1000)      # 23000 / 100000 = 23%
chk("第 1 日不卖", server.check_trims(s) == 0 and h["shares"] == 1000)
chk("同日重复扫描不重复计数", server.check_trims(s) == 0 and s["trimStreak"]["sh600001"]["days"] == 1)
_box["d"] = "2026-11-19"
chk("第 2 日不卖", server.check_trims(s) == 0 and h["shares"] == 1000)
_box["d"] = "2026-11-20"
n = server.check_trims(s)
chk("第 3 日触发卖出", n == 1 and h["shares"] < 1000)
chk("减仓后权重落到 20%~22%", 20.0 <= (h["price"] * h["shares"]) / 100000 * 100 <= 22.0)
chk("写入 trimDate", h.get("trimDate") == "2026-11-20")
chk("成交记录含超配减仓", any("超配减仓" in t["reason"] for t in s["trades"]))

print("trim：权重 21%（未到 22% 触发线）连续 5 日均不卖")
_box["d"] = "2026-11-18"
s, h = mk("cn_aggressive", 79000, 21.0, 1000)      # 21%
tot = 0
for d in ("2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24"):
    _box["d"] = d
    tot += server.check_trims(s)
chk("21% 不触发", tot == 0 and h["shares"] == 1000)

print("trim：周末不断链（周五→周一仍算连续）")
_box["d"] = "2026-11-18"
s, h = mk("cn_aggressive", 77000, 23.0, 1000)
for d in ("2026-11-18", "2026-11-19", "2026-11-20"):
    _box["d"] = d
    server.check_trims(s)
chk("第 3 日（周五）已卖出", h["shares"] < 1000)

print("trim：豁免与顺延")
_box["d"] = "2026-11-18"
s, h = mk("cn_aggressive", 77000, 23.0, 1000, pct=6.0)   # 当日涨 6% ≥5%
for d in ("2026-11-18", "2026-11-19", "2026-11-20"):
    _box["d"] = d
    server.check_trims(s)
chk("当日涨≥5% 豁免", h["shares"] == 1000)

s, h = mk("cn_aggressive", 77000, 23.0, 1000, pct=-4.0)  # 当日跌 4%
_msgs = []
_old_log = server.sim_account.log
server.sim_account.log = lambda m: _msgs.append(str(m))
for d in ("2026-11-18", "2026-11-19", "2026-11-20"):
    _box["d"] = d
    server.check_trims(s)
server.sim_account.log = _old_log
chk("当日跌≥3% 顺延不砸盘", h["shares"] == 1000)
chk("顺延写日志", any("顺延" in m for m in _msgs))

s, h = mk("cn_aggressive", 77000, 23.0, 1000, cost=20.0)  # 浮盈 15% ≥ 止盈线 8%
for d in ("2026-11-18", "2026-11-19", "2026-11-20"):
    _box["d"] = d
    server.check_trims(s)
chk("已进止盈区交给 check_stops", h["shares"] == 1000)

print("trim：冷却期内不再减仓")
_box["d"] = "2026-11-24"
s, h = mk("cn_aggressive", 77000, 23.0, 1000)
h["trimDate"] = "2026-11-18"                      # 3 个交易日前减过
s["trimStreak"] = {"sh600001": {"days": 3, "last": "2026-11-20"}}
chk("10 个交易日冷却生效", server.check_trims(s) == 0 and h["shares"] == 1000)

print("trim：总开关 / 非交易时段")
s, h = mk("cn_aggressive", 77000, 23.0, 1000)
s["config"]["useTrim"] = False
s["trimStreak"] = {"sh600001": {"days": 3, "last": "2026-11-20"}}
chk("useTrim=false 一键回退", server.check_trims(s) == 0)
s["config"]["useTrim"] = True
_old = server.in_trading_hours
server.in_trading_hours = lambda now=None: False
chk("非交易时段不执行", server.check_trims(s) == 0)
server.in_trading_hours = _old

print("trim：美股按 1 股为单位（不整手）")
_box["d"] = "2026-11-18"
s, h = mk("us", 77000, 500.0, 46, code="usTEST")   # 23000 / 100000 = 23%
for d in ("2026-11-18", "2026-11-19", "2026-11-20"):
    _box["d"] = d
    server.check_trims(s)
chk("美股减仓成交", h["shares"] < 46)
chk("美股减至约 20%", abs((h["price"] * h["shares"]) / 100000 * 100 - 20) < 2.5)

print("sell_floor_qty：单笔下限 2×minSellAmount")
server.set_account("cn_aggressive")
s, _ = mk("cn_aggressive", 77000, 23.0, 1000)
chk("2300 元已达下限 -> 原样", server.sell_floor_qty(s, 23.0, 1000, 100) == 100)
chk("100 股×9 元=900 <2000 -> 抬到整手", server.sell_floor_qty(s, 9.0, 1000, 100) == 300)
chk("股数不足时抬到全部", server.sell_floor_qty(s, 9.0, 100, 50) == 100)

print("sell_floor_qty：残值 <1.5×minSellAmount 直接清仓")
s, _ = mk("cn_aggressive", 77000, 10.0, 1000)
chk("卖 900 剩 100 股(1000 元) -> 清仓", server.sell_floor_qty(s, 10.0, 1000, 900) == 1000)
chk("卖 800 剩 200 股(2000 元) -> 保留", server.sell_floor_qty(s, 10.0, 1000, 800) == 800)

print("sell_floor_qty：美股下限（minSellAmount=300）")
s, _ = mk("us", 77000, 470.0, 19, code="usTEST2")
chk("卖 3 股=1410 美元 -> 原样", server.sell_floor_qty(s, 470.0, 19, 3) == 3)
chk("卖后残值 16 股=7520 -> 不清", server.sell_floor_qty(s, 470.0, 19, 3) == 3)
s, _ = mk("us", 77000, 100.0, 4, code="usTEST4")
chk("残值过小时清仓", server.sell_floor_qty(s, 100.0, 4, 2) == 4)   # 卖 2 剩 2 股=200 <450
print("sell_floor_qty：不足 2×minSellAmount 时抬量")
s, _ = mk("us", 77000, 100.0, 20, code="usTEST3")
chk("卖 2 股=200 <600 -> 抬到 6 股", server.sell_floor_qty(s, 100.0, 20, 2) == 6)

print("\n%d passed, %d failed" % (_passed, _failed))
sys.exit(1 if _failed else 0)
