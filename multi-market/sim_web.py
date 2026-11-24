# -*- coding: utf-8 -*-
"""HTTP 接口与页面（Handler）、后台 worker、启动入口 main()。"""

import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import server  # noqa: E402



# ---------------------------------------------------------------- HTTP
INDEX_HTML = os.path.join(server.BASE, "index.html")



class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, fmt, *args):
        pass

    def _account(self):
        """从 query 参数解析账户（?account=us / cn_conservative / cn_aggressive，短名 cnc/cna 亦可）。"""
        from urllib.parse import urlparse, parse_qs
        try:
            q = urlparse(self.path).query
            account = (parse_qs(q).get("account") or [DEFAULT_ACCOUNT])[0]
        except Exception:
            account = DEFAULT_ACCOUNT
        account = ACCOUNT_ALIASES.get(account, account)   # 短名别名映射（2026-10-15）
        return account if account in ACCOUNTS else DEFAULT_ACCOUNT

    def do_GET(self):
        set_account(self._account())
        path = self.path.split("?")[0]
        with server._lock:
            maybe_catchup(load_state())
        if path in ("/", "/index.html"):
            try:
                with open(INDEX_HTML, "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            except Exception:
                self._send(404, "index.html not found", "text/plain; charset=utf-8")
            return
        if path == "/api/state":
            with server._lock:
                self._json(load_state())
            return
        if path == "/api/health":
            self._json({"ok": True, "time": now_str()})
            return
        if path == "/api/daily_facts":
            with server._lock:
                self._json(daily_facts(load_state()))
            return
        if path == "/api/version":
            self._json({"ok": True, "features": ["gate", "pendingReview", "dailyFacts", "dailyReview", "marketContext", "reviewDecoupled", "reviewTTL", "catchup", "approvedTTL", "sameDayBuyMax", "councilInitialReview", "accountAliases", "minBuyAmount", "lossReReview", "tpOncePerDay", "roomVeto", "swapGate", "clearSell", "pxAtReviewV2", "reviewLog600", "enqSkip", "trimRule", "sellFloor", "loseCut", "trailArm", "closeSweep", "tpSellFrac"], "time": now_str()})
            return
        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        set_account(self._account())
        path = self.path.split("?")[0]
        req = self._body()
        s = load_state()
        with server._lock:
            try:
                if path == "/api/trade":
                    side = req.get("side")
                    code = req.get("code")
                    price = float(req.get("price") or 0)
                    qty = int(float(req.get("qty") or 0))
                    item = next((p for p in s["pool"] if p["code"] == code), None)
                    h = find_holding(s, code)
                    name = (item or h or {}).get("name", code)
                    if price <= 0:
                        price = server.current_price(s, item or {"code": code})
                    reason = (req.get("reason") or "手动")[:40]
                    tr = do_buy(s, name, code, price, qty, reason) if side == "buy" \
                        else do_sell(s, name, code, price, qty, reason)
                    save_state()
                    self._json({"ok": bool(tr), "trade": tr})
                    return

                if path == "/api/review_done":
                    # LLM 专家团评审完一只候选后调用。评审与执行解耦：
                    # 这里只把结论写回队列，成交由 scan() 在交易时段内按最新价落地。
                    code = (req.get("code") or "").strip().lower()
                    verdict = (req.get("verdict") or "").strip().upper()[:8]
                    # 2026-10-30 修正：note 从 120 字放宽到 400 字。
                    # 原因：盘中引擎 R6 要求「只数上限否决必须点名置换对象」，而置换结论通常写在
                    # note 末尾，120 字截断后只剩技术面开头，复盘无法核实规则是否执行
                    # （10-30 us 礼来/万事达四轮 HOLD 的 note 全部止于半句）。
                    note = (req.get("note") or "")[:400]
                    try:
                        conf = int(req.get("confidence") or 0)
                    except Exception:
                        conf = 0
                    pend = s.get("pendingReview") or []
                    hit = None
                    for p in pend:
                        if (p.get("code") or "").lower() == code:
                            hit = p
                            break
                    reviewed_at = now_str()
                    s.setdefault("reviewed", {})[code] = reviewed_at
                    if hit is None:
                        log("专家团评审 %s -> %s（%s；队列中已无此候选，多已 TTL 过期）"
                            % (code, verdict or "?", note[:40]))
                        save_state()
                        self._json({"ok": True, "queued": False, "pending": len(pend),
                                    "msg": "queue item gone (expired?)"})
                        return
                    hit["reviewedAt"] = reviewed_at
                    if verdict in ("BUY", "SELL"):
                        hit["state"] = "approved"
                        hit["review"] = {"verdict": verdict, "confidence": conf,
                                         "note": note, "reviewedAt": reviewed_at}
                        outcome = "已批准，待交易时段按最新价成交"
                    else:
                        if hit in pend:
                            pend.remove(hit)
                        outcome = "HOLD，出队"
                    # 评审历史台账：完整留存结论，独立于队列生命周期，供事后对账审计
                    # 补记评审时价格：让「被否决/HOLD 的票后来涨跌」可回算，供每日误杀归因学习
                    # 取值优先级（2026-11-13 调整）：评审时刻价优先——cached_price（poolQuote，
                    # 每轮 refresh 更新）→ current_price 实时拉价 → 入队 px 兜底。
                    # 旧口径 px 优先会让急拉股取到入队价（≈昨收），误杀涨幅系统性虚高
                    # （11-12 东方环宇虚增约 10pp，R7 补买溢价同步失真）
                    _rev_price = cached_price(s, hit.get("code")) or 0.0
                    if _rev_price <= 0:
                        _rev_item = next((it for it in s["pool"] if it.get("code") == hit.get("code")), None)
                        _rev_price = server.current_price(s, _rev_item) if _rev_item else 0.0
                    if _rev_price <= 0:
                        _rev_price = hit.get("px") or 0.0
                    rl = s.setdefault("reviewLog", [])
                    rl.append({"code": hit.get("code"), "name": hit.get("name"),
                               "verdict": verdict, "confidence": conf, "note": note,
                               "reviewedAt": reviewed_at, "outcome": outcome,
                               "queued": verdict in ("BUY", "SELL"),
                               "priceAtReview": round(_rev_price, 3) if _rev_price > 0 else None,
                               "fillPrice": None, "fillTime": None})
                    s["reviewLog"] = rl[-600:]
                    log("专家团评审 %s(%s) -> %s 信心%s（%s）| %s"
                        % (code, hit.get("name"), verdict or "?", conf or "-",
                           note[:30], outcome))
                    done_now = 0
                    if verdict in ("BUY", "SELL") and server.in_trading_hours():
                        done_now = server.execute_approved(s)     # 盘中即时落地；盘后等下一轮
                    save_state()
                    self._json({"ok": True, "verdict": verdict,
                                "queued": verdict in ("BUY", "SELL"),
                                "executedNow": done_now,
                                "pending": len(s.get("pendingReview") or [])})
                    return
                if path == "/api/daily_review":
                    # 每日复盘：由 LLM 复盘官写入当日复盘结论（前端展示，保留最近 30 天）
                    date = (req.get("date") or datetime.now().strftime("%Y-%m-%d"))[:10]
                    summary = (req.get("summary") or "")[:600]
                    bullets = [str(b)[:160] for b in (req.get("bullets") or [])][:8]
                    lessons = [str(b)[:200] for b in (req.get("lessons") or [])][:8]
                    score = req.get("score")
                    if not summary and not bullets:
                        self._json({"ok": False, "msg": "summary/bullets 至少给一个"})
                        return
                    rec = {"date": date, "time": now_str(), "summary": summary,
                           "bullets": bullets, "lessons": lessons,
                           "score": score, "account": round(total_asset(s), 2)}
                    s["dailyReviews"] = ([rec] + [r for r in (s.get("dailyReviews") or [])
                                                  if r.get("date") != date])[:30]
                    log("每日复盘 %s 已存档（%d 条要点 / %d 条经验）"
                        % (date, len(bullets), len(lessons)))
                    save_state()
                    self._json({"ok": True, "date": date, "kept": len(s["dailyReviews"])})
                    return

                if path == "/api/market_context":
                    # 市场环境播报：由「市场环境播报」自动化收盘后写入（前端两张卡片展示）
                    # 结构：{"date":"YYYY-MM-DD", "intl":{impact,strength,summary,items[]},
                    #        "sentiment":{score,label,summary,items[]}}，items 元素 {name,value,note}
                    date = (req.get("date") or datetime.now().strftime("%Y-%m-%d"))[:10]
                    intl, sent = req.get("intl"), req.get("sentiment")
                    if not isinstance(intl, dict) or not isinstance(sent, dict):
                        self._json({"ok": False, "msg": "intl/sentiment 需为对象"})
                        return

                    def norm_items(v):
                        out = []
                        for it in (v or [])[:10]:
                            if not isinstance(it, dict):
                                continue
                            out.append({"name": str(it.get("name") or "")[:20],
                                        "value": str(it.get("value") or "")[:24],
                                        "note": str(it.get("note") or "")[:60]})
                        return out

                    sc = sent.get("score")
                    if isinstance(sc, (int, float)):
                        sc = max(-5, min(5, int(sc)))
                    rec = {
                        "date": date, "time": now_str(),
                        "intl": {"impact": str(intl.get("impact") or "")[:8],
                                 "strength": intl.get("strength"),
                                 "summary": str(intl.get("summary") or "")[:300],
                                 "items": norm_items(intl.get("items"))},
                        "sentiment": {"score": sc,
                                      "label": str(sent.get("label") or "")[:8],
                                      "summary": str(sent.get("summary") or "")[:300],
                                      "items": norm_items(sent.get("items"))},
                    }
                    if not (rec["intl"]["summary"] or rec["intl"]["items"]
                            or rec["sentiment"]["summary"] or rec["sentiment"]["items"]):
                        self._json({"ok": False, "msg": "intl/sentiment 内容为空"})
                        return
                    s["marketContext"] = rec
                    log("市场环境播报 %s 已存档（%s / 情绪 %s）"
                        % (date, rec["intl"]["impact"] or "?", rec["sentiment"]["label"] or "?"))
                    save_state()
                    self._json({"ok": True, "date": date, "time": rec["time"]})
                    return

                if path == "/api/settle":
                    pct = float(req.get("pct") or 0)
                    if s["priceMode"] == "real":
                        server.refresh_prices(s)
                        q = s.get("quote") or {}
                        pct = q.get("pct", pct)
                    settle(s, pct)
                    save_state()
                    self._json({"ok": True})
                    return

                if path == "/api/inject":
                    # 手动投喂一条消息，走与自动扫描**完全相同**的决策链路
                    # （盘后/周末新闻稀少时，用它验证整条链路是否通）
                    text = (req.get("text") or "").strip()
                    if not text:
                        self._json({"ok": False, "msg": "消息内容为空"})
                        return
                    try:
                        compute_signals(s)
                    except Exception as exc:
                        log("投喂前算信号异常: %s" % exc)
                    n = {"id": "inj-" + now_str(), "time": now_str(),
                         "title": (req.get("title") or text)[:60], "text": text,
                         "inject": True}
                    if req.get("score") is not None:
                        try:
                            n["forceScore"] = int(req["score"])
                        except Exception:
                            pass
                    s["roundTrades"] = 0       # 手动投喂自成一轮
                    # 门控模式（autoTrade=False）下投喂与自动扫描保持一致：
                    # 只把候选送进评审队列，不绕过专家团直接下单
                    gate_on = not s["config"].get("autoTrade")
                    act = decide(s, n, force_trade=not gate_on)
                    if gate_on and n.get("action") == "待执行":
                        enqueue_review_candidates(s, [n])
                        act = "待专家团评审"
                    s["news"] = ([n] + list(s.get("news") or []))[:60]
                    save_state()
                    log("手动投喂 {%s} -> %s" % (n["title"][:20], act))
                    self._json({"ok": True, "action": act, "result": n})
                    return

                if path == "/api/undo":
                    if not s["history"]:
                        self._json({"ok": False, "msg": "没有可撤销的记录"})
                        return
                    r = s["history"].pop()
                    try:
                        s["idx"] = s["idx"] / (1 + r["mkt"] / 100.0)
                    except Exception:
                        pass
                    s["nextDate"] = r["date"]
                    log("撤销结算 %s" % r["date"])
                    save_state()
                    self._json({"ok": True})
                    return

                if path == "/api/refresh":
                    err = server.refresh_prices(s)
                    save_state()
                    self._json({"ok": True, "err": err})
                    return

                if path == "/api/scan":
                    res = scan(s, force_trade=bool(req.get("force")))
                    self._json({"ok": True, "result": res})
                    return

                if path == "/api/council":
                    # 召集专家团对全池开一次会（只出意见，不下单）
                    err = server.refresh_prices(s)
                    try:
                        compute_signals(s)
                    except Exception as exc:
                        log("信号计算异常: %s" % exc)
                    sess = now_str()
                    recs = []
                    for item in s["pool"]:
                        try:
                            recs.append(server.run_council(s, item, item["code"],
                                                    news_score=0, session=sess,
                                                    advisory=True))
                        except Exception as exc:
                            log("评议异常 %s: %s" % (item.get("name"), exc))
                    save_state()
                    self._json({"ok": True, "session": sess, "err": err, "records": recs})
                    return

                if path == "/api/pool":
                    act = req.get("action")
                    if act == "add":
                        it = {"name": req.get("name", "").strip(),
                              "code": (req.get("code") or "").strip().lower(),
                              "alias": (req.get("alias") or "").strip(),
                              "tags": (req.get("tags") or "").strip(),
                              "beta": float(req.get("beta") or 1.0)}
                        if not it["name"] or not it["code"]:
                            self._json({"ok": False, "msg": "名称和代码必填"})
                            return
                        if any(p["code"] == it["code"] for p in s["pool"]):
                            self._json({"ok": False, "msg": "该代码已在自选池"})
                            return
                        s["pool"].append(it)
                        log("自选池新增 %s(%s)" % (it["name"], it["code"]))
                    elif act == "del":
                        code = (req.get("code") or "").strip().lower()
                        s["pool"] = [p for p in s["pool"] if p["code"] != code]
                    save_state()
                    self._json({"ok": True, "pool": s["pool"]})
                    return

                if path == "/api/rebalance":
                    # 立即生成一轮调池建议（抓强势榜 + 分析淘汰），写入待评审队列
                    res = build_rebalance(s)
                    self._json({"ok": True, "remove": res.get("remove", 0),
                                "add": res.get("add", 0),
                                "poolChanges": s.get("poolChanges") or []})
                    return

                if path == "/api/pool_change_done":
                    # LLM 门控：写回调池建议结论 {code, action: approve|reject, note}
                    code = (req.get("code") or "").strip().lower()
                    action = (req.get("action") or "").strip().lower()
                    note = (req.get("note") or "")[:120]
                    pend = s.get("poolChanges") or []
                    hit = None
                    for p in pend:
                        if (p.get("code") or "").lower() == code:
                            hit = p
                            break
                    if hit is None:
                        save_state()
                        self._json({"ok": True, "queued": False, "pending": len(pend),
                                    "msg": "pool change item gone"})
                        return
                    if action == "approve":
                        hit["state"] = "approved"
                    else:
                        hit["state"] = "rejected"
                    hit["note"] = note
                    log("调池门控：%s %s %s(%s)（%s）"
                        % ("批准" if action == "approve" else "否决",
                           "淘汰" if hit["direction"] == "remove" else "选入",
                           hit["name"], code, note[:30]))
                    save_state()
                    done = execute_approved_pool_changes(s)   # 纯改池不涉及下单，盘内盘外均可
                    self._json({"ok": True, "approved": action == "approve",
                                "executedNow": done,
                                "pending": len(s.get("poolChanges") or [])})
                    return

                if path == "/api/config":
                    for k, v in (req.get("config") or {}).items():
                        if k in s["config"]:
                            s["config"][k] = v
                    save_state()
                    self._json({"ok": True, "config": s["config"]})
                    return

                if path == "/api/mode":
                    s["priceMode"] = req.get("priceMode", s["priceMode"])
                    save_state()
                    self._json({"ok": True})
                    return

                if path == "/api/reset":
                    account = server.current_account()
                    keep_pool = s.get("pool")
                    server._states[account] = default_state(account)
                    server._states[account]["pool"] = keep_pool or list(ACCOUNTS[account]["pool"])
                    save_state()
                    self._json({"ok": True})
                    return

                self._send(404, "not found", "text/plain; charset=utf-8")
            except Exception as exc:
                self._json({"ok": False, "msg": str(exc)}, 500)



# ---------------------------------------------------------------- 定时
def worker():
    while True:
        time.sleep(30)
        for account in list(ACCOUNTS):
            set_account(account)
            try:
                s = load_state(account)
                cfg = s["config"]
                # 收盘前补扫（2026-11-24，P1）：A股 14:55-14:59 一次纯代码 check_stops，
                # 补 14:40→15:00 的扫描盲区。不调 LLM、不取新闻、不进评审队列，每账户每日一次。
                now = datetime.now()
                if (server.cn_close_sweep_window(now)
                        and s.get("closeSweepDate") != today_str()):
                    try:
                        r = server.close_sweep(s)
                        if r.get("ran"):
                            s["closeSweepDate"] = today_str()
                            if r.get("sold"):
                                msg = "收盘前补扫 %s：止盈止损 %d 笔" % (account, r["sold"])
                            else:
                                msg = "收盘前补扫 %s：无触发%s" % (
                                    account, "（%s）" % r["why"] if r.get("why") else "")
                            log(msg)
                            save_state()
                    except Exception as exc:
                        log("收盘前补扫异常[%s]: %s" % (account, exc))
                        save_state()
                need = cfg.get("scanMin", 30) * 60
                # 频率优化：休市时降低扫描频率（至少 4 小时一次），盘中按 scanMin
                if not server.in_trading_hours():
                    need = max(need, 240 * 60)
                last = s.get("lastScan", "")
                due = True
                if last:
                    try:
                        delta = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
                        due = delta >= need
                    except Exception:
                        due = True
                if due:
                    if s["priceMode"] == "real":
                        server.refresh_prices(s)
                    # autoTrade 关闭时 scan 只刷新资讯/信号并维护待评审队列，不会下单
                    scan(s)
                    # 动态调池：按周期生成建议（门控待评审）+ 落地已批准的调池
                    if s["config"].get("useRebalance"):
                        try:
                            last = s.get("lastRebalance", "")
                            due = (not last) or (
                                (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days
                                >= int(s["config"].get("rebalanceIntervalDays", 7)))
                            if due:
                                build_rebalance(s)
                            execute_approved_pool_changes(s)
                        except Exception as exc:
                            log("动态调池异常[%s]: %s" % (account, exc))
                    # 收盘后自动结算当天（按账户市场区分结算时间）
                    if s["priceMode"] == "real" and s["nextDate"] <= today_str():
                        now = datetime.now()
                        hm = now.hour * 60 + now.minute
                        market = (ACCOUNTS.get(account) or {}).get("market")
                        if market == "us":
                            # 结算窗口 = 收盘后 ~ 下一次开盘前。原写法只判 hm >= close，
                            # 而 close 只有 250/310，于是晚上 21:30 开盘时也被判「已收盘」，
                            # 拿开盘价结算当日（与「每次扫描都结算、标签逐日错位」叠加成灾）
                            close = 4 * 60 + 10 if us_dst(now) else 5 * 60 + 10
                            ope = (21 * 60 + 30) if us_dst(now) else (22 * 60 + 30)
                            settled = close <= hm < ope
                        else:
                            settled = now.hour > 15 or (now.hour == 15 and now.minute >= 10)
                        if settled:
                            q = s.get("quote") or {}
                            lab = settle(s, q.get("pct", 0.0))
                            if lab:
                                log("自动结算 %s（大盘 %+.2f%%）" % (lab, q.get("pct", 0)))
                    s["lastScan"] = now_str()
                    save_state()
            except Exception as exc:
                try:
                    log("定时任务异常[%s]: %s" % (account, exc))
                    save_state()
                except Exception:
                    pass



def main():
    port = int(os.environ.get("PORT", "8000"))
    for account in ACCOUNTS:
        load_state(account)
    if not os.environ.get("NO_WORKER"):      # 测试时可用 NO_WORKER=1 关掉自循环
        threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("多市场模拟炒股服务已启动: http://0.0.0.0:%d" % port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
