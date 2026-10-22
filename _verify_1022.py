#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""2026-10-22 复盘：逐数复算 + 构建三份 POST body（含字数断言）"""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DATE = "2026-10-22"
ok = lambda b, msg: print(("PASS " if b else "FAIL ") + msg)

def load(t):
    return (json.load(open("_facts_%s_1022.json" % t, encoding="utf-8")),
            json.load(open("_state_%s_1022.json" % t, encoding="utf-8")))

# ================= us =================
f, s = load("us")
a = f["account"]; total = a["totalAsset"]; cash = a["cash"]
holds = [h for h in s["holdings"] if h.get("shares", 0) > 0]
fmap = {h["code"]: h for h in f["holdings"]}
rows = sorted(s["history"], key=lambda r: r["date"])
prev_total = [r for r in rows if r["date"] < DATE][-1]["total"]
mkt = f["market"]["pct"]
day_pct = a["todayPnl"] / prev_total * 100
print("== us ==")
ok(abs(cash/total*100 - 18.47) < 0.5, "现金18.5%% (实际%.2f%%)" % (cash/total*100))
ok(abs(day_pct - 0.171) < 0.01, "当日+0.17%% (实际%.3f%%)" % day_pct)
ok(abs((day_pct - mkt) - (-0.409)) < 0.02, "跑输0.41pp (实际%.3f)" % (day_pct - mkt))
ok(len(holds) == 7, "7只持仓")
n_up = [h["name"] for h in holds if h["pct"] > 0]; n_dn = [h["name"] for h in holds if h["pct"] < 0]
p_up = [h["name"] for h in holds if fmap[h["code"]]["pnlPct"] > 0]; p_dn = [h["name"] for h in holds if fmap[h["code"]]["pnlPct"] < 0]
ok(len(n_up) == 3 and len(n_dn) == 4, "3涨4跌 (%s | %s)" % (n_up, n_dn))
ok(len(p_up) == 4 and len(p_dn) == 3, "4浮盈3浮亏 (%s | %s)" % (p_up, p_dn))
ind = {}
tags = {p["code"]: (p.get("tags") or "").split(",")[0] for p in s["pool"]}
for h in holds:
    ind.setdefault(tags.get(h["code"]) or "未分类", 0)
    ind[tags.get(h["code"]) or "未分类"] += h["shares"]*h["price"]/total*100
print("   行业:", {k: round(v, 1) for k, v in sorted(ind.items(), key=lambda x: -x[1])})
mrna = [h for h in holds if h["code"] == "usMRNA"][0]
ok(abs(mrna["shares"]*mrna["price"]/total*100 - 21.10) < 0.05, "MRNA权重21.1%% (实际%.2f)" % (mrna["shares"]*mrna["price"]/total*100))
tr = f["trades"][0]
ok(tr["qty"] == 24 and abs(tr["chgPctSinceTrade"] - (-1.05)) < 0.01, "ABNB 24股 chg-1.05")
cop = [h for h in s["trades"] if h["name"] == "康菲石油"][0]
copnow = [h for h in holds if h["code"] == "usCOP"][0]
ok(abs((copnow["price"]-cop["price"])/cop["price"]*100 - 0.172) < 0.02, "COP 现价较买价+0.17%% (实际%.3f)" % ((copnow["price"]-cop["price"])/cop["price"]*100))
# MRNA 今日否决次数
mrna_veto = [e for e in s["log"] if e["time"].startswith("2026-10-22") and "莫德纳" in e["msg"] and "评审" in e["msg"]]
ok(len(mrna_veto) == 4, "MRNA今日评审4次 (%d)" % len(mrna_veto))
ok(abs(f["account"]["pnlTotal"] - (-4489.99)) < 0.01, "累计-4.49%")

# ================= cnc =================
f, s = load("cnc")
a = f["account"]; total = a["totalAsset"]; cash = a["cash"]
holds = [h for h in s["holdings"] if h.get("shares", 0) > 0]
fmap = {h["code"]: h for h in f["holdings"]}
rows = sorted(s["history"], key=lambda r: r["date"])
prev_total = [r for r in rows if r["date"] < DATE][-1]["total"]
mkt = f["market"]["pct"]
econ = total - prev_total
print("== cnc ==")
ok(abs(econ - (-417.13)) < 0.5, "当日经济盈亏-417.13 (实际%.2f)" % econ)
ok(abs(econ/prev_total*100 - (-0.441)) < 0.01, "当日约-0.44%% (实际%.3f%%)" % (econ/prev_total*100))
ok(abs((econ/prev_total*100 - mkt) - 0.729) < 0.02, "跑赢约0.73pp (实际%.3f, 上证%.2f)" % (econ/prev_total*100 - mkt, mkt))
ok(len(holds) == 4, "4只持仓")
n_up = [h["name"] for h in holds if h["pct"] > 0]; n_dn = [h["name"] for h in holds if h["pct"] < 0]
ok(len(n_up) == 2 and len(n_dn) == 2, "2涨2跌 (%s|%s)" % (n_up, n_dn))
ok(all(fmap[h["code"]]["pnlPct"] < 0 for h in holds), "4只全部浮亏")
ok(abs(cash/total*100 - 41.32) < 0.1, "现金41.3%% (实际%.2f)" % (cash/total*100))
pos_pct = sum(h["shares"]*h["price"]/total*100 for h in holds)
ok(abs(pos_pct - 58.68) < 0.1, "总仓位58.7%% (实际%.2f)" % pos_pct)
w = {h["name"]: h["shares"]*h["price"]/total*100 for h in holds}
print("   权重:", {k: round(v, 2) for k, v in sorted(w.items(), key=lambda x: -x[1])})
ok(abs(w["中国电信"] - 24.98) < 0.05 and abs(w["万丰奥威"] - 12.52) < 0.05 and abs(w["富春染织"] - 10.86) < 0.05 and abs(w["建设银行"] - 10.32) < 0.05, "权重25.0/12.5/10.9/10.3")
ok(abs((w["富春染织"] + w["万丰奥威"]) - 23.38) < 0.1, "未分类合计23.4%")
wf = [t for t in f["trades"] if t["name"] == "万丰奥威"][0]
ok(abs((wf["price"] - 11.19)/11.19*100 - (-7.24)) < 0.05, "万丰成交价较前收-7.2%% (实际%.2f)" % ((wf["price"]-11.19)/11.19*100))
ok(abs(wf["pnlIfHeld"] - (-363.32)) < 0.5, "万丰即时浮亏363元")
rl_today = [x for x in f["reviewLog"] if x["reviewedAt"].startswith(DATE)]
n_buy = [x for x in rl_today if x["verdict"] == "BUY"]; n_hold = [x for x in rl_today if x["verdict"] == "HOLD"]
print("   reviewLog当日: %d条, BUY=%s, HOLD=%d" % (len(rl_today), [x["name"] for x in n_buy], len(n_hold)))
ok(len(f["reviewedToday"]) == 23, "reviewedToday=23 (%d)" % len(f["reviewedToday"]))
council_buy = [x for x in f["councilToday"] if x["verdict"] == "买入"]
ok(len(council_buy) == 8 and len(f["councilToday"]) == 10, "council 10条中8个买入倾向")
ok(abs(a["pnlTotal"] - (-5746.85)) < 0.01, "累计-5.75%")

