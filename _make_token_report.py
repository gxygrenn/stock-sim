# -*- coding: utf-8 -*-
"""根据 _token_usage.json 生成 HTML 报告 + CSV"""
import json, datetime, os

d = json.load(open("_token_usage.json", encoding="utf-8"))
tasks = d["tasks"]
manual = d["manual"]
daily = d["daily"]

def M(x):  # 百万
    return x / 1e6

def fmt(x, nd=1):
    return ("%." + str(nd) + "f") % M(x)

grand = sum(t["total"] for t in tasks) + manual["total"]
auto_total = sum(t["total"] for t in tasks)
auto_runs = sum(t["runs"] for t in tasks)
live = [t for t in tasks if not t["deleted"]]
dead = [t for t in tasks if t["deleted"]]
live_total = sum(t["total"] for t in live)
dead_total = sum(t["total"] for t in dead)
all_req = sum(t["req"] for t in tasks) + manual["req"]
avg_ctx = grand / all_req / 1000

maxv = max(t["total"] for t in tasks)

rows_html = []
for i, t in enumerate(tasks, 1):
    w = t["total"] / maxv * 100
    cls = "dead" if t["deleted"] else "live"
    rows_html.append(f"""
      <tr class="{cls}">
        <td class="idx">{i}</td>
        <td class="name">{t['name']}{'<span class="tag">已删除</span>' if t['deleted'] else ''}</td>
        <td class="num">{t['runs']}</td>
        <td class="num">{t['req']}</td>
        <td class="num strong">{fmt(t['total'])}</td>
        <td class="barcell"><div class="bar {cls}" style="width:{w:.1f}%"></div></td>
        <td class="num">{fmt(t['uncached'])}</td>
        <td class="num">{fmt(t['out'], 2)}</td>
        <td class="num">{t['credit']:.0f}</td>
        <td class="num">{fmt(t['avg_run'], 2)}</td>
        <td class="num">{fmt(t['median_run'], 2)}</td>
        <td class="num muted">{t['avg_req_run']}</td>
      </tr>""")
rows_html = "".join(rows_html)

# 每日柱状图
dvals = [x["total"] for x in daily]
dauto = [x["auto_total"] for x in daily]
dmax = max(dvals) if dvals else 1
bars = []
for x in daily:
    h = x["total"] / dmax * 100
    ha = x["auto_total"] / dmax * 100
    bars.append(
        f'<div class="barcol" title="{x["day"]}：总计 {fmt(x["total"])}M（自动 {fmt(x["auto_total"])}M，{x["runs"]} 次运行）">'
        f'<div class="barwrap"><div class="barinner" style="height:{h:.1f}%"></div>'
        f'<div class="barauto" style="height:{ha:.1f}%"></div></div>'
        f'<div class="daylab">{x["day"][5:]}</div></div>')
bars = "".join(bars)

# 分周对比
pre = [x for x in daily if "2026-10-19" <= x["day"] <= "2026-10-22"]
pre_avg = sum(x["auto_total"] for x in pre) / len(pre) / 1e6 if pre else 0
post = [x for x in daily if x["day"] >= "2026-10-23"]
post_avg = sum(x["auto_total"] for x in post) / len(post) / 1e6 if post else 0

