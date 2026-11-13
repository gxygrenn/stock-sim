# -*- coding: utf-8 -*-
"""验证量价因子 + 行业轮动已接入 compute_signals 的 delta 合成。"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
import server
srv = server

PASS = 0
FAIL = 0


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


def mk_kl(n=65, trend=0.003, amp=0.04, base=100.0, trend2=None, split=20):
    kl = []
    price = base
    for i in range(n):
        o = price
        tr = trend2 if (trend2 is not None and i >= n - split) else trend
        c = o * (1 + tr)
        high = max(o, c) * (1 + amp / 2)
        low = min(o, c) * (1 - amp / 2)
        v = 1000 + i * 10
        kl.append(("2026-%02d-%02d" % (1, i + 1), o, c, high, low, v))
        price = c
    return kl


# 通用 mock：所有外部数据源都返回可控数据
kl_healthy = mk_kl(trend=0.003, amp=0.04)          # 量价健康（买压偏强）
kl_weak = mk_kl(trend=-0.003, amp=0.01, trend2=-0.006)  # 量价差（死水+下跌）
kl_idx_flat = mk_kl(trend=0.0005, base=100.0)


def fake_kl(code, n=65):
    if code == "sh000001":
        return kl_idx_flat[:n]
    if code == "sh600999":       # 弱势股票（量价差）
        return kl_weak[:n]
    return kl_healthy[:n]        # 其余都是健康票


srv.fetch_kline = fake_kl
srv.fetch_lhb = lambda: {}
srv.fetch_announcements = lambda: []


def build_state(pool):
    srv.set_account("cn_conservative")
    s = srv.default_state("cn_conservative")
    s["config"]["autoTrade"] = False
    s["config"]["useTech"] = False       # 关掉技术面，隔离出量价+行业的影响
    s["config"]["useLhb"] = False
    s["config"]["useAnn"] = False
    s["config"]["useSectorRotation"] = True
    s["config"]["usePriceVolume"] = True
    s["config"]["indexCode"] = "sh000001"
    s["pool"] = pool
    return s


print("信号接入：量价因子 + 行业轮动 → delta")

# ---- 1. 量价因子进入 delta，健康票 delta > 弱势票 ----
pool = [
    {"name": "健康票", "code": "sh600001", "tags": "半导体,芯片"},
    {"name": "弱势票", "code": "sh600999", "tags": "白酒,消费"},
]
s = build_state(pool)
sig = srv.compute_signals(s)
d_health = sig["sh600001"]["delta"]
d_weak = sig["sh600999"]["delta"]
chk("compute_signals 产出 pvf 字段", "pvf" in sig["sh600001"])
chk("健康票 delta > 弱势票 delta", d_health > d_weak,
    "health=%s weak=%s" % (d_health, d_weak))
chk("delta 含行业 bonus 字段", "sectorBonus" in sig["sh600001"])

# ---- 2. 量价因子关闭后，两者 delta 应趋同（隔离验证：差异来自量价/行业） ----
s2 = build_state(pool)
s2["config"]["usePriceVolume"] = False
s2["config"]["useSectorRotation"] = False
sig2 = srv.compute_signals(s2)
d2_health = sig2["sh600001"]["delta"]
d2_weak = sig2["sh600999"]["delta"]
chk("关闭量价+行业后两者 delta 趋同", abs(d2_health - d2_weak) < 0.001,
    "health=%s weak=%s" % (d2_health, d2_weak))

# ---- 3. 行业轮动加分：领先行业票 > 落后行业票（控制量价一致） ----
# 构造两个行业：半导体(用 health KL→leading)、白酒(用 weak KL→lagging)
pool3 = [
    {"name": "领先票", "code": "sh600001", "tags": "半导体,芯片"},
    {"name": "落后票", "code": "sh600999", "tags": "白酒,消费"},
]
s3 = build_state(pool3)
s3["config"]["usePriceVolume"] = False   # 只留行业轮动
sig3 = srv.compute_signals(s3)
bonus_lead = sig3["sh600001"].get("sectorBonus", 0)
bonus_lag = sig3["sh600999"].get("sectorBonus", 0)
chk("领先行业 bonus > 落后行业 bonus", bonus_lead > bonus_lag,
    "lead=%s lag=%s" % (bonus_lead, bonus_lag))

# ---- 4. 结果 delta 仍在 [-6,6] 范围内 ----
s4 = build_state(pool)
sig4 = srv.compute_signals(s4)
chk("所有 delta 落在 [-6,6]", all(-6 <= v["delta"] <= 6 for v in sig4.values()))

print("\n结果：%d 通过，%d 失败" % (PASS, FAIL))
import sys
sys.exit(1 if FAIL else 0)
