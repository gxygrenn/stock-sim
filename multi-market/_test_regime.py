# -*- coding: utf-8 -*-
"""市场择时（regime）单元测试。借鉴 QuantsPlaybook 的 RSRS + 扩散指标 + 动量。"""
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


def mk_kline(n, slope=1.0):
    kl = []
    for i in range(n):
        base = 100 + i * slope
        kl.append(("d%d" % i, base, base + 0.5, base + 2.0, base - 0.2, 1000000))
    return kl


print("regime：线性回归")
xs = [1, 2, 3, 4, 5]; ys = [2, 4, 6, 8, 10]
b, a = server._linreg(xs, ys)
chk("斜率 beta=2.0", abs(b - 2.0) < 1e-9)
chk("截距 alpha=0", abs(a) < 1e-9)

print("regime：RSRS 上涨趋势")
kl = mk_kline(80, 1.0)
beta, z, note = server._rsrs(kl)
chk("beta 可计算", beta is not None)
chk("上涨趋势 beta 接近 1（低点跟随高点同步抬升）", abs(beta - 1.0) < 0.1)

print("regime：三账户端到端（mock K线）")
server.fetch_kline = lambda code, n=32: mk_kline(80, 1.0)
for acc in ["cn_conservative", "cn_aggressive", "us"]:
    server.set_account(acc)
    s = server.default_state(acc)
    s["signals"] = {p["code"]: {"notes": ["20日新高+1", "多头排列+1"]} for p in s["pool"]}
    reg = server.market_regime(s)
    chk("[%s] 产出 tier" % acc, "tier" in reg)
    chk("[%s] 产出 score" % acc, "score" in reg)

print("regime：防守档收紧总仓位")
server.set_account("cn_conservative")
s = server.default_state("cn_conservative")
s["regime"] = {"enabled": True, "tier": "defense", "score": -2.0}
s["config"]["useRegime"] = True
s["config"]["regimePosCap"] = 70
s["config"]["maxTotalPos"] = 92
s["config"]["maxPosPct"] = 30
s["config"]["maxHold"] = 8
s["cash"] = 20000
s["holdings"] = [
    {"name": "a", "code": "sh600001", "shares": 100, "price": 200, "cost": 200, "pct": 0},
    {"name": "b", "code": "sh600002", "shares": 100, "price": 200, "cost": 200, "pct": 0},
    {"name": "c", "code": "sh600003", "shares": 100, "price": 200, "cost": 200, "pct": 0},
    {"name": "d", "code": "sh600004", "shares": 100, "price": 200, "cost": 200, "pct": 0},
]
s["signals"] = {}
item = {"name": "x", "code": "sh600099"}
ret = server._risk_check(s, item, "sh600099", "买入", 50.0)
chk("防守档 80% 仓位被拦", "防守" in ret)
s["regime"]["tier"] = "neutral"
ret2 = server._risk_check(s, item, "sh600099", "买入", 50.0)
chk("中性档 80% 仓位放行", ret2 == "")

print("regime：关闭择时不影响决策")
server.set_account("cn_conservative")
s2 = server.default_state("cn_conservative")
s2["config"]["useRegime"] = False
reg0 = server.market_regime(s2)
chk("关闭时 enabled=False", reg0.get("enabled") is False)
chk("关闭时 tier=neutral", reg0.get("tier") == "neutral")

print("\n结果：%d 通过，%d 失败" % (_passed, _failed))
sys.exit(1 if _failed else 0)
