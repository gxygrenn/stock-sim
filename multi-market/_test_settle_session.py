# -*- coding: utf-8 -*-
"""验证 2026-10-28 两项服务端修正（纯逻辑，不碰真实状态、不发网络请求）：

1) 结算标签对齐：settle() 用「最近一个已收盘交易日」做标签，不再用滞后的 nextDate；
   同一交易日只写一条（幂等）。
2) 批单保质期计时起点顺延：approved_age_hours() 从「下一个交易时段开盘」起算，
   周末/深夜批的单不在无成交机会的时段空转过期。
"""
import sys
import datetime
sys.path.insert(0, '.')

src = open('server.py', encoding='utf-8').read()
ns = {'__name__': 'server_under_test', '__file__': 'server.py'}
import server
ns = server.__dict__

settle = ns['settle']
_closed_session_label = ns['_closed_session_label']
_next_session_open = ns['_next_session_open']
next_trading_day = ns['next_trading_day']

# 动态取「今天真实交易日标签」，避免把写测试当天的日期硬编码（否则隔天必烂）
TODAY_LABEL = _closed_session_label('cn', datetime.datetime.now())

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS  %s" % name)
    else:
        fail += 1
        print("FAIL  %s  %s" % (name, detail))


def mk_state(next_date="2026-10-20", history=None):
    return {
        "config": dict(ns['CN_AGGRESSIVE_CONFIG']),
        "cash": 100000.0, "fees": 0.0, "realized": 0.0, "seq": 1,
        "pool": [], "holdings": [], "trades": [], "signals": {},
        "idx": 100.0, "initCapital": 100000.0,
        "history": history if history is not None else [],
        "nextDate": next_date, "priceMode": "mock",
    }


# 固定成 CN 账户（settle 里靠 current_account() 判断市场）
ns['current_account'] = lambda: 'cn_aggressive'

# ============ 一、结算标签对齐 ============
label = _closed_session_label('cn', datetime.datetime(2026, 10, 28, 11, 45))
check("CN 周三盘中 -> 标签为当日", label == "2026-10-28", label)

label = _closed_session_label('cn', datetime.datetime(2026, 10, 25, 10, 0))
check("CN 周日 -> 标签回退到周五", label == "2026-10-23", label)

label = _closed_session_label('cn', datetime.datetime(2026, 10, 2, 10, 0))
check("CN 国庆假期内 -> 跳过假日回退", label == "2026-09-30", label)

# 美股：结算标签必须对应「已经收盘」的那个美股交易日（北京时间比美东快 12h）
label = _closed_session_label('us', datetime.datetime(2026, 10, 22, 4, 10))
check("US 北京周四04:10(美东周三16:10) -> 标签周三",
      label == "2026-10-21", label)
label = _closed_session_label('us', datetime.datetime(2026, 10, 22, 10, 0))
check("US 北京周四白天(美东周三夜) -> 标签周三",
      label == "2026-10-21", label)
label = _closed_session_label('us', datetime.datetime(2026, 10, 21, 21, 30))
check("US 北京周三21:30刚开盘(美东周三09:30) -> 标签退回周二",
      label == "2026-10-20", label)
label = _closed_session_label('us', datetime.datetime(2026, 10, 24, 3, 0))
check("US 北京周六03:00(美东周五15:00未收盘) -> 标签周四",
      label == "2026-10-22", label)
label = _closed_session_label('us', datetime.datetime(2026, 10, 24, 5, 0))
check("US 北京周六05:00(美东周五17:00已收盘) -> 标签周五",
      label == "2026-10-23", label)

# 1a. nextDate 落后 8 天，仍应按真实交易日打标签，而不是补写旧日期
s = mk_state(next_date="2026-10-20")
lab = settle(s, 0.5)
check("滞后账户按真实交易日打标签", lab == TODAY_LABEL and s["history"][-1]["date"] == TODAY_LABEL,
      "lab=%s row=%s" % (lab, s["history"][-1]["date"] if s["history"] else None))
