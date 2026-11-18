# -*- coding: utf-8 -*-
"""B 类参数（防守档开口 / 单票超限减仓 / 卖出股数下限）的只读反事实分析。

数据源：
  1) snap/*/raw_<acct>.json 的 facts.reviewLog（含 note 内 delta 与否决原因）
  2) snap/*/state_<acct>.json（权重、trades）
  3) server.fetch_kline 腾讯日K（前复权）做前瞻收益
不写任何业务数据、不调任何 API。
"""
import json, os, re, sys, glob, statistics as st

sys.path.insert(0, r"C:\Users\49928\WorkBuddy\2026-09-30-19-04-40\multi-market")
sys.stdout.reconfigure(encoding="utf-8")
import server  # noqa: E402

ROOT = r"C:\Users\49928\WorkBuddy\2026-09-30-19-04-40"
ACCS = ["us", "cn_conservative", "cn_aggressive"]
DELTA_RE = re.compile(r"delta\s*=?\s*(-?\d+(?:\.\d+)?)")
KL = {}


def kl(code):
    """返回 {date: close}，失败返回 {}。"""
    if code in KL:
        return KL[code]
    try:
        rows = server.fetch_kline(code, 90)
        KL[code] = {r[0]: r[2] for r in rows}
    except Exception:
        KL[code] = {}
    return KL[code]


def _days_between(d1, d2):
    from datetime import date
    y1, m1, dd1 = map(int, str(d1)[:10].split("-"))
    y2, m2, dd2 = map(int, str(d2)[:10].split("-"))
    return abs((date(y2, m2, dd2) - date(y1, m1, dd1)).days)


def fwd(code, date, n):
    """date 当日收盘 → 之后第 n 个交易日收盘的收益率（%）。样本不足返回 None。"""
    d = kl(code)
    if not d:
        return None
    ds = sorted(d)
    if date not in d:
        # 非交易日：取 date 之前最近交易日
        prev = [x for x in ds if x <= date]
        if not prev:
            return None
        date = prev[-1]
    i = ds.index(date)
    if i + n >= len(ds):
        return None
    return (d[ds[i + n]] / d[date] - 1) * 100