top = tasks[0]
html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>模拟炒股 · 定时任务 Token 消耗清单（近 30 天）</title>
<style>
  :root {{
    --bg: #f6f7f9; --card: #ffffff; --ink: #1c2430; --sub: #67707f;
    --line: #e6e9ee; --brand: #0f7a6c; --brand-soft: #e2f2ef;
    --gray: #b9c0cb; --gray-soft: #f0f2f5; --warn: #b7791f;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 28px 56px; background: var(--bg); color: var(--ink);
    font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 14px; line-height: 1.6; -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 1180px; margin: 0 auto; }}
  h1 {{ font-size: 24px; margin: 0 0 6px; letter-spacing: -0.2px; }}
  .sub {{ color: var(--sub); font-size: 13px; margin-bottom: 24px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 18px; }}
  .kpi {{
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 16px 18px;
  }}
  .kpi .lab {{ color: var(--sub); font-size: 12px; margin-bottom: 6px; }}
  .kpi .val {{ font-size: 26px; font-weight: 650; letter-spacing: -0.5px; }}
  .kpi .val small {{ font-size: 13px; font-weight: 500; color: var(--sub); margin-left: 3px; }}
  .kpi .hint {{ font-size: 12px; color: var(--sub); margin-top: 4px; }}
  .card {{
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 20px 20px 8px; margin-bottom: 18px;
  }}
  .card h2 {{ font-size: 15px; margin: 0 0 4px; }}
  .card .note {{ color: var(--sub); font-size: 12px; margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{
    text-align: right; font-weight: 600; color: var(--sub); font-size: 12px;
    padding: 8px 8px; border-bottom: 1px solid var(--line); white-space: nowrap;
  }}
  th:nth-child(1), th:nth-child(2) {{ text-align: left; }}
  td {{ padding: 9px 8px; border-bottom: 1px solid #f1f3f6; vertical-align: middle; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  td.strong {{ font-weight: 650; }}
  td.idx {{ color: var(--gray); width: 26px; text-align: left; }}
  td.name {{ max-width: 250px; font-weight: 550; }}
  td.muted {{ color: var(--sub); }}
  tr.dead td.name {{ color: #8b939f; font-weight: 500; }}
  .tag {{
    display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 5px;
    background: var(--gray-soft); color: #8b939f; font-size: 11px; font-weight: 500;
  }}
  td.barcell {{ width: 190px; }}
  .bar {{ height: 9px; border-radius: 5px; background: var(--brand); opacity: .85; }}
  .bar.dead {{ background: var(--gray); }}
  .chart {{ display: flex; align-items: flex-end; gap: 4px; height: 190px; padding: 6px 2px 0; }}
  .barcol {{ flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%; }}
  .barwrap {{ position: relative; flex: 1; width: 100%; display: flex; align-items: flex-end; }}
  .barinner {{ width: 100%; background: #cfe6e1; border-radius: 3px 3px 0 0; }}
  .barauto {{ position: absolute; bottom: 0; left: 0; width: 100%; background: var(--brand); border-radius: 3px 3px 0 0; opacity: .8; }}
  .daylab {{ font-size: 10px; color: var(--sub); margin-top: 5px; transform: rotate(-45deg); height: 22px; }}
  .legend {{ display: flex; gap: 16px; font-size: 12px; color: var(--sub); margin: 2px 0 12px; }}
  .dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 3px; margin-right: 5px; vertical-align: middle; }}
  .insight {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  .ins {{ background: #fbfcfd; border: 1px solid var(--line); border-left: 3px solid var(--brand); border-radius: 8px; padding: 12px 14px; }}
  .ins b {{ display: block; margin-bottom: 3px; }}
  .ins span {{ color: var(--sub); font-size: 12.5px; }}
  .foot {{ color: var(--sub); font-size: 12px; margin-top: 8px; }}
  @media (max-width: 900px) {{ .kpis {{ grid-template-columns: repeat(2,1fr); }} .insight {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
  <h1>定时任务 Token 消耗清单</h1>
  <div class="sub">统计窗口 {d['window']['start']} — {d['window']['end']}　·　数据源：本地请求级用量记录（每个 API 请求一条）</div>

  <div class="kpis">
    <div class="kpi"><div class="lab">总消耗</div><div class="val">{fmt(grand)}<small>M tokens</small></div>
      <div class="hint">日均 {grand/30/1e6:.1f}M</div></div>
    <div class="kpi"><div class="lab">自动任务占比</div><div class="val">{auto_total/grand*100:.0f}<small>%</small></div>
      <div class="hint">{fmt(auto_total)}M / {auto_runs} 次运行</div></div>
    <div class="kpi"><div class="lab">平均上下文</div><div class="val">{avg_ctx:.0f}<small>k / 请求</small></div>
      <div class="hint">{all_req} 个请求，缓存命中约 96%</div></div>
    <div class="kpi"><div class="lab">实际计费输入</div><div class="val">{fmt(sum(t['uncached'] for t in tasks)+manual['uncached'])}<small>M 未缓存</small></div>
      <div class="hint">其余为缓存命中，成本低一个量级</div></div>
  </div>

  <div class="card">
    <h2>各任务消耗明细（按总量降序）</h2>
    <div class="note">「在用 4 个任务」共 {fmt(live_total)}M，「已删除任务」{fmt(dead_total)}M —— 后者是 10-23 合并前的旧任务，token 已停止产生。</div>
    <table>
      <thead><tr>
        <th>#</th><th>任务</th><th>运行</th><th>请求</th><th>总量(M)</th><th>占比</th>
        <th>未缓存(M)</th><th>输出(M)</th><th>点数</th><th>均次(M)</th><th>中位(M)</th><th>请求/次</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
      <tfoot>
        <tr class="dead">
          <td></td><td class="name">人工对话 / 其他会话（非定时任务）</td>
          <td class="num">{manual['runs']}</td><td class="num">{manual['req']}</td>
          <td class="num strong">{fmt(manual['total'])}</td><td></td>
          <td class="num">{fmt(manual['uncached'])}</td><td class="num">{fmt(manual['out'],2)}</td>
          <td class="num">{manual['credit']:.0f}</td>
          <td class="num">{fmt(manual['total']/manual['runs'],2) if manual['runs'] else '-'}</td>
          <td class="num">-</td><td class="num muted">-</td>
        </tr>
        <tr>
          <td></td><td class="name" style="font-weight:700">合计</td>
          <td class="num" style="font-weight:700">{auto_runs + manual['runs']}</td>
          <td class="num" style="font-weight:700">{all_req}</td>
          <td class="num" style="font-weight:700">{fmt(grand)}</td><td></td>
          <td class="num" style="font-weight:700">{fmt(sum(t['uncached'] for t in tasks)+manual['uncached'])}</td>
          <td class="num" style="font-weight:700">{fmt(sum(t['out'] for t in tasks)+manual['out'],2)}</td>
          <td class="num" style="font-weight:700">{sum(t['credit'] for t in tasks)+manual['credit']:.0f}</td>
          <td class="num"></td><td class="num"></td><td class="num"></td>
        </tr>
      </tfoot>
    </table>
  </div>

  <div class="card">
    <h2>每日消耗趋势</h2>
    <div class="note">深色 = 定时任务自动消耗，浅色 = 人工对话。柱子按请求发生日期归集。</div>
    <div class="legend">
      <span><i class="dot" style="background:#0f7a6c"></i>自动任务</span>
      <span><i class="dot" style="background:#cfe6e1"></i>人工对话</span>
    </div>
    <div class="chart">{bars}</div>
    <div class="foot">峰值 {dvals and [x['day'] for x in daily if x['total']==max(dvals)][0]}：{fmt(max(dvals))}M　·　合并前 10-19~10-22 工作日自动日均 {pre_avg:.1f}M　·　合并后 10-23 全天 24.3M、10-24（周六休市）15.2M</div>
    <div style="height:10px"></div>
  </div>

  <div class="card" style="padding-bottom:20px">
    <h2>几个关键结论</h2>
    <div class="note">下一步优化怎么下手，看这四条</div>
    <div class="insight">
      <div class="ins"><b>① 钱主要花在「次数」，不是「单次」</b>
        <span>平均每次请求只带 {avg_ctx:.0f}k 上下文，但一个月发了 {all_req} 个请求。盘中引擎 30 天跑了 {tasks[1]['runs'] if len(tasks)>1 else auto_runs} 次，占了 {fmt(tasks[1]['total']) if len(tasks)>1 else '-'}M。</span></div>
      <div class="ins"><b>② 收盘复盘是单次最贵</b>
        <span>单次均价 {fmt(tasks[2]['avg_run'],2) if len(tasks)>2 else '-'}M、{tasks[2]['avg_req_run'] if len(tasks)>2 else '-'} 个请求/次，是盘中引擎的 17 倍——自查回环 + 三个账户重复取数是主因。</span></div>
      <div class="ins"><b>③ 96% 输入是缓存命中</b>
        <span>总输入 308M 中仅 {fmt(sum(t['uncached'] for t in tasks))}M 未命中缓存。真正贵的是「未缓存 + 输出」，以及请求数量本身。</span></div>
      <div class="ins"><b>④ 合并见效了，但还要一周验证</b>
        <span>10-19~10-22 四个工作日自动任务日均 {pre_avg:.1f}M；合并当天 10-23 全天 24.3M，10-24（周六休市）15.2M。等价降幅要等下周一至周五跑完才能定论。</span></div>
    </div>
    <div class="foot">口径说明：token 数取自每次 API 请求的 usage 记录（input + output，input 含缓存命中部分）；「点数」为同一记录里的 credit 字段，与计费口径一致。人工对话指未绑定定时任务的会话。</div>
  </div>
</div>
</body>
</html>
"""

open("deliverables/token-usage-report-2026-10-25.html", "w", encoding="utf-8").write(html) \
    if os.path.isdir("deliverables") else open("token-usage-report-2026-10-25.html", "w", encoding="utf-8").write(html)

# CSV
with open("_token_usage_by_task.csv", "w", encoding="utf-8-sig") as f:
    f.write("排名,任务,状态,运行次数,请求数,总token,未缓存输入,输出token,缓存token,点数,单次均值,单次中位,请求数每次\n")
    for i, t in enumerate(tasks, 1):
        f.write("%d,%s,%s,%d,%d,%d,%d,%d,%d,%.2f,%d,%d,%.1f\n" % (
            i, t["name"], "已删除" if t["deleted"] else "在用", t["runs"], t["req"],
            t["total"], t["uncached"], t["out"], t["cached"], t["credit"],
            t["avg_run"], t["median_run"], t["avg_req_run"]))
    f.write("-,人工对话/其他,非任务,%d,%d,%d,%d,%d,%d,%.2f,%d,-,-\n" % (
        manual["runs"], manual["req"], manual["total"], manual["uncached"],
        manual["out"], manual["cached"], manual["credit"],
        (manual["total"] // manual["runs"]) if manual["runs"] else 0))
print("HTML + CSV 已生成")
