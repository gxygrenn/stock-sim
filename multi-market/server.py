#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟炒股 · 消息驱动自动交易 后端服务

只用 Python 标准库，无第三方依赖。
  GET  /api/state             读取完整状态
  POST /api/trade             手动买卖 {side, code, price, qty}
  POST /api/settle            手动结算 {pct}
  POST /api/refresh           刷新行情（指数 + 持仓真实价）
  POST /api/scan              立即执行一轮「抓新闻 -> 打分 -> 自动交易」
  POST /api/council           召集专家团对全池开会（四位分析师 + 风控官，只出意见不下单）
  POST /api/review_done       LLM 专家团写入评审结论 {code, verdict, confidence, note}
                              结论先落队列，成交由 scan() 在交易时段按最新价落地
  POST /api/inject            手动投喂一条消息 {text, score?}，走完整决策链路（演练）
  POST /api/pool              自选池增删 {action, item}
  POST /api/config            更新配置
  POST /api/reset             重置账户
  POST /api/mode              切换价格模式 {priceMode}
  POST /api/daily_review      每日复盘结论写入（复盘自动化调用，前端展示）
  POST /api/market_context    市场环境播报写入 {date, intl, sentiment}（播报自动化调用）
"""

import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, "data-multi.json")   # 独立数据文件，避免读到旧单账户版残留的 data.json
# 跨部署持久化快照：随代码一起进 git 仓库并部署，重新部署后若 data-multi.json 丢失则从此恢复。
SNAPSHOT_DIR = os.path.join(BASE, "snapshot")
SNAPSHOT_FILE = os.path.join(SNAPSHOT_DIR, "data-multi.snapshot.json")
if os.environ.get("SIM_PRODUCTION") != "1":
    # 测试/工具导入隔离（2026-11-13）：worker 线程曾随 import 启动，周期性 save_state
    # 把单账户存档写进真实 data-multi.json 与跨部署种子，随发布包上传后把线上 us/cna
    # 存档冲成默认值（当日已从备份恢复）。凡非 `python server.py` 直接运行的导入场景，
    # 数据文件一律重定向到 PID 后缀临时文件并在进程退出时清理，从根上杜绝污染。
    _TEST_PID = os.getpid()
    DATA_FILE = os.path.join(BASE, "data-multi.test-%d.json" % _TEST_PID)
    SNAPSHOT_FILE = os.path.join(SNAPSHOT_DIR, "data-multi.test-%d.snapshot.json" % _TEST_PID)
    import atexit as _atexit
    @_atexit.register
    def _cleanup_test_data_files():
        for _p in (DATA_FILE, SNAPSHOT_FILE):
            try:
                if os.path.exists(_p):
                    os.remove(_p)
            except Exception:
                pass




def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



def today_str():
    return datetime.now().strftime("%Y-%m-%d")



def default_state(account=None):
    account = account or current_account()
    acc = ACCOUNTS.get(account) or ACCOUNTS[DEFAULT_ACCOUNT]
    return {
        "account": account,
        "initCapital": 100000.0,
        "cash": 100000.0,
        "holdings": [],
        "pool": list(acc["pool"]),
        "history": [],
        "news": [],
        "trades": [],
        "log": [],
        "seenNews": [],
        "lastTrade": {},
        "roundTrades": 0,  # 本轮（本次扫描/投喂）已成交笔数，每轮开始时归零
        "idx": 100.0,
        "nextDate": today_str(),
        "priceMode": "real",
        "fees": 0.0,
        "realized": 0.0,
        "seq": 1,
        "quote": None,
        "poolQuote": {},
        "lastScan": "",
        "closeSweepDate": "",  # 收盘前补扫（A股 14:55）当日已执行标记，每账户每日一次
        "scanErr": "",
        "signals": {},     # code -> {tech,lhb,ann,delta,block,notes[],time}
        "sigFired": {},    # code -> "2026-09-13+" 综合信号当日已触发方向
        "council": [],     # 评议团合议记录（最近若干次）
        "pendingReview": [],  # 待 LLM 专家团评审的候选（autoTrade 关闭时由 scan 维护）
        "reviewed": {},    # code -> 最近一次专家评审时间（冷却用）
        "reviewLog": [],   # 专家团评审历史台账（code/name/verdict/confidence/note/outcome/成交价，最近 600 条，独立于队列生命周期；2026-11-13 起 200→600：跨周对账需 ≥2 周样本，200 条只够回溯 5~8 个交易日）
        "expiredReviews": [],  # 队列 TTL 超时作废 / 已批准未成交的候选（最近 100 条）
        "dailyReviews": [],  # 每日复盘存档（最近 30 天，由复盘自动化写入）
        "marketContext": None,  # 最近一次市场环境播报（国际因素 + 市场情绪，由播报自动化写入）
        "poolChanges": [],      # 待 LLM 评审的调池建议（淘汰/选入候选，门控确认后生效）
        "poolChangeLog": [],    # 调池历史台账（最近 100 条：code/name/direction/result/时间/理由）
        "lastRebalance": "",    # 最近一次生成调池建议的日期（按 rebalanceIntervalDays 节流）
        "regime": None,         # 市场环境温度计（RSRS+扩散指标+动量，进攻/中性/防守）
        "sectors": None,        # 行业轮动 RRG 结果（相对旋转图，领先/转强/转弱/落后行业）
        "config": dict(acc["config"]),
    }



_states = {}

_lock = threading.RLock()

_save_broken = False

_last_catchup = 0.0

_tls = threading.local()



def set_account(account):
    """设置当前线程/上下文的账户。"""
    _tls.account = account if account in ACCOUNTS else DEFAULT_ACCOUNT



def current_account():
    """当前上下文要操作的账户。"""
    return getattr(_tls, "account", DEFAULT_ACCOUNT)



def load_state(account=None):
    account = account or current_account()
    if account not in ACCOUNTS:
        account = DEFAULT_ACCOUNT
    if account in _states:
        return _states[account]
    d = default_state(account)
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            # 新格式 {account: state}；旧格式单 state 归入默认账户（向后兼容）
            acct_data = None
            if isinstance(saved, dict) and any(k in ACCOUNTS for k in saved):
                acct_data = saved.get(account)
            elif account == DEFAULT_ACCOUNT:
                acct_data = saved
            if acct_data:
                for k, v in d.items():
                    if k not in acct_data:
                        acct_data[k] = v
                for k, v in ACCOUNTS[account]["config"].items():
                    if k not in acct_data.get("config", {}):
                        acct_data["config"][k] = v
                # 老存档补 tags 字段
                for p in acct_data.get("pool", []):
                    if "tags" not in p:
                        p["tags"] = ""
                d = acct_data
        except Exception as exc:
            d["log"] = [{"time": now_str(), "msg": "读取存档失败(%s)，已使用新账户" % exc}]
    else:
        # 主存档不存在（如重新部署后被清空）→ 尝试从跨部署快照恢复
        _restore_from_snapshot(account, d)
    _states[account] = d
    return d



def _restore_from_snapshot(account, d):
    """主存档缺失时，尝试从跨部署快照恢复（重新部署后 data-multi.json 会被清空，快照随代码部署带上）。"""
    try:
        if os.path.exists(DATA_FILE):
            return
        if not os.path.exists(SNAPSHOT_FILE):
            return
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        acct_data = None
        if isinstance(saved, dict) and any(k in ACCOUNTS for k in saved):
            acct_data = saved.get(account)
        elif account == DEFAULT_ACCOUNT:
            acct_data = saved
        if not acct_data:
            return
        # 与 load_state 相同的补键逻辑：快照里的旧字段缺失时用默认补上
        for k, v in d.items():
            if k not in acct_data:
                acct_data[k] = v
        for k, v in ACCOUNTS[account]["config"].items():
            if k not in acct_data.get("config", {}):
                acct_data["config"][k] = v
        for p in acct_data.get("pool", []):
            if "tags" not in p:
                p["tags"] = ""
        d.clear()
        d.update(acct_data)
        d.setdefault("log", []).insert(0, {"time": now_str(),
                                           "msg": "已从跨部署快照恢复最近一次存档"})
    except Exception as exc:
        d.setdefault("log", []).insert(0, {"time": now_str(),
                                           "msg": "快照恢复失败(%s)，使用新账户" % exc})



def save_state():
    """存档失败（如云端只读目录）不能拖垮服务，降级为纯内存运行。"""
    global _save_broken
    if _save_broken:
        return
    try:
        with _lock:
            if not _states:
                return
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(_states, fh, ensure_ascii=False)
            os.replace(tmp, DATA_FILE)
            _write_snapshot(_states)
    except Exception as exc:
        _save_broken = True
        print("存档写入失败，转为内存模式: %s" % exc, flush=True)



def _write_snapshot(states):
    """把当前状态同步写一份到快照文件（随代码部署，跨部署持久化）。失败不影响主存档。"""
    try:
        if not os.path.isdir(SNAPSHOT_DIR):
            os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        tmp = SNAPSHOT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(states, fh, ensure_ascii=False)
        os.replace(tmp, SNAPSHOT_FILE)
    except Exception as exc:
        print("快照写入失败: %s" % exc, flush=True)



def log(msg):
    s = load_state()
    s["log"].insert(0, {"time": now_str(), "msg": msg})
    s["log"] = s["log"][:200]



def fetch_prices(codes):
    """批量行情，返回 {code: quote}。"""
    codes = [c for c in codes if c]
    if not codes:
        return {}
    out = {}
    for i in range(0, len(codes), 20):
        chunk = codes[i:i + 20]
        txt = http_get("https://qt.gtimg.cn/q=" + ",".join(chunk), encoding="gbk")
        for m in re.finditer(r'v_(\w+)="([^"]*)"', txt):
            code, body = m.group(1), m.group(2)
            p = body.split("~")
            if len(p) < 33:
                continue
            try:
                price, pre = float(p[3] or 0), float(p[4] or 0)
                is_us = code.startswith("us")
                amount = float(p[37]) * (1 if is_us else 1e4) if len(p) > 37 and p[37] else 0.0
            except Exception:
                continue
            out[code] = {"name": p[1], "code": code, "price": price, "preClose": pre,
                         "chg": float(p[31] or 0), "pct": float(p[32] or 0),
                         "amount": amount, "time": _fmt_ts(p[30] if len(p) > 30 else ""),
                         "turn": _f(p, 38), "pe": _f(p, 39), "pb": _f(p, 46)}
    return out



KLINE_HOSTS = [
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=%s,day,,,%d,qfq",
]

_kline_ok = 0      # 当前生效的源下标，成功即复用，避免每次都去撞被拦的主源



_kline_cache = {}      # code -> (fetch_ts, klines)，日K历史盘中不变，缓存避免多轮重复拉取

_kline_ttl = 300       # K线缓存 5 分钟



def fetch_kline(code, n=32):
    """腾讯日K（多主机兜底 + 内存缓存 + 美股交易所后缀 fallback）。

    返回 [(date, open, close, high, low, vol), ...] 旧->新。
    美股代码需带交易所后缀才能拿到完整历史：纳斯达克 .OQ、纽交所 .N。
    """
    global _kline_ok
    now = time.time()
    hit = _kline_cache.get(code)
    if hit and hit[0] + _kline_ttl > now and len(hit[1]) >= n:
        return hit[1][-n:]

    cands = [code]
    if code.startswith("us"):
        # 美股指数：标普500 在腾讯源的正确代码是 us.INX（带点号），纳指 us.IXIC
        if code == "usINX":
            cands = ["us.INX"]
        else:
            cands = [code + ".OQ", code + ".N"]

    last = None
    for try_code in cands:
        order = [_kline_ok] + [i for i in range(len(KLINE_HOSTS)) if i != _kline_ok]
        for i in order:
            try:
                d = json.loads(http_get(KLINE_HOSTS[i] % (try_code, n)))["data"].get(try_code)
                rows = (d or {}).get("qfqday") or (d or {}).get("day") or []
                if len(rows) < 20:
                    raise RuntimeError("K线不足(%d根)" % len(rows))
                if i != _kline_ok:
                    log("K线源切换 -> %s" % KLINE_HOSTS[i].split("/")[2])
                    _kline_ok = i
                klines = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                          for r in rows]
                _kline_cache[code] = (now, klines)
                return klines
            except Exception as exc:
                last = exc
    raise RuntimeError("K线全部源不可用: %s" % last)



def fetch_lhb():
    """东财龙虎榜（datacenter 域，与被封的 push2 不同）。返回 {6位代码: 净买额(元)}。"""
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
           "?reportName=RPT_DAILYBILLBOARD_DETAILS&columns=ALL&pageSize=300"
           "&sortColumns=TRADE_DATE&sortTypes=-1")
    d = json.loads(http_get(url))
    rows = ((d.get("result") or {}).get("data")) or []
    out = {}
    for r in rows:
        code = r.get("SECURITY_CODE") or ""
        try:
            net = float(r.get("BILLBOARD_NET_AMT") or 0)
        except Exception:
            continue
        if code:
            out[code] = out.get(code, 0) + net
    return out



def fetch_strength_candidates(limit=40):
    """强势股候选：东财 push2 涨幅榜(f3)+主力净流入榜(f62)为主，龙虎榜净买兜底。

    返回统一结构 [{code(带sh/sz), name, industry, pct, amount, turn, pe, netIn, volRatio}]。
    amount/netIn 单位元。带重试 + 15 分钟内存缓存 + 优雅降级。
    """
    now = time.time()
    hit = _RANK_CACHE.get("cn")
    if hit and hit[0] + _RANK_TTL > now:
        return hit[1]

    cands, seen = [], set()
    for fid in ("f3", "f62"):
        url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=%d&po=1&np=1"
               "&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=%s"
               "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
               "&fields=f2,f3,f5,f6,f8,f9,f10,f12,f13,f14,f20,f62,f100" % (limit, fid))
        d = None
        for _ in range(2):
            try:
                d = json.loads(_rank_get(url))
                break
            except Exception:
                time.sleep(1)
        if not d:
            continue
        for r in ((d.get("data") or {}).get("diff") or []):
            f12 = str(r.get("f12") or "")
            if not f12 or f12 in seen:
                continue
            seen.add(f12)
            try:
                pct = float(r.get("f3") or 0)
                amount = float(r.get("f6") or 0)
                turn = float(r.get("f8") or 0)
                pe = float(r.get("f9") or 0)
                net_in = float(r.get("f62") or 0)
                volr = float(r.get("f10") or 0) or 0.0
            except Exception:
                continue
            cands.append({"code": _cn_code_prefix(f12, r.get("f13")),
                          "name": str(r.get("f14") or "").strip(),
                          "industry": str(r.get("f100") or "").strip(),
                          "pct": pct, "amount": amount, "turn": turn,
                          "pe": pe, "netIn": net_in, "volRatio": volr})

    if len(cands) < 5:
        # 兜底：龙虎榜净买为正的票（datacenter 域，比 push2 稳），补腾讯行情字段
        try:
            lhb = fetch_lhb()
        except Exception:
            lhb = {}
        top = sorted(lhb.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        codes = []
        for c6, net in top:
            if net <= 0:
                continue
            full = ("sh" if c6[:1] in ("6", "9") else "sz") + c6
            codes.append((full, net))
        if codes:
            quotes = fetch_prices([c for c, _ in codes])
            for full, net in codes:
                q = quotes.get(full) or {}
                if not q.get("price"):
                    continue
                cands.append({"code": full, "name": q.get("name", full),
                              "industry": "", "pct": q.get("pct", 0),
                              "amount": q.get("amount", 0), "turn": q.get("turn", 0),
                              "pe": q.get("pe", 0), "netIn": net, "volRatio": 0.0})

    _RANK_CACHE["cn"] = (now, cands)
    return cands



def fetch_announcements(pages=3):
    """巨潮今日全市场公告，返回 [{code, title, time(ms)}]。一次拉全市场再本地过滤。"""
    se = "%s~%s" % (today_str(), today_str())
    out = []
    for pg in range(1, pages + 1):
        body = ("pageNum=%d&pageSize=100&column=sse&tabName=fulltext&plate=&stock="
                "&searchkey=&secid=&category=&trade=&seDate=%s&sortName=&sortType="
                "&isHLtitle=true" % (pg, se)).encode("utf-8")
        req = urllib.request.Request(
            "http://www.cninfo.com.cn/new/hisAnnouncement/query",
            headers={"User-Agent": UA["User-Agent"],
                     "Referer": "http://www.cninfo.com.cn/",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data=body)
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read().decode("utf-8", "ignore"))
        anns = d.get("announcements") or []
        for a in anns:
            code = str(a.get("secCode") or "")
            title = re.sub(r"<[^>]+>", "", a.get("announcementTitle") or "")
            if code and title:
                out.append({"code": code, "title": title,
                            "time": a.get("announcementTime") or 0})
        if len(anns) < 100:
            break
    return out



# ---------------------------------------------------------------- 扫描
def refresh_prices(s):
    """刷新大盘指数；真实价格模式下刷新持仓现价。返回错误信息或空串。"""
    err = ""
    cfg = s["config"]
    try:
        s["quote"] = fetch_quote(cfg.get("indexCode", "sh000001"))
    except Exception as exc:
        err = "指数行情失败: %s" % exc
    if s["priceMode"] == "real" and s["holdings"]:
        try:
            quotes = fetch_prices([h["code"] for h in s["holdings"]])
            for h in s["holdings"]:
                q = quotes.get(h["code"])
                if q and q["price"] > 0:
                    h["preClose"] = q["preClose"] or h["preClose"]
                    h["price"] = q["price"]
                    h["pct"] = q["pct"]
        except Exception as exc:
            err = (err + " / " if err else "") + "个股行情失败: %s" % exc
    if s["pool"]:
        try:
            s["poolQuote"] = fetch_prices([p["code"] for p in s["pool"]])
        except Exception:
            pass
    return err



def in_trading_hours(now=None):
    """按当前账户判断是否处于交易时段。

    评审与执行解耦后由它决定「已批准的结论现在能不能落地」：
    盘中直接按最新价成交；盘后评审出的结论挂到下一个盘中时点再执行。
    """
    now = now or datetime.now()
    if (ACCOUNTS.get(current_account()) or {}).get("market") == "us":
        return us_trading(now)
    return cn_trading(now)



def execute_approved(s):
    """把 LLM 专家团「已批准」的候选落地成成交（评审与执行解耦的核心）。

    只在交易时段内执行，一律按**当前最新价**成交，并把「评审 -> 成交」的滞后
    写进日志，让复盘能区分「结论对」与「执行晚」。非交易时段直接返回；
    无报价、持仓已满等可恢复情况留在队列里下轮再试。
    """
    pend = s.get("pendingReview") or []
    if not pend or not in_trading_hours():
        return 0
    cfg = s["config"]
    by_code = {p["code"]: p for p in s["pool"]}

    def drop(p, why):
        exp = s.setdefault("expiredReviews", [])
        exp.append({"code": p.get("code"), "name": p.get("name"),
                    "direction": p.get("direction"), "state": "rejected",
                    "why": why, "time": now_str()})
        s["expiredReviews"] = exp[-100:]
        pend.remove(p)
        log("已批准候选 %s 未成交出队：%s" % (p.get("name") or p.get("code"), why))

    done = 0
    # 2026-11-13：执行顺序改为「先卖后买」。原按队列先后，批准的 BUY 可能排在
    # SELL 之前，满席账户卖出的席位要等下一轮才能被 BUY 使用；换仓闸门（swapGate）
    # 依赖「卖出先成交腾位、买入后回补」的同轮配对。非 approved / 非 SELL 的相对
    # 顺序保持稳定（sort 稳定排序），等待中的候选不受影响。
    _pend = list(pend)
    _pend.sort(key=lambda p: 0 if ((p.get("state") or "") == "approved"
              and ((p.get("review") or {}).get("verdict") or "").upper() == "SELL") else 1)
    for p in _pend:
        if (p.get("state") or "") != "approved":
            continue
        stop = quota_block(s)
        if stop:
            log("评审补做中止：%s" % stop)
            break
        item = by_code.get(p["code"])
        if item is None:
            drop(p, "该股已不在自选池")
            continue
        rev = p.get("review") or {}
        verdict = (rev.get("verdict") or "").upper()
        conf = rev.get("confidence") or 0
        sig = (s.get("signals") or {}).get(p["code"]) or {}
        price = current_price(s, item)
        if price <= 0:
            continue                      # 没报价，留着下轮再试
        lag = age_hours(p.get("reviewedAt") or p.get("time"))
        # 保质期按盘中时间计（2026-10-28）：墙钟 48h 若跨周末可能只等于 8h 盘中，
        # 用墙钟判会把周五收盘前的好结论误杀
        lag_eff = approved_age_hours(p.get("reviewedAt") or p.get("time")) \
            if lag >= 0 else -1.0
        # 闸门一：批准结论的保质期。过期说明结论已脱离行情（如周六批准、周一才开盘），
        # 宁可作废重评也不拿旧结论下单（2026-10-11 教训：国轩高科按 68.8h 前的结论成交）
        try:
            attl = float(cfg.get("approvedTTLHours", 24) or 24)
        except Exception:
            attl = 24.0
        if lag >= 0 and lag_eff > attl:
            s.setdefault("reviewed", {})[(p.get("code") or "").lower()] = now_str()
            drop(p, "批准结论盘中已过期 %.1fh（保质期 %.0fh，墙钟 %.1fh）"
                 % (lag_eff, attl, lag))
            continue
        lagtxt = ("评审后 %.1fh 按最新价" % lag) if lag >= 0 else "按最新价"
        reason = "专家团%s信心%s(%s):%s" % (verdict, conf or "-", lagtxt,
                                            (p.get("reason") or "")[:16])
        if verdict == "BUY":
            if sig.get("block"):
                drop(p, "已被估值过滤")
                continue
            # 闸门二：同股当日限买。评审冷却（30~60 分钟）只管「重复入队」，管不住
            # 冷却间隙里多次入队-批准-成交的连买（2026-10-12 教训：Palantir/Shopify
            # 各 2 小时内买了 3 次）。在执行咽喉统一拦截。
            day_max = int(cfg.get("sameDayBuyMax", 1) or 1)
            ckey = (p.get("code") or "").lower()
            _nbuys = buys_today(s, p.get("code"))
            if _nbuys >= day_max:
                s.setdefault("reviewed", {})[ckey] = now_str()
                drop(p, "同股当日已买 %d 次（限 %d 次/日）" % (_nbuys, day_max))
                continue
            floor = cfg.get("buySigFloor", -6)
            sd = int(sig.get("delta", 0) or 0)
            if sd < floor:
                drop(p, "信号分 %+d 跌破下限 %+d" % (sd, floor))
                continue
            if len([h for h in s["holdings"] if h["shares"] > 0]) >= cfg["maxHold"] \
                    and find_holding(s, p["code"]) is None:
                continue                  # 持仓已满，留着等卖出一只再执行
            total = total_asset(s)
            room = total * cfg["maxPosPct"] / 100.0
            h = find_holding(s, p["code"])
            # 2026-11-18（B3 配套）：已超减仓触发线的持仓不得再加仓，否则
            # 「今天买上去、明天被 check_trims 卖回 20%」自相抵消，白付双边成本。
            if h and h["price"] * h["shares"] / total * 100 > float(cfg.get("trimPct", 22) or 22):
                s.setdefault("reviewed", {})[ckey] = now_str()
                drop(p, "单票权重已超 %.0f%% 减仓线，禁止加仓" % float(cfg.get("trimPct", 22) or 22))
                continue
            if h:
                room -= h["price"] * h["shares"]
            budget = min(s["cash"] * cfg["buyPct"] / 100.0, room, s["cash"] * 0.98)
            # 2026-10-15 教训：资金不足最小下单金额时明确弃单停买，而不是零股塞单
            min_amt = float(cfg.get("minBuyAmount", 0) or 0)
            if min_amt > 0 and budget < min_amt:
                s.setdefault("reviewed", {})[ckey] = now_str()
                drop(p, "资金不足最小下单金额 %.0f，停买保留弹药" % min_amt)
                continue
            qty = int(budget / price)
            if qty < 1:
                drop(p, "资金或单票额度不足")
                continue
            tr = do_buy(s, item["name"], p["code"], price, qty, reason)
            if tr:
                pend.remove(p)
                done += 1
                _link_review_log(s, p["code"], price, "已买入")
                log("评审补做买入 %s %d 股 @%.3f（%s）" % (item["name"], qty, price, lagtxt))
        elif verdict == "SELL":
            h = find_holding(s, p["code"])
            if not h or h["shares"] <= 0:
                drop(p, "已无持仓")
                continue
            # 2026-11-13（R15）：强制复核终审 SELL（forced）与 note 带【R15清仓腾位】
            # 的置换 SELL 一律卖全量——部分卖出既不释放席位，也把残仓留在深亏里。
            # 实证：cnc 三一重工 11-13 卖 100/326 股，余 226 股仍 -6.24%，席位未腾，
            # 东方环宇同日 14:32 再被满席否决；cna 大洋电机两轮共卖 700 股，
            # 余 244 股仍 -12.77%。普通 LLM SELL 仍按 sellPct 部分卖出。
            if _is_clear_sell(p):
                qty = h["shares"]
            else:
                qty = max(100, int(h["shares"] * cfg["sellPct"] / 100.0 // 100) * 100)
                # 2026-11-18（B4）：普通 SELL 也受统一下限约束——单笔 ≥2×minSellAmount，
                # 卖后残值 <1.5×minSellAmount 直接清仓，避免「卖了没腾位」的残股。
                qty = sell_floor_qty(s, price, h["shares"], qty)
            qty = min(qty, h["shares"])
            tr = do_sell(s, item["name"], p["code"], price, qty, reason)
            if tr:
                pend.remove(p)
                done += 1
                _link_review_log(s, p["code"], price, "已卖出")
                log("评审补做卖出 %s %d 股 @%.3f（%s）" % (item["name"], qty, price, lagtxt))
    return done



def current_price(s, item):
    code = item.get("code") if isinstance(item, dict) else item
    if s["priceMode"] == "real":
        try:
            q = fetch_prices([code]).get(code)
            if q and q["price"] > 0:
                return q["price"]
        except Exception:
            pass
        # 实时拉价失败 → 回退到扫描时已存的最新报价（poolQuote 里 A股/美股都常驻有价）
        pq = (s.get("poolQuote") or {}).get(code) or {}
        if (pq.get("price") or 0) > 0:
            return pq["price"]
    for h in s["holdings"]:
        if h["code"] == code and h["price"] > 0:
            return h["price"]
    return 0.0


# ---------------------------------------------------------------- 模块装配（facade）
# 双实例硬闸门：sim_* 子模块通过 `import server` 找回共享命名空间，
# 换名加载（spec_from_file_location / exec 改名）会产生第二实例、两套 globals，
# 状态分裂且极难排查——当场报错，禁止。
if __name__ != "server":
    raise ImportError("server.py 必须以 `import server` 方式加载（生产入口 python run.py）；"
                      "改名加载会导致 sim_* 子模块双实例")
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
