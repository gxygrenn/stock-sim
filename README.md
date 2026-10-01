# 模拟炒股双版本对照实验

> A 股模拟交易机器人：10 万元虚拟本金、15 只自选股票池，云端 7×24 运行，消息驱动 + LLM 专家团评审，全自动闭环。

## 项目简介

本仓库包含一个 **A 股模拟交易系统**，含**稳健版**和**激进版**两个参数不同的对照实验，以及一个交易工作台。两版均运行在 WorkBuddy 云端，实现「采集 → 打分 → LLM 评审 → 交易时段自动成交」的完整闭环。

## 目录结构

| 目录 | 说明 |
|---|---|
| `stock-sim/` | 模拟炒股**稳健版**（源码 = 部署目录）|
| `aggressive-app/` | 模拟炒股**激进版**（源码 = 部署目录）|
| `stock-sim-cloud/` | 云服务版（历史留档）|
| `stock-sim-docs/` | 说明书副本（历史留档）|
| `trade-desk/` | 交易工作台（A股/港股/美股/期货实时行情）|
| `deliverables/` | 说明书、复盘、经验库（PLAYBOOK）|

## 快速开始

### 本地运行（纯 Python 标准库，无需安装依赖）

```bash
cd stock-sim && python server.py        # 稳健版，端口 8000
cd aggressive-app && python server.py   # 激进版，端口 8000
cd trade-desk && python server.py       # 交易工作台，端口 8123
```

访问 `http://localhost:8000/` 或 `http://localhost:8123/` 查看面板。

### 云端部署 / 新设备复现

完整的环境复现（代码 + 云端部署 + 自动化配置）步骤见 **[SETUP.md](./SETUP.md)**。

## 文档

- **[SETUP.md](./SETUP.md)** — 环境复现指南（新设备部署）
- **[HANDOFF.md](./HANDOFF.md)** — 项目交接说明（含参数基线、踩坑口径、已知缺陷台账）
- **[LESSONS.md](./LESSONS.md)** — 经验教训
- `deliverables/stock-sim-manual.html` — 使用说明书（网页版）

## 双版本参数对照

| 参数 | 稳健版 | 激进版 |
|---|---|---|
| autoTrade（门控）| False | False |
| minScore | 4 | 2 |
| buyPct / maxPosPct | 20% / 30% | 35% / 45% |
| minAgree / maxHold | 2 / 8 | 1 / 12 |
| cooldownMin / maxTotalPos | 30 / 92% | 10 / 98% |
| stopLossPct | -8% | -15% |

## 版本历史

- **v3**（2026-09-25）：评审与执行解耦 + 评审队列 TTL 24h
- **云端全自动版**（2026-10-01）：双版本部署上线 + 唤醒/评审自动化闭环 + Git 版本管理
