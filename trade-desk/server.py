#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易工作台后端 — 仅用 Python 标准库。

数据源（免费公开）：
- 腾讯 qt.gtimg.cn：A股(sh/sz)、港股(hk)、美股(us) 实时行情
- 新浪 hq.sinajs.cn：国内期货(nf_ 前缀) 实时行情
- 腾讯 ifzq（多主机兜底）：股票日K线；期货日K线走新浪 InnerFuturesNewService
"""
import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
INDEX_HTML = os.path.join(BASE, "index.html")
PORT = int(os.environ.get("PORT", "8123"))
os.makedirs(DATA_DIR, exist_ok=True)

_FILES = {
    "watchlist": os.path.join(DATA_DIR, "watchlist.json"),
    "alerts": os.path.join(DATA_DIR, "alerts.json"),
    "trades": os.path.join(DATA_DIR, "trades.json"),
    "reviews": os.path.join(DATA_DIR, "reviews.json"),
}
_lock = threading.Lock()
ERROR_LOG = os.path.join(DATA_DIR, "error.log")

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com"}
SINA_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
QUOTE_CACHE = {}          # code -> (ts, quote)
QUOTE_CACHE_TTL = 10.0


def log_error(tag, msg):
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write("%s [%s] %s\n" % (datetime.now().isoformat(), tag, msg))
    except Exception:
        pass


# ---------------- 持久化 ----------------

def load_json(name, default):
    path = _FILES[name]
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(name, obj):
    path = _FILES[name]
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception as e:
        log_error("save", "%s: %s" % (name, e))


# ---------------- 网络抓取 ----------------

def http_get(url, headers=None, encoding="utf-8", timeout=10):
    h = dict(headers or UA)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(encoding, "replace")


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def classify_code(code):
    c = (code or "").strip().lower()
    if c.startswith("nf_"):
        return "futures"
    if c.startswith("sh") or c.startswith("sz"):
        return "a"
    if c.startswith("hk"):
        return "hk"
    if c.startswith("us"):
        return "us"
    if re.fullmatch(r"\d{6}", c):
        return "a"
    return "unknown"


def parse_qq_quote(code, raw):
    m = re.search(r'"(.*)"', raw)
    if not m:
        return None
    f = m.group(1).split("~")
    if len(f) < 5 or f[1] in ("", " pv_none_match"):
        return None
    quote = {
        "code": code,
        "name": f[1],
        "price": _f(f[3]),
        "prevClose": _f(f[4]),
        "open": _f(f[5]),
        "high": _f(f[33]) if len(f) > 33 else None,
        "low": _f(f[34]) if len(f) > 34 else None,
        "time": f[30] if len(f) > 30 else "",
        "change": _f(f[31]) if len(f) > 31 else None,
        "changePct": _f(f[32]) if len(f) > 32 else None,
        "market": {"a": "A股", "hk": "港股", "us": "美股"}.get(classify_code(code), ""),
    }
    if quote["change"] is None and quote["price"] is not None and quote["prevClose"]:
        quote["change"] = round(quote["price"] - quote["prevClose"], 3)
        quote["changePct"] = round(quote["change"] / quote["prevClose"] * 100, 2)
    return quote


def parse_sina_futures_quote(code, raw):
    m = re.search(r'"(.*)"', raw)
    if not m or "hq_str_nf_" not in raw:
        return None
    f = m.group(1).split(",")
    if len(f) < 9 or not f[0]:
        return None
    price = _f(f[8])
    prev = _f(f[10]) if len(f) > 10 else None
    if price is None:
        return None
    change = round(price - prev, 3) if prev else None
    return {
        "code": code,
        "name": f[0],
        "price": price,
        "prevClose": prev,
        "open": _f(f[2]),
        "high": _f(f[3]),
        "low": _f(f[4]),
        "time": (f[17] if len(f) > 17 else "") + " " + (f[1] if len(f) > 1 else ""),
        "change": change,
        "changePct": round(change / prev * 100, 2) if change is not None and prev else None,
        "market": "期货",
    }


def fetch_quote_one(code):
    """抓单只标的行情，多源兜底；全部失败时返回带 unavailable 标记的占位。"""
    kind = classify_code(code)
    if kind == "futures":
        url = "https://hq.sinajs.cn/list=" + code
        try:
            raw = http_get(url, SINA_HEADERS, "gbk")
            q = parse_sina_futures_quote(code, raw)
            if q:
                return q
            log_error("quote", "期货解析失败: %s" % code)
        except Exception as e:
            log_error("quote", "期货请求失败 %s: %s" % (code, e))
        return {"code": code, "name": code, "unavailable": "期货行情数据不可用"}
    # 腾讯行情（A股/港股/美股）
    try:
        raw = http_get("https://qt.gtimg.cn/q=" + code, encoding="gbk")
        q = parse_qq_quote(code, raw)
        if q:
            q["market"] = q.get("market") or {"a": "A股", "hk": "港股", "us": "美股"}.get(kind, "")
            return q
        log_error("quote", "腾讯解析失败: %s" % code)
    except Exception as e:
        log_error("quote", "腾讯请求失败 %s: %s" % (code, e))
    return {"code": code, "name": code, "unavailable": "行情数据不可用"}


def get_quote(code):
    now = time.time()
    with _lock:
        hit = QUOTE_CACHE.get(code)
        if hit and now - hit[0] < QUOTE_CACHE_TTL:
            return hit[1]
    q = fetch_quote_one(code)
    with _lock:
        QUOTE_CACHE[code] = (time.time(), q)
    return q


def get_quotes(codes):
    out = []
    for c in codes:
        c = (c or "").strip()
        if c:
            out.append(get_quote(c))
    return out


KLINE_HOSTS = [
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{n},qfq",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{n},qfq",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get?param={code},day,,,{n},qfq",
]


def fetch_kline(code, n=250):
    """返回按日期升序的日K: [{date, open, close, high, low, volume}]"""
    kind = classify_code(code)
    if kind == "futures":
        sym = code[3:]
        url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
               "var%20a=/InnerFuturesNewService.getDailyKLine?symbol=" + sym)
        try:
            raw = http_get(url, SINA_HEADERS, "utf-8")
            m = re.search(r"\((\[.*\])\)", raw, re.S)
            if not m:
                log_error("kline", "期货K线解析失败: %s" % code)
                return None
            arr = json.loads(m.group(1))[-n:]
            return [{
                "date": r["d"],
                "open": float(r["o"]), "close": float(r["c"]),
                "high": float(r["h"]), "low": float(r["l"]),
                "volume": _f(r.get("v"), 0) or 0,
            } for r in arr]
        except Exception as e:
            log_error("kline", "期货K线失败 %s: %s" % (code, e))
            return None
    for tpl in KLINE_HOSTS:
        try:
            raw = http_get(tpl.format(code=code, n=n), encoding="utf-8")
            data = json.loads(raw)
            node = data.get("data", {}).get(code, {})
            rows = node.get("qfqday") or node.get("day") or []
            if not rows:
                continue
            out = []
            for r in rows:
                out.append({
                    "date": r[0],
                    "open": float(r[1]), "close": float(r[2]),
                    "high": float(r[3]), "low": float(r[4]),
                    "volume": _f(r[5] if len(r) > 5 else None, 0) or 0,
                })
            return out
        except Exception as e:
            log_error("kline", "%s 失败: %s" % (tpl.split("/")[2], e))
    return None


# ---------------- 提醒规则（决策函数，测试可直接导入复用） ----------------

def evaluate_alert(alert, quote):
    """对单条提醒 + 单个行情快照做判定。返回 (triggered, detail)。

    type: above / below / pct_up / pct_down
    """
    if quote.get("unavailable") or quote.get("price") is None:
        return False, "行情不可用"
    price = quote["price"]
    pct = quote.get("changePct") or 0.0
    val = _f(alert.get("value"))
    if val is None:
        return False, "规则阈值无效"
    t = alert.get("type")
    if t == "above":
        ok = price >= val
        return ok, "现价 %.3f %s 阈值 %.3f" % (price, ">=" if ok else "<", val)
    if t == "below":
        ok = price <= val
        return ok, "现价 %.3f %s 阈值 %.3f" % (price, "<=" if ok else ">", val)
    if t == "pct_up":
        ok = pct >= val
        return ok, "涨跌幅 %.2f%% %s 阈值 %.2f%%" % (pct, ">=" if ok else "<", val)
    if t == "pct_down":
        ok = pct <= val
        return ok, "涨跌幅 %.2f%% %s 阈值 %.2f%%" % (pct, "<=" if ok else ">", val)
    return False, "未知规则类型 %s" % t


def check_alerts_now():
    alerts = load_json("alerts", [])
    if not alerts:
        return []
    codes = sorted({a.get("code", "") for a in alerts if a.get("code")})
    quotes = {q["code"]: q for q in get_quotes(codes)}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changed = False
    results = []
    for a in alerts:
        q = quotes.get(a.get("code", ""), {"unavailable": "无行情"})
        hit, detail = evaluate_alert(a, q)
        row = dict(a)
        row["hit"] = hit
        row["detail"] = detail
        row["currentPrice"] = q.get("price")
        if hit and not a.get("triggered"):
            a["triggered"] = True
            a["triggeredAt"] = now
            a["triggeredPrice"] = q.get("price")
            changed = True
            row["justTriggered"] = True
        results.append(row)
    if changed:
        save_json("alerts", alerts)
    return results


# ---------------- 回测（决策函数，测试可直接导入复用） ----------------

def sma(values, period):
    out = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def ma_cross_signals(closes, short=5, long=20):
    """双均线策略信号：短线上穿长线 -> 1(买入)，下穿 -> -1(卖出)。"""
    if short < 1 or long <= short:
        return []
    ss = sma(closes, short)
    sl = sma(closes, long)
    sig = [0] * len(closes)
    for i in range(1, len(closes)):
        if ss[i] is None or sl[i] is None or ss[i - 1] is None or sl[i - 1] is None:
            continue
        if ss[i - 1] <= sl[i - 1] and ss[i] > sl[i]:
            sig[i] = 1
        elif ss[i - 1] >= sl[i - 1] and ss[i] < sl[i]:
            sig[i] = -1
    return sig


def ma_breakout_signals(closes, period=20):
    """均线突破：收盘价上穿 MA(period) 买入，下穿卖出。"""
    if period < 2:
        return []
    m = sma(closes, period)
    sig = [0] * len(closes)
    for i in range(1, len(closes)):
        if m[i] is None or m[i - 1] is None:
            continue
        if closes[i - 1] <= m[i - 1] and closes[i] > m[i]:
            sig[i] = 1
        elif closes[i - 1] >= m[i - 1] and closes[i] < m[i]:
            sig[i] = -1
    return sig


def run_backtest(kline, signals, init_capital=100000.0, fee_rate=0.0008):
    """按收盘价成交、全额进出。返回权益曲线、成交流水与统计。"""
    cash = init_capital
    shares = 0.0
    equity = []
    trades = []
    open_trade = None
    wins = 0
    round_trips = 0
    for i, bar in enumerate(kline):
        sig = signals[i] if i < len(signals) else 0
        px = bar["close"]
        if sig == 1 and shares == 0:
            shares = cash * (1 - fee_rate) / px
            cash = 0.0
            open_trade = {"date": bar["date"], "price": px, "side": "buy"}
        elif sig == -1 and shares > 0:
            cash = shares * px * (1 - fee_rate)
            if open_trade:
                pnl = (px * (1 - fee_rate) - open_trade["price"] * (1 + fee_rate)) / (open_trade["price"] * (1 + fee_rate))
                trades.append(dict(open_trade, sellDate=bar["date"], sellPrice=px,
                                   pnlPct=round(pnl * 100, 2)))
                round_trips += 1
                if pnl > 0:
                    wins += 1
            open_trade = None
            shares = 0.0
        equity.append(round(cash + shares * px, 2))
    if open_trade:
        trades.append(dict(open_trade, sellDate=None, sellPrice=None, pnlPct=None, holding=True))
    final = equity[-1] if equity else init_capital
    peak = init_capital
    max_dd = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak)
    total_ret = (final - init_capital) / init_capital * 100
    days = max(len(kline), 1)
    annual = ((final / init_capital) ** (243.0 / days) - 1) * 100 if final > 0 else -100.0
    # 买入持有基准
    first = kline[0]["close"] if kline else 1
    last = kline[-1]["close"] if kline else 1
    buy_hold = (last - first) / first * 100
    stats = {
        "initCapital": init_capital,
        "finalEquity": round(final, 2),
        "totalReturnPct": round(total_ret, 2),
        "annualizedPct": round(annual, 2),
        "maxDrawdownPct": round(max_dd * 100, 2),
        "tradeCount": round_trips,
        "winRatePct": round(wins / round_trips * 100, 2) if round_trips else None,
        "buyHoldReturnPct": round(buy_hold, 2),
    }
    return {"equity": equity, "trades": trades, "stats": stats}


def backtest(code, strategy="ma_cross", short=5, long=20, period=20,
             init_capital=100000.0, fee_rate=0.0008, n=500):
    kline = fetch_kline(code, n=n)
    if not kline or len(kline) < 5:
        return None
    closes = [b["close"] for b in kline]
    if strategy == "ma_breakout":
        signals = ma_breakout_signals(closes, period)
        params = {"period": period}
    else:
        strategy = "ma_cross"
        signals = ma_cross_signals(closes, short, long)
        params = {"short": short, "long": long}
    bt = run_backtest(kline, signals, init_capital, fee_rate)
    return {"code": code, "strategy": strategy, "params": params,
            "kline": kline, "signals": signals, **bt}


# ---------------- HTTP 服务 ----------------

def read_body(handler):
    try:
        length = int(handler.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        return json.loads(handler.rfile.read(length).decode("utf-8"))
    except Exception as e:
        log_error("body", str(e))
        return {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _ok(self, **kw):
        self._send(200, kw)

    def _err(self, code, msg):
        self._send(code, {"ok": False, "error": msg})

    def log_message(self, fmt, *args):
        pass

    # ---- GET ----
    def do_GET(self):
        path = self.path.split("?")[0]
        qs = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
        try:
            if path == "/" or path == "/index.html":
                try:
                    with open(INDEX_HTML, "rb") as f:
                        self._send(200, f.read(), "text/html; charset=utf-8")
                except Exception as e:
                    self._err(500, "index.html 读取失败: %s" % e)
            elif path == "/api/health":
                self._ok(ok=True, time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            elif path == "/api/quote":
                codes = (qs.get("codes", [""])[0] or "").split(",")
                codes = [c.strip() for c in codes if c.strip()][:50]
                if not codes:
                    return self._err(400, "缺少 codes 参数")
                self._ok(ok=True, quotes=get_quotes(codes))
            elif path == "/api/watchlist":
                wl = load_json("watchlist", [])
                codes = [w["code"] if isinstance(w, dict) else w for w in wl]
                self._ok(ok=True, codes=codes, quotes=get_quotes(codes))
            elif path == "/api/alerts":
                self._ok(ok=True, alerts=load_json("alerts", []))
            elif path == "/api/check_alerts":
                self._ok(ok=True, results=check_alerts_now())
            elif path == "/api/trades":
                self._ok(ok=True, trades=load_json("trades", []))
            elif path == "/api/positions":
                self._ok(ok=True, **compute_positions())
            elif path == "/api/kline":
                code = (qs.get("code", [""])[0] or "").strip()
                n = int(qs.get("n", ["250"])[0])
                data = fetch_kline(code, n=min(max(n, 10), 800))
                if data is None:
                    return self._err(502, "K线数据不可用（%s）" % code)
                self._ok(ok=True, code=code, kline=data)
            elif path == "/api/backtest":
                code = (qs.get("code", [""])[0] or "").strip()
                if not code:
                    return self._err(400, "缺少 code 参数")
                r = backtest(code,
                             qs.get("strategy", ["ma_cross"])[0],
                             int(qs.get("short", ["5"])[0]),
                             int(qs.get("long", ["20"])[0]),
                             int(qs.get("period", ["20"])[0]))
                if r is None:
                    return self._err(502, "回测失败：K线数据不可用（%s）" % code)
                self._ok(ok=True, **r)
            elif path == "/api/reviews":
                reviews = load_json("reviews", [])
                reviews.sort(key=lambda x: x.get("date", ""), reverse=True)
                self._ok(ok=True, reviews=reviews, insights=compute_insights(reviews))
            elif path == "/api/review_digest":
                r = make_review_digest(qs.get("date", [None])[0])
                self._ok(ok=True, **r)
            elif path == "/api/insights":
                self._ok(ok=True, **compute_insights(load_json("reviews", [])))
            else:
                self._err(404, "not found")
        except BrokenPipeError:
            pass
        except Exception as e:
            log_error("GET", "%s: %s" % (path, e))
            self._err(500, "服务器内部错误: %s" % e)

    # ---- POST ----
    def do_POST(self):
        path = self.path.split("?")[0]
        body = read_body(self)
        try:
            if path == "/api/watchlist":
                action = body.get("action", "add")
                code = (body.get("code") or "").strip()
                with _lock:
                    wl = load_json("watchlist", [])
                    codes = [w["code"] if isinstance(w, dict) else w for w in wl]
                    if action == "add":
                        if not code:
                            return self._err(400, "缺少 code")
                        if classify_code(code) == "unknown":
                            return self._err(400, "无法识别代码：%s（A股: sh600519 / 港股: hk00700 / 美股: usAAPL / 期货: nf_RB0）" % code)
                        if code not in codes:
                            wl.append({"code": code})
                            save_json("watchlist", wl)
                    elif action == "remove":
                        wl = [{"code": c} for c in codes if c != code]
                        save_json("watchlist", wl)
                codes = [w["code"] for w in load_json("watchlist", [])]
                self._ok(ok=True, codes=codes, quotes=get_quotes(codes))
            elif path == "/api/alerts":
                if body.get("action") == "delete":
                    aid = body.get("id")
                    with _lock:
                        alerts = [a for a in load_json("alerts", []) if a.get("id") != aid]
                        save_json("alerts", alerts)
                    return self._ok(ok=True, alerts=alerts)
                code = (body.get("code") or "").strip()
                atype = body.get("type")
                val = _f(body.get("value"))
                if not code or atype not in ("above", "below", "pct_up", "pct_down") or val is None:
                    return self._err(400, "参数不完整：需要 code/type/value")
                with _lock:
                    alerts = load_json("alerts", [])
                    alert = {"id": "a%d" % int(time.time() * 1000), "code": code,
                             "type": atype, "value": val,
                             "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             "triggered": False}
                    alerts.append(alert)
                    save_json("alerts", alerts)
                self._ok(ok=True, alert=alert, alerts=alerts)
            elif path == "/api/trades":
                if body.get("action") == "delete":
                    tid = body.get("id")
                    with _lock:
                        trades = [t for t in load_json("trades", []) if t.get("id") != tid]
                        save_json("trades", trades)
                    return self._ok(ok=True, trades=trades, **compute_positions(trades))
                side = body.get("side")
                code = (body.get("code") or "").strip()
                price = _f(body.get("price"))
                qty = _f(body.get("qty"))
                if side not in ("buy", "sell") or not code or not price or price <= 0 or not qty or qty <= 0:
                    return self._err(400, "参数不完整：需要 side(buy/sell)/code/price/qty")
                name = (body.get("name") or "").strip() or get_quote(code).get("name", code)
                trade = {"id": "t%d" % int(time.time() * 1000),
                         "side": side, "code": code, "name": name,
                         "price": price, "qty": qty,
                         "amount": round(price * qty, 2),
                         "date": (body.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M")),
                         "note": (body.get("note") or "").strip()}
                with _lock:
                    trades = load_json("trades", [])
                    trades.insert(0, trade)
                    save_json("trades", trades)
                self._ok(ok=True, trade=trade, trades=trades, **compute_positions(trades))
            elif path == "/api/reviews":
                if body.get("action") == "delete":
                    rid = body.get("id")
                    with _lock:
                        reviews = [r for r in load_json("reviews", []) if r.get("id") != rid]
                        save_json("reviews", reviews)
                    return self._ok(ok=True, reviews=reviews, insights=compute_insights(reviews))
                date = (body.get("date") or "").strip()[:10]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    return self._err(400, "日期格式应为 YYYY-MM-DD")
                mood = body.get("mood") if body.get("mood") in MOODS else "中性"
                tags = [str(t).strip() for t in (body.get("tags") or []) if str(t).strip()][:10]
                note = str(body.get("note") or "").strip()[:5000]
                digest = body.get("digest") if isinstance(body.get("digest"), dict) else None
                with _lock:
                    reviews = [r for r in load_json("reviews", []) if r.get("date") != date]
                    review = {"id": "r%d" % int(time.time() * 1000), "date": date,
                              "mood": mood, "tags": tags, "note": note,
                              "pnl": _f(body.get("pnl"), 0) or 0, "digest": digest,
                              "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                    reviews.append(review)
                    save_json("reviews", reviews)
                self._ok(ok=True, review=review, reviews=reviews, insights=compute_insights(reviews))
            else:
                self._err(404, "not found")
        except BrokenPipeError:
            pass
        except Exception as e:
            log_error("POST", "%s: %s" % (path, e))
            self._err(500, "服务器内部错误: %s" % e)


def compute_positions(trades=None):
    """由交易流水汇总持仓与盈亏。卖出按移动平均成本扣减。"""
    trades = trades if trades is not None else load_json("trades", [])
    lots = {}  # code -> {name, qty, cost}
    for t in sorted(trades, key=lambda x: x.get("date", "")):
        c = t.get("code")
        if not c:
            continue
        q = _f(t.get("qty"), 0) or 0
        p = _f(t.get("price"), 0) or 0
        pos = lots.setdefault(c, {"name": t.get("name") or c, "qty": 0.0, "cost": 0.0})
        pos["name"] = t.get("name") or pos["name"]
        if t.get("side") == "buy":
            total_cost = pos["cost"] * pos["qty"] + p * q
            pos["qty"] += q
            pos["cost"] = total_cost / pos["qty"] if pos["qty"] else 0.0
        else:
            pos["qty"] -= q
            if pos["qty"] <= 1e-9:
                pos["qty"] = 0.0
                pos["cost"] = 0.0
    codes = sorted({c for c, v in lots.items() if v["qty"] > 1e-9})
    quotes = {q["code"]: q for q in get_quotes(codes)}
    positions = []
    total_value = total_pnl = 0.0
    for c in codes:
        pos = lots[c]
        q = quotes.get(c, {})
        last = q.get("price") or pos["cost"]
        value = last * pos["qty"]
        pnl = (last - pos["cost"]) * pos["qty"]
        pnl_pct = (last / pos["cost"] - 1) * 100 if pos["cost"] else None
        positions.append({"code": c, "name": pos["name"], "qty": pos["qty"],
                          "cost": round(pos["cost"], 4), "price": last,
                          "value": round(value, 2), "pnl": round(pnl, 2),
                          "pnlPct": round(pnl_pct, 2) if pnl_pct is not None else None,
                          "unavailable": q.get("unavailable")})
        total_value += value
        total_pnl += pnl
    return {"positions": positions,
            "totalValue": round(total_value, 2),
            "totalPnl": round(total_pnl, 2)}


# ---------------- 每日复盘 ----------------

MOODS = ("乐观", "中性", "谨慎", "悲观")


def make_review_digest(date_str=None):
    """生成复盘底稿：当日自选涨跌 + 当日交易流水 + 持仓盈亏快照。"""
    date_str = (date_str or "").strip() or datetime.now().strftime("%Y-%m-%d")
    trades = [t for t in load_json("trades", [])
              if (t.get("date") or "").startswith(date_str)]
    pos = compute_positions()
    wl = load_json("watchlist", [])
    codes = [w["code"] if isinstance(w, dict) else w for w in wl]
    quotes = get_quotes(codes)
    movers = sorted([q for q in quotes if not q.get("unavailable")
                     and q.get("changePct") is not None],
                    key=lambda q: q["changePct"], reverse=True)
    return {
        "date": date_str,
        "quotes": quotes,
        "topGainers": movers[:3],
        "topLosers": list(reversed(movers[-3:])) if len(movers) > 3 else [],
        "trades": trades,
        "tradeCount": len(trades),
        "buyAmount": round(sum(t.get("amount", 0) for t in trades if t.get("side") == "buy"), 2),
        "sellAmount": round(sum(t.get("amount", 0) for t in trades if t.get("side") == "sell"), 2),
        "totalValue": pos["totalValue"],
        "totalPnl": pos["totalPnl"],
    }


def compute_insights(reviews):
    """从历史复盘中提炼经验：标签 × 当日盈亏的关联统计。

    纯函数，测试可直接导入复用。复盘标签越多、记录越久，洞察越可靠。
    """
    tag_stats = {}
    for r in reviews:
        pnl = _f(r.get("pnl"), 0) or 0
        for tag in (r.get("tags") or []):
            s = tag_stats.setdefault(tag, {"sum": 0.0, "count": 0, "wins": 0})
            s["sum"] += pnl
            s["count"] += 1
            if pnl > 0:
                s["wins"] += 1
    tags = [{"tag": t, "count": s["count"], "sumPnl": round(s["sum"], 2),
             "avgPnl": round(s["sum"] / s["count"], 2),
             "winRatePct": round(s["wins"] / s["count"] * 100, 1)}
            for t, s in tag_stats.items()]
    tags.sort(key=lambda x: x["avgPnl"])
    # 单次标签视为偶然事件，只有重复出现（>=2次）才构成"经验模式"
    patterns = [t for t in tags if t["count"] >= 2]
    worst = patterns[0] if patterns and patterns[0]["avgPnl"] < 0 else None
    best = patterns[-1] if len(patterns) > 1 and patterns[-1]["avgPnl"] > 0 else None
    lessons = []
    if worst:
        lessons.append("「%s」出现过的 %d 个交易日平均亏损 %.2f——这是你最需要警惕的行为模式"
                       % (worst["tag"], worst["count"], abs(worst["avgPnl"])))
    if best:
        lessons.append("「%s」出现过的 %d 个交易日平均盈利 %.2f——把它固化成纪律"
                       % (best["tag"], best["count"], best["avgPnl"]))
    if len(reviews) < 5:
        lessons.append("复盘样本还少（%d 篇），坚持记录 5 篇以上，洞察会明显更可靠" % len(reviews))
    return {"reviewCount": len(reviews), "tags": tags,
            "worstTag": worst, "bestTag": best, "lessons": lessons}


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("交易工作台已启动: http://0.0.0.0:%d" % PORT, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
