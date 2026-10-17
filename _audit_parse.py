import json
from collections import Counter

fmap = {'us': '_audit_us.json', 'cn_conservative': '_audit_cnc.json', 'cn_aggressive': '_audit_cna.json'}

for acc, fn in fmap.items():
    d = json.load(open(fn, encoding='utf-8'))
    print('=' * 70)
    print('ACCOUNT', acc)
    print('cash=%.2f realized=%.2f fees=%.2f initCapital=%.0f' % (d['cash'], d['realized'], d['fees'], d['initCapital']))
    print('nextDate=%s idx=%.4f' % (d.get('nextDate'), d.get('idx')))

    h = d['holdings']
    print('--- HOLDINGS (shares>0) ---')
    tot_cost = tot_val = 0.0
    for x in h:
        if x.get('shares', 0) > 0:
            cost = x.get('cost', 0); price = x.get('price', 0); sh = x.get('shares', 0)
            pnl = (price - cost) * sh
            pnlpct = (price / cost - 1) * 100 if cost else 0
            tot_cost += cost * sh; tot_val += price * sh
            print('  %-10s %-8s sh=%7d cost=%.3f price=%.3f pnl=%+10.2f (%+.2f%%)' % (x['code'], x['name'], sh, cost, price, pnl, pnlpct))
    print('  TOT holdings cost=%.2f val=%.2f floatPnl=%+.2f' % (tot_cost, tot_val, tot_val - tot_cost))
    print('  totalAsset(approx)=%.2f (%.2f%%)' % (d['cash'] + tot_val, (d['cash'] + tot_val) / d['initCapital'] * 100 - 100))

    tr = d.get('trades', [])
    buys = [t for t in tr if t.get('side') == 'BUY' or t.get('action') == 'BUY']
    sells = [t for t in tr if t.get('side') == 'SELL' or t.get('action') == 'SELL']
    print('--- TRADES: total=%d buys=%d sells=%d ---' % (len(tr), len(buys), len(sells)))
    if tr:
        print('  trade keys sample:', list(tr[0].keys()))
    for t in sells:
        print('  SELL:', json.dumps(t, ensure_ascii=False))

    rl = d.get('reviewLog', [])
    vc = Counter(r.get('verdict') for r in rl)
    print('--- reviewLog: total=%d verdicts=%s ---' % (len(rl), dict(vc)))
    buys_rl = [r for r in rl if r.get('verdict') == 'BUY']
    sells_rl = [r for r in rl if r.get('verdict') == 'SELL']
    print('  BUY verdicts=%d  SELL verdicts=%d' % (len(buys_rl), len(sells_rl)))
    for r in buys_rl:
        keys = {k: r.get(k) for k in ('code', 'name', 'verdict', 'confidence', 'outcome', 'fillPrice', 'note', 'ts', 'date')}
        print('   BUY:', json.dumps(keys, ensure_ascii=False))
