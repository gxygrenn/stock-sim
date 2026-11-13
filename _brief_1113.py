import json, urllib.request
BASE="https://stock-sim-us.app.workbuddy.host"
SC="6R7GBTHXQI"
def get(p):
    u=BASE+p+("&" if "?" in p else "?")+"sharecode="+SC
    req=urllib.request.Request(u, headers={"User-Agent":"Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))

for acc in ["cn_conservative","cn_aggressive"]:
    s=get("/api/state?account="+acc)
    if "state" in s and isinstance(s["state"],dict): s=s["state"]
    print("="*8, acc)
    tot=0; rows=[]
    for h in s.get("holdings",[]):
        if h.get("shares",0)<=0: continue
        p=h["price"]; c=h["cost"]; sh=h["shares"]
        mv=p*sh; tot+=mv
        pnl=(p/c-1)*100 if c else 0
        rows.append((pnl,h["code"],h.get("name"),p,round(h.get("pct",0),2),sh,round(pnl,2),h.get("peak")))
    rows.sort()
    print("持仓(按累盈排序) 只数=",len(rows))
    for pnl,code,name,p,pct,sh,pn,peak in rows:
        extra=""
        if peak: extra=" peakPnl=%.2f%% trail=%.2fpp"%( (peak/ [r for r in [1]][0] -1)*100 if False else 0,0)
        print("  %-10s %-8s px=%-8.3f 日%+6.2f%% 累%+7.2f%% sh=%-6d peak=%s"%(code,name,p,pct,pn,sh,peak))
    cash=s.get("cash"); print("cash=",cash,"持仓市值=",round(tot,2))
    lt=s.get("lastTrade") or {}
    print("lastTrade:", {k:v for k,v in lt.items() if v})
    print("config:", {k:s.get("config",{}).get(k) for k in ["maxHold","takeProfitPct","trailPct","maxPositionPct"]})
