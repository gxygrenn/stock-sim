import json, time

now = time.time()
base = r'C:\Users\49928\.workbuddy\projects\c-Users-49928-WorkBuddy-2026-09-30-19-04-40\7efbaad5-794d-41eb-8681-e6d2b153efc0\tool-results'
files = {
    'us': 'call_00_2hFyFAuOAGeghx4FQCZF4980.txt',
    'cn_conservative': 'call_01_UNrBqVuYtY9o4inDnlux1637.txt',
    'cn_aggressive': 'call_02_ThCU6DjLfAo4Qb1uZcf50069.txt',
}
for acct, fn in files.items():
    with open(base + '\\' + fn, encoding='utf-8') as f:
        d = json.load(f)
    pr = d.get('pendingReview', [])
    reviewed = d.get('reviewed', {})
    print('====', acct, '====')
    print('pendingReview count:', len(pr))
    for c in pr:
        print(json.dumps(c, ensure_ascii=False))
    print('reviewed:', json.dumps(reviewed, ensure_ascii=False))
    print()
