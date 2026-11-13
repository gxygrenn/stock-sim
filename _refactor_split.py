# -*- coding: utf-8 -*-
"""一次性脚本：把 multi-market/server.py 按功能拆分为多模块。

原则：
- 保留核心 server.py = 状态存档 + 测试打桩面 + facade 装配；
- 子模块对打桩面函数/核心可变全局的引用改写为 server.<名>；
- 其余跨模块引用依赖装配期命名空间联合（与单体等价）；
- 代码块按行原文搬移，注释全部保留；字符串/注释中的名字不改写。
"""
import ast
import builtins
import json
import os
import re
import subprocess
import sys
import symtable

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.abspath(__file__))
MM = os.path.join(ROOT, "multi-market")
SRC = os.path.join(MM, "server.py")

MAPPING = {
    "sim_config": ["GOOD", "BAD", "US_POOL", "CN_POOL", "CN_CONSERVATIVE_CONFIG",
                   "CN_AGGRESSIVE_CONFIG", "US_CONFIG", "ACCOUNTS", "DEFAULT_ACCOUNT", "ACCOUNT_ALIASES"],
    "sim_quotes": ["UA", "http_get", "_f", "_fmt_ts", "fetch_quote", "fetch_news"],
    "sim_signals": ["tech_score", "flag_pattern", "limitup_washout", "limitdown_reversal",
                    "_linreg", "_rsrs", "_regime_rsrs_score", "_regime_momentum_score",
                    "_regime_breadth_score", "market_regime", "_volatility_profile", "_vol_scale",
                    "lhb_score", "price_volume_factors", "_SECTOR_MAP", "sector_of", "sector_rotation"],
    "sim_pool": ["ANN_GOOD", "ANN_BAD", "ANN_STRONG", "_cn_code_prefix", "_RANK_CACHE", "_RANK_TTL",
                 "_rank_get", "_is_bad_name", "pool_remove_candidates", "build_rebalance",
                 "execute_approved_pool_changes", "ann_score", "val_filter", "compute_signals",
                 "score_text", "match_pool", "pool_keys"],
    "sim_account": ["total_mv", "total_asset", "buy_fee", "sell_fee", "find_holding", "do_buy", "do_sell",
                    "CN_HOLIDAYS_2026", "is_cn_holiday", "next_trading_day", "_closed_session_label",
                    "settle", "_update_peak", "check_stops"],
    "sim_council": ["ANALYSTS", "_stance", "_regime_tier", "_val_opinion", "_flow_opinion",
                    "_is_clear_sell", "_approved_clear_sell_pending", "_risk_check", "run_council", "decide"],
    "sim_time": ["age_hours", "us_dst", "cn_trading", "us_trading", "_next_session_open",
                 "approved_age_hours", "cached_price"],
    "sim_engine": ["daily_facts", "expire_stale_reviews", "_link_review_log", "maybe_catchup",
                   "scan", "enqueue_review_candidates", "execute", "done_today_count", "buys_today",
                   "quota_block"],
    "sim_web": ["INDEX_HTML", "Handler", "worker", "main"],
    "CORE": ["now_str", "today_str", "default_state", "set_account", "current_account", "load_state",
             "_restore_from_snapshot", "save_state", "_write_snapshot", "log",
             "_states", "_lock", "_save_broken", "_last_catchup", "_tls",
             "KLINE_HOSTS", "_kline_ok", "_kline_cache", "_kline_ttl",
             "fetch_kline", "fetch_prices", "fetch_announcements", "fetch_lhb",
             "fetch_strength_candidates", "refresh_prices", "execute_approved",
             "in_trading_hours", "current_price"],
}
# 子模块中必须改写为 server.<名> 的打桩面 + 核心可变全局
PREFIX_SET = ["fetch_kline", "fetch_prices", "fetch_announcements", "fetch_lhb",
             "fetch_strength_candidates", "refresh_prices", "execute_approved",
             "current_price", "in_trading_hours", "_last_catchup",
             "_states", "_save_broken", "_lock", "_tls", "DATA_FILE",
             "SNAPSHOT_FILE", "SNAPSHOT_DIR", "BASE"]
