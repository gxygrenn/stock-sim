# -*- coding: utf-8 -*-
"""A 股费率（2026-11-26 用户拍板新增）纯逻辑测试，不碰真实存档、不发网络请求。

口径：A 股佣金万 2.5（单笔最低 5 元，双边）+ 印花税 0.05%（仅卖出）+ 过户费 0.001%（双边）；
美股维持原样（买入 0、卖出 SEC 费 2.3bp）。
另验证费用流向：买入费摊进持仓成本（h["cost"]），卖出费冲减已实现收益（realized）。
"""
import sys
sys.path.insert(0, '.')
import server

ns = server.__dict__
buy_fee = ns['buy_fee']
sell_fee = ns['sell_fee']
do_buy = ns['do_buy']
do_sell = ns['do_sell']

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS  %s" % name)
    else:
        fail += 1
        print("FAIL  %s  %s" % (name, detail))


def mk_state(account, cash=1_000_000.0):
    return {
        "account": account,
        "config": dict(ns['CN_CONSERVATIVE_CONFIG']),
        "cash": float(cash), "fees": 0.0, "realized": 0.0, "seq": 1,
        "pool": [], "holdings": [], "trades": [], "signals": {},
        "idx": 100.0, "initCapital": float(cash),
        "history": [], "nextDate": "2026-11-26", "priceMode": "mock",
        "lastTrade": {}, "roundTrades": 0, "seenNews": [], "log": [],
    }


# ============ 一、费率函数本身 ============
check("A股买入 10万 -> 佣金25+过户1=26", buy_fee(100000, {"account": "cn_conservative"}) == 26.0,
      buy_fee(100000, {"account": "cn_conservative"}))
check("A股买入 1000 -> 最低佣金5+过户0.01=5.01", buy_fee(1000, {"account": "cn_aggressive"}) == 5.01,
      buy_fee(1000, {"account": "cn_aggressive"}))
check("A股卖出 10万 -> 佣金25+印花50+过户1=76", sell_fee(100000, {"account": "cn_conservative"}) == 76.0,
      sell_fee(100000, {"account": "cn_conservative"}))
check("A股卖出 1000 -> 5+0.5+0.01=5.51", sell_fee(1000, {"account": "cn_conservative"}) == 5.51,
      sell_fee(1000, {"account": "cn_conservative"}))
check("美股买入仍为 0", buy_fee(100000, {"account": "us"}) == 0.0, buy_fee(100000, {"account": "us"}))
check("美股卖出仍为 SEC 费 2.3bp", sell_fee(100000, {"account": "us"}) == 2.30,
      sell_fee(100000, {"account": "us"}))
check("卖出费 > 买入费（印花税只在卖出端）",
      sell_fee(50000, {"account": "cn_conservative"}) > buy_fee(50000, {"account": "cn_conservative"}))
# 未知账户回落当前请求账户（默认 cn_conservative）
ns['current_account'] = lambda: 'cn_conservative'
check("无 account 字段时回落当前账户 -> 按A股计费", buy_fee(100000) == 26.0, buy_fee(100000))

# ============ 二、买入：费用进成本、扣现金 ============
# 注：A 股 config.minBuyAmount=2000，测试单笔金额须高于该门槛
s = mk_state("cn_conservative", 100000.0)
tr = do_buy(s, "测试股", "sh600000", 20.0, 100, "单测")
check("买入成交返回记录", tr is not None)
check("买入现金扣 2000+5.02", abs(s["cash"] - (100000.0 - 2005.02)) < 1e-6, s["cash"])
check("买入累计手续费 5.02", abs(s["fees"] - 5.02) < 1e-6, s["fees"])
check("买入费摊进成本 -> 20.0502", abs(s["holdings"][0]["cost"] - 20.0502) < 1e-6,
      s["holdings"][0]["cost"])
check("成交记录带 fee 字段", tr.get("fee") == 5.02, tr.get("fee"))

# 加仓：两笔费用一并摊薄
do_buy(s, "测试股", "sh600000", 30.0, 100, "加仓")
exp_cost = (2005.02 + 3005.03) / 200.0
check("加仓后成本含两笔费用", abs(s["holdings"][0]["cost"] - exp_cost) < 1e-6,
      s["holdings"][0]["cost"])

# ============ 三、卖出：费用冲减已实现 ============
s2 = mk_state("cn_conservative", 100000.0)
do_buy(s2, "测试股", "sh600000", 20.0, 100, "建仓")
cash_after_buy = s2["cash"]
tr = do_sell(s2, "测试股", "sh600000", 22.0, 100, "止盈")
# 2200: 佣金 max(0.55,5)=5 + 印花 1.1 + 过户 0.022 = 6.12
check("卖出费 6.12（最低佣金生效）", tr.get("fee") == 6.12, tr.get("fee"))
check("卖出现金加 2200-6.12", abs(s2["cash"] - (cash_after_buy + 2200 - 6.12)) < 1e-6, s2["cash"])
exp_real = (22.0 - 20.0502) * 100 - 6.12
check("已实现收益已扣卖出费", abs(s2["realized"] - exp_real) < 1e-6, s2["realized"])
check("清仓后持仓清零", s2["holdings"][0]["shares"] == 0)

# ============ 四、美股不受影响 ============
s3 = mk_state("us", 100000.0)
s3["config"] = dict(ns['US_CONFIG'])
do_buy(s3, "测试美股", "usAAPL", 100.0, 10, "单测")
check("美股买入零费用、成本=成交价", abs(s3["holdings"][0]["cost"] - 100.0) < 1e-9 and s3["fees"] == 0.0,
      (s3["holdings"][0]["cost"], s3["fees"]))

# ============ 五、现金不足时费用计入判断 ============
s4 = mk_state("cn_conservative", 1000.0)
check("现金 1000 买 2000 元 A股被拒（需 2005.02）",
      do_buy(s4, "测试股", "sh600000", 20.0, 100, "单测") is None)
s4["cash"] = 2005.02
check("现金刚好 2005.02 可成交",
      do_buy(s4, "测试股", "sh600000", 20.0, 100, "单测") is not None)

print("\n结果：%d 通过 / %d 失败" % (ok, fail))
sys.exit(1 if fail else 0)
