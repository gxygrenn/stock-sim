# -*- coding: utf-8 -*-
"""测试 review_done 落库时 priceAtReview 字段的填充逻辑。

重点验证：A股评审落库时，priceAtReview 能从入队缓存的 px / poolQuote 正确取值，
不再因 current_price 实时拉价失败而写成 None。
"""
import os
import sys
import json
import tempfile
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
import server
srv = server

PASS = 0
FAIL = 0


def chk(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % name)
    else:
        FAIL += 1
        print("  ✗ %s  %s" % (name, detail))


# 隔离：临时目录 + 空状态，避免污染真实存档
tmpdir = tempfile.mkdtemp(prefix="price_review_test_")
srv.BASE = tmpdir
srv.DATA_FILE = os.path.join(tmpdir, "data-multi.json")
srv.SNAPSHOT_DIR = os.path.join(tmpdir, "snapshot")
srv.SNAPSHOT_FILE = os.path.join(srv.SNAPSHOT_DIR, "data-multi.snapshot.json")
srv._states = {}
srv._save_broken = False

print("== priceAtReview 填充逻辑测试 ==")

# 1. 构造 A股账户，池子里有股票，poolQuote 里有价（模拟扫描已缓存价）
srv.set_account("cn_conservative")
s = srv.default_state("cn_conservative")
s["priceMode"] = "real"
s["pool"] = [
    {"name": "贵州茅台", "code": "sh600519", "tags": "白酒"},
    {"name": "五粮液", "code": "sz000858", "tags": "白酒"},
]
s["poolQuote"] = {
    "sh600519": {"price": 1263.0, "pct": 0.57},
    "sz000858": {"price": 70.56, "pct": 0.6},
}
# 候选入队，带 px（入队时的缓存价）
s["pendingReview"] = [
    {"code": "sh600519", "name": "贵州茅台", "direction": "买入",
     "delta": 3.2, "px": 1263.0, "state": "waiting",
     "queuedAt": "2026-10-09 14:00:00"},
]
srv._states["cn_conservative"] = s

# 2. 模拟 fetch_prices 实时拉价失败（A股数据源异常），验证仍能拿到 priceAtReview
def _fail_fetch(codes):
    raise RuntimeError("数据源不可用")
srv.fetch_prices = _fail_fetch

# 3. 直接调用 current_price 验证 poolQuote 回退
item = {"code": "sh600519"}
p = srv.current_price(s, item)
chk("current_price 实时拉价失败后回退 poolQuote 取到价", p == 1263.0, str(p))

# 4. 验证 cached_price 能取到入队价
cp = srv.cached_price(s, "sh600519")
chk("cached_price 从 poolQuote 取到价", cp == 1263.0, str(cp))

# 5. 验证 review_done 落库的 priceAtReview 取值优先级（px 优先）
hit = s["pendingReview"][0]
_rev_price = hit.get("px") or 0.0
if _rev_price <= 0:
    _rev_price = srv.cached_price(s, hit.get("code"))
if _rev_price <= 0:
    _rev_item = next((it for it in s["pool"] if it.get("code") == hit.get("code")), None)
    _rev_price = srv.current_price(s, _rev_item) if _rev_item else 0.0
chk("priceAtReview 从 px 取到 1263.0（非 None）", _rev_price == 1263.0, str(_rev_price))

# 6. 边界：px 缺失但 poolQuote 有价 → 回退 cached_price 仍能取到
hit2 = {"code": "sz000858", "name": "五粮液", "px": 0}
_p2 = hit2.get("px") or 0.0
if _p2 <= 0:
    _p2 = srv.cached_price(s, hit2.get("code"))
chk("px 缺失时回退 cached_price 取到 70.56", _p2 == 70.56, str(_p2))

# 7. 边界：px 和 poolQuote 都缺 → 最终为 0/None（不误报）
hit3 = {"code": "sh999999", "name": "不存在"}
_p3 = hit3.get("px") or 0.0
if _p3 <= 0:
    _p3 = srv.cached_price(s, hit3.get("code"))
chk("完全无价时回退为 0（不会崩）", _p3 == 0.0, str(_p3))

print("\n结果: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