DOC = {
    "sim_config": "词库与三账户配置模板（纯数据）。由 server.py 装配注入共享命名空间。",
    "sim_quotes": "行情/新闻网络获取（http 层）。打桩面 fetch_* 保留在 server.py。",
    "sim_signals": "技术信号：K线打分、形态、RSRS择时、波动率、量价因子与行业轮动。",
    "sim_pool": "动态调池：强度榜候选、剔除、再平衡、公告打分、信号综合计算。",
    "sim_account": "账户与交易：买卖成交、费用、节假日、结算、峰值跟踪与止盈止损。",
    "sim_council": "专家评议团：初审意见、风控复核、裁决与快讯匹配。",
    "sim_time": "交易时段与时间工具：A股/美股开市判定、批单时效、价格缓存。",
    "sim_engine": "扫描引擎：每日事实包、评审队列进出、执行撮合与当日限额。",
    "sim_web": "HTTP 接口与页面（Handler）、后台 worker、启动入口 main()。",
}
name2mod = {}
for mod, names in MAPPING.items():
    for n in names:
        if n in name2mod:
            sys.exit("名字重复映射: %s" % n)
        name2mod[n] = mod


def dump_names(tag):
    out = os.path.join(MM, "_names_%s.json" % tag)
    code = ("import json,server;json.dump(sorted(n for n in dir(server) "
            "if not n.startswith('__')),open(r'%s','w'))" % out)
    subprocess.run([sys.executable, "-c", code], cwd=MM, check=True,
                   env={**os.environ, "NO_PROXY": "*", "no_proxy": "*"})
    return json.load(open(out, encoding="utf-8"))


def node_name(n):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [n.name]
    if isinstance(n, ast.Assign):
        out = []
        for t in n.targets:
            if isinstance(t, ast.Name):
                out.append(t.id)
            elif isinstance(t, ast.Tuple):
                out += [e.id for e in t.elts if isinstance(e, ast.Name)]
        return out
    return []