# ================= cna =================
f, s = load("cna")
a = f["account"]; total = a["totalAsset"]; cash = a["cash"]
holds = [h for h in s["holdings"] if h.get("shares", 0) > 0]
fmap = {h["code"]: h for h in f["holdings"]}
rows = sorted(s["history"], key=lambda r: r["date"])
prev_total = [r for r in rows if r["date"] < DATE][-1]["total"]
mkt = f["market"]["pct"]
# 当日浮动（无成交，等于经济盈亏）；prev_total 实为10-21收盘（结算错标行）
float_pnl = sum(h["shares"]*(h["price"]-h["preClose"]) for h in holds)
print("== cna ==")
ok(abs((total - prev_total) - (-1196.00)) < 0.5, "总值-前日=-1196.00 (实际%.2f)" % (total - prev_total))
ok(abs(float_pnl - (-1196.00)) < 0.5, "当日浮动复算-1196.00 (实际%.2f)" % float_pnl)
ok(abs(float_pnl/prev_total*100 - (-1.266)) < 0.01, "当日约-1.27%% (实际%.3f%%)" % (float_pnl/prev_total*100))
ok(abs((float_pnl/prev_total*100 - mkt) - (-0.196)) < 0.02, "跑输约0.2pp (实际%.3f, 上证%.2f)" % (float_pnl/prev_total*100 - mkt, mkt))
ok(len(holds) == 9, "9只持仓")
n_up = [h for h in holds if h["pct"] > 0]; n_dn = [h for h in holds if h["pct"] < 0]; n_fl = [h for h in holds if h["pct"] == 0]
ok(len(n_up) == 3 and len(n_dn) == 5 and len(n_fl) == 1, "3涨5跌1平 (涨:%s 跌:%s 平:%s)" % ([h["name"] for h in n_up], [h["name"] for h in n_dn], [h["name"] for h in n_fl]))
w = {h["name"]: h["shares"]*h["price"]/total*100 for h in holds}
ok(abs(w["万科A"] + w["国轩高科"] - 60.90) < 0.1, "万科+国轩合计60.9%% (实际%.2f)" % (w["万科A"]+w["国轩高科"]))
wk = [h for h in holds if h["name"] == "万科A"][0]
ok(abs(wk["shares"]*(wk["price"]-wk["preClose"]) - (-1253.04)) < 1, "万科今日亏损约-1253元")
ok(abs((25.45-26.13)/26.13*100 - (-2.60)) < 0.05, "国轩自cnc止损价26.13后又跌2.6%%")
gx = [h for h in holds if h["name"] == "国轩高科"][0]
ok(abs(gx["shares"]*(gx["price"]-26.05) - (-517)) < 10, "国轩自复核价26.05再亏约500元 (实际%.0f)" % (gx["shares"]*(gx["price"]-26.05)))
bank = w["工商银行"] + w["农业银行"]; coal = w["中国神华"]
ok(abs(bank - 26.72) < 0.1 and abs(coal - 6.69) < 0.1, "银行26.7%%煤炭6.7%% (实际%.2f/%.2f)" % (bank, coal))
ok(len(f["councilToday"]) == 10 and all(x["verdict"] == "否决" for x in f["councilToday"]), "10个买入提案全否决")
exp = [x for x in f["expiredRecent"] if x["name"] == "万科A"][0]
ok(exp["state"] == "approved" and abs(exp["waitedH"] - 11.8) < 0.1 and "8h" in exp["why"], "万科SELL批单超8h保质期作废(waitedH=%.1f)" % exp["waitedH"])
ok(abs(a["pnlTotal"] - (-6719.89)) < 0.01, "累计-6.72%")
ok(abs(cash/total*100 - 1.331) < 0.02, "现金1.3%% (实际%.2f)" % (cash/total*100))
ok(len(f["trades"]) == 0, "今日0成交")

print("\nDONE-VERIFY")
