# -*- coding: utf-8 -*-
"""生成招商银行(600036.SH)投资分析报告的 HTML（全自包含，图表用内联 SVG，不依赖 CDN）"""
import csv, os, math, json, html

BASE = r"C:\Users\汪的猪\WorkBuddy\2026-09-12-20-37-50\deliverables\trading-agent"
WORK = os.path.join(BASE, "_work")
OUT = os.path.join(BASE, "sh600036-analysis-2026-09-13.html")

# ---------- 1. 读取真实行情并计算 SMA20 ----------
rows = []
with open(os.path.join(WORK, "cmb_raw.csv"), encoding="utf-8") as f:
    for r in csv.DictReader(f):
        rows.append((r["date"], float(r["close"])))
rows.sort(key=lambda x: x[0])

closes_all = [c for _, c in rows]
sma20_all = []
for i in range(len(closes_all)):
    if i >= 19:
        sma20_all.append(sum(closes_all[i - 19:i + 1]) / 20.0)
    else:
        sma20_all.append(None)

N = 60
dates = [d for d, _ in rows][-N:]
closes = closes_all[-N:]
sma20 = sma20_all[-N:]

# ---------- 2. 评分数据（全部来自成员产出） ----------
RADAR = [("技术面", 7.3), ("基本面", 8.0), ("新闻面", 7.0), ("情绪面", 4.0), ("风险面", 6.0)]
RISK3 = [("激进派", 8.5), ("中性派", 5.0), ("保守派", 3.0)]
BULL = [
    ("PB 0.91（历史25.14%分位）破净安全边际 + ROE 13.44%", 9),
    ("净息差降幅腰斩至 5bp，行业Q2 17个季度首次单季回升", 9),
    ("资产质量最优 + 385%~410% 拨备的利润反哺期权", 7),
    ("股东回报行业第一：中期分红率35%、股息率4.88%", 8),
    ("大财富管理第二曲线：中收+5.99%、财富管理+26.53%", 7),
    ("技术面多头排列（ADX 67.68）+ 十五五金融规划政策β", 6),
]
BEAR = [
    ("零售之王护城河失守：零售利润-17.58%被对公反超", 9),
    ("息差拐点只靠负债端，资产端量价双杀", 9),
    ("PB 0.91 是价值陷阱而非安全边际", 8),
    ("拨备覆盖率18个月411.98%→385.10%，反哺期权被消耗", 8),
    ("六路资金全线流出，“占比小”镜像=没有增量买盘", 7),
    ("技术面高位滞涨 + 政策利好出尽", 6),
    ("零增长锁死分红天花板，股息溢价仅0.55pct", 6),
]

PRICE = 41.35
# ---------- 3. SVG 图表绘制 ----------
def esc(s):
    return html.escape(str(s))

def radar_svg(items, maxv=10.0, size=420, rings=(2, 4, 6, 8, 10), fill="#2563EB"):
    cx = cy = size / 2.0
    R = size / 2.0 - 78
    n = len(items)
    def pt(i, frac):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        return cx + R * frac * math.cos(ang), cy + R * frac * math.sin(ang)
    out = [f'<svg viewBox="0 0 {size} {size}" width="100%" style="max-width:{size}px" role="img">']
    # 网格
    for r in rings:
        frac = r / maxv
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, frac) for i in range(n)))
        out.append(f'<polygon points="{pts}" fill="none" stroke="#e5e7eb" stroke-width="1"/>')
        lx, ly = pt(0, frac)
        out.append(f'<text x="{lx:.1f}" y="{ly - 3:.1f}" font-size="9" fill="#9ca3af" text-anchor="middle">{r}</text>')
    # 轴线
    for i in range(n):
        x, y = pt(i, 1.0)
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#e5e7eb" stroke-width="1"/>')
    # 数据多边形
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, items[i][1] / maxv) for i in range(n)))
    out.append(f'<polygon points="{pts}" fill="{fill}" fill-opacity="0.18" stroke="{fill}" stroke-width="2.2"/>')
    for i in range(n):
        x, y = pt(i, items[i][1] / maxv)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{fill}"/>')
        out.append(f'<text x="{x:.1f}" y="{y - 10:.1f}" font-size="11" font-weight="600" fill="{fill}" text-anchor="middle">{items[i][1]}</text>')
    # 轴标签
    for i in range(n):
        x, y = pt(i, 1.0)
        dx = x - cx
        anchor = "middle"
        if dx > 12:
            anchor = "start"
        elif dx < -12:
            anchor = "end"
        ly = y + (20 if y > cy else -12)
        out.append(f'<text x="{x:.1f}" y="{ly:.1f}" font-size="12.5" font-weight="600" fill="#374151" text-anchor="{anchor}">{esc(items[i][0])}</text>')
    out.append("</svg>")
    return "\n".join(out)

