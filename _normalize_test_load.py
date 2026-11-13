# -*- coding: utf-8 -*-
"""一次性脚本：规范化 10 个测试文件的 server 加载方式，消除双实例问题。

spec_from_file_location("srv") / exec(compile) 改名加载会让 sim_* 子模块
`import server` 产生第二个实例，两套 globals 导致状态分裂。
统一改为标准 `import server`（srv = server / ns = server.__dict__），
测试体逻辑与 monkeypatch 语义零改动。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
MM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi-market")

SPEC_OLD = '''spec = importlib.util.spec_from_file_location("srv", os.path.join(HERE, "server.py"))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)'''
SPEC_OLD2 = '''spec = importlib.util.spec_from_file_location("server", os.path.join(HERE, "server.py"))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)'''
SPEC_NEW = '''import server
srv = server'''

EXEC_OLD_A = "exec(compile(open('server.py', encoding='utf-8').read(), 'server.py', 'exec'), ns)"
EXEC_OLD_B = "exec(compile(src, 'server.py', 'exec'), ns)"
EXEC_NEW = "import server\nns = server.__dict__"

spec_files = ["_test_catchup.py", "_test_gate.py", "_test_price_review.py",
              "_test_rebalance.py", "_test_sequoia.py", "_test_signal_factors.py",
              "_test_snapshot.py"]
exec_files = ["_test_council_enqueue.py", "_test_settle_session.py", "_test_ttl_sameday.py"]

for f in spec_files:
    p = os.path.join(MM, f)
    t = open(p, encoding="utf-8").read()
    if SPEC_OLD in t:
        t = t.replace(SPEC_OLD, SPEC_NEW)
    elif SPEC_OLD2 in t:
        t = t.replace(SPEC_OLD2, SPEC_NEW)
    else:
        sys.exit("!! %s 未找到 spec 加载块" % f)
    open(p, "w", encoding="utf-8", newline="").write(t)
    print("OK spec ->", f)

for f in exec_files:
    p = os.path.join(MM, f)
    t = open(p, encoding="utf-8").read()
    if EXEC_OLD_A in t:
        t = t.replace(EXEC_OLD_A, EXEC_NEW)
    elif EXEC_OLD_B in t:
        t = t.replace(EXEC_OLD_B, EXEC_NEW)
    else:
        sys.exit("!! %s 未找到 exec 加载块" % f)
    open(p, "w", encoding="utf-8", newline="").write(t)
    print("OK exec ->", f)

# server.py 装配处加双实例硬闸门
p = os.path.join(MM, "server.py")
t = open(p, encoding="utf-8").read()
GUARD_OLD = "# ---------------------------------------------------------------- 模块装配（facade）"
GUARD_NEW = ('''# ---------------------------------------------------------------- 模块装配（facade）
# 双实例硬闸门：sim_* 子模块通过 `import server` 找回共享命名空间，
# 换名加载（spec_from_file_location / exec 改名）会产生第二实例、两套 globals，
# 状态分裂且极难排查——当场报错，禁止。
if __name__ != "server":
    raise ImportError("server.py 必须以 `import server` 方式加载（生产入口 python run.py）；"
                      "改名加载会导致 sim_* 子模块双实例")''')
if GUARD_OLD not in t:
    sys.exit("!! server.py 未找到装配注释")
open(p, "w", encoding="utf-8", newline="").write(t.replace(GUARD_OLD, GUARD_NEW, 1))
print("OK server.py 装配闸门已加")
