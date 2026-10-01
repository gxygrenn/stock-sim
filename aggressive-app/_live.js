// 端到端验证：真实 HTTP 请求腾讯行情 -> GBK 解码 -> 解析（模拟浏览器里的完整链路）
const fs = require('fs');
const src = fs.readFileSync('index.html', 'utf8');
let js = src.match(/<script>([\s\S]*?)<\/script>/)[1];
js = js.replace(/\}\)\(\);\s*$/,
  'globalThis.__api={parseQuote:parseQuote,decodeGb:decodeGb,quoteUrl:quoteUrl,LIVE_IDX:LIVE_IDX};})();');

const ctx = new Proxy({}, { get: () => () => {}, set: () => true });
function El() {
  return { textContent: '', className: '', value: '', innerHTML: '', style: {}, dataset: {},
    clientWidth: 600, clientHeight: 220, width: 0, height: 0,
    addEventListener() {}, appendChild() {}, remove() {}, click() {}, focus() {},
    querySelector: () => El(), getContext: () => ctx, closest: () => null,
    classList: { add() {}, remove() {} } };
}
global.document = { getElementById: () => El(), createElement: () => El(),
  body: { appendChild() {} }, head: { appendChild() {} },
  addEventListener() {}, activeElement: null, hidden: false };
global.window = { addEventListener() {}, devicePixelRatio: 1 };
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
global.confirm = () => true; global.alert = () => {};

(0, eval)(js);
const A = globalThis.__api;

(async () => {
  let ok = 0, bad = 0;
  for (const item of A.LIVE_IDX) {
    const url = A.quoteUrl(item.code);
    try {
      const r = await fetch(url, {
        cache: 'no-store',
        headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
      });
      const acao = r.headers.get('access-control-allow-origin');
      const buf = await r.arrayBuffer();
      const txt = A.decodeGb(buf);
      const q = A.parseQuote(txt);
      const nameOk = q.name && q.name.length > 1;
      if (r.ok && acao === '*' && isFinite(q.price) && q.price > 0 && nameOk) ok++;
      else { bad++; }
      console.log(
        (r.ok ? '[OK ]' : '[ERR]') + ' ' + item.code + ' ' + item.name +
        ' | HTTP ' + r.status + ' | ACAO: ' + acao +
        ' | 解析名: ' + q.name + ' | 点位 ' + q.price +
        ' | ' + (q.pct >= 0 ? '+' : '') + q.pct + '%' +
        ' | 成交额 ' + (q.amount / 1e8).toFixed(2) + '亿' +
        ' | ' + q.time
      );
    } catch (e) {
      bad++;
      console.log('[ERR] ' + item.code + ' ' + item.name + ' -> ' + e.message);
    }
  }
  console.log('\n可正常获取: ' + ok + ' / ' + A.LIVE_IDX.length + '，失败 ' + bad);
  process.exit(bad ? 1 : 0);
})();
