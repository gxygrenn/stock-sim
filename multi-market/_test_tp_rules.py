# -*- coding: utf-8 -*-
"""2026-11-23 新增止盈/减仓规则回归测试。

背景：用户反馈「很多股票到不了止盈线就开始跌」。
根因：移动止盈被 `pnl_pct > 0` 门控，跌回成本以下即永久失效，只能等 -6% 硬清。
改动：
  1) loseCutPct / loseCutFrac —— 浮亏达线先减半（每个持仓只触发一次）
  2) trailArmPct —— 峰值曾赚到达标值后，移动止盈不再要求「当前仍浮盈」
"""
import os, sys
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


server.in_trading_hours = lambda now=None: True


def mk_state(acc, cost, price, shares=1000, **cfg_over):
    server.set_account(acc)
    s = server.default_state(acc)
    s["config"]["maxPerRound"] = 10
    s["config"]["maxPerDay"] = 100
    s["config"].update(cfg_over)
    s["holdings"] = [{"name": "测试股", "code": "sh600000", "shares": shares,
                      "price": price, "cost": cost, "pct": 0.0}]
    s["cash"] = 100000.0
    return s, s["holdings"][0]


print("一、loseCutPct 浮亏减半")
s, h = mk_state("us", 100.0, 97.8, loseCutPct=-2, loseCutFrac=0.5)
n = server.check_stops(s)
chk("浮亏-2.2% 触发减半", n == 1 and h["shares"] == 500, "n=%s shares=%s" % (n, h["shares"]))
chk("记录 loseCutDate", bool(h.get("loseCutDate")))
chk("不误标 tpDate", not h.get("tpDate"))

print("二、同一持仓不重复减半")
n2 = server.check_stops(s)
chk("第二轮不再卖", n2 == 0 and h["shares"] == 500, "n=%s shares=%s" % (n2, h["shares"]))

print("三、关闭开关")
s, h = mk_state("us", 100.0, 97.8, loseCutPct=0)
chk("loseCutPct=0 不触发", server.check_stops(s) == 0 and h["shares"] == 1000)

print("四、硬止损优先于减半")
s, h = mk_state("us", 100.0, 93.5, loseCutPct=-2)
n = server.check_stops(s)
chk("浮亏-6.5% 直接清仓而非减半", n == 1 and h["shares"] == 0, "shares=%s" % h["shares"])

print("五、trailArmPct 解开「当前必须浮盈」门控")
s, h = mk_state("us", 100.0, 99.5, shares=100, trailArmPct=4, trailPct=5, loseCutPct=0)
h["peak"] = 105.0          # 峰值曾 +5%
n = server.check_stops(s)
chk("峰值+5%、回撤5.2%、当前-0.5% -> 清仓", n == 1 and h["shares"] == 0,
    "n=%s shares=%s" % (n, h["shares"]))

print("六、trailArmPct=0 时保持旧行为（不卖）")
s, h = mk_state("us", 100.0, 99.5, shares=100, trailArmPct=0, trailPct=5, loseCutPct=0)
h["peak"] = 105.0
n = server.check_stops(s)
chk("旧逻辑下同一情形不卖", n == 0 and h["shares"] == 100, "n=%s" % n)

print("七、峰值未达 arm 门槛时不启用")
s, h = mk_state("us", 100.0, 97.8, shares=100, trailArmPct=4, trailPct=5, loseCutPct=0)
h["peak"] = 103.0          # 峰值仅 +3% < arm 4
n = server.check_stops(s)
chk("峰值+3% 未达 arm -> 不卖", n == 0 and h["shares"] == 100, "n=%s" % n)

print("八、同时满足「峰值回撤」与「浮亏减半」时，清仓优先（不拆两笔）")
s, h = mk_state("us", 100.0, 97.8, shares=100, trailArmPct=4, trailPct=5, loseCutPct=-2)
h["peak"] = 104.0          # 峰值+4% 达标，当前 -2.2%，回撤 6.0%（两条件都满足）
n = server.check_stops(s)
chk("直接清仓而非减半", n == 1 and h["shares"] == 0, "n=%s shares=%s" % (n, h["shares"]))
chk("不写 loseCutDate（走的是移动止盈）", not h.get("loseCutDate"))

print("九、清仓后重置标记（可重新建仓再触发）")
s, h = mk_state("cn_aggressive", 100.0, 97.5, shares=1000, loseCutPct=-2, loseCutFrac=0.5)
server.check_stops(s)
chk("已减半", h["shares"] == 500)
h["shares"] = 0            # 模拟后续被止损清空
h["price"] = 100.0
s["holdings"] = [x for x in s["holdings"] if x["shares"] > 0]
chk("持仓列表已清空", len(s["holdings"]) == 0)

print("十、默认配置不启用（三账户现状不变）")
for acc in ["us", "cn_conservative", "cn_aggressive"]:
    server.set_account(acc)
    cfg = server.default_state(acc)["config"]
    chk("%s 默认 loseCutPct 未启用" % acc, float(cfg.get("loseCutPct") or 0) == 0)
    chk("%s 默认 trailArmPct 未启用" % acc, float(cfg.get("trailArmPct") or 0) == 0)

print("汇总: %d 通过, %d 失败" % (_p, _f))
sys.exit(1 if _f else 0)
