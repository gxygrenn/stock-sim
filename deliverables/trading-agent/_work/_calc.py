# -*- coding: utf-8 -*-
"""基于 cmb_raw.csv 计算技术指标"""
import csv, os, json

BASE = os.path.dirname(os.path.abspath(__file__))
rows = []
with open(os.path.join(BASE, "cmb_raw.csv"), encoding="utf-8") as f:
    for r in csv.DictReader(f):
        rows.append({k: (v if k in ("date", "turnover") else float(v)) for k, v in r.items()})
rows.sort(key=lambda x: x["date"])
C = [r["close"] for r in rows]
H = [r["high"] for r in rows]
L = [r["low"] for r in rows]
O = [r["open"] for r in rows]
V = [r["volume"] for r in rows]
D = [r["date"] for r in rows]
n = len(C)

def sma(x, p):
    if len(x) < p: return None
    return sum(x[-p:]) / p

def sma_series(x, p):
    return [None]*(p-1) + [sum(x[i-p+1:i+1])/p for i in range(p-1, len(x))]

def ema_series(x, p):
    a = 2/(p+1)
    out = [x[0]]
    for i in range(1, len(x)):
        out.append(x[i]*a + out[-1]*(1-a))
    return out

def ema(x, p):
    return ema_series(x, p)[-1]

e12 = ema_series(C, 12); e26 = ema_series(C, 26)
dif = [a-b for a, b in zip(e12, e26)]
dea = ema_series(dif, 9)
hist = [a-b for a, b in zip(dif, dea)]

# RSI14 Wilder
def rsi_wilder(x, p=14):
    gains, losses = [], []
    for i in range(1, len(x)):
        ch = x[i]-x[i-1]
        gains.append(max(ch, 0)); losses.append(max(-ch, 0))
    ag = sum(gains[:p])/p; al = sum(losses[:p])/p
    out = []
    def val(g, l):
        return 100.0 if l == 0 else 100 - 100/(1+g/l)
    out.append(val(ag, al))
    for i in range(p, len(gains)):
        ag = (ag*(p-1)+gains[i])/p
        al = (al*(p-1)+losses[i])/p
        out.append(val(ag, al))
    return out  # len = len(x)-p

rsi = rsi_wilder(C, 14)

# Bollinger 20
sma20s = sma_series(C, 20)
import statistics
upper, lower = [], []
for i in range(19, len(C)):
    w = C[i-19:i+1]
    m = sum(w)/20
    sd = statistics.pstdev(w)
    upper.append(m+2*sd); lower.append(m-2*sd)

# ATR14 Wilder
tr = [H[0]-L[0]]
for i in range(1, n):
    tr.append(max(H[i]-L[i], abs(H[i]-C[i-1]), abs(L[i]-C[i-1])))
atr = [sum(tr[:14])/14]
for i in range(14, n):
    atr.append((atr[-1]*13+tr[i])/14)

def vwma(p):
    num = sum(C[i]*V[i] for i in range(n-p, n)); den = sum(V[i] for i in range(n-p, n))
    return num/den

res = {
    "last_date": D[-1], "last_close": C[-1],
    "SMA5": sma(C, 5), "SMA10": sma(C, 10), "SMA20": sma(C, 20), "SMA50": sma(C, 50),
    "EMA10": ema(C, 10), "EMA12": e12[-1], "EMA26": e26[-1],
    "MACD_DIF": dif[-1], "MACD_DEA": dea[-1], "MACD_HIST": hist[-1],
    "DIF_prev": dif[-2], "DEA_prev": dea[-2], "HIST_prev": hist[-2],
    "RSI14": rsi[-1], "RSI14_prev": rsi[-2], "RSI14_5ago": rsi[-6] if len(rsi) >= 6 else None,
    "BB_UP": upper[-1], "BB_MID": sma20s[-1], "BB_LOW": lower[-1],
    "BB_UP_prev": upper[-2], "BB_LOW_prev": lower[-2],
    "ATR14": atr[-1], "ATR14_pct": atr[-1]/C[-1]*100,
    "VWMA20": vwma(20),
    "period_high": max(H), "period_low": min(L),
    "high_date": D[H.index(max(H))], "low_date": D[L.index(min(L))],
    "last20_high": max(H[-20:]), "last20_low": min(L[-20:]),
    "vol5": sum(V[-5:])/5, "vol20": sum(V[-20:])/20,
}
print(json.dumps(res, ensure_ascii=False, indent=2))
print("--- close series (last 60) ---")
print(",".join(f"{d},{c}" for d, c in list(zip(D, C))[-60:]))
print("SMA50_series_last:", sma_series(C, 50)[-5:])
print("DIF last6:", [round(x,4) for x in dif[-6:]])
print("DEA last6:", [round(x,4) for x in dea[-6:]])
print("HIST last6:", [round(x,4) for x in hist[-6:]])
print("RSI last6:", [round(x,2) for x in rsi[-6:]])