def price_svg(dates, closes, sma20, refs, w=940, h=380):
    padL, padR, padT, padB = 62, 176, 22, 46
    pw, ph = w - padL - padR, h - padT - padB
    vals = [v for v in closes if v is not None] + [v for v in sma20 if v is not None] + [v for _, v, _ in refs]
    lo, hi = min(vals), max(vals)
    span = hi - lo
    lo -= span * 0.10
    hi += span * 0.10
    n = len(dates)
    def X(i):
        return padL + pw * i / (n - 1)
    def Y(v):
        return padT + ph * (1 - (v - lo) / (hi - lo))
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    # 横向网格 + y 轴刻度
    steps = 6
    for k in range(steps + 1):
        v = lo + (hi - lo) * k / steps
        y = Y(v)
        out.append(f'<line x1="{padL}" y1="{y:.1f}" x2="{padL + pw}" y2="{y:.1f}" stroke="#eef0f3" stroke-width="1"/>')
        out.append(f'<text x="{padL - 8}" y="{y + 4:.1f}" font-size="10.5" fill="#9ca3af" text-anchor="end">{v:.1f}</text>')
    # x 轴日期
    for i in range(0, n, 10):
        x = X(i)
        out.append(f'<line x1="{x:.1f}" y1="{padT + ph}" x2="{x:.1f}" y2="{padT + ph + 4}" stroke="#d1d5db" stroke-width="1"/>')
        out.append(f'<text x="{x:.1f}" y="{padT + ph + 18:.1f}" font-size="10" fill="#9ca3af" text-anchor="middle">{dates[i][5:]}</text>')
    # 参考线
    for label, v, color in refs:
        y = Y(v)
        out.append(f'<line x1="{padL}" y1="{y:.1f}" x2="{padL + pw}" y2="{y:.1f}" stroke="{color}" stroke-width="1.3" stroke-dasharray="6 4"/>')
        out.append(f'<text x="{padL + pw + 8}" y="{y + 4:.1f}" font-size="10" font-weight="600" fill="{color}">{esc(label)}</text>')
    # SMA20
    seg = [(X(i), Y(v)) for i, v in enumerate(sma20) if v is not None]
    if seg:
        out.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in seg) +
                   '" fill="none" stroke="#f59e0b" stroke-width="1.8" stroke-dasharray="5 3"/>')
    # 收盘价折线 + 渐变面积
    line = [(X(i), Y(v)) for i, v in enumerate(closes)]
    area = line + [(X(n - 1), padT + ph), (X(0), padT + ph)]
    out.append('<defs><linearGradient id="pg" x1="0" y1="0" x2="0" y2="1">'
               '<stop offset="0%" stop-color="#2563EB" stop-opacity="0.28"/>'
               '<stop offset="100%" stop-color="#2563EB" stop-opacity="0.02"/></linearGradient></defs>')
    out.append('<polygon points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in area) + '" fill="url(#pg)"/>')
    out.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in line) +
               '" fill="none" stroke="#2563EB" stroke-width="2.2"/>')
    # 末点高亮
    lx, ly = line[-1]
    out.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="4.5" fill="#2563EB" stroke="#fff" stroke-width="2"/>')
    out.append(f'<text x="{lx - 6:.1f}" y="{ly - 12:.1f}" font-size="11" font-weight="700" fill="#2563EB" text-anchor="end">41.35</text>')
    # 图例
    ly0 = padT + 6
    out.append(f'<line x1="{padL + 8}" y1="{ly0}" x2="{padL + 34}" y2="{ly0}" stroke="#2563EB" stroke-width="2.2"/>')
    out.append(f'<text x="{padL + 40}" y="{ly0 + 4}" font-size="11" fill="#374151">收盘价</text>')
    out.append(f'<line x1="{padL + 96}" y1="{ly0}" x2="{padL + 122}" y2="{ly0}" stroke="#f59e0b" stroke-width="1.8" stroke-dasharray="5 3"/>')
    out.append(f'<text x="{padL + 128}" y="{ly0 + 4}" font-size="11" fill="#374151">SMA20</text>')
    out.append("</svg>")
    return "\n".join(out)

