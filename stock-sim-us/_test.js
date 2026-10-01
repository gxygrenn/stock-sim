// 前端渲染测试：用 DOM 桩件跑真实页面脚本，喂真实 API 样本，校验各面板渲染
// 样本由 _state_fixture.json 提供（curl /api/state 抓取），因此结果可复现
const fs = require('fs');

const src = fs.readFileSync('index.html', 'utf8');
let js = src.match(/<script>([\s\S]*?)<\/script>/)[1];
js = js.replace(/}\)\(\);\s*$/, [
  'globalThis.__ui={',
  '  setS:function(d){S=d;}, getS:function(){return S;},',
  '  render:render, renderPool:renderPool, renderCouncil:renderCouncil,',
  '  renderNews:renderNews, renderCards:renderCards, renderScan:renderScan,',
  '  renderIntl:renderIntl, renderSent:renderSent',
  '};',
  '})();',
].join('\n'));

const STATE = JSON.parse(fs.readFileSync('_state_fixture.json', 'utf8'));

const ctx = new Proxy({}, { get: () => () => {}, set: () => true });
const els = {};
function El(id) {
  return {
    id: id || '', textContent: '', className: '', value: '', innerHTML: '',
    style: {}, dataset: {}, clientWidth: 600, clientHeight: 220, width: 0, height: 0,
    _kids: [], _q: {},
    addEventListener() {}, appendChild(k) { this._kids.push(k); }, remove() {},
    click() {}, focus() {},
    querySelector(sel) { if (!this._q[sel]) this._q[sel] = El(sel); return this._q[sel]; },
    getContext() { return ctx; }, closest() { return null; },
    classList: { add() {}, remove() {} },
  };
}
global.document = {
  getElementById(id) { if (!els[id]) els[id] = El(id); return els[id]; },
  createElement() { return El(); },
  body: { appendChild() {}, insertAdjacentHTML() {} },
  head: { appendChild() {} },
  addEventListener() {}, activeElement: null, hidden: false,
};
global.window = { addEventListener() {}, devicePixelRatio: 1 };
global.setInterval = () => 0;
global.clearInterval = () => {};
global.setTimeout = () => 0;
global.confirm = () => true;
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
global.fetch = function (p) {
  return Promise.resolve({
    json: function () {
      return Promise.resolve(p === '/api/state' ? STATE : { ok: true });
    },
  });
};

(0, eval)(js);
const U = globalThis.__ui;

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name + (extra ? '  ' + extra : '')); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '  ' + extra : '')); }
}

U.setS(STATE);
U.render();

console.log('\n[1] 脚本可执行');
ok('暴露渲染入口', !!(U && U.render));
ok('状态已注入', U.getS() === STATE);

console.log('\n[2] 账户总览');
ok('总资产已渲染', els.cTotal.textContent.length > 0, els.cTotal.textContent);
ok('现金已渲染', els.cCash.textContent.length > 0, els.cCash.textContent);

console.log('\n[3] 自选池 + 信号列 + 评议团徽章');
const poolRows = (els.tblPool._q.tbody._kids || []).map(k => k.innerHTML);
ok('池子行数与数据一致', poolRows.length === STATE.pool.length,
  poolRows.length + '/' + STATE.pool.length);
ok('每行含信号分', poolRows.every(html => html.includes('badge') || html.includes('-')),
  poolRows[0] ? poolRows[0].slice(0, 120) : '(空)');
const withVerdict = poolRows.filter(h => /买入|卖出|观望/.test(h));
ok('池子行带评议团倾向', withVerdict.length > 0, withVerdict.length + ' 行');

console.log('\n[4] 专家评议团面板');
const cb = els.councilBox.innerHTML;
const items = cb.split("class='citem'").length - 1;
ok('渲染条数与记录数一致', items === STATE.council.length,
  items + '/' + STATE.council.length);
ok('含四位分析师', ['技术分析师', '资金面分析师', '消息面分析师', '估值分析师']
  .every(r => cb.includes(r)));
ok('含风控官', cb.includes('风控官'));
ok('含决议徽章', /买入|卖出|观望|否决/.test(cb));
ok('含票数统计', /(\d+:\d+ 通过|同意不足 \d+:\d+|风控否决 \d+:\d+)/.test(cb),
  (cb.match(/(\d+:\d+ 通过|同意不足 \d+:\d+|风控否决 \d+:\d+)/) || ['(未匹配到票数样式)'])[0]);
