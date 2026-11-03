# -*- coding: utf-8 -*-
import sys, _intraday as I
acc = sys.argv[1]
st, s = I.call("/api/state?account=%s" % acc)
hold = [h for h in (s.get("holdings") or []) if (h.get("shares") or 0) > 0]
for h in sorted(hold, key=lambda x: ((x.get("price") or 0)/(x.get("cost") or 1)-1)):
    cum = ((h.get("price") or 0)/(h.get("cost") or 1)-1)*100
    print("%-9s %-8s sh=%-4s cost=%-9.2f px=%-9.2f 累%+.2f%% 日%+.2f%%" % (h.get("code"),(h.get("name") or "")[:8],h.get("shares"),h.get("cost") or 0,h.get("price") or 0,cum,h.get("pct") or 0))
