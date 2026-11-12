import json, urllib.request, urllib.parse

BASE = "https://stock-sim-us.app.workbuddy.host/api/review_done"
SHARECODE = "6R7GBTHXQI"  # 2026-11-12 起平台网关要求所有请求必须带 sharecode，否则 403

# (account, code, verdict, confidence, note)
candidates = [
    ("us", "usMSFT", "HOLD", 60, "主审HOLD：council否决『持仓只数已达上限8』（账户已持8只达上限，veto与事实相符），服从仓位否决。复核无致命风险。delta2.9临界但仓位否决约束力优先。"),
    ("us", "usPLTR", "HOLD", 60, "主审HOLD：council否决『单票仓位已达20%上限』（已持126股仓位满），服从集中度否决。复核无致命风险。"),
    ("us", "usV", "HOLD", 55, "主审HOLD：delta2.4<2.5，仅多头排列+买压偏强，无20日新高/放量/阳线突破等强特征，未达强势门槛；council观望无否决。复核无致命风险，一致HOLD。"),
    ("us", "usMA", "HOLD", 60, "主审HOLD：council否决『持仓只数已达上限8』（账户已持8只达上限，veto与事实相符），服从仓位否决。复核无致命风险。"),
    ("us", "usPFE", "HOLD", 60, "主审HOLD：council否决『持仓只数已达上限8』（账户已持8只达上限，veto与事实相符），服从仓位否决。复核无致命风险。"),
    ("us", "usABBV", "HOLD", 60, "主审HOLD：council否决『持仓只数已达上限8』（账户已持8只达上限，veto与事实相符），服从仓位否决。复核无致命风险。"),
    ("cn_aggressive", "sh601728", "HOLD", 60, "主审HOLD：council否决『总仓位超98%（防守档收紧），保留弹药』（账户现金仅168元总仓位近100%，veto与事实相符），服从仓位否决。复核无致命风险。"),
    ("cn_aggressive", "sz000002", "HOLD", 60, "主审HOLD：council否决『总仓位超98%（防守档收紧），保留弹药』（账户现金仅168元总仓位近100%，veto与事实相符），服从仓位否决。复核无致命风险。"),
    ("cn_aggressive", "sz002640", "HOLD", 60, "主审HOLD：council否决『总仓位超98%（防守档收紧），保留弹药』（账户现金仅168元总仓位近100%，veto与事实相符），服从仓位否决。复核无致命风险。"),
]

for acct, code, verdict, conf, note in candidates:
    url = BASE + "?account=" + acct + "&sharecode=" + SHARECODE
    body = json.dumps({"code": code, "verdict": verdict, "confidence": conf, "note": note}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = r.read().decode("utf-8")
        print(f"[OK] {acct} {code} -> {verdict} conf{conf} | {resp}")
    except Exception as e:
        print(f"[ERR] {acct} {code} -> {e}")