ok('含加权分', cb.includes('加权分'));
ok('会次标记已展示', String(els.councilTag.textContent).includes('条合议记录'),
  els.councilTag.textContent);

console.log('\n[4b] 研判会标记与议案表述');
const advRows = (STATE.council || []).filter(r => r.advisory);
ok('研判记录带「研判」标记', advRows.length === 0 || cb.includes('研判'),
  advRows.length + ' 条研判记录');
const noProp = (STATE.council || []).filter(r => !r.proposal);
ok('未形成议案时不显示票数', noProp.length === 0 || cb.includes('无议案'),
  noProp.length + ' 条无议案');
const shortVote = (STATE.council || []).filter(r => r.proposal && r.verdict === '观望');
ok('有议案但票数不足显示「同意不足」', shortVote.length === 0 || cb.includes('同意不足'),
  shortVote.length + ' 条票数不足');

console.log('\n[5] 消息与流水');
ok('新闻列表已渲染', (els.newsBox.innerHTML || '').length > 100);
ok('成交流水已渲染', (els.trCount.textContent || '').length > 0, els.trCount.textContent);
ok('扫描状态已渲染', (els.scanStatus.textContent || '').length > 0,
  els.scanStatus.textContent);

console.log('\n[6] 渲染健康度（字段错配会露出 undefined/NaN）');
const all = Object.keys(els).map(k => (els[k].innerHTML || '') + (els[k].textContent || '')).join('');
ok('无 undefined', !all.includes('undefined'));
ok('无 NaN', !all.includes('NaN'));
ok('无 [object Object]', !all.includes('[object Object]'));

console.log('\n[7] 投喂演练入口');
// 输入框只在点击处理函数里被访问，脚本加载时不会创建桩件，所以直接查页面源码
ok('投喂输入框已挂载', src.includes('id="injText"'));
ok('投喂按钮已绑定事件', typeof els.btnInject.onclick === 'function');

console.log('\n[8] 自动交易开关即时保存');
ok('开关已绑定 onchange', typeof els.cfgAuto.onchange === 'function');
ok('轮询不覆盖焦点控件', src.includes('document.activeElement'),
  'renderScan 焦点守卫');
// 记录 fetch 调用后模拟拨动开关：应立即向 /api/config 写库（fetch 在 api() 内同步触发）
const calls = [];
const origFetch = global.fetch;
global.fetch = function (p, opt) {
  calls.push(p + ' ' + ((opt && opt.body) || ''));
  return origFetch(p, opt);
};
els.cfgAuto.checked = true;
els.cfgAuto.onchange();
global.fetch = origFetch;
ok('拨动开关立即写库',
  calls.some(c => c.indexOf('/api/config') >= 0 && c.indexOf('autoTrade') >= 0),
  calls.join(' | ') || '无调用');

console.log('\n[9] 每日复盘面板');
ok('复盘面板已挂载', src.includes('id="dailyReviewBox"'));
ok('无复盘数据时显示空态', (els.dailyReviewBox.innerHTML || '').indexOf('还没有复盘记录') >= 0,
  (els.dailyReviewBox.innerHTML || '').slice(0, 40));
// 注入一条复盘后应渲染出摘要、要点与经验
const S2 = JSON.parse(JSON.stringify(STATE));
S2.dailyReviews = [{
  date: '2026-09-14', time: '2026-09-14 15:45:00', summary: '当日 2 笔成交，收盘合计 +0.6%',
  bullets: ['买入五粮液 200 股，收盘 +1.2%', '卖出东方财富 300 股，规避 -0.8% 回落'],
  lessons: ['利好快讯需资金面配合，否则次日回落概率高'], score: 1, account: 100123.45,
}];
U.setS(S2);
U.render();
const dr = els.dailyReviewBox.innerHTML || '';
ok('复盘日期已渲染', dr.indexOf('2026-09-14') >= 0);
ok('复盘要点已渲染', dr.indexOf('收盘 +1.2%') >= 0);
ok('经验区已渲染', dr.indexOf('利好快讯需资金面配合') >= 0);
ok('复盘标签显示天数', els.drTag.textContent.indexOf('天') >= 0, els.drTag.textContent);

