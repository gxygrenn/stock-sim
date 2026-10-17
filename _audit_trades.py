import json

fmap = {'us': '_audit_us.json', 'cn_conservative': '_audit_cnc.json', 'cn_aggressive': '_audit_cna.json'}
for acc, fn in fmap.items():
    d = json.load(open(fn, encoding='utf-8'))
    tr = d.get('trades', [])
    print('=' * 60)
    print('ACCOUNT', acc, 'total trades', len(tr))
    for t in tr:
        side = t.get('side'); code = t.get('code'); name = t.get('name')
        price = t.get('price'); qty = t.get('qty'); amount = t.get('amount'); time = t.get('time'); reason = t.get('reason')
        print('  %s %s %-8s %-8s px=%s qty=%s amt=%s time=%s' % (side, code, name, '', price, qty, amount, time))
        if side == 'SELL':
            print('      REASON:', reason)
    # SELL in reviewLog
    rl = d.get('reviewLog', [])
    sells = [r for r in rl if r.get('verdict') == 'SELL']
    for r in sells:
        print('  REVIEW-SELL:', json.dumps({k: r.get(k) for k in ('code','name','confidence','outcome','fillPrice','note')}, ensure_ascii=False))
