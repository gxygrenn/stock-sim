# -*- coding: utf-8 -*-
"""成交流水单笔已实现盈亏（2026-11-27 新增）纯逻辑测试，不碰真实存档、不发网络请求。

口径：卖出成交记录新增 pnl（净盈亏）/ pnlPct（相对成本）/ costPrice（当时的持仓成本，
含买入费）；与 s["realized"] 同口径（成本含买入费、已扣卖出费）。买入记录不带 pnl。
"""
import sys
sys.path.insert(0, '.')
import server

ns = server.__dict__
do_buy = ns['do_buy']
do_sell = ns['do_sell']
buy_fee = ns['buy_fee']
sell_fee = ns['sell_fee']

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
    cfg = ns['US_CONFIG'] if account == 'us' else ns['CN_CONSERVATIVE_CONFIG']
    return {
        "account": account, "config": dict(cfg),
        "cash": float(cash), "fees": 0.0, "realized": 0.0, "seq": 1,
        "pool": [], "holdings": [], "trades": [], "signals": {},
        "idx": 100.0, "initCapital": float(cash),
        "history": [], "nextDate": "2026-11-27", "priceMode": "mock",
        "lastTrade": {}, "roundTrades": 0, "seenNews": [], "log": [],
    }


# ============ 一、A 股盈利卖出 ============
s = mk_state('cn_conservative')
do_buy(s, '测试股', 'sh600000', 10.0, 1000, '建仓')
buy = s['trades'][0]
check("买入记录不带 pnl 字段", 'pnl' not in buy, buy.get('pnl'))
cost = (10000 + buy_fee(10000, s)) / 1000
do_sell(s, '测试股', 'sh600000', 11.0, 1000, '止盈')
sell = s['trades'][0]
exp_real = (11.0 - cost) * 1000 - sell_fee(11000, s)
check("卖出记录 pnl = 毛差价-卖出费", abs(sell['pnl'] - round(exp_real, 2)) < 1e-6,
      (sell.get('pnl'), round(exp_real, 2)))
check("卖出记录 pnl 与 realized 同口径", abs(sell['pnl'] - s['realized']) < 1e-6,
      (sell['pnl'], s['realized']))
check("pnlPct = 净盈亏/成本额", abs(sell['pnlPct'] - round(exp_real / (cost * 1000) * 100, 2)) < 1e-6,
      sell.get('pnlPct'))
check("costPrice = 当时含费成本", abs(sell['costPrice'] - round(cost, 3)) < 1e-6, sell.get('costPrice'))
check("盈利笔 pnl 为正", sell['pnl'] > 0, sell['pnl'])
check("清仓笔 pnlPct 不为 0（成本清零前已计算）", sell['pnlPct'] > 9, sell.get('pnlPct'))

# ============ 二、A 股亏损卖出 ============
s2 = mk_state('cn_conservative')
do_buy(s2, '亏损股', 'sh600001', 10.0, 1000, '建仓')
c2 = s2['holdings'][0]['cost']
do_sell(s2, '亏损股', 'sh600001', 9.0, 1000, '止损')
sl = s2['trades'][0]
exp2 = (9.0 - c2) * 1000 - sell_fee(9000, s2)
check("亏损笔 pnl 为负且数值正确", abs(sl['pnl'] - round(exp2, 2)) < 1e-6, (sl['pnl'], round(exp2, 2)))
check("亏损笔 pnlPct 为负", sl['pnlPct'] < 0, sl.get('pnlPct'))

# ============ 三、部分卖出：收益率按当时成本算，持仓成本不被改写 ============
s3 = mk_state('cn_conservative')
do_buy(s3, '分批股', 'sh600002', 10.0, 1000, '建仓')
c3 = s3['holdings'][0]['cost']
do_sell(s3, '分批股', 'sh600002', 11.0, 400, '减半')
part = s3['trades'][0]
exp3 = (11.0 - c3) * 400 - sell_fee(4400, s3)
check("部分卖出 pnl 只算已卖股数", abs(part['pnl'] - round(exp3, 2)) < 1e-6, (part['pnl'], round(exp3, 2)))
check("部分卖出后持仓成本不变", abs(s3['holdings'][0]['cost'] - c3) < 1e-9, s3['holdings'][0]['cost'])
check("部分卖出后余 600 股", s3['holdings'][0]['shares'] == 600, s3['holdings'][0]['shares'])

# ============ 四、多笔卖出 pnl 之和 = realized 增量 ============
s4 = mk_state('cn_conservative')
do_buy(s4, '多笔股', 'sh600003', 10.0, 1000, '建仓')
do_sell(s4, '多笔股', 'sh600003', 10.8, 500, '止盈一')
do_sell(s4, '多笔股', 'sh600003', 9.6, 500, '减仓二')
tot = sum(t['pnl'] for t in s4['trades'] if t['side'] == '卖出')
check("多笔卖出 pnl 累加等于 realized", abs(tot - s4['realized']) < 1e-6, (round(tot, 2), round(s4['realized'], 2)))
check("一盈一亏两笔符号相反", s4['trades'][1]['pnl'] > 0 > s4['trades'][0]['pnl'],
      (s4['trades'][1]['pnl'], s4['trades'][0]['pnl']))

# ============ 五、美股口径（买入 0 费、卖出 SEC 费 2.3bp） ============
s5 = mk_state('us')
do_buy(s5, 'Test Corp', 'usTEST', 100.0, 10, '建仓')
check("美股买入费为 0", s5['trades'][0]['fee'] == 0, s5['trades'][0]['fee'])
do_sell(s5, 'Test Corp', 'usTEST', 110.0, 10, '止盈')
us_sell = s5['trades'][0]
exp5 = (110.0 - 100.0) * 10 - round(1100 * 0.000023, 2)
check("美股卖出 pnl 扣 SEC 费", abs(us_sell['pnl'] - exp5) < 0.02, (us_sell['pnl'], exp5))
check("美股 pnlPct 约 +10%（仅扣 SEC 费）", abs(us_sell['pnlPct'] - 10.0) < 0.02, us_sell.get('pnlPct'))

# ============ 六、边界：无持仓卖出不产生记录 ============
n_before = len(s5['trades'])
do_sell(s5, 'Test Corp', 'usTEST', 120.0, 5, '无效')
check("无持仓卖出不写流水", len(s5['trades']) == n_before, len(s5['trades']))

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
