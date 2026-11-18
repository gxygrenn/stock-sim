# 2026-11-18 单票超配减仓 + 卖出股数下限上线

## 背景
收盘复盘长期挂着的 B 类「待人工确认」三项，用 1526 条 reviewLog + 25 天快照 +
腾讯日 K 做反事实回放（`_b_param_sim.py`）后给出建议值，用户当天拍板「全部按推荐改」。

## 变更

### 1. 单票超配定期减仓（B3）—— 新增 `check_trims()`
- 触发：连续 **3 个交易日**收盘权重 > **22%**（20~22% 是赢家漂移区，5 日超额 +2.92%，
  不砍；22~25% 为 −5.26%，胜率 13%，该砍）
- 动作：卖到 **20%**（按整手取最接近目标的一档，避免向下取整后仍在触发线上）
- 冷却：同标的 **10 个交易日**
- 豁免：当日涨幅 ≥5%（强者恒强）｜浮盈 ≥takeProfitPct（交 check_stops/R10）｜
  当日跌幅 ≥3%（顺延，不在下跌中砸盘）
- 开关：`useTrim`（false 一键回退）
- 调用点：`sim_engine.scan()` 紧跟 `check_stops()` 之后

### 2. 卖出股数下限（B4）—— 新增 `sell_floor_qty()`
- 单笔卖出金额 ≥ **2×minSellAmount**（A股 2,000 元 / 美股 600 美元），不足则抬量
- 卖后残值 < **1.5×minSellAmount**（A股 1,500 元 / 美股 450 美元）→ 一次清仓
- 接入三处：`check_stops()` 目标止盈分批、`execute_approved()` 普通 SELL、
  `check_trims()` 超配减仓
- forced / 【R15清仓腾位】仍按原逻辑全量卖出，不受下限影响

### 3. 配置默认值（sim_config.py，三账户）
```
useTrim=True, trimPct=22, trimTargetPct=20, trimConfirmDays=3,
trimCooldownDays=10, sellFloorRatio=2.0, clearRemainderRatio=1.5
```
线上存档 config 缺这些键时按上述默认值生效，无需推 /api/config。

### 4. 未改动（有数据依据）
- **防守档开口子（B2）不改**：δ≥3.9 且被仓位否决的候选，去重 20 个 episode
  5 日收益 −1.49%，按 2pp 仓位估算累计 −558 元；当日涨 ≥7% 的一组 5 日超额 −7.13%。
  样本内「仓位否决」是对的，维持 `regimePosCap` 70/80。

## 测试
- 新增 `_test_trim.py` 27 项（触发线/连续日/周末不断链/三档豁免/冷却/开关/美股 1 股/
  下限与残值清仓）
- 因 B4 下限，更新两处旧断言：`_test_stops.py`（157 股残仓一把清完）、
  `_test_swap_gate.py`（放大到 2,260 股，保留「无 R15 标记不做清仓」本意）
- 全量 21 个测试文件 0 失败

## 上线验收
- features 新增 `trimRule` / `sellFloor`（23 项）
- 三账户现金/持仓/待评审/成交数与部署前备份逐项一致
- 当前命中对象：cna 工商银行 23.0%（需连续 3 日 >22% 才触发，最早 11-20）

## 回退
- 关闭减仓：/api/config 推 `{"config":{"useTrim":false}}`
- 调阈值：`trimPct` / `trimTargetPct` / `trimConfirmDays` / `trimCooldownDays`
- 放宽下限：`sellFloorRatio` 1.0、`clearRemainderRatio` 0（等价关闭）
