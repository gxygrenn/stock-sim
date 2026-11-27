# -*- coding: utf-8 -*-
"""扩池（2026-11-27）单元测试：拒绝项过期清理 / 门槛放宽 / 池子上限 / 强势榜取样数。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
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


def kline_ramp(n=61, start=10.0, end=10.0):
    step = (end - start) / (n - 1)
    return [("2026-01-01", start + step * i, start + step * i, start + step * i,
             start + step * i, 1000.0) for i in range(n)]


def fresh_cn():
    srv.set_account("cn_conservative")
    s = srv.default_state("cn_conservative")
    s["pool"] = [{"name": "老票%d" % i, "code": "sh60000%d" % i, "alias": "老%d" % i,
                  "beta": 1.0, "tags": "测试"} for i in range(3)]
    s["poolQuote"] = {p["code"]: {"pe": 15.0, "amount": 5e8, "price": 10.0} for p in s["pool"]}
    s["poolChanges"] = []
    s["poolChangeLog"] = []
    return s


def stub(cands, kline=None, pvf=2.0):
    srv.refresh_prices = lambda st: None
    srv.fetch_kline = kline or (lambda code, n=61: kline_ramp(61, 10.0, 10.3))  # +3%
    srv.fetch_strength_candidates = lambda limit=40: cands
    srv.price_volume_factors = lambda code: (pvf, ["测试量价"], {})


def cand(code="sz300001", name="强势中盘", amount=8e7, turn=22.0, pe=25.0, netIn=2e8, pct=4.0):
    return {"code": code, "name": name, "industry": "元件", "pct": pct,
            "amount": amount, "turn": turn, "pe": pe, "netIn": netIn}


print("扩池：被否建议过期清理（此前永久滞留导致同一批票无法再提议）")
s = fresh_cn()
stub([cand()])
s["poolChanges"] = [
    {"code": "sz300001", "name": "强势中盘", "direction": "add", "state": "rejected",
     "queuedAt": "2026-11-20 10:00:00", "reason": "旧否决"},
]
srv.build_rebalance(s)
codes = [p["code"] for p in s["poolChanges"]]
chk("超 72h 的 rejected 条目被清出", "sz300001" not in [p["code"] for p in s["poolChanges"]
                                              if p.get("state") == "rejected"])
chk("清出后同一只票可重新提议", "sz300001" in codes and
    any(p["code"] == "sz300001" and p["direction"] == "add" for p in s["poolChanges"]), str(codes))

print("扩池：冷却期内的 rejected 仍挡住（避免一小时一次反复弹同一只票）")
s = fresh_cn()
stub([cand()])
s["poolChanges"] = [
    {"code": "sz300001", "name": "强势中盘", "direction": "add", "state": "rejected",
     "queuedAt": srv.now_str(), "reason": "刚被否"},
]
srv.build_rebalance(s)
chk("TTL 内的 rejected 仍占位、不重复提议",
    not any(p["code"] == "sz300001" and p["state"] == "waiting" for p in s["poolChanges"]))

print("扩池：门槛放宽后可入池的样本（旧门槛 amount≥1亿 / 换手3-20 / 60日≥5% 全会被挡）")
s = fresh_cn()
stub([cand(amount=8e7, turn=22.0)])
srv.build_rebalance(s)
adds = [p for p in s["poolChanges"] if p["direction"] == "add"]
chk("成交额 0.8 亿 + 换手 22% + 60日+3% 的候选现在可入池", len(adds) == 1, str(adds[:1]))

print("扩池：60 日趋势仍是硬门槛（放宽不等于取消）")
s = fresh_cn()
stub([cand(code="sz300002")], kline=lambda code, n=61: kline_ramp(61, 10.0, 10.1))  # +1%
srv.build_rebalance(s)
chk("60日仅+1% 仍被 rebalanceMinR60=2 挡下",
    not any(p["direction"] == "add" for p in s["poolChanges"]))

print("扩池：池子上限")
s = fresh_cn()
stub([cand()])
s["config"] = dict(s["config"], rebalancePoolCap=3)   # 当前池正好 3 只
srv.build_rebalance(s)
chk("池子达到 cap 后不再新增", not any(p["direction"] == "add" for p in s["poolChanges"]))
s["config"] = dict(s["config"], rebalancePoolCap=4)
srv.build_rebalance(s)
chk("cap 放宽 1 席后恢复新增", any(p["direction"] == "add" for p in s["poolChanges"]))

print("扩池：强势榜取样条数随配置走")
seen = {}
s = fresh_cn()
stub([cand()])
srv.fetch_strength_candidates = lambda limit=40: (seen.setdefault("limit", limit), [])[1]
s["config"] = dict(s["config"], rebalanceStrengthLimit=70)
srv.build_rebalance(s)
chk("按 rebalanceStrengthLimit 取样（原写死 40）", seen.get("limit") == 70, str(seen))

print("扩池：默认配置就位")
cfg_c = srv.CN_CONSERVATIVE_CONFIG
cfg_a = srv.CN_AGGRESSIVE_CONFIG
chk("稳健版 maxAdd≥10 / cool≤10 / interval≤4",
    cfg_c["rebalanceMaxAdd"] >= 10 and cfg_c["rebalanceCooldownDays"] <= 10
    and cfg_c["rebalanceIntervalDays"] <= 4, str({k: cfg_c[k] for k in
                                                 ("rebalanceMaxAdd", "rebalanceCooldownDays",
                                                  "rebalanceIntervalDays")}))
chk("激进版 maxAdd≥12 / cool≤8 / interval≤3",
    cfg_a["rebalanceMaxAdd"] >= 12 and cfg_a["rebalanceCooldownDays"] <= 8
    and cfg_a["rebalanceIntervalDays"] <= 3)
chk("两版都有 poolCap / TTL / 抢位开关",
    all(k in cfg_c and k in cfg_a for k in
        ("rebalancePoolCap", "poolRejectTTLHours", "enqueueDeltaPriority")))

print("\n结果：%d 通过，%d 失败" % (PASS, FAIL))
raise SystemExit(1 if FAIL else 0)