def comparison_svg(bull, bear, w=940):
    rowH = 32
    labW = 690
    barX = 700
    barMaxW = 200
    h = 30 + len(bull) * rowH + 46 + len(bear) * rowH + 14
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    y = 22
    out.append(f'<text x="8" y="{y}" font-size="13" font-weight="700" fill="#b91c1c">多头论点（权重越高越强，红条）</text>')
    y += 14
    for txt, wt in bull:
        y += rowH
        bw = barMaxW * wt / 10.0
        ty = y + 13
        out.append(f'<text x="8" y="{ty:.1f}" font-size="11.5" fill="#374151">{esc(txt)}</text>')
        out.append(f'<rect x="{barX}" y="{y:.1f}" width="{barMaxW}" height="17" rx="4" fill="#f3f4f6"/>')
        out.append(f'<rect x="{barX}" y="{y:.1f}" width="{bw:.1f}" height="17" rx="4" fill="#dc2626" fill-opacity="0.85"/>')
        out.append(f'<text x="{barX + barMaxW + 12}" y="{ty:.1f}" font-size="11.5" font-weight="700" fill="#b91c1c">{wt}</text>')
    y += 34
    out.append(f'<text x="8" y="{y}" font-size="13" font-weight="700" fill="#166534">空头论点（权重越高越强，绿条）</text>')
    y += 14
    for txt, wt in bear:
        y += rowH
        bw = barMaxW * wt / 10.0
        ty = y + 13
        out.append(f'<text x="8" y="{ty:.1f}" font-size="11.5" fill="#374151">{esc(txt)}</text>')
        out.append(f'<rect x="{barX}" y="{y:.1f}" width="{barMaxW}" height="17" rx="4" fill="#f3f4f6"/>')
        out.append(f'<rect x="{barX}" y="{y:.1f}" width="{bw:.1f}" height="17" rx="4" fill="#16a34a" fill-opacity="0.85"/>')
        out.append(f'<text x="{barX + barMaxW + 12}" y="{ty:.1f}" font-size="11.5" font-weight="700" fill="#166534">{wt}</text>')
    out.append("</svg>")
    return "\n".join(out)

def tally_svg():
    """多空权重合计对比"""
    b = sum(w for _, w in BULL)
    s = sum(w for _, w in BEAR)
    w, h = 940, 66
    tot = b + s
    bl = (w - 40) * b / tot
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    out.append(f'<rect x="20" y="18" width="{bl:.1f}" height="26" rx="5" fill="#dc2626" fill-opacity="0.85"/>')
    out.append(f'<rect x="{20 + bl:.1f}" y="18" width="{(w - 40) - bl:.1f}" height="26" rx="5" fill="#16a34a" fill-opacity="0.85"/>')
    out.append(f'<text x="34" y="36" font-size="13" font-weight="700" fill="#fff">多头合计 {b}</text>')
    out.append(f'<text x="{w - 34:.0f}" y="36" font-size="13" font-weight="700" fill="#fff" text-anchor="end">空头合计 {s}</text>')
    out.append(f'<text x="20" y="60" font-size="11.5" fill="#6b7280">风险主管口径：多空加权强度 7.67 : 7.57，几近打平 —— 这正是最终给出 HOLD 而非强方向的量化依据。</text>')
    out.append("</svg>")
    return "\n".join(out)

# ---------- 4. 组装 HTML ----------
price_refs = [
    ("目标价 44.50", 44.50, "#dc2626"),
    ("加仓线 41.78", 41.78, "#ea580c"),
    ("现价/成本 41.35", 41.35, "#6b7280"),
    ("减仓线 40.09", 40.09, "#16a34a"),
    ("MA60 当前 38.51", 38.51, "#0d9488"),
    ("强支撑 34.28", 34.28, "#7c3aed"),
]
price_refs.sort(key=lambda x: -x[1])

CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
max-width:1040px;margin:0 auto;padding:26px 20px 60px;background:#f7f8fa;color:#1f2937;line-height:1.72;font-size:14.5px}
h1{font-size:19px;margin:0 0 6px}
.sub{color:#6b7280;font-size:12.5px;margin-bottom:22px}
.decision-card{background:linear-gradient(135deg,#f59e0b,#fbbf24);color:#fff;padding:30px 32px;border-radius:16px;
margin-bottom:22px;box-shadow:0 8px 24px rgba(245,158,11,.28)}
.dc-top{display:flex;align-items:center;gap:20px;flex-wrap:wrap}
.dc-badge{font-size:44px;font-weight:800;letter-spacing:1px;line-height:1.05}
.dc-sub{font-size:14px;opacity:.95}
.pill{display:inline-block;padding:3px 11px;border-radius:999px;background:rgba(255,255,255,.24);
font-size:12px;font-weight:600;margin-right:8px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:20px}
.gi{background:rgba(255,255,255,.2);padding:11px 13px;border-radius:9px}
.gi b{display:block;font-size:11.5px;opacity:.9;font-weight:600;margin-bottom:3px}
.gi span{font-size:16px;font-weight:700}
.card{background:#fff;padding:22px 24px;border-radius:12px;margin-bottom:18px;box-shadow:0 1px 3px rgba(16,24,40,.06)}
.card h2{margin:0 0 4px;font-size:15.5px}
.card .hint{color:#9ca3af;font-size:12px;margin-bottom:14px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 9px;border-bottom:1px solid #f0f1f3;text-align:left;vertical-align:top}
th{background:#fafbfc;font-weight:600;color:#4b5563;font-size:12px}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
th.num{text-align:right;white-space:nowrap}
td:first-child,th:first-child{white-space:nowrap}
td:first-child{font-weight:600}
details{border:1px solid #eef0f3;border-radius:9px;padding:11px 14px;margin-bottom:9px;background:#fcfcfd}
summary{cursor:pointer;font-weight:650;font-size:13.5px;color:#111827}
details p,details ul{margin:9px 0 3px;font-size:13px;color:#374151}
ul{padding-left:20px}
li{margin:4px 0}
.tag{display:inline-block;padding:1px 8px;border-radius:5px;font-size:11.5px;font-weight:700}
.tag-red{background:#fee2e2;color:#b91c1c}
.tag-green{background:#dcfce7;color:#166534}
.tag-amber{background:#fef3c7;color:#92400e}
.tag-blue{background:#dbeafe;color:#1d4ed8}
.disclaimer{color:#6b7280;font-size:12px;padding:15px 18px;border-top:1px solid #e5e7eb;margin-top:22px;
background:#fff;border-radius:10px;line-height:1.7}
.src{color:#9ca3af;font-size:11.5px;margin-top:6px}
"""

H = []
A = H.append
A('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">')
A('<meta name="viewport" content="width=device-width,initial-scale=1">')
A('<title>招商银行(600036.SH) 投资分析报告 · 2026-09-13</title>')
A(f"<style>{CSS}</style></head><body>")
A('<h1>招商银行（600036.SH）投资分析报告</h1>')
A('<div class="sub">分析日期：2026-09-13（周日休市）｜最近交易日：2026-09-11 收盘 41.35 元｜'
  '分析团队：交易分析团队 12 位专业角色 · 五阶段协作｜数据来源：NeoData 金融数据服务（唯一数据源）</div>')

# 1 决策卡片
A('<div class="decision-card"><div class="dc-top">')
A('<div><div class="dc-badge">HOLD</div>')
A('<div class="dc-sub" style="margin-top:8px">维持持有 · 防守型持有，用价位纪律替代方向押注</div></div>')
A('<div style="margin-left:auto;text-align:right">')
A('<span class="pill">信心水平：中</span><span class="pill">风险等级：中</span><span class="pill">建议持仓期：中期（1-6 个月）</span>')
A('</div></div>')
A('<div class="grid">')
for k, v in [("最终决策", "HOLD"), ("建议仓位", "10%~17%"),
             ("加仓价（需验证）", "41.78"), ("第一目标价", "44.50"),
             ("第二目标价", "46.00~47.70"), ("第一减仓线", "40.09"),
             ("第二减仓线", "38.03~38.20"), ("清仓线", "34.28")]:
    A(f'<div class="gi"><b>{esc(k)}</b><span>{esc(v)}</span></div>')
A('</div></div>')

# 2 雷达图
A('<div class="card"><h2>📊 综合评分雷达图</h2>')
A('<div class="hint">五个维度 0-10 分。技术/基本面/新闻/情绪四维由 Phase 1 分析师各自打分；风险面为"风险可控度"（10=完全可控），由风险主管在压力测试后给出。</div>')
A(radar_svg(RADAR))
A('<table style="margin-top:6px"><tr><th>维度</th><th class="num">评分</th><th>依据</th></tr>')
for name, sc, why in [
    ("技术面", "7.3", "中期多头排列（MA5 41.18 > MA20 40.09 > MA60 38.51），但 MACD 红柱连缩 5 日、量能萎缩 17%、RSI14 63.38 —— 强势整理"),
    ("基本面", "8.0", "ROE 13.44%、不良率 0.94%、拨备 410%、PB 0.91（25.14% 分位）、股息率 4.88%；唯一短板是成长性 5.5（3 年 CAGR 近零）"),
    ("新闻面", "7.0", "《金融强国建设“十五五”规划》+ 央行配套方案落地，中报营收 +4.83%/净利 +2.02%；扣分项为零售利润 -17.58% 及公募减持"),
    ("情绪面", "4.0", "主力近 5 日 -7.27 亿、融资余额 11 连降、公募 -130 家、调研 0 次；对冲项为券商 100% 买入与 +29.6% 目标价空间"),
    ("风险面", "6.0", "0.69x 极尾口径压力测试 -1,857 元（账户 -1.86%），守住 2% 单标的预算；PB 0.91 与拨备 385% 提供缓冲，但缺增量买盘"),
]:
    A(f'<tr><td><b>{esc(name)}</b></td><td class="num"><b>{esc(sc)}</b></td><td>{esc(why)}</td></tr>')
A('</table></div>')

# 3 价格走势
A('<div class="card"><h2>📈 价格走势与关键价位</h2>')
A('<div class="hint">近 60 个交易日收盘价（2026-06-22 ~ 2026-09-11，真实数据）＋ SMA20 均线；'
  '水平虚线为报告裁定的关键价位。受数据接口返回窗口限制，MA60 以当前值 38.51 作为参考线呈现。</div>')
A(price_svg(dates, closes, sma20, price_refs))
A('<table style="margin-top:10px"><tr><th>类型</th><th>价位</th><th>依据</th></tr>')
for t, p, why in [
    ("压力位 1", "41.78", "近 20 日及样本区间最高点（2026-09-04），短期直接压制；同时是加仓触发价"),
    ("压力位 2", "42.23", "布林带上轨（20 日，2σ）"),
    ("支撑位 1（核心）", "40.09", "MA20（40.085）+ 布林中轨（40.085）+ VWMA20（40.10）三线共振；第一减仓线"),
    ("支撑位 2", "39.35~39.73", "MA30（39.728）+ 8 月下旬平台整理区"),
    ("支撑位 3", "38.03~38.20", "8/18 阶段低点 38.03；第二减仓线"),
    ("强支撑", "34.28", "样本区间最低点（2026-07-01），中期趋势生命线；清仓线"),
]:
    A(f'<tr><td><b>{esc(t)}</b></td><td class="num"><b>{esc(p)}</b></td><td>{esc(why)}</td></tr>')
A('</table></div>')

# 4 多空对比
A('<div class="card"><h2>⚖️ 多空论点对比</h2>')
A('<div class="hint">每条论点后的数字为权重（1-10，由研究员自评）；条形长度与权重成正比。'
  '两侧权重合计接近，是本轮无法给出强方向的直接原因。</div>')
A(tally_svg())
A('<div style="height:10px"></div>')
A(comparison_svg(BULL, BEAR))
A('</div>')

# 5 风险三角
A('<div class="card"><h2>📋 风险评估三方立场</h2>')
A('<div class="hint">分值含义统一为"风险偏好强度"（10 = 最愿意承担风险）。'
  '三方在辩论中自报系数均为 8，风险主管按其实际主张反向校准后得出可区分的分值。</div>')
A('<div style="display:flex;justify-content:center">')
A(radar_svg(RISK3, fill="#7C3AED"))
A('</div>')
A('<table><tr><th>立场</th><th class="num">分值</th><th>核心主张</th><th>风险主管裁决</th></tr>')
for name, sc, claim, verdict in [
    ("激进派", "8.5", "质疑“三季报≥4 项”实为加仓禁令；实证 8/25~8/28 连续四日收盘均在 40.09 下方，照规则 8/26 即被洗出，之后涨至 41.69（+5.95%）；主张现价加 100 股",
     '<span class="tag tag-red">否决</span> 现价加仓、取消 40.09、放宽至 25%、上调目标价'),
    ("中性派", "5.0", "指出减仓股数倒挂：25% 股数只承担 6.8% 亏损、50% 股数承担 75.8%；主张改 200/100/100，最坏损失降 31.1% 而上涨情景代价为 0",
     '<span class="tag tag-green">采纳为主轴</span> 股数 200/100/100、双轨验证、现金预案、假破位回补'),
    ("保守派", "3.0", "认为“最坏 -1,865 元”低估约 2.1 倍：PB 若收敛至板块均值 0.69x 对应 31.33 元，400 股损失 -4,008 元；主张先减 200 股锁定零成本窗口",
     '<span class="tag tag-blue">部分采纳</span> 采纳 0.69x 压力测试口径；否决现价减 200 股'),
]:
    A(f'<tr><td><b>{esc(name)}</b></td><td class="num"><b>{esc(sc)}</b></td><td>{esc(claim)}</td><td>{verdict}</td></tr>')
A('</table></div>')

# 6 详细文本
A('<div class="card"><h2>📝 详细分析</h2>')
A('<details open><summary>四维分析摘要</summary>')
A('<p><b>技术面：</b>趋势判定为<b>上涨</b>（高位强势整理）。收盘 41.35 站上全部主要均线并呈多头排列，近 60 日累计 +12.82%；'
  '但短期动能转弱——MACD 红柱连续 5 日收缩、KDJ 高位死叉雏形、5 日均量较 20 日均量缩约 17%，9/9~9/11 在 40.90~41.57 横盘未破 9/4 高点。'
  'RSI14 63.38 属中性偏多，ATR14 0.672（1.62%）。核心支撑 40.09（三线共振），强支撑 34.28。</p>')
A('<p><b>基本面：</b>2026H1 营收 1,781.81 亿（+4.83%）、归母净利 764.45 亿（+2.02%）；净息差 1.83%（降幅收窄至 5bp）、'
  '不良率 0.94%、拨备覆盖率 410.03%、加权 ROE 13.44%。估值 PE(TTM) 6.87（58.56% 分位）、PB 0.91（25.14% 分位）、股息率约 4.88%。'
  '三维判断：盈利能力<b>好</b>、资产质量<b>好但边际有隐忧</b>（关注类 1.29%→1.43%、信用卡不良 1.90%）、成长性<b>中偏弱</b>（3 年 CAGR 近零）。'
  '估值结论：PB 处历史 25.14% 分位，<b>低估（合理偏低）</b>。</p>')
A('<p><b>新闻面：</b>总体倾向<b>偏多（7/10）</b>。《金融强国建设"十五五"规划》9/10-9/11 出台叠加央行 9 份配套方案，银行板块 42 股全涨；'
  '中报营收利润双增，中期分红率 35%，卖方近半年评级 100% 买入/增持、综合目标价 53.59 元（+29.60%）。'
  '但公司内部结构性矛盾突出：<b>零售税前利润 -17.58%，十年来首次被对公（+23.33%）反超</b>，且招行不在 3,000 亿特别国债注资名单内。</p>')
A('<p><b>情绪面：</b>总体<b>悲观（4.0/10）</b>。主力资金当日净流出 -1.89 亿元、近 5 日 -7.27 亿元；融资余额 98.16 亿元、<b>11 连降</b>；'
  '融券余量 8/31→9/11 增加 33%；陆股通 Q2 减持约 1.005 亿股；主动偏股基金持股减少 130 家、比例 -0.25pct、机构调研 0 次；'
  '股东户数 +18.91%（筹码分散）。与卖方一致看多形成<b>严重背离</b>。</p>')
A('</details>')

A('<details open><summary>多空辩论结论</summary>')
A('<p><b>多头核心论点（加权强度 7.67）：</b>以"PB 0.91 处 25.14% 分位 + ROE 13.44%"为地基，'
  '主张息差降幅腰斩至 5bp 已是行业先行指标（行业 Q2 17 个季度首次单季回升）、385%~410% 拨备构成利润反哺期权、'
  '股息率 4.88% 与派现募资比行业第 1 提供防御，上行情景目标价 44.5 / 47.7 / 52.2 元。'
  '对资金流出的辩护是"六项合计约 30 亿，不足流通市值 0.4%"。自认最脆弱一环即<b>零售核心逻辑受损</b>。</p>')
A('<p><b>空头核心论点（7 条）：</b>最重一击直取多头地基——"多头说零售之王的净资产更值钱，中报说零售之王正在变成更贵的对公银行"。'
  '其余关键点：息差"降幅收窄"是止跌而非回升且只靠负债端（净利息收入 +5.6% 全靠总资产 +8.91% 以量补价，边际回报仅 0.63 倍）；'
  'PB 0.91 在板块均值 0.69x 面前已属最贵之一、是价值陷阱；拨备覆盖率 18 个月从 411.98% 降至 385.10%，"反哺"实为消耗；'
  '六路资金全线流出的镜像含义是<b>没有增量买盘</b>。下行情景目标价 38.6 / 36.3 / 34.1 元。</p>')
A('<p><b>研究主管裁决：HOLD（防守型持有），信心中、风险中。</b>裁决口径为：多空加权 7.67 : 7.57 几近打平，标的处三季报关键等待期，'
  '现价 41.35 等于成本且 PB 处 25% 分位，已知信息已反映；16.6% 仓位既无加仓空间也无斩仓必要。'
  '五个争议点的裁定为：破净<b>托底有效但不构成进攻理由</b>（修正支持多头）；"降幅收窄"<b>不等于拐点</b>（支持空头定性）；'
  '拨备属<b>正在释放的缓冲</b>但 385% 仍是监管 2.5 倍（支持空头方向）；零售受损<b>倾向结构性、溢价收窄而非崩塌</b>（倾向支持空头）；'
  '资金流出<b>确认缺增量买盘但不构成恐慌出逃</b>（支持空头方向）。</p>')
A('</details>')

A('<details open><summary>风险评估结论与最终参数</summary>')
A('<p><b>三方交锋焦点：</b>激进派主张现价加仓、把第一减仓线下移到 38.03~38.20，理由是 40.09 距现价仅 1.87 倍 ATR，'
  '且 8 月最后一周的连续四日收盘已在 40.09 下方——照规则 8/26 就会被洗出，此后股价涨至 41.69。'
  '保守派针锋相对：现价等于成本是唯一的"零成本窗口"，且"最坏 -1,865 元"低估约 2.1 倍，主张先减 200 股。'
  '中性派则指出真正的缺陷在股数分配——原方案 100/100/200 让 25% 的股数只承担 6.8% 亏损、而 50% 的股数承担 75.8%。</p>')
A('<p><b>风险主管最终裁决：HOLD，采纳中性派为主轴。</b>七项分歧裁决结果：'
  '①否决现在加仓（事件前投放＝信息真空放大押注，且招行不在注资名单，行业β≠个股α）；'
  '②否决现价减 200 股（减仓不锁定任何正收益，3% 波幅在 2 倍 ATR 内属噪音）；'
  '③保留 40.09 为第一道线但股数改为 <b>200/100/100</b>；'
  '④确认方式维持"连 2 日收盘"为主并保留跳空/放量例外；'
  '⑤采纳双轨验证并新增"中性态"分支（2~3 项→维持不动），填补指令真空；'
  '⑥压力测试口径采纳保守派的 <b>0.69x 板块均值</b>（对应 31.33 元）；'
  '⑦现金 30,902 元事件前全额闲置、事件后条件投放上限 200 股（8,270 元），始终保留 ≥22.6%。</p>')
A('<table style="margin-top:8px"><tr><th>关键价位</th><th>数值</th><th>对应动作</th></tr>')
for k, v, act in [
    ("加仓线", "放量站上 41.78（量 >20 日均量 1.3 倍）", "且三季报双轨验证通过 → 分两档各加 100 股，上限 600 股"),
    ("第一减仓线", "40.09（连 2 日收盘破位）", "减 <b>200 股</b>"),
    ("第二减仓线", "38.03~38.20", "再减 100 股"),
    ("清仓线", "34.28", "清出剩余 100 股"),
    ("假破位回补", "3 日内收回 40.09 上方且主力转净流入", "回补 100 股"),
    ("目标价", "第一 44.50 / 第二 46.00~47.70", "PB 0.98x / 1.05x，含息总回报 +12.5%~+20.2%"),
]:
    A(f'<tr><td><b>{esc(k)}</b></td><td>{esc(v)}</td><td>{act}</td></tr>')
A('</table>')
A('<p style="margin-top:12px"><b>最坏情景压力测试（0.69x 极尾 + 滑点口径）：</b>'
  '最终方案损失 <b>-1,857 元（账户 -1.86%）</b>，守住 2% 单标的风险预算；'
  '而交易员原方案在同口径下为 -2,674 元（-2.67%），已突破预算。'
  '温和情景 -1,284 元（-1.28%）。账户层面风险预算：单标的 2%（约 2,000 元）、账户整体 8%（约 8,000 元）。</p>')
A('<p><b>相对交易员原方案的 6 处改动：</b>①减仓股数 100/100/200 → 200/100/100，最坏损失改善 31.1% 而上涨情景代价为 0；'
  '②加仓条件从单点门槛改为双轨验证 + 中性态分支；③新增假破位回补规则；④新增现金事件后条件投放预案；'
  '⑤压力测试口径从 0.75x 收紧至 0.69x 板块均值；⑥正式设定账户风险预算。'
  '保留未动的部分：HOLD 方向、本阶段 0 操作、三条价位线、41.78 加仓线、44.5/46.0~47.7 目标价、10%~17% 仓位区间。</p>')
A('</details>')

A('<details><summary>关键催化剂与风险事件</summary>')
A('<p><b>正面催化剂：</b>①2026 年三季报（10 月下旬）若净息差降幅继续收窄、关注类回落，将直接证伪空头核心；'
  '②35% 中期分红方案落地与股息率兑现；③《金融强国建设"十五五"规划》配套细则陆续出台；'
  '④Q4 潜在降准降息对负债端成本形成支撑；⑤PB 自 0.91x 向 1.05x 修复的估值弹性。</p>')
A('<p><b>风险事件：</b>①零售信用周期继续下行（零售不良 1.16%、信用卡不良 1.90%、信用卡关注类 4.60%）；'
  '②关注类贷款率若升破 1.6%，触发改判 SELL；③拨备覆盖率若跌破 380%（当前 385.10%，安全垫仅 5pct）；'
  '④不良率破 0.95%；⑤房地产业公司贷款不良 4.24% 的进一步暴露；⑥"利好出尽"与公募持续减仓带来的资金面压制。</p>')
A('</details>')

A('<details><summary>团队成员与产出文件（12 份，可溯）</summary>')
A('<ul>'
  '<li>Phase 1：01-market-analyst（技术·涂一线）、02-fundamentals-analyst（基本面·季本实）、03-news-analyst（新闻·闻一新）、04-sentiment-analyst（情绪·莫慌言）</li>'
  '<li>Phase 2：05-bull-researcher（多头·牛正阳）、06-bear-researcher（空头·熊正寒）、07-research-manager（研究主管·蔡定锋）</li>'
  '<li>Phase 3：08-trader（交易员·程交远）</li>'
  '<li>Phase 4：09-risk-aggressive（激进·甘为先）、10-risk-conservative（保守·沈行远）、11-risk-neutral（中性·钟允平）、12-risk-manager（风险主管·严控风）</li>'
  '</ul>')
A('<p class="src">全部原始报告位于本报告同级的 <code>_work/</code> 目录，原始接口返回留档于 <code>_work/_raw/</code>。'
  '所有财务与行情数字均来自 NeoData 金融数据服务单次查询，未使用训练数据或第三方来源补全；接口未返回项已在各报告中如实标注。</p>')
A('</details>')
A('</div>')

A('<div class="disclaimer" id="tail">⚠️ <b>免责声明</b>：本报告由 AI 基于公开信息与实时金融数据整理生成，'
  '仅供参考，<b>不构成任何投资建议或个股推荐</b>。所有价位、仓位与触发条件均为分析框架下的推演结果，'
  '不保证未来实现。投资有风险，决策需谨慎；过去的表现不代表未来的结果。请结合自身风险承受能力做出独立判断。</div>')
A('</body></html>')

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(H))

print("OK ->", OUT)
print("size:", os.path.getsize(OUT), "bytes")
print("price points:", len(closes), "sma20 points:", sum(1 for v in sma20 if v is not None))
print("close range:", min(closes), "-", max(closes))
print("HOLD block present:", "HOLD" in "\n".join(H))
