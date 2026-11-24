# -*- coding: utf-8 -*-
"""2026-11-24 新增收盘前补扫 + 止盈全清回归测试。

背景：扫描时点在每小时 :40，A 股 14:40→15:00 有 20 分钟盲区。建设银行 11-20 收盘
+8.75%、盘中最高 +8.92%，tpDate 为空、一股未卖；11-23 盘中 +8.41% 再次漏掉。
改动：
  1) cn_close_sweep_window + close_sweep：14:55 一次纯代码 check_stops
  2) takeProfitSellFrac：0.5=卖一半（旧行为），1.0=全清（B 档）
"""
import os, sys
from datetime import datetime
os.environ["NO_WORKER"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

_p = _f = 0


def chk(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1
        print("  PASS  %s" % name)
    else:
        _f += 1
        print("  FAIL  %s %s" % (name, extra))


def mk_state(acc, cost, price, shares=1000, **cfg_over):
    server.set_account(acc)
    s = server.default_state(acc)
    s["config"]["maxPerRound"] = 10
    s["config"]["maxPerDay"] = 100
    s["config"].update(cfg_over)
    s["holdings"] = [{"name": "测试股", "code": "sh600000", "shares": shares,
                      "price": price, "cost": cost, "pct": 0.0}]
    s["cash"] = 100000.0
    s["priceMode"] = "real"
    return s, s["holdings"][0]


print("一、补扫时间窗（14:40-14:59，仅 A 股交易日）")
mon = datetime(2026, 11, 24)      # 周二
chk("14:39 不触发", not server.cn_close_sweep_window(mon.replace(hour=14, minute=39)))
chk("14:40 触发", server.cn_close_sweep_window(mon.replace(hour=14, minute=40)))
chk("14:50 触发", server.cn_close_sweep_window(mon.replace(hour=14, minute=50)))
chk("14:55 触发", server.cn_close_sweep_window(mon.replace(hour=14, minute=55)))
chk("14:59 触发", server.cn_close_sweep_window(mon.replace(hour=14, minute=59)))
chk("15:00 不触发（已收盘）", not server.cn_close_sweep_window(mon.replace(hour=15, minute=0)))
sat = datetime(2026, 11, 28).replace(hour=14, minute=55)
chk("周六不触发", not server.cn_close_sweep_window(sat))

print("二、close_sweep 用盘中价触发止盈（全清）")
server.in_trading_hours = lambda now=None: True
server.fetch_prices = lambda codes: {c: {"price": 108.0, "preClose": 100.0, "pct": 8.0}
                                     for c in codes}
s, h = mk_state("cn_conservative", 100.0, 100.0, takeProfitPct=8, takeProfitSellFrac=1.0)
s["roundTrades"] = 99          # 14:40 那轮已用满额度，补扫要独立计轮
r = server.close_sweep(s)
chk("补扫执行且卖出 1 笔", r["ran"] and r["sold"] == 1, str(r))
chk("全清 1000 股", h["shares"] == 0, "shares=%s" % h["shares"])
chk("现价被补扫刷新为 108", h["price"] == 108.0, "price=%s" % h["price"])
chk("roundTrades 从 99 归零后只计本笔=1（不被 14:40 额度挡住）",
    s["roundTrades"] == 1, "roundTrades=%s" % s["roundTrades"])

print("三、takeProfitSellFrac=0.5 保持旧行为（卖一半）")
s, h = mk_state("cn_conservative", 100.0, 100.0, takeProfitPct=8, takeProfitSellFrac=0.5)
r = server.close_sweep(s)
chk("卖一半 500 股", r["sold"] == 1 and h["shares"] == 500, "shares=%s" % h["shares"])

print("四、未到止盈线不卖")
s, h = mk_state("cn_conservative", 100.0, 100.0, takeProfitPct=8, takeProfitSellFrac=1.0)
server.fetch_prices = lambda codes: {c: {"price": 105.0, "preClose": 100.0, "pct": 5.0}
                                     for c in codes}
r = server.close_sweep(s)
chk("+5% 不触发", r["sold"] == 0 and h["shares"] == 1000, str(r))

print("五、开关与前置条件")
s, h = mk_state("cn_conservative", 100.0, 100.0, takeProfitPct=8,
                takeProfitSellFrac=1.0, useCloseSweep=False)
server.fetch_prices = lambda codes: {c: {"price": 108.0, "preClose": 100.0, "pct": 8.0}
                                     for c in codes}
r = server.close_sweep(s)
chk("useCloseSweep=False 不执行", not r["ran"] and h["shares"] == 1000, str(r))

s, h = mk_state("cn_conservative", 100.0, 100.0, takeProfitPct=8, takeProfitSellFrac=1.0)
s["priceMode"] = "sim"
r = server.close_sweep(s)
chk("模拟价格模式不执行", not r["ran"] and h["shares"] == 1000, str(r))

s, h = mk_state("cn_conservative", 100.0, 100.0, takeProfitPct=8, takeProfitSellFrac=1.0)
server.in_trading_hours = lambda now=None: False
r = server.close_sweep(s)
chk("非交易时段不执行", not r["ran"] and h["shares"] == 1000, str(r))
server.in_trading_hours = lambda now=None: True

print("六、行情失败时不阻断判定（用旧价）")
s, h = mk_state("cn_conservative", 100.0, 108.0, takeProfitPct=8, takeProfitSellFrac=1.0)

def boom(codes):
    raise RuntimeError("网络超时")
server.fetch_prices = boom
r = server.close_sweep(s)
chk("行情异常仍按旧价判定并清仓", r["ran"] and r["sold"] == 1 and h["shares"] == 0, str(r))
chk("异常原因写进 why", "行情刷新失败" in (r.get("why") or ""), str(r))

print("七、无持仓不报错")
server.fetch_prices = lambda codes: {}
s, h = mk_state("cn_conservative", 100.0, 100.0)
s["holdings"] = []
r = server.close_sweep(s)
chk("空仓直接返回", r["ran"] and r["sold"] == 0, str(r))

print("八、maybe_close_sweep 请求触发入口（窗口外不动作 / 节流 / 不重复卖）")
import time as _time
s, h = mk_state("cn_conservative", 100.0, 100.0, takeProfitPct=8, takeProfitSellFrac=1.0)
server.cn_close_sweep_window = lambda now=None: False
chk("窗口外返回 0", server.maybe_close_sweep(s, "cn_conservative") == 0 and h["shares"] == 1000)

server.cn_close_sweep_window = lambda now=None: True
server.fetch_prices = lambda codes: {c: {"price": 108.0, "preClose": 100.0, "pct": 8.0} for c in codes}
s, h = mk_state("cn_conservative", 100.0, 100.0, takeProfitPct=8, takeProfitSellFrac=1.0)
chk("窗口内触发并全清", server.maybe_close_sweep(s, "cn_conservative") == 1 and h["shares"] == 0)
chk("标记 closeSweepDate", s.get("closeSweepDate") == server.today_str(), str(s.get("closeSweepDate")))
chk("二次调用被 3 分钟节流挡住", server.maybe_close_sweep(s, "cn_conservative") == 0)

print("九、节流过期后可再检查（尾盘价格还在动），不会重复卖")
s["_sweepTs"] = _time.time() - 200
chk("过期后再跑无新增卖出",
    server.maybe_close_sweep(s, "cn_conservative") == 0 and h["shares"] == 0)

print("\n结果：PASS %d / FAIL %d" % (_p, _f))
sys.exit(1 if _f else 0)