def load_reviews():
    """跨所有快照合并 reviewLog（按 account+code+reviewedAt 去重），保留最新口径。"""
    seen, out = set(), []
    for p in sorted(glob.glob(os.path.join(ROOT, "snap", "*", "raw_*.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        f = d.get("facts", d)
        acc = os.path.basename(p)[4:-5]        # raw_<account>.json
        for r in (f.get("reviewLog") or []):
            key = (acc, str(r.get("code")), str(r.get("reviewedAt")))
            if key in seen:
                continue
            seen.add(key)
            note = str(r.get("note") or "")
            m = DELTA_RE.search(note)
            if not m:
                continue
            r["_acc"] = acc
            r["_delta"] = float(m.group(1))
            r["_date"] = str(r.get("reviewedAt"))[:10]
            r["_note"] = note
            out.append(r)
    return out


def cat(note):
    """否决原因分类。"""
    if any(k in note for k in ("总仓位", "仓位否决", "防守档", "保留弹药", "满仓")):
        return "仓位"
    if any(k in note for k in ("只数已达上限", "席位", "唯一新席", "名额")):
        return "席位"
    if any(k in note for k in ("单票", "额度", "不足一手", "权重上限")):
        return "单票额度"
    return "其他"


def show(tag, xs):
    if not xs:
        print("  %-22s n=0" % tag)
        return
    xs2 = sorted(xs)
    win = sum(1 for x in xs2 if x > 0) / len(xs2) * 100
    print("  %-22s n=%2d  均值%+6.2f%%  中位%+6.2f%%  胜率%5.1f%%  最好%+6.2f%%  最差%+6.2f%%"
          % (tag, len(xs2), st.mean(xs2), st.median(xs2), win, xs2[-1], xs2[0]))


def fwd_ex(code, date, n, bench):
    """超额收益：个股 n 日收益 − 基准指数同期收益。"""
    a, b = fwd(code, date, n), fwd(bench, date, n)
    if a is None or b is None:
        return None
    return a - b


# ============ B2：防守档/仓位否决放行后的前瞻收益 ============
print("=" * 78)
print("B2 被【仓位类】否决的候选，若当时放行，5/10 个交易日后的收益（原始 / 超大盘）")
print("=" * 78)
rev = load_reviews()
print("reviewLog 合并样本 %d 条（三账户去重）" % len(rev))

for n in (5, 10):
    print("\n—— 前瞻 %d 个交易日 ——" % n)
    buckets = {"delta≥4.4": [], "3.9≤delta<4.4": [], "3.0≤delta<3.9": [], "2.5≤delta<3.0": []}
    exc = {k: [] for k in buckets}
    for r in rev:
        if r.get("verdict") != "HOLD":
            continue
        if cat(r["_note"]) != "仓位":
            continue
        d = r["_delta"]
        k = ("delta≥4.4" if d >= 4.4 else "3.9≤delta<4.4" if d >= 3.9 else
             "3.0≤delta<3.9" if d >= 3.0 else "2.5≤delta<3.0" if d >= 2.5 else "delta<2.5")
        if k not in buckets:
            continue
        x = fwd(r.get("code"), r["_date"], n)
        if x is None:
            continue
        buckets[k].append(x)
        bench = "sh000001" if r["_acc"] != "us" else "usINX"
        e = fwd_ex(r.get("code"), r["_date"], n, bench)
        if e is not None:
            exc[k].append(e)
    for k, v in buckets.items():
        show(k, v)
    print("   —— 超大盘（同窗口）——")
    for k, v in exc.items():
        show(k + " 超额", v)

# 分账户：只看 delta≥3.9（提案门槛）的实际结果
print("\n—— 分账户（delta≥3.9，5 日原始收益）——")
for acc in ACCS:
    xs = []
    for r in rev:
        if r["_acc"] != acc or r.get("verdict") != "HOLD" or r["_delta"] < 3.9:
            continue
        if cat(r["_note"]) != "仓位":
            continue
        x = fwd(r.get("code"), r["_date"], 5)
        if x is not None:
            xs.append(x)
    show(acc, xs)


# ============ B3：权重超限持仓的前瞻超额收益 ============
print("\n" + "=" * 78)
print("B3 单票权重 >20% 的持仓，相对同账户其余持仓等权的超额收益（5/10 日）")
print("=" * 78)
for n in (5, 10):
    ex, det = [], []
    for p in sorted(glob.glob(os.path.join(ROOT, "snap", "*", "state_*.json"))):
        date = os.path.basename(os.path.dirname(p))
        acc = os.path.basename(p)[6:-5]
        try:
            s = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        hs = [h for h in (s.get("holdings") or []) if h.get("shares", 0) > 0]
        if len(hs) < 3:
            continue
        tot = sum(h["price"] * h["shares"] for h in hs) + float(s.get("cash") or 0)
        if tot <= 0:
            continue
        rows = []
        for h in hs:
            w = h["price"] * h["shares"] / tot * 100
            x = fwd(h.get("code"), date, n)
            rows.append((h.get("name"), h.get("code"), w, x))
        ok = [r for r in rows if r[3] is not None]
        if len(ok) < 3:
            continue
        base = st.mean([r[3] for r in ok])          # 账户内等权基准
        for name, code, w, x in ok:
            if w > 20:
                ex.append(x - base)
                det.append((date, acc, name, round(w, 1), round(x, 1), round(x - base, 1)))
    show("超20%%超额(%d日)" % n, ex)
    if det and n == 5:
        print("  明细（日期 账户 名称 权重/前瞻收益/超额）:")
        for t in det[-14:]:
            print("   ", t)

# ============ B4：部分卖出的残余与清仓耗时 ============
print("\n" + "=" * 78)
print("B4 卖出成交的股数分布：部分卖出后剩余是否长期占用席位")
print("=" * 78)
for acc in ACCS:
    p = os.path.join(ROOT, "snap", "2026-11-17", "state_%s.json" % acc)
    s = json.load(open(p, encoding="utf-8"))
    trades = sorted(s.get("trades") or [], key=lambda x: x.get("time", ""))
    pos, part, full, leftover = 0.0, 0, 0, []
    held = {}
    for t in trades:
        c, q = t.get("code", "").lower(), int(t.get("qty") or 0)
        if t.get("side") == "买入":
            held[c] = held.get(c, 0) + q
        else:
            before = held.get(c, q)
            after = max(0, before - q)
            held[c] = after
            pos += q * float(t.get("price") or 0)
            if after > 0:
                part += 1
                leftover.append((t.get("time", "")[:10], t.get("name"), q, after))
            else:
                full += 1
    print("  %-14s 卖出 %d 笔（清仓 %d / 部分 %d），成交额合计 %.0f 元"
          % (acc, part + full, full, part, pos))
    if leftover:
        print("     部分卖出样例（日期 名称 卖出 剩余）:", leftover[-3:])

# ============ 当前约束水位 ============
print("\n" + "=" * 78)
print("当前约束水位（2026-11-17 快照）")
print("=" * 78)
for acc in ACCS:
    s = json.load(open(os.path.join(ROOT, "snap", "2026-11-17", "state_%s.json" % acc), encoding="utf-8"))
    c = s.get("config") or {}
    hs = [h for h in (s.get("holdings") or []) if h.get("shares", 0) > 0]
    mv = sum(h["price"] * h["shares"] for h in hs)
    tot = mv + float(s.get("cash") or 0)
    cap = c.get("regimePosCap")
    over = [(h["name"], round(h["price"] * h["shares"] / tot * 100, 1)) for h in hs
            if h["price"] * h["shares"] / tot * 100 > 20]
    print("  %-14s 仓位%5.1f%% 防守档%2s%% 现金%5.1f%% | 单票>20%%: %s"
          % (acc, mv / tot * 100, cap, float(s.get("cash") or 0) / tot * 100, over))

# ============ B2b：delta≥3.9 内，按「当日涨幅」切分（追高 vs 未追高） ============
print("")
print("=" * 78)
print("B2b delta>=3.9 被仓位否决者，按当日涨幅切分（5 日超额 / 原始）")
print("=" * 78)


def day_pct(code, date):
    d = kl(code)
    if not d:
        return None
    ds = sorted(d)
    prev = [x for x in ds if x < date]
    if date not in d or not prev:
        return None
    return (d[date] / d[prev[-1]] - 1) * 100


grp = {"急涨>=7%": [], "3%~7%": [], "0~3%": [], "当日下跌": []}
raw_g = {k: [] for k in grp}
for r in rev:
    if r.get("verdict") != "HOLD" or r["_delta"] < 3.9 or cat(r["_note"]) != "仓位":
        continue
    x = fwd(r.get("code"), r["_date"], 5)
    bench = "sh000001" if r["_acc"] != "us" else "usINX"
    e = fwd_ex(r.get("code"), r["_date"], 5, bench)
    dp = day_pct(r.get("code"), r["_date"])
    if x is None or e is None or dp is None:
        continue
    k = "急涨>=7%" if dp >= 7 else "3%~7%" if dp >= 3 else "0~3%" if dp >= 0 else "当日下跌"
    grp[k].append(e)
    raw_g[k].append(x)
for k in grp:
    show(k + " 超额", grp[k])
    show(k + " 原始", raw_g[k])

# ============ B3b：按标的聚合（同标的连续多日只取首日，避免重复计数） ============
print("\n" + "=" * 78)
print("B3b 超 20% 持仓的超额（按 episode 去重，5 日）")
print("=" * 78)
epi, last = {}, {}
for p in sorted(glob.glob(os.path.join(ROOT, "snap", "*", "state_*.json"))):
    date = os.path.basename(os.path.dirname(p))
    acc = os.path.basename(p)[6:-5]
    s = json.load(open(p, encoding="utf-8"))
    hs = [h for h in (s.get("holdings") or []) if h.get("shares", 0) > 0]
    if len(hs) < 3:
        continue
    tot = sum(h["price"] * h["shares"] for h in hs) + float(s.get("cash") or 0)
    rows = [(h.get("name"), h["price"] * h["shares"] / tot * 100, fwd(h.get("code"), date, 5)) for h in hs]
    ok = [(n, w, x) for n, w, x in rows if x is not None]
    if len(ok) < 3:
        continue
    base = st.mean([x for _, _, x in ok])
    for n, w, x in ok:
        if w <= 20:
            continue
        key = (acc, n)
        if key in last and _days_between(last[key], date) <= 3:
            continue
        epi.setdefault(key, []).append(round(x - base, 2))
        last[key] = date
print("  %-28s %3s %8s %8s" % ("账户/标的", "n", "均值超额", "最差"))
for k, v in sorted(epi.items(), key=lambda kv: st.mean(kv[1])):
    print("  %-28s %3d %+7.2f%% %+7.2f%%" % (k[0][:6] + "/" + str(k[1]), len(v), st.mean(v), min(v)))

# ============ B4b：碎单与清仓耗时 ============
print("\n" + "=" * 78)
print("B4b 部分卖出后的残余：是否低于最小单笔金额、需要几天才清掉")
print("=" * 78)
for acc in ACCS:
    s = json.load(open(os.path.join(ROOT, "snap", "2026-11-17", "state_%s.json" % acc), encoding="utf-8"))
    msa = float((s.get("config") or {}).get("minSellAmount") or 1000)
    trades = sorted(s.get("trades") or [], key=lambda x: x.get("time", ""))
    held, small, days = {}, 0, []
    first_part = {}
    for t in trades:
        c, q = t.get("code", "").lower(), int(t.get("qty") or 0)
        px, d = float(t.get("price") or 0), str(t.get("time"))[:10]
        if t.get("side") == "买入":
            held[c] = held.get(c, 0) + q
            continue
        before = held.get(c, q)
        after = max(0, before - q)
        held[c] = after
        if after > 0:
            if after * px < msa:
                small += 1
            first_part.setdefault(c, d)
        elif c in first_part:
            days.append(_days_between(first_part[c], d))
    print("  %-14s 残余市值<最小单笔(%d元) 的部分卖出 %d 笔；部分→清仓跨度 %s 天"
          % (acc, msa, small, days if days else "无完整闭环"))
