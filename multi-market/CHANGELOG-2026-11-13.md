# 服务端修正记录 · 2026-11-13（待部署）

改动文件：`multi-market/server.py`（换仓闸门 + 清仓口径，三处逻辑 + 三份 config 模板）
新增测试：`_test_swap_gate.py`（21 条断言，全绿）
回归：`_test_gate` 38/38、`_test_stops` 23/23、`_test_room_veto` 6/6、`_test_ttl_sameday` 11/11、
`_test_rebalance` 18/18、`_test_settle_session` 20/20、`_test_rereview_worsen` 11/11、
`_test_catchup` 5/5、`_test_council_enqueue` 14/14 全绿。

> **状态：代码已改完并通过测试，尚未部署。** 与 11-10 的 b2d56d3（roomVeto + tpOncePerDay）
> 同在待发布队列，可一次发布。用户 11-13 晚已对「换仓闸门 + 置换 SELL 清仓」拍板
> （「好 直接改吧」），本条不再是「待人工确认」项；上线仍需明确说发布。

---

## 1. 满席换仓闸门（swapGate）——T14/T10/T28 第 21 日的死锁出口

**证据**：cnc 东方环宇 delta5.9、6 项分量命中 4~6 项，11-10 @21.79 / 11-11 @24.42 /
11-13 @27.99 连续 4 个交易日因「持仓只数已达上限8」HOLD，累计 +28.5%；账户现金 25.2%
（23,488 元）与满 8 席并存——钱有、分没有。us 同病（8/8 满席，2,724 元只能加仓
delta2.9 的旧票）。

**改法**（三份 config 模板新增 `"swapGate": True`，2026-11-13 用户拍板开启）：
- `_risk_check` 买入分支：满席且候选为新票时，若队列里已有**已批准且将清仓**的 SELL
  （`_approved_clear_sell_pending`），不再一票否决，按「先卖后买」放行。
- 执行侧不变式保留：`execute_approved` 的 BUY 分支仍硬性要求「成交时刻持仓只数 <
  maxHold」，席位没真正腾出来就绝不超买；配对 SELL 未成交时 BUY 留队下轮再试。
- 老存档兼容：`swapGate` 是新键，存档 config 缺该键时 `load_state` 会从模板补上
  （与 reReviewWorsenPp 同路径），部署后即生效；回退方式 `/api/config` 传 `false`。

**清仓口径（闸门只认「将清仓」的 SELL）**：forced=true 的浮亏强制复核终审 SELL，或
note 带 `【R15清仓腾位` 标记的置换 SELL（评审端 R15 于 11-13 早间落地，与本条同日）。
普通 sellPct 部分卖出不释放席位，不触发放行。

## 2. 执行顺序改为「先卖后买」

`execute_approved` 原按队列先后执行，批准的 BUY 排在 SELL 前时，卖出席位要等下一轮
才能被使用。改为单次调用内先执行全部 approved SELL、再执行 approved BUY（稳定排序，
非 SELL 项相对顺序不变）。这是 swapGate 能同轮配对的前提，也是独立收益：止盈腾位当
轮即可回补（11-05 us「Visa 止盈清仓→40 分钟后被万事达占用」的换股形态，至少不再多等
一轮）。

## 3. 置换型 SELL 清仓口径（R15 服务端落地）

**证据**：cnc 三一重工 11-13 13:15 置换 SELL 仅成交 100/326 股（`sellPct=50` 折半后
`//100*100` 归零、兜底 100 股），余 226 股仍 -6.24%、席位未释放，同日 14:32 东方环宇
（涨 4.06%）再被满席否决——「已批 SELL」被当成「名额已解决」。cna 大洋电机两轮
强制复核共卖 700 股，余 244 股仍 -12.77%，同源。

**改法**：`execute_approved` SELL 分支——forced 终审 SELL 与 note 带 `【R15清仓腾位` 的
SELL 一律卖全量（`qty = h["shares"]`）；普通 LLM SELL 仍按 `sellPct` 部分卖出。
防误判：note 里只出现「清仓」字样而无 R15 标记（如「建议下轮出清仓单」）不触发全量
（有测试覆盖）。`sellPct`、`minSellAmount` 等参数本身未动。

## 与评审端（盘中引擎 prompt）的配合

11-13 早间复盘已落地 R15：置换型 SELL 必须写「清仓 XXX N 股（腾出 1 席）」+ 
`【R15清仓腾位:...】`，或「部分卖出、余 N 股、席位未释放」+ `【R15席位未释放:...】`。
服务端本次改动让这两类标记有了执行端语义：清仓标记 → 卖全量；满席下达标候选的
HOLD 标注（名额未释放）→ 下一轮若清仓 SELL 已批准，初审将不再出现只数否决。

## 部署清单

与 b2d56d3 一并发布，沿用 11-05 那套流程：

1. **发布前必做**：用最新 state 刷新 `multi-market/snapshot/data-multi.snapshot.json`
   跨部署种子（防本地测试污染 / 旧数据回退）。
2. 备份三账户 state 到 `backup/`；确认本地不把 `data-multi*.local` 旧档随包上传。
3. 重新发布（workbuddy_sites_deploy，目录 multi-market，startCmd=python server.py）。
4. 上线验收：
   - `GET /api/` features 含 `tpOncePerDay`、`roomVeto`、`swapGate`、`clearSell`；
   - 三账户 cash / 持仓只数 / 成交笔数与备份逐位一致；`pendingReview` approved 项还在；
   - 下一笔 forced SELL 成交日志的股数 = 全部持仓股数（清仓口径验收点）；
   - 满席账户出现「forced/R15 清仓 SELL 已批 + 达标 BUY 候选」时，初审不再报只数
     否决、成交日志顺序为先卖后买（swapGate 验收点）。
