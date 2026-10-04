# -*- coding: utf-8 -*-
"""验证 Sequoia-X 三个新形态：旗形突破 / 涨停洗盘 / 跌停反包。"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("srv", os.path.join(HERE, "server.py"))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

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


# ---- 构造 K 线：closes / highs / vols ----
def mk_seq(closes):
    """由收盘价序列构造 (closes, highs, vols)，highs 略高于 close，vols 默认均衡。"""
    n = len(closes)
    highs = [c * 1.01 for c in closes]
    vols = [1000.0] * n
    return closes, highs, vols


print("Sequoia-X 新形态识别")

# === 1. 旗形突破 flag_pattern ===
# 旗杆：某日 +10%；旗面：随后 4 日窄幅整理；最新价突破旗面上沿
closes = [100.0]*5 + [110.0] + [110.5, 109.8, 110.2, 110.0, 111.5]
c, h, v = mk_seq(closes)
chk("旗形向上突破=+1", srv.flag_pattern(c, h, v) == 1, str(srv.flag_pattern(c, h, v)))

# 无旗杆（没有急涨）
closes2 = [100.0 + i*0.5 for i in range(16)]
c2, h2, v2 = mk_seq(closes2)
chk("无急涨旗杆=0", srv.flag_pattern(c2, h2, v2) == 0)

# 旗形向下破位
closes3 = [100.0]*5 + [110.0] + [110.5, 109.8, 110.2, 109.0, 108.0]
c3, h3, v3 = mk_seq(closes3)
chk("旗形向下破位=-1", srv.flag_pattern(c3, h3, v3) == -1, str(srv.flag_pattern(c3, h3, v3)))

# === 2. 涨停洗盘 limitup_washout ===
# 涨停(第5根 +10%)，之后缩量回踩不破支撑
closes4 = [100.0]*5 + [110.0, 108.0, 107.5, 107.0]
c4, h4, v4 = mk_seq(closes4)
v4[5] = 2000.0      # 涨停日放量
v4[6:] = [500.0, 450.0, 480.0]  # 回踩缩量
chk("涨停后缩量回踩=洗盘", srv.limitup_washout(c4, v4) is True)

# 无涨停 → 不是洗盘
closes5 = [100.0 + i*0.3 for i in range(12)]
c5, h5, v5 = mk_seq(closes5)
chk("无涨停=非洗盘", srv.limitup_washout(c5, v5) is False)

# === 3. 跌停反包 limitdown_reversal ===
# 跌停(-10%)，次日放量阳线收复阴线实体一半
closes6 = [100.0]*5 + [90.0, 96.0]
c6, h6, v6 = mk_seq(closes6)
v6[5] = 1000.0      # 跌停日
v6[6] = 2000.0      # 反包日放量
chk("跌停次日放量反包=True", srv.limitdown_reversal(c6, v6) is True, str(srv.limitdown_reversal(c6, v6)))

# 跌停后继续阴跌 → 非反包
closes7 = [100.0]*5 + [90.0, 88.0]
c7, h7, v7 = mk_seq(closes7)
chk("跌停后继续阴跌=False", srv.limitdown_reversal(c7, v7) is False)

# === 4. tech_score 集成：A股账户产出涨停洗盘/跌停反包 note，美股不产出 ===
print("\ntech_score 集成验证（A股 vs 美股 隔离）")

# 构造涨停洗盘的 K 线（A股），需 >= 20 根满足 tech_score 门槛
# 前 15 根平稳，第 16 根涨停，之后缩量回踩
closes_wash = [100.0]*15 + [110.0, 108.0, 107.5, 107.0, 106.8, 106.5, 106.2, 106.0]
vols_wash = [800.0]*15 + [2000.0] + [500.0, 450.0, 480.0, 460.0, 440.0, 420.0, 400.0]
def mk_kl_cn(closes, vols):
    n = len(closes)
    kl = []
    for i in range(n):
        o = closes[i-1] if i > 0 else closes[i]
        high = max(o, closes[i]) * 1.01
        low = min(o, closes[i]) * 0.99
        kl.append(("2026-%02d-%02d" % (1, i+1), o, closes[i], high, low, vols[i]))
    return kl

kl_washout = mk_kl_cn(closes_wash, vols_wash)
srv.fetch_kline = lambda code, n=65, _kl=kl_washout: _kl[:n]

srv.set_account("cn_aggressive")
s_cn = srv.default_state("cn_aggressive")
s_cn["config"]["useTech"] = True
s_cn["poolQuote"] = {"sh600000": {"price": 0, "pct": 0, "amount": 5e8}}
score_cn, notes_cn = srv.tech_score(s_cn, "sh600000")
chk("A股 tech_score 含涨停洗盘 note", any("洗盘" in n for n in notes_cn),
    str(notes_cn))

srv.set_account("us")
s_us = srv.default_state("us")
s_us["config"]["useTech"] = True
s_us["poolQuote"] = {"usTEST": {"price": 0, "pct": 0, "amount": 5e8}}
score_us, notes_us = srv.tech_score(s_us, "usTEST")
chk("美股 tech_score 不含涨停洗盘 note", not any("洗盘" in n for n in notes_us),
    str(notes_us))

print("\n结果：%d 通过，%d 失败" % (PASS, FAIL))
import sys
sys.exit(1 if FAIL else 0)
