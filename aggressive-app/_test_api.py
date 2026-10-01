# -*- coding: utf-8 -*-
"""后端端到端回归测试

  A. 初始状态
  B. 专家评议团（独立研判 / 合议投票 / 风控否决 / 开关降级）
  C. 扫描买入 -> 卖出 -> 结算 -> 撤销
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8124"
PASS = 0
FAIL = 0


def post(path, body=None, timeout=90):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def get(path, timeout=30):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read().decode("utf-8"))


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


# ================================================================ A 初始状态
print("A. 初始状态")
st = get("/api/state")
chk("初始资金 100000", abs(st["cash"] - 100000) < 0.01, "cash=%.2f" % st["cash"])
chk("自选池 15 只", len(st["pool"]) == 15, "pool=%d" % len(st["pool"]))

# ================================================================ B 专家评议团
print("\nB. 专家评议团")
post("/api/reset", {})
c0 = get("/api/state")["config"]
chk("评议团默认开启", bool(c0.get("useCouncil")))
chk("评议团配置齐全",
    all(k in c0 for k in ("useCouncil", "minAgree", "useRisk",
                          "maxTotalPos", "stopLossPct")),
    "minAgree=%s useRisk=%s" % (c0.get("minAgree"), c0.get("useRisk")))

rc = post("/api/council", {}, timeout=240)
recs = rc.get("records") or []
chk("开会返回全池意见", len(recs) == 15, "records=%d" % len(recs))
chk("会议附会次标记", all(r.get("session") for r in recs))
chk("决议结构完整",
    all(all(k in r for k in ("verdict", "votes", "agree", "oppose", "total",
                             "abstain", "proposal", "veto", "score", "name"))
        for r in recs))
chk("决议取值合法", all(r["verdict"] in ("买入", "卖出", "观望", "否决") for r in recs),
    str(sorted(set(r["verdict"] for r in recs))))
chk("含 4 位分析师 + 风控官", all(len(r["votes"]) == 5 for r in recs),
    "votes=%s" % [len(r["votes"]) for r in recs][:4])
chk("风控官排在最后一位", all(r["votes"][-1]["role"] == "风控官" for r in recs))
chk("四位分析师不重名",
    all(len({v["role"] for v in r["votes"][:4]}) == 4 for r in recs))
chk("弃权不计入票池",
    all(r["agree"] + r["oppose"] <= r["total"] and r["abstain"] == 4 - r["total"]
        for r in recs),
    str([(r["agree"], r["oppose"], r["total"], r["abstain"]) for r in recs][:4]))
chk("通过的决议票数达标",
    all(r["agree"] >= c0["minAgree"] and r["agree"] > r["oppose"]
        for r in recs if r["verdict"] in ("买入", "卖出")))
chk("观望的决议票数不达标",
    all(not (r["agree"] >= c0["minAgree"] and r["agree"] > r["oppose"])
        for r in recs if r["verdict"] == "观望"))
chk("风控否决必有理由",
    all(bool(r["veto"]) for r in recs if r["verdict"] == "否决"))
chk("无议案时不给否决",
    all(not r["veto"] for r in recs if not r["proposal"]))
chk("加权分在 ±3 内", all(-3 <= r["score"] <= 3 for r in recs))
chk("合议记录已入库", len(get("/api/state").get("council") or []) >= 15)
chk("自选池带评议团预览",
    all("council" in (g or {}) for g in (get("/api/state").get("signals") or {}).values()))

# 评议团模式下，成交理由应带票数
# （用投喂而不是扫描：否则结果取决于当天恰好有没有利好新闻，测试会时灵时不灵）
post("/api/config", {"config": {"autoTrade": True, "useCouncil": True, "minAgree": 1,
                                "useTech": False, "useLhb": False,
                                "useAnn": False, "useVal": False}})
post("/api/inject", {"text": "招商银行发布业绩预增公告，拟回购股份提振投资者信心"})
tr_c = get("/api/state")["trades"]
chk("评议团模式真的成交了", len(tr_c) > 0, "%d 笔" % len(tr_c))
chk("成交理由带评议票数",
    all("评议" in (t.get("reason") or "") for t in tr_c),
    str([t["reason"] for t in tr_c][:2]))
post("/api/config", {"config": {"minAgree": 2, "useTech": True, "useLhb": True,
                                "useAnn": True, "useVal": True}})

# 关掉估值 -> 估值分析师弃权
post("/api/config", {"config": {"useVal": False}})
recs_v = post("/api/council", {}, timeout=240).get("records") or []
vnotes = [v["note"] for r in recs_v for v in r["votes"] if v["role"] == "估值分析师"]
chk("关闭估值后该分析师弃权", bool(vnotes) and all("未启用" in n for n in vnotes),
    str(vnotes[:2]))

# 门槛拉满 -> 不可能通过
post("/api/config", {"config": {"useVal": True, "minAgree": 4}})
recs_h = post("/api/council", {}, timeout=240).get("records") or []
chk("门槛拉满后无买入卖出决议",
    all(r["verdict"] not in ("买入", "卖出") for r in recs_h),
    str(sorted(set(r["verdict"] for r in recs_h))))

# 关掉风控官 -> 不再出现否决
post("/api/config", {"config": {"minAgree": 2, "useRisk": False}})
recs_r0 = post("/api/council", {}, timeout=240).get("records") or []
chk("关闭风控官后无否决",
    all(r["verdict"] != "否决" for r in recs_r0),
    str(sorted(set(r["verdict"] for r in recs_r0))))
chk("关闭风控官后风控官仍列席",
    all(r["votes"][-1]["stance"] == "放行" for r in recs_r0))
post("/api/config", {"config": {"useRisk": True}})

# 本轮上限压到 0 -> 任何议案都进不了场
n0 = len(get("/api/state")["trades"])
post("/api/config", {"config": {"maxPerRound": 0}})
post("/api/scan", {"force": True})
n1 = len(get("/api/state")["trades"])
chk("本轮上限为0时不再成交", n1 == n0, "%d -> %d" % (n0, n1))

# 额度用尽时，人工召集的研判会仍须给出真实意见
# （「本轮笔数上限」是执行额度，不是交易该不该做的理由，不该堵住研判）
recs_a = post("/api/council", {}, timeout=240).get("records") or []
quota_vetoes = [v["note"] for r in recs_a for v in r["votes"]
                if v["role"] == "风控官" and "上限" in (v["note"] or "")]
chk("研判会不受本轮交易额度影响", not quota_vetoes, str(quota_vetoes[:2]))
chk("研判会记录带 advisory 标记", bool(recs_a) and all(r.get("advisory") for r in recs_a))
chk("额度用尽时研判会仍给出分析师意见",
    any(v["stance"] != "中性" for r in recs_a for v in r["votes"][:4]),
    str(sorted(set(r["verdict"] for r in recs_a))))
chk("研判模式不会显示「风控官已关闭」",
    all("已关闭" not in v["note"] for r in recs_a for v in r["votes"]
        if v["role"] == "风控官"))
post("/api/config", {"config": {"maxPerRound": 3}})

# 关掉评议团 -> 回落旧逻辑
post("/api/reset", {})
post("/api/config", {"config": {"autoTrade": True, "useCouncil": False}})
post("/api/scan", {"force": True})
chk("关闭评议团后走旧逻辑（理由无票数）",
    not any("评议" in (t.get("reason") or "") for t in get("/api/state")["trades"]))
post("/api/reset", {})

# ================================================================ C 主流程
print("\nC. 扫描 / 投喂 / 卖出 / 结算 / 撤销")
post("/api/reset", {})
post("/api/config", {"config": {"autoTrade": True, "useCouncil": True}})
r = post("/api/scan", {"force": True})["result"]
chk("扫描抓到新闻", r["new"] > 0, "new=%d" % r["new"])
chk("扫描无报错", not r.get("err"), str(r.get("err")))
st_scan = get("/api/state")
chk("成交笔数统计与实际一致", r["traded"] == len(st_scan["trades"]),
    "traded=%s 实际 %d 笔" % (r["traded"], len(st_scan["trades"])))

# ---- 四路信号 ----
print("\n-- 四路信号 --")
sigs = st_scan.get("signals") or {}
chk("信号已计算", len(sigs) >= 10, "signals=%d" % len(sigs))
sample = sigs.get("sh600519") or next(iter(sigs.values()), {})
chk("信号结构完整",
    all(k in sample for k in ("tech", "lhb", "ann", "delta", "block", "notes")),
    str(sample)[:80])
chk("信号分在 ±6 内", all(-6 <= (g.get("delta") or 0) <= 6 for g in sigs.values()))
chk("技术面有产出", any(g.get("tech") != 0 for g in sigs.values()),
    "非零技术分 %d 只" % sum(1 for g in sigs.values() if g.get("tech")))
q0 = (st_scan.get("poolQuote") or {}).get("sh600519") or {}
chk("行情含估值字段", "pe" in q0 and "turn" in q0,
    "pe=%s turn=%s" % (q0.get("pe"), q0.get("turn")))
chk("信号开关已入库",
    all(k in st_scan["config"] for k in ("useTech", "useLhb", "useAnn", "useVal", "sigScore")))
post("/api/config", {"config": {"useTech": False, "useLhb": False,
                                "useAnn": False, "useVal": False}})
post("/api/scan", {"force": True})
st2 = get("/api/state")
chk("关闭信号源后 delta 归零",
    all((g.get("delta") or 0) == 0 for g in (st2.get("signals") or {}).values()))
post("/api/config", {"config": {"useTech": True, "useLhb": True,
                                "useAnn": True, "useVal": True}})

# ---- 命中精度：板块标签只认标题 ----
print("\n-- 命中精度 --")
post("/api/reset", {})
post("/api/config", {"config": {"minScore": 999}})      # 先只验匹配，不下单
pr = post("/api/inject", {"title": "国际市场综述：欧加磋商进入新阶段",
                          "text": "国际市场综述：欧加磋商进入新阶段。"
                                  "一批来自金融与政界的顾问参与其中。"}).get("result") or {}
chk("板块标签只认标题：正文提及不算命中", not pr.get("hits"),
    "hits=%s keys=%s" % (pr.get("hits"), pr.get("hitKeys")))
pt = post("/api/inject", {"title": "白酒板块午后集体走强",
                          "text": "白酒板块午后集体走强，机构看好龙头。"}).get("result") or {}
chk("板块标签出现在标题则命中", "五粮液" in (pt.get("hits") or []), str(pt.get("hits")))
pn = post("/api/inject", {"text": "五粮液发布业绩预增公告，公司拟回购股份。"}).get("result") or {}
chk("个股名称在正文也算命中", "五粮液" in (pn.get("hits") or []), str(pn.get("hits")))

# ---- 投喂演练：盘后无新闻也能确定性验证整条链路 ----
print("\n-- 投喂演练 --")
post("/api/reset", {})
# 关掉四路信号、门槛降到 1 票，让「消息面」成为唯一变量，结果可确定
post("/api/config", {"config": {"autoTrade": True, "useCouncil": True, "minAgree": 1,
                                "minScore": 2, "useTech": False, "useLhb": False,
                                "useAnn": False, "useVal": False}})
inj = post("/api/inject", {"text": "五粮液发布业绩预增公告，净利润大幅增长，"
                                    "公司拟回购股份提振投资者信心"})
chk("投喂接口 ok", inj.get("ok"), str(inj.get("action")))
ni = inj.get("result") or {}
chk("投喂消息命中自选池", bool(ni.get("hits")), str(ni.get("hits")))
chk("利好词已识别", (ni.get("score") or 0) >= 2,
    "score=%s good=%s" % (ni.get("score"), ni.get("good")))
st = get("/api/state")
buys = [t for t in st["trades"] if t["side"] == "买入"]
chk("投喂利好后自动买入", len(buys) > 0, "action=%s" % inj.get("action"))
chk("成交理由含消息与评议", all("消息" in t["reason"] and "评议" in t["reason"] for t in buys),
    str([t["reason"] for t in buys][:2]))
chk("每笔都是 100 的整数倍", all(t["qty"] % 100 == 0 for t in buys))
chk("持仓与流水一致",
    sum(h["shares"] for h in st["holdings"]) == sum(t["qty"] for t in buys))
chk("现金为正", st["cash"] > 0, "cash=%.2f" % st["cash"])
chk("手续费已计入", st["fees"] > 0, "fees=%.2f" % st["fees"])
if buys:
    t0 = buys[0]
    q = st["poolQuote"].get(t0["code"])
    chk("成交价取实时价", q is None or abs(q["price"] - t0["price"]) < 0.02,
        "%s %.3f" % (t0["name"], t0["price"]))
chk("利好新闻已入消息流",
    any(n.get("inject") for n in (st.get("news") or [])))

# ---- 执行额度：每轮一笔 / 每日兜底 ----
print("\n-- 执行额度 --")
post("/api/reset", {})
post("/api/config", {"config": {"autoTrade": True, "minScore": 2, "maxPerRound": 1,
                                "maxPerDay": 99, "minAgree": 1, "useTech": False,
                                "useLhb": False, "useAnn": False, "useVal": False}})
post("/api/inject", {"text": "五粮液与招商银行双双发布业绩预增公告，净利润大幅增长，"
                             "两家公司均拟回购股份提振投资者信心"})
d1 = get("/api/state")
b1 = [t for t in d1["trades"] if t["side"] == "买入"]
chk("一轮内命中多只也受每轮笔数限制", len(b1) == 1, "买入 %d 笔" % len(b1))
chk("每轮计数在本轮内累积", d1.get("roundTrades") == 1, "roundTrades=%s" % d1.get("roundTrades"))

post("/api/config", {"config": {"maxPerRound": 5, "maxPerDay": 1}})
post("/api/inject", {"text": "比亚迪发布业绩预增公告，净利润大幅增长，拟回购股份"})
d2 = get("/api/state")
chk("触及每日上限后不再成交", len(d2["trades"]) == len(d1["trades"]),
    "%d -> %d" % (len(d1["trades"]), len(d2["trades"])))
post("/api/config", {"config": {"maxPerRound": 3, "maxPerDay": 12}})

# ---- 卖出 ----
st = get("/api/state")          # 上面重置过账户，重新取一次最新持仓
held = [h for h in st["holdings"] if h["shares"] > 0]
chk("有持仓可卖", len(held) > 0, "持仓 %d 只" % len(held))
if held:
    h0 = held[0]
    cash_before = st["cash"]
    r = post("/api/trade", {"side": "卖出", "code": h0["code"], "qty": h0["shares"]})
    chk("卖出接口 ok", r.get("ok"), str(r)[:80])
    st = get("/api/state")
    sells = [t for t in st["trades"] if t["side"] == "卖出"]
    chk("卖出流水已记录", len(sells) > 0, "卖出 %d 笔" % len(sells))
    chk("卖出后现金增加", st["cash"] > cash_before, "%.2f -> %.2f" % (cash_before, st["cash"]))
    chk("该股已清仓",
        all(h["shares"] == 0 for h in st["holdings"] if h["code"] == h0["code"]))

# ---- 结算 ----
asset_before = st["cash"] + sum(h["price"] * h["shares"] for h in st["holdings"])
r = post("/api/settle", {"pct": 0.6})
chk("结算接口 ok", r.get("ok"), str(r)[:80])
st = get("/api/state")
asset_after = st["cash"] + sum(h["price"] * h["shares"] for h in st["holdings"])
chk("产生历史记录", len(st["history"]) > 0, "history=%d" % len(st["history"]))
print("       总资产 %.2f -> %.2f" % (asset_before, asset_after))
hh = st["history"][-1]
chk("历史含盈亏字段", "dayPnl" in hh and "mkt" in hh, str(hh)[:100])

# ---- 撤销 ----
r = post("/api/undo", {})
chk("撤销接口 ok", r.get("ok"), str(r)[:80])
st2 = get("/api/state")
chk("撤销后历史回退", len(st2["history"]) == len(st["history"]) - 1,
    "%d -> %d" % (len(st["history"]), len(st2["history"])))
a2 = st2["cash"] + sum(h["price"] * h["shares"] for h in st2["holdings"])
chk("撤销后总资产还原", abs(a2 - asset_before) < 0.01,
    "%.2f vs %.2f" % (a2, asset_before))

# ================================================================ D 专家团门控
print("\nD. 专家团门控（autoTrade=False）")
post("/api/reset", {})
post("/api/config", {"config": {"autoTrade": False}})
n0 = len(get("/api/state")["trades"])
r = post("/api/scan", {}, timeout=240)["result"]
st_g = get("/api/state")
chk("门控模式扫描不成交", len(st_g["trades"]) == n0,
    "%d -> %d" % (n0, len(st_g["trades"])))
chk("门控模式仍刷新资讯", r["new"] >= 0 and st_g.get("lastScan"),
    "new=%d lastScan=%s" % (r["new"], st_g.get("lastScan")))
chk("状态带待评审队列字段", isinstance(st_g.get("pendingReview"), list),
    "pending=%d" % len(st_g.get("pendingReview") or []))
for p in (st_g.get("pendingReview") or []):
    chk("候选方向合法", p.get("direction") in ("买入", "卖出"), str(p)[:60])
    break

# review_done 出队 + 冷却
rv = post("/api/review_done", {"code": "sh600036", "verdict": "HOLD", "note": "测试评审"})
chk("review_done 接口 ok", rv.get("ok"), str(rv)[:60])
st_g2 = get("/api/state")
chk("review_done 后记入冷却", "sh600036" in (st_g2.get("reviewed") or {}))
chk("review_done 后该码不在队列",
    all(p["code"] != "sh600036" for p in (st_g2.get("pendingReview") or [])))

# /api/trade 自定义理由（专家团代下单通道）
post("/api/config", {"config": {"autoTrade": False}})
q = (st_g2.get("poolQuote") or {}).get("sh600036") or {}
if q.get("price"):
    tr = post("/api/trade", {"side": "buy", "code": "sh600036", "qty": 100,
                             "reason": "专家团评审:BUY信心7/10"})
    chk("专家团代下单 ok", tr.get("ok"), str(tr)[:80])
    t0 = get("/api/state")["trades"][0]
    chk("成交理由为专家团评审", "专家团评审" in (t0.get("reason") or ""),
        str(t0.get("reason")))
else:
    chk("专家团代下单 ok", False, "无招行报价")
post("/api/reset", {})
post("/api/config", {"config": {"autoTrade": True}})

# ================================================================ E 每日复盘
print("\nE. 每日复盘（daily_facts / daily_review）")
post("/api/reset", {})
post("/api/config", {"config": {"autoTrade": True, "useCouncil": True, "minAgree": 1,
                                "minScore": 2, "useTech": False, "useLhb": False,
                                "useAnn": False, "useVal": False}})
post("/api/inject", {"text": "五粮液发布业绩预增公告，净利润大幅增长，拟回购股份"})
f = get("/api/daily_facts")
chk("facts 带日期", bool(f.get("date")), str(f.get("date")))
chk("facts 账户字段齐全",
    all(k in (f.get("account") or {}) for k in ("cash", "totalAsset", "pnlTotal",
                                                "retTotalPct", "realized", "fees")),
    str(f.get("account"))[:100])
chk("facts 含当日成交且带事后表现",
    len(f.get("trades") or []) > 0 and
    all("chgPctSinceTrade" in t and "pnlIfHeld" in t for t in f["trades"]),
    str((f.get("trades") or [{}])[0])[:120])
chk("facts 含持仓快照",
    all(k in h for h in (f.get("holdings") or []) for k in ("name", "shares", "cost", "price")))
chk("facts 含消息统计", all(k in (f.get("newsStats") or {}) for k in ("total", "hitCount", "topHits")),
    str(f.get("newsStats", {}).get("hitCount")))
chk("facts 含队列与市场", "pendingReview" in f and "market" in f)

rv = post("/api/daily_review", {"summary": "测试复盘摘要", "score": -1,
                                "bullets": ["当日买入 1 笔，收盘浮盈 +0.8%"],
                                "lessons": ["利好类快讯在无资金配合时胜率偏低"]})
chk("复盘接口 ok", rv.get("ok"), str(rv)[:80])
d1 = get("/api/state").get("dailyReviews") or []
chk("复盘已入库", len(d1) == 1 and d1[0]["summary"] == "测试复盘摘要", str(d1[:1])[:120])
chk("复盘保留要点与经验",
    d1 and len(d1[0]["bullets"]) == 1 and len(d1[0]["lessons"]) == 1)
chk("复盘记录账户快照", d1 and d1[0].get("account") > 0)
post("/api/daily_review", {"summary": "同日第二次复盘应覆盖", "bullets": ["覆盖测试"]})
d2 = [r for r in (get("/api/state").get("dailyReviews") or []) if r["date"] == d1[0]["date"]]
chk("同日复盘去重（只保留最新）", len(d2) == 1 and d2[0]["summary"] == "同日第二次复盘应覆盖",
    "同日 %d 条" % len(d2))
bad = post("/api/daily_review", {})
chk("空复盘被拒绝", bad.get("ok") is False, str(bad)[:60])
v = get("/api/version")
chk("版本接口列出复盘能力", "dailyFacts" in (v.get("features") or []), str(v.get("features")))
post("/api/reset", {})

# ================================================================ F 买入信号分下限
print("\nF. 买入信号分下限（buySigFloor）")
post("/api/reset", {})
post("/api/config", {"config": {"autoTrade": True, "useCouncil": True, "minAgree": 1,
                                "minScore": 2, "useTech": False, "useLhb": False,
                                "useAnn": False, "useVal": False, "buySigFloor": 1}})
chk("配置含 buySigFloor", get("/api/state")["config"].get("buySigFloor") == 1)
post("/api/inject", {"text": "五粮液发布业绩预增公告，净利润大幅增长，拟回购股份"})
chk("信号分为0时低于下限1 → 不买入", len(get("/api/state")["trades"]) == 0,
    "成交 %d 笔" % len(get("/api/state")["trades"]))
post("/api/config", {"config": {"buySigFloor": 0}})
post("/api/inject", {"text": "五粮液发布业绩预增公告，净利润大幅增长，拟回购股份"})
buys_f = [t for t in get("/api/state")["trades"] if t["side"] == "买入"]
chk("下限降到0后恢复买入", len(buys_f) > 0, "买入 %d 笔" % len(buys_f))
post("/api/config", {"config": {"buySigFloor": -6, "minScore": 4}})
chk("建议门槛已生效（minScore=4）", get("/api/state")["config"]["minScore"] == 4)
post("/api/reset", {})
post("/api/config", {"config": {"minScore": 2, "autoTrade": True}})


# ================================================================ G 评审与执行解耦 + 队列 TTL
print("\nG. 评审与执行解耦（结论落队列）与队列 TTL")
post("/api/reset", {})
post("/api/config", {"config": {"autoTrade": False, "minAgree": 1, "minScore": 2,
                                "useTech": False, "useLhb": False, "useAnn": False,
                                "useVal": False, "buySigFloor": -6}})
post("/api/inject", {"text": "五粮液发布业绩预增公告，净利润大幅增长，拟回购股份"})
pend = get("/api/state").get("pendingReview") or []
chk("G1 门控下候选入队", len(pend) >= 1, "队列 %d" % len(pend))
f = get("/api/daily_facts")
chk("G2 facts 带 expiredRecent 字段", "expiredRecent" in f, str(f.get("expiredRecent"))[:60])
if pend:
    p0 = pend[0]
    chk("G3 候选带 queuedAt / px / state",
        all(k in p0 for k in ("queuedAt", "px", "state")) and p0["state"] == "waiting",
        str(p0)[:110])
    f0 = (f.get("pendingReview") or [{}])[0]
    chk("G4 facts 队列项带等待时长 waitH", "waitH" in f0, str(f0)[:90])
    n_before = len(get("/api/state")["trades"])
    rv = post("/api/review_done", {"code": p0["code"], "verdict": "BUY",
                                   "confidence": 7, "note": "测试：批准买入"})
    chk("G5 review_done 返回 queued=True", rv.get("queued") is True, str(rv)[:110])
    chk("G6 review_done 返回 executedNow 字段", "executedNow" in rv, str(rv)[:110])
    st2 = get("/api/state")
    pp = [p for p in (st2.get("pendingReview") or []) if p["code"] == p0["code"]]
    if rv.get("executedNow"):
        chk("G7a 盘中：批准后立即落地成交", len(st2["trades"]) > n_before,
            "成交 %d -> %d" % (n_before, len(st2["trades"])))
    else:
        chk("G7b 盘后：已批准候选留在队列等盘中，不成交",
            len(pp) == 1 and pp[0]["state"] == "approved" and len(st2["trades"]) == n_before,
            str(pp)[:110])
        chk("G8 评审结论写入候选",
            (pp[0].get("review") or {}).get("verdict") == "BUY",
            str(pp[0].get("review"))[:90])
    post("/api/review_done", {"code": p0["code"], "verdict": "HOLD", "note": "撤回"})
    chk("G9 HOLD 直接出队",
        all(p["code"] != p0["code"] for p in (get("/api/state").get("pendingReview") or [])))
v = get("/api/version")
chk("G10 版本接口列出去耦与 TTL 能力",
    "reviewDecoupled" in (v.get("features") or []) and "reviewTTL" in (v.get("features") or []),
    str(v.get("features")))
post("/api/reset", {})
post("/api/config", {"config": {"minScore": 2, "autoTrade": True}})

print("\n%d passed, %d failed" % (PASS, FAIL))