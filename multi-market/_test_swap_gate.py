# -*- coding: utf-8 -*-
"""换仓闸门 + 清仓型 SELL（2026-11-13，用户拍板「直接改吧」）。

T14/T10/T28 三案并修：
1. swapGate：满席时若队列已有「将清仓」的已批 SELL（forced / 【R15清仓腾位】），
   买入初审不再一票否决，按「先卖后买」放行。
   背景：cnc 东方环宇 delta5.9 连续 4 日因只数上限 HOLD，11-10 @21.79 →
   11-13 @27.99（+28.5%），账户现金 25.2% 与满 8 席并存。
2. 执行顺序先卖后买：批准的 BUY 不再因排在 SELL 之前而浪费当轮腾出的席位。
3. 清仓口径：forced 终审 SELL 与 R15 清仓标记 SELL 卖全量。
   背景：cnc 三一 11-13 置换 SELL 只成交 100/326 股，余 226 股仍 -6.24%，
   席位未释放，东方环宇同日 14:32 再被满席否决。
"""
import os, sys
os.environ["NO_WORKER"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server


def _stub_cp(s, item):
    """mock 价：poolQuote 优先，其次持仓价（沿用 _test_ttl_sameday 的打桩法）。"""
    code = item.get("code") if isinstance(item, dict) else item
    pq = (s.get("poolQuote") or {}).get(code) or {}
    if (pq.get("price") or 0) > 0:
        return pq["price"]
    for h in s.get("holdings") or []:
        if h.get("code") == code and (h.get("price") or 0) > 0:
            return h["price"]
    return 0.0


server.current_price = _stub_cp

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


def mk_state(swap_gate=True):
    """cnc 满席状态：8 只持仓（每只 10,000 元）+ 现金 20,000 = 总资产 100,000。"""
    server.set_account("cn_conservative")
    s = server.default_state("cn_conservative")
    s["config"]["maxHold"] = 8
    s["config"]["maxPosPct"] = 30
    s["config"]["swapGate"] = swap_gate
    s["priceMode"] = "test"
    holdings = []
    for i in range(7):
        holdings.append({"name": "旧仓%d" % i, "code": "sh60010%d" % i,
                         "shares": 1000, "price": 10.0, "cost": 10.0, "pct": 0.0})
    holdings.append({"name": "三一重工", "code": "sh600031",
                     "shares": 226, "price": 18.0, "cost": 19.55, "pct": 0.0})
    s["holdings"] = holdings
    s["cash"] = 20000.0
    s["pool"] = [{"name": "三一重工", "code": "sh600031"},
                 {"name": "东方环宇", "code": "sh603706"}]
    s["poolQuote"] = {"sh600031": {"price": 18.0},
                      "sh603706": {"price": 28.0}}
    return s


def pend_sell(code, name, forced=False, note="forced 处置", state="approved"):
    return {"code": code, "name": name, "direction": "卖出", "state": state,
            "forced": forced, "px": 18.0,
            "queuedAt": server.now_str(), "reviewedAt": server.now_str(),
            "review": {"verdict": "SELL", "confidence": 78, "note": note}}


BUY_ITEM = {"name": "东方环宇", "code": "sh603706"}

print("一、满席 + 无已批 SELL → 买入初审仍否决（旧行为不变）")
s = mk_state()
v = server._risk_check(s, BUY_ITEM, "sh603706", "买入", 28.0, advisory=True)
chk("否决", "只数已达上限" in v)
chk("swapGate 未误放行（无配对卖出）", v != "")

print("二、满席 + forced SELL 已批 → 买入按先卖后买放行")
s = mk_state()
s["pendingReview"] = [pend_sell("sh600031", "三一重工", forced=True)]
v = server._risk_check(s, BUY_ITEM, "sh603706", "买入", 28.0, advisory=True)
chk("放行（空串）", v == "")

print("三、满席 + R15 清仓标记 SELL 已批 → 放行")
s = mk_state()
s["pendingReview"] = [pend_sell("sh600031", "三一重工", forced=False,
                                note="R15清仓腾位 三一重工 226股（腾出 1 席）【R15清仓腾位:三一重工 226股】")]
v = server._risk_check(s, BUY_ITEM, "sh603706", "买入", 28.0, advisory=True)
chk("放行（空串）", v == "")

print("四、满席 + 普通 SELL（sellPct 部分卖出）已批 → 仍否决（不清仓不腾位）")
s = mk_state()
s["pendingReview"] = [pend_sell("sh600031", "三一重工", forced=False,
                                note="趋势走弱，部分减持【R15席位未释放:三一重工 余113股】")]
v = server._risk_check(s, BUY_ITEM, "sh603706", "买入", 28.0, advisory=True)
chk("否决", "只数已达上限" in v)

print("五、forced SELL 尚在 waiting（未批）→ 不放行")
s = mk_state()
s["pendingReview"] = [pend_sell("sh600031", "三一重工", forced=True, state="waiting")]
v = server._risk_check(s, BUY_ITEM, "sh603706", "买入", 28.0, advisory=True)
chk("否决", "只数已达上限" in v)

print("六、swapGate=False → 即使有 forced 已批 SELL 也否决（可一键回退）")
s = mk_state(swap_gate=False)
s["pendingReview"] = [pend_sell("sh600031", "三一重工", forced=True)]
v = server._risk_check(s, BUY_ITEM, "sh603706", "买入", 28.0, advisory=True)
chk("否决", "只数已达上限" in v)

print("七、同轮先卖后买：BUY 排在 SELL 前，执行后席位真正腾出并回补")
server.in_trading_hours = lambda now=None: True
s = mk_state()
# 队列顺序故意 BUY 在前、forced SELL 在后
buy_p = {"code": "sh603706", "name": "东方环宇", "direction": "买入", "state": "approved",
         "px": 28.0, "queuedAt": server.now_str(), "reviewedAt": server.now_str(),
         "review": {"verdict": "BUY", "confidence": 72, "note": "R1命中 5 项"}}
sell_p = pend_sell("sh600031", "三一重工", forced=True)
s["pendingReview"] = [buy_p, sell_p]
done = server.execute_approved(s)
alive = [h for h in s["holdings"] if h["shares"] > 0]
sany = server.find_holding(s, "sh600031")
dfh = server.find_holding(s, "sh603706")
chk("两笔都成交", done == 2)
chk("三一清仓（226 股全卖，席位释放）", sany is None or sany["shares"] <= 0)
chk("东方环宇已买入", dfh is not None and dfh["shares"] > 0)
chk("持仓只数仍为 8（先卖 1 再买 1）", len(alive) == 8)
sides = [t["side"] for t in s["trades"]]
# trades 列表最新在前（insert(0)），故先卖后买体现为 [买入, 卖出]
chk("成交顺序先卖后买（trades 最新在前）", sides == ["买入", "卖出"])
chk("队列清空", len(s["pendingReview"]) == 0)

print("八、forced SELL 全量卖出（旧行为只卖 100/326）")
s = mk_state()
h = server.find_holding(s, "sh600031")
h["shares"] = 326
s["pendingReview"] = [pend_sell("sh600031", "三一重工", forced=True)]
server.execute_approved(s)
h = server.find_holding(s, "sh600031")
chk("326 股全部卖出", h["shares"] <= 0)
chk("单笔卖出股数 326（非旧逻辑 100）",
    any(t["code"] == "sh600031" and t["qty"] == 326 for t in s["trades"]))

print("九、普通 SELL 仍按 sellPct 部分卖出（1000 股 → 500）")
s = mk_state()
h = server.find_holding(s, "sh600031")
h["shares"] = 1000
s["pendingReview"] = [pend_sell("sh600031", "三一重工", forced=False,
                                note="技术面走弱，部分减持")]
server.execute_approved(s)
h = server.find_holding(s, "sh600031")
chk("卖出 500 股", any(t["code"] == "sh600031" and t["qty"] == 500 for t in s["trades"]))
chk("余 500 股", h["shares"] == 500)

print("十、R15 清仓标记的普通 SELL → 全量")
s = mk_state()
s["pendingReview"] = [pend_sell("sh600031", "三一重工", forced=False,
                                note="清仓腾位【R15清仓腾位:三一重工 226股】")]
server.execute_approved(s)
h = server.find_holding(s, "sh600031")
chk("226 股全部卖出", h["shares"] <= 0)

print("十一、note 里只出现『清仓』字样但无 R15 标记 → 不误判为清仓指令")
s = mk_state()
s["pendingReview"] = [pend_sell("sh600031", "三一重工", forced=False,
                                note="建议下轮对该置换标的出清仓单【R15席位未释放:三一重工 余113股】")]
server.execute_approved(s)
h = server.find_holding(s, "sh600031")
chk("仍部分卖出 100 股（226→126）",
    any(t["code"] == "sh600031" and t["qty"] == 100 for t in s["trades"]))

print("十二、满席下 BUY 已批但配对 SELL 未批/未成交 → 买入留队不超限")
s = mk_state()
buy_p2 = dict(buy_p)
sell_waiting = pend_sell("sh600031", "三一重工", forced=True, state="waiting")
s["pendingReview"] = [dict(buy_p2), sell_waiting]
server.execute_approved(s)
alive = [h for h in s["holdings"] if h["shares"] > 0]
chk("BUY 未成交（无席位可用，持仓仍 8）", len(alive) == 8)
chk("BUY 仍在队列", any(p.get("code") == "sh603706" and p.get("state") == "approved"
                        for p in s["pendingReview"]))

print("\n结果：%d 通过，%d 失败" % (_passed, _failed))
sys.exit(1 if _failed else 0)
