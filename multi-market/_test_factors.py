# -*- coding: utf-8 -*-
"""量价因子 + 行业轮动 RRG 单元测试。"""
import os, sys, math
os.environ["NO_WORKER"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

_passed = 0
_failed = 0

def chk(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print("  PASS %s" % name)
    else:
        _failed += 1
        print("  FAIL %s" % name)

# ---- 构造 K 线数据 ----
def mk_kl(n=65, trend=0.001, amp=0.03, up_vol_ratio=0.6, base=100.0, pulse=False,
          trend2=None, split=20):
    """生成模拟 K 线。trend=日涨幅，amp=日内振幅。
    trend2 若给出，则最后 split 根用 trend2（模拟加速/减速）。"""
    kl = []
    price = base
    import random
    rnd = random.Random(42)
    for i in range(n):
        o = price
        tr = trend2 if (trend2 is not None and i >= n - split) else trend
        if pulse and i == n - 1:
            c = o * 1.05       # 单日脉冲 +5%
        else:
            c = o * (1 + tr)
        high = max(o, c) * (1 + amp / 2)
        low = min(o, c) * (1 - amp / 2)
        v = 1000 + i * 10
        kl.append(("2026-%02d-%02d" % (1, i + 1), o, c, high, low, v))
        price = c
    return kl

def test_price_volume_factors():
    print("\n== 量价因子 price_volume_factors ==")
    # 1. 健康上涨：趋势向上 + 买压偏强 + 适中振幅
    kl_healthy = mk_kl(trend=0.003, amp=0.04, up_vol_ratio=0.65)
    server.fetch_kline = lambda code, n=65, _kl=kl_healthy: _kl[:n]
    score, notes, meta = server.price_volume_factors("sh600000")
    chk("健康上涨趋势得分>=1", score >= 1)
    chk("返回因子元数据含压力/振幅/信息比", all(k in meta for k in ("pressure", "avgAmp", "infoRatio")))
    chk("买压比>0.5", meta["pressure"] > 0.5)

    # 2. 下跌趋势：卖压偏强 + 超额收益为负
    kl_down = mk_kl(trend=-0.003, amp=0.03, up_vol_ratio=0.4)
    server.fetch_kline = lambda code, n=65, _kl=kl_down: _kl[:n]
    score2, _, meta2 = server.price_volume_factors("sh600000")
    chk("下跌趋势得分<0", score2 < 0)
    chk("卖压比<0.5", meta2["pressure"] < 0.5)

    # 3. K线不足
    server.fetch_kline = lambda code, n=65: [("d", 1, 1, 1, 1, 1)] * 5
    s3, n3, _ = server.price_volume_factors("sh600000")
    chk("K线不足返回0分", s3 == 0)

def test_sector_of():
    print("\n== 行业归类 sector_of ==")
    chk("白酒->食品饮料", server.sector_of({"tags": "白酒,食品饮料,消费"}) == "食品饮料")
    chk("半导体->电子", server.sector_of({"tags": "半导体,芯片,国产替代"}) == "电子")
    chk("券商->非银金融", server.sector_of({"tags": "券商,证券,金融"}) == "非银金融")
    chk("未知->其他", server.sector_of({"tags": "神秘行业,未知"}) == "其他")
    chk("空tags->其他", server.sector_of({"tags": ""}) == "其他")

def test_sector_rotation():
    print("\n== 行业轮动 sector_rotation ==")
    server.set_account("cn_conservative")
    s = server.default_state("cn_conservative")
    s["config"]["useSectorRotation"] = True
    # 大盘：平稳微涨
    kl_flat = mk_kl(trend=0.0005, base=100.0)
    # 行业A(半导体/电子)：强且加速（前段小涨，近20日大涨）→ leading
    kl_lead = mk_kl(trend=0.001, trend2=0.008, split=20, base=100.0)
    # 行业B(白酒/食品饮料)：弱且减速（前段小跌，近20日大跌）→ lagging
    kl_lag = mk_kl(trend=-0.001, trend2=-0.008, split=20, base=100.0)

    pool = [
        {"name": "A1", "code": "sh600001", "tags": "半导体,芯片"},
        {"name": "A2", "code": "sh600002", "tags": "半导体,芯片"},
        {"name": "B1", "code": "sh600003", "tags": "白酒,消费"},
        {"name": "B2", "code": "sh600004", "tags": "白酒,消费"},
    ]
    s["pool"] = pool
    s["config"]["indexCode"] = "sh000001"

    def fake_kl(code, n=65):
        if code == "sh000001":
            return kl_flat[:n]
        if code in ("sh600001", "sh600002"):
            return kl_lead[:n]
        return kl_lag[:n]
    server.fetch_kline = fake_kl

    res = server.sector_rotation(s)
    chk("返回 sectors 列表", res is not None and len(res.get("sectors", [])) >= 2)
    sec_map = {x["name"]: x for x in res["sectors"]}
    chk("识别出电子和食品饮料两个行业", "电子" in sec_map and "食品饮料" in sec_map)
    # 电子（大涨）相对强度应 > 食品饮料（下跌）
    chk("电子相对强度 > 食品饮料", sec_map["电子"]["ratio"] > sec_map["食品饮料"]["ratio"])
    # 电子：强且加速 → leading；食品饮料：弱且减速 → lagging
    chk("电子 tier = leading", sec_map["电子"]["tier"] == "leading")
    chk("食品饮料 tier = lagging", sec_map["食品饮料"]["tier"] == "lagging")
    # leading 列表正确
    chk("leading 含电子", "电子" in res["leading"])

    # 大盘K线不足 -> None
    server.fetch_kline = lambda code, n=65: [("d", 1, 1, 1, 1, 1)] * 10
    chk("大盘K线不足返回None", server.sector_rotation(s) is None)

test_price_volume_factors()
test_sector_of()
test_sector_rotation()

print("\n结果：%d 通过，%d 失败" % (_passed, _failed))
sys.exit(1 if _failed else 0)
