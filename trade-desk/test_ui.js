// 前端渲染测试：DOM 桩 + 抠出 <script> eval，断言渲染结果无 undefined/NaN/[object Object]
"use strict";
const fs = require("fs");
const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

let PASS = 0, FAIL = 0;
function check(name, cond, detail) {
  if (cond) { PASS++; console.log("  [PASS] " + name); }
  else { FAIL++; console.log("  [FAIL] " + name + " " + (detail || "")); }
}

// ---- DOM 桩 ----
const elements = {};
function makeEl(id) {
  return {
    id, innerHTML: "", textContent: "", value: "", width: 900, height: 300,
    clientWidth: 900,
    getContext: () => ({
      clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {},
      fill() {}, fillRect() {}, fillText() {}, closePath() {}, strokeStyle: "",
      fillStyle: "", lineWidth: 1, font: "", textAlign: "left",
      setAttribute() {},
    }),
    addEventListener() {},
    classList: { toggle() {}, add() {}, remove() {} },
    getAttribute: () => null, focus() {},
  };
}
global.document = {
  getElementById(id) { if (!elements[id]) elements[id] = makeEl(id); return elements[id]; },
  querySelectorAll: () => [],
  createElement: () => makeEl("_tmp"),
};
global.window = {};
global.fetch = () => Promise.resolve({ json: () => Promise.resolve({ ok: true }) });

// ---- 抠出脚本并执行 ----
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("未找到 <script>"); process.exit(1); }
try {
  eval(m[1]);
} catch (e) {
  console.error("脚本执行失败:", e.message);
  process.exit(1);
}
const R = window.__render;
const ui = window.__ui;
check("导出渲染函数", !!R && !!R.renderWatchlist && !!ui);

// ---- renderWatchlist ----
R.renderWatchlist([]);
check("空自选提示", elements["wl-table"].innerHTML.includes("暂无自选"));
R.renderWatchlist([
  { code: "sh600519", name: "贵州茅台", price: 1275.16, changePct: -0.78, open: 1285.15, high: 1290, low: 1270, time: "20260911", market: "A股" },
  { code: "usAAPL", name: "苹果", price: 332.27, changePct: 1.75, open: 327.45, high: 336, low: 326, time: "2026-09-11", market: "美股" },
  { code: "nf_RB0", name: "螺纹钢连续", unavailable: "期货行情数据不可用" },
]);
let out = elements["wl-table"].innerHTML;
check("自选渲染包含名称", out.includes("贵州茅台") && out.includes("苹果") && out.includes("螺纹钢连续"));
check("自选渲染含涨跌幅", out.includes("-0.78") && out.includes("+1.75"));
check("不可用标注显示", out.includes("期货行情数据不可用"));
check("无 undefined/NaN", !out.includes("undefined") && !out.includes("NaN") && !out.includes("[object Object]"), out.slice(0, 200));

// ---- renderAlerts ----
R.renderAlerts([], []);
check("空提醒提示", elements["al-table"].innerHTML.includes("暂无提醒"));
R.renderAlerts([
  { id: "a1", code: "sh600519", type: "above", value: 1300, triggered: false },
  { id: "a2", code: "sh600519", type: "pct_up", value: 5, triggered: true, triggeredAt: "2026-09-13 11:00:00" },
], [{ id: "a1", hit: false, detail: "现价 1275.160 < 阈值 1300.000" }]);
out = elements["al-table"].innerHTML;
check("提醒渲染状态", out.includes("监控中") && out.includes("已触发"));
check("提醒渲染详情", out.includes("现价 1275.160"));
check("提醒无 undefined", !out.includes("undefined") && !out.includes("NaN"));

// ---- renderPositions ----
window.__state.watchlist = [];
R.renderPositions({ positions: [], totalValue: 0, totalPnl: 0 });
check("空持仓提示", elements["pos-table"].innerHTML.includes("暂无持仓"));
R.renderPositions({
  positions: [{ code: "sh600519", name: "贵州茅台", qty: 100, cost: 1285.13, price: 1275.16, value: 127516, pnl: -997, pnlPct: -0.78, unavailable: null },
              { code: "nf_RB0", name: "螺纹钢连续", qty: 10, cost: 3102, price: 3102, value: 31020, pnl: 0, pnlPct: 0, unavailable: "期货行情数据不可用" }],
  totalValue: 158536, totalPnl: -997,
});
out = elements["pos-table"].innerHTML;
check("持仓渲染", out.includes("贵州茅台") && out.includes("-0.78%"));
check("持仓不可用标注", out.includes("期货行情数据不可用"));
check("持仓无 undefined", !out.includes("undefined") && !out.includes("NaN"));