console.log('\n[10] 市场环境：国际因素 + 市场情绪');
ok('两块面板已挂载', src.includes('id="intlBox"') && src.includes('id="sentBox"'));
ok('无播报时国际因素显示空态', (els.intlBox.innerHTML || '').indexOf('暂无数据') >= 0,
  (els.intlBox.innerHTML || '').slice(0, 40));
ok('无播报时情绪显示空态', (els.sentBox.innerHTML || '').indexOf('暂无数据') >= 0,
  (els.sentBox.innerHTML || '').slice(0, 40));
const S3 = JSON.parse(JSON.stringify(STATE));
S3.marketContext = {
  date: '2026-09-15', time: '2026-09-15 15:35:02',
  intl: { impact: '偏空', strength: 2, summary: '隔夜美股走弱，今日A股承压。',
          items: [{ name: '道琼斯', value: '-1.2%', note: '通胀超预期' },
                   { name: '美元/离岸人民币', value: '7.12', note: '' }] },
  sentiment: { score: -1, label: '偏谨慎', summary: '跌多涨少，量能萎缩。',
               items: [{ name: '涨跌家数', value: '1900:3200', note: '' },
                        { name: '两市成交额', value: '2.1万亿', note: '较昨-5%' }] },
};
U.setS(S3);
U.renderIntl();
U.renderSent();
ok('国际因素渲染影响徽章', els.intlBox.innerHTML.indexOf('对今日大盘 偏空') >= 0,
  (els.intlBox.innerHTML.match(/对今日大盘 [^<]+/) || ['(未匹配)'])[0]);
ok('国际因素渲染行情条目',
  els.intlBox.innerHTML.indexOf('道琼斯') >= 0 && els.intlBox.innerHTML.indexOf('-1.2%') >= 0);
ok('国际因素渲染摘要', els.intlBox.innerHTML.indexOf('隔夜美股走弱') >= 0);
ok('国际因素日期标签', (els.mcIntlTag.textContent || '') === '09-15', els.mcIntlTag.textContent);
ok('情绪渲染打分徽章', els.sentBox.innerHTML.indexOf('情绪 -1') >= 0,
  (els.sentBox.innerHTML.match(/情绪 [^<]+/) || ['(未匹配)'])[0]);
ok('情绪渲染标签', els.sentBox.innerHTML.indexOf('偏谨慎') >= 0);
ok('情绪标签写入tag', (els.mcSentTag.textContent || '') === '偏谨慎', els.mcSentTag.textContent);
ok('情绪渲染量能条目', els.sentBox.innerHTML.indexOf('两市成交额') >= 0);
const all3 = [els.intlBox, els.sentBox].map(e => (e.innerHTML || '') + (e.textContent || '')).join('');
ok('播报区无 undefined/NaN', !all3.includes('undefined') && !all3.includes('NaN'));
U.setS(STATE);
U.render();

console.log('\n[10] 待评审队列：等待时长与批准状态');
ok('队列容器已挂载', src.includes('id="reviewQueue"'));
const SQ = JSON.parse(JSON.stringify(STATE));
SQ.pendingReview = [
  { code: 'sz002230', name: '科大讯飞', direction: '买入', score: 7, delta: -1,
    reason: '快讯: 机器人合作', time: '2026-09-15 10:14:41',
    queuedAt: '2026-09-15 10:14:41', px: 48.2, state: 'waiting', waitH: 101.2 },
  { code: 'sh688981', name: '中芯国际', direction: '买入', score: 8, delta: -1,
    reason: '快讯: 存储芯片反弹', time: '2026-09-19 19:00:00',
    queuedAt: '2026-09-19 19:00:00', px: 88.1, state: 'approved', waitH: 1.4,
    review: { verdict: 'BUY', confidence: 7 } },
];
U.setS(SQ);
U.render();
const rq = els.reviewQueue.innerHTML || '';
ok('队列标题显示候选数', rq.indexOf('待专家团评审') >= 0 && rq.indexOf('2') >= 0, rq.slice(0, 90));
ok('显示已等待小时数', rq.indexOf('已等 101.2h') >= 0, rq.slice(0, 220));
ok('已批准候选带待成交标记', rq.indexOf('已批准·待成交') >= 0);
ok('队列说明 TTL 与成交口径', rq.indexOf('24 小时') >= 0 && rq.indexOf('按最新价成交') >= 0);
SQ.pendingReview = [];
U.setS(SQ);
U.render();
ok('队列为空时不渲染', (els.reviewQueue.innerHTML || '') === '');

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
