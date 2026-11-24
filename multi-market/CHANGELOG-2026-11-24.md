# 2026-11-24 收盘前补扫（P1）+ 止盈全清（B 档）上线

## 背景

用户问「为什么没在最高点卖」。全量核对三账户 21 只持仓的峰值回吐（合计约 3,706 元）后
定位到两类真问题，均由 `deliverables/auto-trade/tp-residual-guard-2026-11-24.md` 的回测给出：

1. **扫描时点盲区**：`check_stops` 只在每小时 :40 取价，A 股 14:40→15:00 有 20 分钟无人看盘。
   建设银行 11-20 收盘 +8.75%、盘中最高 +8.92%，`tpDate` 为空、一股未卖；11-23 盘中 +8.41%
   再次漏掉 —— 不是阈值问题，是「根本没机会触发」。
2. **止盈只卖一半**：+8% 落袋 50% 后，残仓只靠 `trailPct`（要求仍浮盈）与 −6%/−8% 硬止损，
   中间没有任何出口。12 个 episode 回测：**8% 全清相对半仓 +2,728 元**，优于残仓保护
   1.5%（+1,998）。

用户 2026-11-24 12:28 拍板：「改 p1 跟选 B」。

## 变更

### 1. P1 收盘前补扫 —— `sim_time.cn_close_sweep_window()` + `sim_engine.close_sweep()` / `maybe_close_sweep()`

- **窗口**：A 股工作日 **14:40–14:59**（法定节假日 `is_cn_holiday` 排除），窗口内 **3 分钟节流**
  可重复检查（尾盘价格还在动）；卖出幂等由 `tpDate` / `loseCutDate` 保证，不会重复卖。
- **动作**：只做「刷持仓现价 → `check_stops`」两件事。不取新闻、不算信号、不进评审队列、
  不调 LLM，**零 token**。
- **触发位置（双通道，请求为主）**：
  1. `Handler.do_GET` —— 任何一次面板刷新/自动化访问落在窗口内即触发。**这是主通道**：
     云端沙箱无人访问时后台线程会被挂起（11-24 12:36~12:38 连续心跳实测，worker 自 11:15
     起 80 分钟没有推进扫描；`maybe_catchup` 的注释早就记录过同一现象）。
  2. `worker()` 账户循环 + 独立 `sweep_worker()` 线程 —— 线程活着时的兜底，与扫描主循环解耦，
     避免一次长扫描把补扫一起拖住。
  窗口从 14:40 起：14:40 那轮盘中自动化通常只跑 2~5 分钟，窗口若从 14:50 起会整段错过；
  从 14:40 起必然被覆盖，窗口内 3 分钟节流还能在后续访问里重复检查到收盘。
- **额度**：进入前 `roundTrades=0`（视为独立一轮），当日 `maxPerDay` 仍生效。
- **成交**：走与 `scan()` 相同的 `do_sell` 路径，14:55 仍在连续竞价内，可正常成交。
- **开关**：`useCloseSweep`（false 一键回退）；非 `real` 价格模式、非交易时段自动跳过。
- **美股**：不覆盖。14:55 北京时间 `in_trading_hours()` 为 False，天然被挡；美股收盘前
  补扫（03:55 / 04:55）留待后续，需单独立项。

### 2. B 档：目标止盈全清 —— `sim_account.check_stops()`

- 新增配置 `takeProfitSellFrac`：`0.5`（旧行为，卖一半）｜`1.0`（全清）。
- `>= 0.999` 视为全清，直接卖 `h["shares"]`，不再走 `sell_floor_qty`（无残仓可留）。
- 中间档（0.05~0.999）仍走原有卖出下限，防碎单。
- 默认值 `0.5`（代码不改行为），线上三账户 `/api/config` 推送 `1.0` 生效 —— 回退只需推回 `0.5`。

## 影响面与回退

| 项 | 值 | 回退方式 |
|---|---|---|
| `useCloseSweep` | True（三账户） | `/api/config` 传 false |
| `takeProfitSellFrac` | 1.0（三账户） | `/api/config` 传 0.5 |
| 补扫窗口 | 14:55–14:59 | 代码常量（`sim_time.cn_close_sweep_window`） |

## 测试

- 新增 `_test_close_sweep.py`：23 项全通过（时间窗 6 项、全清/半仓/未达标、三前置条件、
  行情异常降级、空仓、请求入口的窗口判定/节流/不重复卖）。
- 新增 `_test_close_sweep_http.py`：4 项全通过 —— 真起 `ThreadingHTTPServer` 打 GET
  `/api/state`，验证 `do_GET → maybe_close_sweep → close_sweep` 这条接线真的通（判定逻辑
  另测；这里只证明「有访问就会跑」）。
- 回归：`_test_tp_rules` 19、`_test_stops` 24、`_test_trim` 29、`_test_swap_gate` 21、
  `_test_ttl_sameday` 11、`_test_e2e` 17、`_test_gate` 38、`_test_room_veto` 6、
  `_test_settle_session` 20，全部 0 失败。

## 上线记录（2026-11-24 12:31–12:36）

1. 刷新跨部署种子 `snapshot/data-multi.snapshot.json`（3 账户，1402KB），确认 `multi-market/` 无 `data-multi.json`。
2. 备份三账户 state 至 `backup/2026-11-24-1231/`。
3. 首轮发布（12:33）：只挂在 worker 循环里 → 随后实测发现 worker 被沙箱挂起，
   改为「请求触发为主 + 双线程兜底」（窗口同时放宽到 14:40），二次发布（12:52）。
4. 验收：`/api/version` features 含 `closeSweep` / `tpSellFrac`；三账户 现金/持仓只数/市值/
   待评审/已实现/成交笔数 与部署前逐项一致（us 65,475.09 / 4 只 / 2 待评审；cnc 28,096.91 /
   6 只 / 0；cna 20,206.00 / 11 只 / 0）。
5. 配置推送：三账户 `takeProfitSellFrac=1.0`，读回确认；`takeProfitPct` 仍为 8。

## 待观察

- **今日 14:40 首次实弹**：这是第一条能证明「盲区已补」的现场证据。若 cnc 建行/工行、cna 工行
  等在 14:55 后仍回到 +8% 而无卖出记录，需查 worker 是否被重启打断或 `closeSweepDate` 误标。
- 全清会削右尾：样本期（10-14~11-24）是单边回落区间，上行区间未验证；若后续出现「+8% 清仓后
  继续大涨」的连续案例，优先回退 `takeProfitSellFrac` 到 0.5，而不是降止盈线。
