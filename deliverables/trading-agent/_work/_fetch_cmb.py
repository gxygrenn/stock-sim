# -*- coding: utf-8 -*-
"""分片拉取招商银行600036.SH历史日线并合并为本目录下 cmb_raw.csv"""
import json, re, subprocess, sys, os

SKILL = r"C:/Users/汪的猪/.workbuddy/plugins/cache/cb_teams_marketplace/finance-data/1.5.0/skills/neodata-financial-search"
PY = r"C:/Users/汪的猪/.workbuddy/binaries/python/versions/3.13.12/python.exe"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cmb_raw.csv")

CHUNKS = [
    ("2026-06-12", "2026-06-21"),
    ("2026-06-22", "2026-07-01"),
    ("2026-07-02", "2026-07-11"),
    ("2026-07-12", "2026-07-21"),
    ("2026-07-22", "2026-07-31"),
    ("2026-08-01", "2026-08-10"),
    ("2026-08-11", "2026-08-20"),
    ("2026-08-21", "2026-08-30"),
    ("2026-08-31", "2026-09-09"),
    ("2026-09-10", "2026-09-11"),
]

rows = {}
for a, b in CHUNKS:
    q = f"招商银行 600036.SH {a} 至 {b} 每日开盘 收盘 最高 最低 成交量 成交额 换手率 日线行情"
    p = subprocess.run([PY, os.path.join(SKILL, "scripts", "query.py"), "--query", q, "--data-type", "api"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    txt = p.stdout
    try:
        j = json.loads(txt)
        blocks = j.get("data", {}).get("apiData", {}).get("apiRecall", [])
        content = "\n".join(x.get("content", "") for x in blocks)
    except Exception as e:
        print("PARSE_FAIL", a, b, e, file=sys.stderr)
        continue
    n = 0
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("| 2026-"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 12:
            continue
        date = cells[0]
        try:
            o, h, l, c = float(cells[1]), float(cells[2]), float(cells[3]), float(cells[4])
            vol = float(cells[9]); amt = float(cells[10]); turn = cells[11]
        except Exception:
            continue
        rows[date] = (o, h, l, c, vol, amt, turn)
        n += 1
    print(f"{a}~{b}: +{n} rows (cum {len(rows)})", file=sys.stderr)

with open(OUT, "w", encoding="utf-8") as f:
    f.write("date,open,high,low,close,volume,amount,turnover\n")
    for d in sorted(rows):
        o, h, l, c, vol, amt, turn = rows[d]
        f.write(f"{d},{o},{h},{l},{c},{vol:.0f},{amt:.0f},{turn}\n")
print("WROTE", OUT, "rows=", len(rows))
