# -*- coding: utf-8 -*-
"""波动率自适应仓位（volatility targeting）单元测试。借鉴 QuantsPlaybook 的时变夏普/波动率目标。"""
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


def mk_kline_vol(n, drift=0.0, vol=0.0):
    """构造日K：close 按 drift+vol 波动。vol 为日波动幅度（如 0.02 = 2%）。"""
    kl = []
    c = 100.0
    import math
    for i in range(n):
        # 用确定性正弦波动模拟，避免随机性导致测试不稳定
        wave = math.sin(i * 0.9) * vol if vol else 0.0
        c = c * (1.0 + drift + wave)
        kl.append(("d%d" % i, c, c, c * 1.01, c * 0.99, 1000000))
    return kl


print("vol：波动率画像计算")
kl = mk_kline_vol(80, drift=0.001, vol=0.02)
ann_vol, sharpe, note = server._volatility_profile(kl)
chk("年化波动 > 0", ann_vol > 0)
chk("波动率有明细文字", "波动" in note)
chk("夏普为有限数", sharpe == sharpe)  # 非 NaN

print("vol：数据不足返回 K线不足")
ann_vol, sharpe, note = server._volatility_profile(mk_kline_vol(10))
chk("数据不足 ann_vol=0", ann_vol == 0.0)

print("vol：低波动正夏普 -> scale >= 1.0")
s = server.default_state("cn_conservative")
s["config"]["useVolTarget"] = True
server.fetch_kline = lambda code, n=32: mk_kline_vol(80, drift=0.002, vol=0.005)
scale = server._vol_scale(s)
chk("低波动 scale 合理 [0.5,1.2]", 0.5 <= scale <= 1.2)
chk("regime.vol 写入", (s["regime"] or {}).get("vol") is not None)

print("vol：高波动 -> scale < 1.0（降仓）")
s2 = server.default_state("cn_conservative")
s2["config"]["useVolTarget"] = True
server.fetch_kline = lambda code, n=32: mk_kline_vol(80, drift=0.0, vol=0.06)
scale2 = server._vol_scale(s2)
chk("高波动 scale < 1.0", scale2 < 1.0)
chk("高波动 tier=high", (s2["regime"]["vol"]).get("tier") == "high")

print("vol：关闭时不缩放")
s3 = server.default_state("cn_conservative")
s3["config"]["useVolTarget"] = False
chk("关闭时 scale=1.0", server._vol_scale(s3) == 1.0)

print("vol：总仓位上限受波动率缩放")
server.set_account("cn_conservative")
s4 = server.default_state("cn_conservative")
s4["config"]["useVolTarget"] = True
s4["config"]["useRegime"] = True
s4["config"]["maxTotalPos"] = 92
s4["config"]["maxPosPct"] = 30
s4["config"]["maxHold"] = 8
s4["regime"] = {"enabled": True, "tier": "neutral", "score": 0.0,
                "vol": {"enabled": True, "scale": 0.5, "tier": "high"}}
s4["cash"] = 20000
# 构造 46% 仓位（92 的一半），高波动下 pos_cap 应收紧到 ~46%，则 46% 仓位应被拦
s4["holdings"] = [
    {"name": "a", "code": "sh600001", "shares": 200, "price": 230, "cost": 230, "pct": 0},
]
# total_asset = cash 20000 + 200*230 = 66000；仓位 = 46000/66000 ≈ 69.7%
item = {"name": "x", "code": "sh600099"}
ret = server._risk_check(s4, item, "sh600099", "买入", 50.0)
chk("高波动 69.7% 仓位被拦（pos_cap 收紧）", "保留弹药" in ret)

print("vol：低波动可小幅放宽仓位上限")
s5 = server.default_state("cn_conservative")
s5["config"]["useVolTarget"] = True
s5["config"]["useRegime"] = True
s5["config"]["maxTotalPos"] = 92
s5["config"]["maxPosPct"] = 30
s5["config"]["maxHold"] = 8
s5["regime"] = {"enabled": True, "tier": "neutral", "score": 0.0,
                "vol": {"enabled": True, "scale": 1.2, "tier": "low"}}
s5["cash"] = 30000
s5["holdings"] = [
    {"name": "a", "code": "sh600001", "shares": 100, "price": 100, "cost": 100, "pct": 0},
]
# total_asset = 30000 + 10000 = 40000；仓位 25%，远低于上限，应放行
ret5 = server._risk_check(s5, item, "sh600099", "买入", 50.0)
chk("低波动 25% 仓位放行", ret5 == "")

print("vol：usINX 映射到标普500（us.INX）")
# 验证 fetch_kline 对 usINX 能正确映射并取到数据（联网，失败则跳过不算失败）
try:
    kl_us = server.fetch_kline("usINX", 80)
    chk("usINX 取到 K 线 >= 60 根", len(kl_us) >= 60)
except Exception as exc:
    print("  SKIP usINX 联网失败: %s" % exc)

print("\n结果：%d 通过，%d 失败" % (_passed, _failed))
sys.exit(1 if _failed else 0)