check("结算后 nextDate 推进到下一交易日",
      s["nextDate"] == next_trading_day(TODAY_LABEL), s["nextDate"])

# 1b. 幂等：同一天连续结算两次，只允许一条 history
s2 = mk_state(next_date="2026-10-20")
settle(s2, 0.5)
n_before = len(s2["history"])
lab2 = settle(s2, 0.8)
check("同一交易日重复结算被幂等跳过",
      lab2 is None and len(s2["history"]) == n_before,
      "lab2=%s len=%d" % (lab2, len(s2["history"])))

# 1c. 已有今日行时，不再追加（防「同一天结算两次」老 bug 复发）
s3 = mk_state(history=[{"date": TODAY_LABEL, "mkt": 0.1, "total": 100000.0,
                        "dayPnl": 0.0, "ret": 0.0, "mret": 0.0}])
lab3 = settle(s3, 0.5)
check("history 已有当日行 -> 不追加", lab3 is None and len(s3["history"]) == 1, lab3)

# 1d. 显式指定日期（手动 /api/settle）保持旧行为，不被幂等拦截
s4 = mk_state(history=[{"date": TODAY_LABEL, "mkt": 0.1, "total": 100000.0,
                        "dayPnl": 0.0, "ret": 0.0, "mret": 0.0}])
lab4 = settle(s4, 0.2, date_str="2026-10-27")
check("显式日期仍可写入（手动结算不被拦截）",
      lab4 == "2026-10-27" and len(s4["history"]) == 2, lab4)

# ============ 二、保质期计时起点顺延 ============
# 2a. 周六 10:00 批的单 -> 起点顺延到周一 09:30，而不是周六 10:00
sat = datetime.datetime(2026, 10, 24, 10, 0)
start = _next_session_open(sat, 'cn')
check("CN 周六批单 -> 起点为周一开盘",
      start == datetime.datetime(2026, 10, 26, 9, 30), start)

# 2b. 周五收盘后 20:00 批的单 -> 起点同为周一开盘
fri_night = datetime.datetime(2026, 10, 23, 20, 0)
start = _next_session_open(fri_night, 'cn')
check("CN 周五夜间批单 -> 起点为周一开盘",
      start == datetime.datetime(2026, 10, 26, 9, 30), start)

# 2c. 盘中批的单：起点保持原时刻（隔夜陈旧结论照样会被作废）
intraday = datetime.datetime(2026, 10, 28, 10, 15)
check("CN 盘中批单 -> 起点不变", _next_session_open(intraday, 'cn') == intraday)

# 2d. 午间休市 12:00 -> 顺延到 13:00
lunch = datetime.datetime(2026, 10, 28, 12, 0)
check("CN 午休批单 -> 顺延到 13:00",
      _next_session_open(lunch, 'cn') == datetime.datetime(2026, 10, 28, 13, 0))

# 2e. 收盘后 15:30 -> 顺延到次一交易日 09:30
after = datetime.datetime(2026, 10, 28, 15, 30)
check("CN 收盘后批单 -> 顺延到次日 09:30",
      _next_session_open(after, 'cn') == datetime.datetime(2026, 10, 29, 9, 30))

# 2f. 美股：北京周六凌晨 03:00 属美股周五盘中 -> 起点不变
us_tail = datetime.datetime(2026, 10, 24, 3, 0)
check("US 周六凌晨(美股周五盘中) -> 起点不变",
      _next_session_open(us_tail, 'us') == us_tail, _next_session_open(us_tail, 'us'))

# 2g. 美股：北京白天 10:00 -> 顺延到当日 21:30（夏令时）
us_day = datetime.datetime(2026, 10, 28, 10, 0)
check("US 北京白天 -> 顺延到当日 21:30",
      _next_session_open(us_day, 'us') == datetime.datetime(2026, 10, 28, 21, 30),
      _next_session_open(us_day, 'us'))

print("\n结果：%d 通过, %d 失败" % (ok, fail))
sys.exit(1 if fail else 0)