def main():
    old_names = dump_names("old")
    if "模块装配（facade）" in open(SRC, encoding="utf-8").read():
        sys.exit("server.py 已是拆分后形态，本脚本仅可对单体运行一次，禁止重跑")
    src = open(SRC, encoding="utf-8", newline="").read()
    lines = src.split("\n")
    tree = ast.parse(src)

    movable = [n for n in tree.body if n.lineno >= 56 and not (
        isinstance(n, ast.If) and n.lineno >= 4100)]
    blocks = []
    for i, n in enumerate(movable):
        start = movable[i - 1].end_lineno + 1 if i else 56
        blocks.append({"node": n, "start": start, "end": n.end_lineno,
                       "names": node_name(n), "text": "\n".join(lines[start - 1:n.end_lineno])})

    assigned = {}
    for b in blocks:
        if not b["names"]:
            sys.exit("无名可映射的顶层节点 @%d: %s" % (b["start"], b["text"][:60]))
        for nm in b["names"]:
            if nm not in name2mod:
                sys.exit("未映射的名字: %s @%d" % (nm, b["start"]))
            if nm in assigned:
                sys.exit("名字重复定义: %s" % nm)
            assigned[nm] = b
    missing = set(name2mod) - set(assigned)
    if missing:
        sys.exit("映射了但源码缺失的名字: %s" % sorted(missing))

    header = "\n".join(lines[0:55])
    header = header.replace(
        "if __name__ != \"__main__\":",
        "if os.environ.get(\"SIM_PRODUCTION\") != \"1\":")
    header = header.replace(
        "# 存档冲成默认值（当日已从备份恢复）。凡非 `python server.py` 直接运行的导入场景，\n"
        "    # 数据文件一律重定向到 PID 后缀临时文件并在进程退出时清理，从根上杜绝污染。",
        "# 存档冲成默认值（当日已从备份恢复）。生产入口 run.py 会设 SIM_PRODUCTION=1；其余\n"
        "    # 一切场景（测试/调试/任何 import server）自动重定向到 PID 临时文件并在退出时清理。")

    import_block = "\n".join(lines[23:32])  # 第24~32行：import 语句

    def mask(text):
        tree2 = ast.parse(text)
        spans = []
        for nd in ast.walk(tree2):
            if isinstance(nd, ast.Constant) and isinstance(nd.value, str):
                spans.append((nd.lineno, nd.col_offset, nd.end_lineno, nd.end_col_offset))
            elif isinstance(nd, ast.JoinedStr):
                spans.append((nd.lineno, nd.col_offset, nd.end_lineno, nd.end_col_offset))
        tlines = text.split("\n")
        offs, cur = [], 0
        for l in tlines:
            offs.append(cur)
            cur += len(l) + 1
        chars = []
        for li, l in enumerate(tlines):
            st = offs[li]
            if l.lstrip().startswith("#"):
                spans.append((li + 1, 0, li + 1, len(l)))
        real = []
        for (a, b, c, d) in spans:
            real.append((offs[a - 1] + b, offs[c - 1] + d))
        real.sort()
        masked, pos, stores = "", 0, []
        for (s, e) in real:
            if s < pos:
                continue
            masked += text[pos:s] + "\x00%d\x00" % len(stores)
            stores.append(text[s:e])
            pos = e
        masked += text[pos:]
        return masked, stores

    def rewrite_submodule(mod, text):
        # 1) 剥掉对核心可变全局的 global 声明（_last_catchup 等）
        text = re.sub(r"[ \t]*global (_last_catchup|_states|_save_broken)\s*\n", "", text)
        # 2) 打桩面/核心全局 → server.<名>
        masked, stores = mask(text)
        for nm in PREFIX_SET:
            masked = re.sub(r"(?<![\w.])\b%s\b" % nm, "server." + nm, masked)
        out = []
        store_i = [0]

        def unmask_chunk(chunk):
            def rep(m):
                idx = int(m.group(1))
                return stores[idx]
            return re.sub("\x00(\\d+)\x00", rep, chunk)

        return unmask_chunk(masked)

    core_text = []
    sub_texts = {m: [] for m in MAPPING if m != "CORE"}
    for b in blocks:
        mod = name2mod[b["names"][0]]
        if len(set(name2mod[x] for x in b["names"])) != 1:
            sys.exit("同一块跨模块: %s @%d" % (b["names"], b["start"]))
        (core_text if mod == "CORE" else sub_texts[mod]).append(b["text"])

    for m, chunks in sub_texts.items():
        body = rewrite_submodule(m, "\n\n".join(chunks))
        head = ("# -*- coding: utf-8 -*-\n\"\"\"%s\"\"\"\n\n%s\nimport server  # noqa: E402\n\n"
                % (DOC[m], import_block))
        sub_texts[m] = head + body.strip("\n") + "\n"

    wiring = '''

# ---------------------------------------------------------------- 模块装配（facade）
# 2026-11-13 架构拆分：本文件保留「状态存档 + 测试打桩面 + 执行底座」，
# 其余按功能拆至 sim_*.py。装配保持单体时代的单一命名空间语义：
# ① 子模块定义装回 server 命名空间（import server 的测试与脚本直接可用）；
# ② server 共享全局注入各子模块（子模块内裸名引用与单体等价）。
# 注意：打桩面函数（fetch_* / refresh_prices / execute_approved / in_trading_hours /
# current_price / _last_catchup 等）在子模块中的引用已统一改写为 server.<名>，
# 保证 monkeypatch server.<名> 后全链路生效。
import types

import sim_config, sim_quotes, sim_signals, sim_pool, sim_account  # noqa: E402
import sim_council, sim_time, sim_engine, sim_web  # noqa: E402

_SUBMODULES = (sim_config, sim_quotes, sim_signals, sim_pool, sim_account,
               sim_council, sim_time, sim_engine, sim_web)
for _m in _SUBMODULES:
    for _k, _v in list(_m.__dict__.items()):
        if _k.startswith("__") or _k == "server" or isinstance(_v, types.ModuleType):
            continue
        globals().setdefault(_k, _v)
for _m in _SUBMODULES:
    for _k, _v in list(globals().items()):
        if _k.startswith("__"):
            continue
        _m.__dict__.setdefault(_k, _v)
del _m, _k, _v, _SUBMODULES
'''
    new_server = (header.rstrip("\n") + "\n\n\n" +
                  "\n\n".join(t.strip("\n") for t in core_text) + "\n" + wiring)
    # server.py 不再作为入口运行（生产入口 run.py），移除 __main__ 块

    run_py = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产入口（沙箱 startCmd = python run.py）。

