# -*- coding: utf-8 -*-
"""最近 30 天各定时任务 token 消耗明细（按请求时间归日）-> JSON + CSV"""
import os, json, glob, sqlite3, datetime, collections, statistics

HOME = os.path.expanduser("~")
WB = os.path.join(HOME, ".workbuddy")
DB = os.path.join(WB, "workbuddy.db")
NOW = datetime.datetime.now()
START = NOW - datetime.timedelta(days=30)

con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
con.row_factory = sqlite3.Row

autos = {}
for r in con.execute("select id,name,status,rrule,deleted_at from automations"):
    autos[r["id"]] = {"name": r["name"], "status": r["status"],
                      "deleted": bool(r["deleted_at"]), "rrule": r["rrule"]}

sess2auto = {}
for r in con.execute("select automation_id, metadata_json from automation_runs"):
    sid = None
    try:
        md = json.loads(r["metadata_json"] or "{}")
        sid = md.get("sessionId") or md.get("conversationId")
    except Exception:
        pass
    if sid:
        sess2auto[sid] = r["automation_id"]

def parse_file(path):
    sid = os.path.basename(path)[:-6]
    reqs = {}
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            pd = d.get("providerData") or {}
            u = pd.get("usage") or {}
            if not u:
                continue
            key = (pd.get("conversationRequestId") or d.get("id"), pd.get("stepSeq"))
            raw = pd.get("rawUsage") or {}
            tot = int(u.get("totalTokens") or 0)
            inp = int(u.get("inputTokens") or 0)
            out = int(u.get("outputTokens") or 0)
            cached = sum(int(x.get("cached_tokens") or 0) for x in (u.get("inputTokensDetails") or []))
            prev = reqs.get(key)
            if prev is None or tot > prev[0]:
                reqs[key] = (tot, inp, out, cached, float(raw.get("credit") or 0),
                             (d.get("timestamp") or 0) / 1000.0)
    return sid, reqs

# reqs: list of dicts, 每请求一条
all_req = []
for p in glob.glob(os.path.join(WB, "projects", "*", "*.jsonl")):
    try:
        sid, reqs = parse_file(p)
    except Exception:
        continue
    for (tot, inp, out, cached, credit, ts) in reqs.values():
        if not ts:
            continue
        dt = datetime.datetime.fromtimestamp(ts)
        if dt < START:
            continue
        all_req.append({"sid": sid, "ts": ts, "day": dt.strftime("%Y-%m-%d"),
                        "total": tot, "in": inp, "out": out, "cached": cached,
                        "credit": credit, "auto": sess2auto.get(sid)})

# 每个会话的运行次数（用于"运行数/单次均值"）
sess_runs = collections.Counter(r["sid"] for r in all_req)
sess_total = collections.defaultdict(int)
for r in all_req:
    sess_total[r["sid"]] += r["total"]

def agg(items):
    return {
        "req": len(items),
        "total": sum(i["total"] for i in items),
        "in": sum(i["in"] for i in items),
        "out": sum(i["out"] for i in items),
        "cached": sum(i["cached"] for i in items),
        "uncached": sum(i["in"] - i["cached"] for i in items),
        "credit": round(sum(i["credit"] for i in items), 1),
    }

by_auto = collections.defaultdict(list)
manual = []
for r in all_req:
    (by_auto[r["auto"]] if r["auto"] else manual).append(r)

rows = []
for aid, items in by_auto.items():
    a = agg(items)
    sess_ids = {i["sid"] for i in items}
    per_run = [sess_total[s] for s in sess_ids]
    a.update({"id": aid, "name": autos.get(aid, {}).get("name", aid),
              "deleted": autos.get(aid, {}).get("deleted", False),
              "runs": len(sess_ids), "per_run": per_run})
    rows.append(a)
rows.sort(key=lambda r: -r["total"])
mrow = agg(manual)

daily = collections.defaultdict(lambda: {"total": 0, "req": 0, "runs": 0, "auto_total": 0})
for r in all_req:
    d = daily[r["day"]]
    d["total"] += r["total"]
    d["req"] += 1
    d["runs"] += 0
    if r["auto"]:
        d["auto_total"] += r["total"]
# 每日会话数
seen = collections.defaultdict(set)
for r in all_req:
    seen[r["day"]].add(r["sid"])
days = sorted(daily)
for d in days:
    daily[d]["runs"] = len(seen[d])

out = {
    "window": {"start": START.strftime("%Y-%m-%d %H:%M"), "end": NOW.strftime("%Y-%m-%d %H:%M")},
    "days": days,
    "daily": [{"day": d, **daily[d]} for d in days],
    "tasks": [{
        "name": r["name"], "deleted": r["deleted"], "runs": r["runs"], "req": r["req"],
        "total": r["total"], "inp": r["in"], "out": r["out"], "cached": r["cached"],
        "uncached": r["uncached"], "credit": r["credit"],
        "avg_run": round(r["total"] / r["runs"]) if r["runs"] else 0,
        "median_run": round(statistics.median(r["per_run"])) if r["per_run"] else 0,
        "max_run": max(r["per_run"]) if r["per_run"] else 0,
        "avg_req_run": round(r["req"] / r["runs"], 1) if r["runs"] else 0,
    } for r in rows],
    "manual": {"runs": len({i["sid"] for i in manual}), "req": mrow["req"],
               "total": mrow["total"], "inp": mrow["in"], "out": mrow["out"],
               "cached": mrow["cached"], "uncached": mrow["uncached"], "credit": mrow["credit"]},
}
json.dump(out, open("_token_usage.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("窗口", out["window"])
print("%-38s %5s %6s %8s %8s %8s %7s %6s %6s" %
      ("任务", "运行", "请求", "总M", "未缓存M", "输出M", "点数", "均次M", "中位M"))
for t in out["tasks"]:
    print("%-38s %5d %6d %8.2f %8.2f %8.2f %7.0f %6.2f %6.2f%s" % (
        t["name"][:36], t["runs"], t["req"], t["total"]/1e6, t["uncached"]/1e6,
        t["out"]/1e6, t["credit"], t["avg_run"]/1e6, t["median_run"]/1e6,
        " [已删]" if t["deleted"] else ""))
m = out["manual"]
print("%-38s %5d %6d %8.2f %8.2f %8.2f %7.0f %6.2f %6s" % (
    "【人工对话/其他】", m["runs"], m["req"], m["total"]/1e6, m["uncached"]/1e6,
    m["out"]/1e6, m["credit"], (m["total"]/m["runs"]/1e6) if m["runs"] else 0, ""))
print()
print("按天：")
for d in days:
    print("  %s  %6.2fM  自动%6.2fM  请求%4d  会话%3d" % (
        d, daily[d]["total"]/1e6, daily[d]["auto_total"]/1e6, daily[d]["req"], daily[d]["runs"]))
grand = sum(t["total"] for t in out["tasks"]) + m["total"]
print("\n总计 %.2fM tokens / %d 请求" % (grand/1e6, sum(t["req"] for t in out["tasks"]) + m["req"]))
