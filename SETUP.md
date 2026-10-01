# 环境复现指南（新设备部署）

> 本文档用于在**另一台设备/终端**上完整复现本项目环境（代码 + 云端部署 + 自动化配置）。
> 适用场景：换电脑、重装系统、多设备并行开发。

---

## 一、克隆代码

```bash
git clone https://github.com/gxygrenn/stock-sim.git
cd stock-sim
```

> 若在 WorkBuddy 中操作，建议克隆到自己的工作区目录（如 `C:\Users\<你的用户名>\WorkBuddy\...`）。

克隆后目录结构：

| 目录 | 说明 |
|---|---|
| `stock-sim/` | 模拟炒股**稳健版**（源码 = 部署目录）|
| `aggressive-app/` | 模拟炒股**激进版**（源码 = 部署目录）|
| `stock-sim-cloud/` | 云服务版（历史）|
| `stock-sim-docs/` | 说明书副本（历史）|
| `trade-desk/` | 交易工作台 |
| `deliverables/` | 说明书、复盘、经验库 |
| `HANDOFF.md` / `LESSONS.md` | 交接说明 / 经验教训 |

---

## 二、本地运行（可选，先验证代码能跑）

两个服务都是纯 Python 标准库，无需安装依赖：

```bash
# 稳健版（端口 8000）
cd stock-sim && python server.py

# 激进版（端口 8000）
cd aggressive-app && python server.py

# 交易工作台（端口 8123）
cd trade-desk && python server.py
```

访问 `http://localhost:8000/`、`http://localhost:8123/` 验证。

---

## 三、云端部署（把代码发布成常驻在线应用）

> 云端部署与本地工作区是**隔离的两套状态**。部署会用 `default_state()` 生成干净初始态（10 万现金、零持仓）。

### 3.1 稳健版

1. 在 WorkBuddy 对话中说：**「把 stock-sim 目录部署为在线应用」**
2. 启动命令若自动检测错误，显式指定 `python server.py`
3. 部署成功后，记录返回的在线地址

### 3.2 激进版

> ⚠️ **关键坑**：部署工具按目录判定应用归属，`stock-sim` 和 `aggressive-app` 若放在同一父目录下，可能被识别成同一个应用、互相覆盖。当前项目已把激进版放在独立目录 `aggressive-app/` 规避此问题。**新设备上请保持这个目录结构**（不要把 aggressive-app 改名或并回 stock-sim）。

1. 对 `aggressive-app` 目录单独部署
2. 若也被识别成同一应用，复制到全新独立目录再部署

### 3.3 部署后设置门控模式

两版默认都要设为门控模式（`autoTrade=false`）：

```bash
curl -X POST https://<你的域名>/api/config \
  -H "Content-Type: application/json" \
  -d '{"config":{"autoTrade":false}}'
```

---

## 四、自动化配置（定时任务）

> 自动化是 WorkBuddy 云端调度的定时任务，**依赖 WorkBuddy 客户端运行**才触发。以下是需要重建的自动化清单。

| 自动化名称 | 频率 | 作用 |
|---|---|---|
| 稳健版·盘中定时唤醒扫描 | 工作日 9/10/11/13/14/15 点整点 | curl 触发 refresh+scan，防沙箱挂起 |
| 稳健版·云端LLM专家团评审 | 工作日 9/10/11/13/14/15 点 :10 | 读队列→LLM 评审→写回 |
| 激进版·盘中定时唤醒扫描 | 工作日 9/10/11/13/14/15 点 :05 | 同上 |
| 激进版·云端LLM专家团评审 | 工作日 9/10/11/13/14/15 点 :15 | 同上 |
| 每日代码自动提交推送 | 每天 23:30 | git add -A + commit + push |

### 唤醒扫描自动化 prompt 模板

```
对云端「模拟炒股<版本>版」服务做一次定时唤醒与扫描。用 curl 依次请求以下接口（服务地址 https://<你的域名>/ ）：

1. POST /api/refresh —— 刷新指数与持仓真实行情（Content-Type: application/json，body 可为空 {}）
2. POST /api/scan —— 触发一轮「抓新闻→打分→维护待评审队列」扫描（body 可为空 {}）
3. GET /api/state —— 检查 lastScan 时间戳是否更新、scanErr 是否为空

目标：确保云端沙箱进程保持活跃（不被挂起）、盘中每小时自动刷新行情并推进待评审队列。门控模式下 scan 只产出候选进队列、不自动下单。执行后简要报告 lastScan 时间与 scanErr 状态即可，不要改动任何配置。
```

### LLM 评审自动化 prompt 模板

```
你是「模拟炒股<版本>版」的专家团评审官。请对云端服务的待评审候选逐个做 LLM 评审，并把结论写回。

服务地址：https://<你的域名>/

执行步骤：
1. GET /api/state 读取当前状态，取出 pendingReview 数组（state 为 "waiting" 的候选）。
2. 若 pendingReview 为空，直接报告「无待评审候选」并结束，不要做任何写操作。
3. 对每个候选，综合字段（name 股票名、code 代码、direction 建议方向、score 新闻分、delta 信号分、reason 快讯摘要、px 最新价）审慎研判，verdict 为 BUY / SELL / HOLD 之一，并给出 confidence（0-100）和简短 note（≤40字）。
4. 对每个候选，POST /api/review_done，body 形如 {"code":"sz000977","verdict":"BUY","confidence":70,"note":"..."}，code 必须与队列完全一致。
5. 报告评审结果与写回是否成功。

评审纪律：<版本>版风格<稳健/进取>，宁可 HOLD 不为刷成交勉强下单，只依据候选字段和公开常识判断，不编造数据，不调用 /api/reset 或 /api/config。
```

---

## 五、Git 提交与推送

```bash
git add -A
git commit -m "chore: <说明>"
git push origin main
```

> 本机已配置 GitHub 凭据。新设备首次推送若提示认证，需要重新登录 GitHub 凭据（Git Credential Manager）或配置 SSH key / PAT。

---

## 六、已知注意事项

1. **国庆休市**：成交逻辑在 `in_trading_hours()` 为 False 时不会立即成交，评审 BUY 的候选会进入 `approved` 状态，待复市后第一个交易时段按最新价成交。
2. **自动化依赖客户端运行**：WorkBuddy 自动化（含每日推送）只在客户端运行时触发。
3. **`.gitignore` 排除项**：`data.json`、`downloads/`、`.workbuddy/`、`.wbapp_*.genie` 不进版本库，新设备需按上述步骤重新生成。
4. **两版参数差异**（稳健 vs 激进）：
   - minScore：4 vs 2
   - buyPct：20% vs 35%
   - maxHold：8 vs 12
   - stopLossPct：-8% vs -15%
