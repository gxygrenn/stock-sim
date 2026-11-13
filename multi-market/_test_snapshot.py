# -*- coding: utf-8 -*-
"""测试跨部署快照持久化：save_state 写快照 + load_state 从快照恢复。"""
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


# 用临时目录替换 BASE / DATA_FILE / SNAPSHOT_DIR，避免污染真实存档
tmpdir = tempfile.mkdtemp(prefix="snapshot_test_")
srv.BASE = tmpdir
srv.DATA_FILE = os.path.join(tmpdir, "data-multi.json")
srv.SNAPSHOT_DIR = os.path.join(tmpdir, "snapshot")
srv.SNAPSHOT_FILE = os.path.join(srv.SNAPSHOT_DIR, "data-multi.snapshot.json")
srv._states = {}
srv._save_broken = False

print("== 快照持久化测试 ==")

# 1. 造一个账户状态并 save_state
acct = "us"
st = srv.default_state(acct)
st["cash"] = 88888.88
st["positions"] = [{"code": "usAMD", "name": "AMD", "qty": 10}]
st["reviewLog"] = [{"code": "usAMD", "verdict": "BUY", "priceAtReview": 100.0}]
srv._states[acct] = st
srv.save_state()

chk("主存档已写入", os.path.exists(srv.DATA_FILE))
chk("快照已写入", os.path.exists(srv.SNAPSHOT_FILE))

# 2. 快照内容与主存档一致
snap = json.load(open(srv.SNAPSHOT_FILE, encoding="utf-8"))
chk("快照含 us 账户", "us" in snap)
chk("快照 cash 正确", snap["us"]["cash"] == 88888.88)

# 3. 模拟重新部署：删除主存档（快照保留），load_state 应从快照恢复
os.remove(srv.DATA_FILE)
srv._states = {}
recovered = srv.load_state(acct)
chk("主存档删除后从快照恢复 cash", recovered.get("cash") == 88888.88)
chk("恢复 positions", len(recovered.get("positions", [])) == 1)
chk("恢复 reviewLog", len(recovered.get("reviewLog", [])) == 1)
chk("恢复日志含提示", any("快照恢复" in m.get("msg", "") for m in recovered.get("log", [])))

# 4. 主存档和快照都缺失 → 用默认新账户（不崩溃）
srv._states = {}
os.path.exists(srv.SNAPSHOT_FILE) and os.remove(srv.SNAPSHOT_FILE)
srv.DATA_FILE = os.path.join(tmpdir, "nonexist.json")
fresh = srv.load_state(acct)
chk("无任何存档时回退默认账户", fresh.get("cash") == srv.default_state(acct).get("cash"))

print("\n结果: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