server.py 自 2026-11-13 拆分后只作为可导入的核心模块与 facade，
直接运行 server.py 不再启动服务；必须经由本入口启动，
并在导入 server 之前设置 SIM_PRODUCTION=1 以使用真实存档
（测试与调试导入不设置该变量，自动落到 PID 临时文件，防止污染真实数据）。
"""
import os
import sys

os.environ.setdefault("SIM_PRODUCTION", "1")
import server  # noqa: E402

sys.exit(server.main())
'''

    # ---- 静态自检：symtable 全局引用必须落在命名空间联合内 ----
    imported = set()
    for nd in ast.parse(import_block).body:
        if isinstance(nd, ast.Import):
            imported |= {a.asname or a.name.split(".")[0] for a in nd.names}
        elif isinstance(nd, ast.ImportFrom):
            imported |= {a.asname or a.name for a in nd.names}
    all_defs = set(name2mod) | {"server", "types"} | imported
    bi = set(dir(builtins))
    for m, text in sub_texts.items():
        allowed = all_defs | {n for n in name2mod if name2mod[n] == m}
        st = symtable.symtable(text, m + ".py", "exec")

        def walk(st_, acc):
            for s in st_.get_symbols():
                if s.is_global():
                    acc.add(s.get_name())
            for ch in st_.get_children():
                walk(ch, acc)
        globs = set()
        walk(st, globs)
        bad = {g for g in globs if g not in allowed and g not in bi}
        if bad:
            sys.exit("!! %s 存在未定义全局引用: %s" % (m, sorted(bad)))
    st = symtable.symtable(new_server, "server.py", "exec")
    globs = set()

    def walk2(st_, acc):
        for s in st_.get_symbols():
            if s.is_global():
                acc.add(s.get_name())
        for ch in st_.get_children():
            walk2(ch, acc)
    walk2(st, globs)
    core_defs = {n for n in name2mod if name2mod[n] == "CORE"} | {
        "BASE", "DATA_FILE", "SNAPSHOT_DIR", "SNAPSHOT_FILE", "UA", "server", "types",
        "__file__", "_TEST_PID", "_atexit", "_cleanup_test_data_files",
        "_m", "_k", "_v", "_SUBMODULES"} | {m for m in MAPPING if m != "CORE"}
    bad = {g for g in globs if g not in all_defs and g not in bi and g not in core_defs}
    if bad:
        sys.exit("!! server.py 存在未定义全局引用: %s" % sorted(bad))

    # ---- 写盘 ----
    for m, text in sub_texts.items():
        open(os.path.join(MM, m + ".py"), "w", encoding="utf-8", newline="").write(text)
    open(SRC, "w", encoding="utf-8", newline="").write(new_server)
    open(os.path.join(MM, "run.py"), "w", encoding="utf-8", newline="").write(run_py)

    # ---- 编译与对照 ----
    for f in ["server.py", "run.py"] + [m + ".py" for m in sub_texts]:
        r = subprocess.run([sys.executable, "-m", "py_compile", f], cwd=MM,
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit("!! py_compile 失败 %s:\n%s" % (f, r.stderr))
    new_names = dump_names("new")
    lost = set(old_names) - set(new_names)
    if lost:
        sys.exit("!! 拆分后丢失名字: %s" % sorted(lost))
    added = set(new_names) - set(old_names)
    print("拆分完成。新增顶层名(应仅为模块名/types): %s" % sorted(added))
    print("server.py %d 行；子模块: %s" % (
        new_server.count("\n") + 1,
        ", ".join("%s %d行" % (m, t.count("\n") + 1) for m, t in sub_texts.items())))


if __name__ == "__main__":
    main()