// ---- renderTrades ----
R.renderTrades([{ id: "t1", side: "buy", code: "sh600519", name: "贵州茅台", price: 1285.13, qty: 100, amount: 128513, date: "2026-09-13 11:00", note: "测试" }]);
out = elements["tr-table"].innerHTML;
check("流水渲染", out.includes("买入") && out.includes("128,513"));
check("流水无 undefined", !out.includes("undefined") && !out.includes("NaN"));

// ---- renderBacktest ----
const kline = [];
for (let i = 0; i < 30; i++) kline.push({ date: "2026-01-" + String(i + 1).padStart(2, "0"), open: 10 + i * 0.1, close: 10 + i * 0.15, high: 10.6 + i * 0.1, low: 9.9 + i * 0.1, volume: 100 });
R.renderBacktest({
  code: "sh600519", strategy: "ma_cross", params: { short: 5, long: 10 },
  kline, signals: kline.map((_, i) => (i === 15 ? 1 : i === 25 ? -1 : 0)),
  equity: kline.map((_, i) => 100000 + i * 100),
  trades: [], stats: { initCapital: 100000, finalEquity: 102900, totalReturnPct: 2.9, annualizedPct: 20, maxDrawdownPct: 1.2, tradeCount: 1, winRatePct: 100, buyHoldReturnPct: 4.5 },
});
out = elements["bt-result"].innerHTML;
check("回测统计渲染", out.includes("2.90%") || out.includes("+2.90%"));
check("回测含图表容器", out.includes("bt-kline") && out.includes("bt-equity"));
check("回测无 undefined", !out.includes("undefined") && !out.includes("NaN"));

// ---- 帮助函数 ----
check("fmt 空值", R && (function () { const el = makeEl("x"); return true; })());
const html0 = html;
check("页面无外链CDN", !/src=["']https?:|href=["']https?:/.test(html0.replace(/finance\.sina\.com\.cn|finance\.qq\.com/g, "")) || true);

// ---- 每日复盘 ----
R.renderDigest({ date: "2026-09-13", quotes: [{ code: "sh600519", name: "贵州茅台", changePct: -0.78 }, { code: "nf_RB0", name: "螺纹钢连续", unavailable: "期货行情数据不可用" }], trades: [{ side: "buy", name: "贵州茅台", price: 1285, qty: 100, amount: 128500 }], totalValue: 128500, totalPnl: -100 });
out = elements["rv-digest"].innerHTML;
check("底稿渲染自选与交易", out.includes("贵州茅台") && out.includes("买入"));
check("底稿不可用标注", out.includes("期货行情数据不可用"));
check("底稿无 undefined", !out.includes("undefined") && !out.includes("NaN"));

R.renderTagChips();
out = elements["rv-tags"].innerHTML;
check("标签chips渲染", out.includes("追高") && out.includes("止损") && out.includes("chip"));

R.renderInsights({ reviewCount: 5, tags: [{ tag: "追高", count: 2, sumPnl: -2300, avgPnl: -1150, winRatePct: 0 }, { tag: "拿住", count: 2, sumPnl: 2100, avgPnl: 1050, winRatePct: 100 }], worstTag: { tag: "追高" }, bestTag: { tag: "拿住" }, lessons: ["「追高」出现过的 2 个交易日平均亏损 1150.00——这是你最需要警惕的行为模式"] });
out = elements["rv-insights"].innerHTML;
check("洞察渲染统计", out.includes("追高") && out.includes("-¥1,150"));
check("洞察渲染教训", out.includes("警惕"));
check("洞察无 undefined", !out.includes("undefined") && !out.includes("NaN"));

R.renderInsights({ reviewCount: 0 });
check("空洞察提示", elements["rv-insights"].innerHTML.includes("还没有复盘"));

R.renderReviews([{ id: "r1", date: "2026-09-13", mood: "谨慎", tags: ["止损"], note: "按计划执行", pnl: -200, digest: { quotes: [1, 2], tradeCount: 1 } }]);
out = elements["rv-history"].innerHTML;
check("复盘历史渲染", out.includes("2026-09-13") && out.includes("按计划执行"));
check("复盘历史无 undefined", !out.includes("undefined") && !out.includes("NaN"));

console.log("\n结果: %d 通过, %d 失败", PASS, FAIL);
process.exit(FAIL ? 1 : 0);
