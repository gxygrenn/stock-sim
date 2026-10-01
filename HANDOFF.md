# HANDOFF · 模拟炒股双版本对照实验

> 交接件。**收尾时更新【本session】段；主线收尾时更新【主线状态】段。**
> 最后更新：2026-09-25 20:45（本 session 收尾）
> 工作区：`C:\Users\汪的猪\WorkBuddy\2026-09-12-20-37-50`

---

## 【主线状态】

### 准星（一句话）

双版本（稳健 / 激进）三个应用已上线、五条自动化已挂。**v3 已于 2026-09-25 21:18 部署上线**
（评审与执行解耦 + 队列 TTL 24 小时均已生效），两版 `/api/version` 已含 `reviewDecoupled` / `reviewTTL`，
线上日志实证新逻辑在跑（激进版「评审队列 TTL 作废 1 只：浪潮信息」）。
**下一关键节点 = 09-28 复市**：验证「盘后评审 → `state=approved` → 服务端交易时段按最新价成交」首次真正闭环。

### 节点

| 项 | 状态 |
|---|---|
| 三个线上应用 | ✅ 稳健版 / 激进版 / 说明书，均在线 |
| 五条自动化 | ✅ 评审×2（每小时）、复盘×2（工作日 15:45 / 15:50）、播报×1（工作日 15:35，**从未运行过**） |
| 经验库闭环 | ✅ 两版 PLAYBOOK 独立维护，每日复盘写回面板（两版 `dailyReviews` 各 3 条） |
| **v3 修复** | ✅ **已部署上线**（2026-09-25 21:18，两版 `/api/version` 均含 `reviewDecoupled` / `reviewTTL`）|
| 交易闭环 | ⚠️ 链路已通，**待 09-28 复市验证成交** |
| `history` 超额归因 | ❌ `settle()` 日期错配，基于它的跑赢跑输结论**全部不可信** |

### 下一步（按优先级）

1. **09-28（周一）复市验证闭环**：读两版 `/api/state` 的 `trades` / `history`，确认
   「评审自动化入 `state=approved` → 服务端交易时段按最新价成交」第一次真正发生。
   注意：09-25 那批 4 个 `BUY` 结论在旧 handler 下已丢失，**不会再补**；本次验证的是**新入队**的候选。
2. **修 `settle()` 的结算日对齐 / 单日单次结算**（两个复盘官独立指出同一缺陷）。
3. 修 `87892d61` 播报自动化的坏 `cwds` 字段；决定是否给激进版补 `/api/market_context`。
4. 修技术面信号全量失效（每轮「技术面数据获取失败 5/20 只，记 0 分」，v3 上线后日志仍在报）。

### 未决项（等用户拍板）

| # | 未决项 | 冲突点 |
|---|---|---|
| 1 | ~~是否部署 v3~~ | ✅ **已解决**：2026-09-25 21:18 两版部署上线（用户指示「线上 v3 部署」）|
| 2 | ~~队列过期 4h vs 24h~~ | ✅ **已定**：随 v3 统一为 **24 小时**。稳健版线上存档里仍留着旧的 `reviewMaxAgeMin: 240` 键，但代码已不再读取（`grep` 确认 `server.py` 无引用、前端也不展示），属无害残留 |
| 3 | 市场环境播报归属 | 播报自动化只写稳健版（激进版没有该路由）；稳健版线上数据停在 `2026-09-15`，即面板长期显示 10 天前的"对今日大盘偏空" |
| 4 | `settle()` 缺陷 | 复盘官已给出参数建议，状态"待用户确认"，**建议是修链路而非调阈值** |

---

## 【本session】

**目标**：用户裸挂载 `session-continuity`（无动作词），按指定做**收尾**。

**做了什么**：只做**状态核查 + 落交接件**，未改任何代码、未部署、未动自动化。

核查中确认的关键事实（均有一手证据）：

1. **线上 v3 未部署**：两版 `/api/version` 的 features 均无 `reviewDecoupled` / `reviewTTL`
   （稳健版 = `["gate","pendingReview","dailyFacts","dailyReview","marketContext"]`，
   激进版 = 前四项）。**上次会话末问的部署问题从未被回答。**
2. **今天 20:16–20:37 的日志是自动化自己写的**，不是人机对话（稳健复盘 20:16、激进复盘 20:24、激进评审 20:33）。
3. **激进版评审的 4 个 BUY 结论已丢失**（旧 handler 不落 approval）：线上 `pendingReview` 6→1（剩浪潮信息
   `time=2026-09-15 10:35:57`、无 `queuedAt`）、`reviewed=5`、**`trades=0` / `cash=100000`**。
4. **稳健版**：`trades=12`（全部停在 09-14）、`pending=0`、`reviewed=0`、`reviewMaxAgeMin=240`；
   `history` 尾部 = `09-23(-1.22,-1016) / 09-24(-1.22,0) / 09-25(-1.22,0)` → **09-25 是休市日却被写入了净值行**。
5. **激进版 `nextDate=2026-09-23`**，落后稳健版（`2026-09-28`）→ 面板停机导致结算停滞。
6. **播报自动化 `87892d61` 从未运行**（无记忆文件），且 `cwds` 存成了 JSON 字符串而非数组：
   `"[\"C:\\\\Users\\\\汪的猪\\\\...\"]"`。
7. **09-25 为中秋节休市**（9/25–9/27 休市，**09-28 周一复市**）——由激进版复盘官核实公告得出。

**卡点**：无阻塞，仅"发布需用户同意"这一硬门槛。

---

## 关键事实 / 口径（新 session 直接用，不必回读对话）

### 线上地址

| 用途 | 地址 |
|---|---|
| 稳健版 | https://stock-sim-pnl.app.workbuddy.host/ |
| 激进版 | https://stock-sim-aggressive.app.workbuddy.host/ |
| 说明书 | https://stock-sim-manual.app.workbuddy.host/ |

管理入口：**设置—数据管理—应用**。

### 两版参数基线（线上实测）

| 参数 | 稳健版 | 激进版 |
|---|---|---|
| autoTrade（门控） | False | False |
| minScore | 4 | 2 |
| buySigFloor | 0 | -2 |
| buyPct / maxPosPct | 20% / 30% | 35% / 45% |
| minAgree / maxHold | 2 / 8 | 1 / 12 |
| cooldownMin / maxTotalPos | 30 / 92% | 10 / 98% |
| useVal / stopLossPct | 开 / -8% | 关 / -15% |
| reviewCooldownMin | 60 | 30 |
| reviewMaxAgeMin | 240（**将在 v3 中被 24h TTL 取代**） | 无 |

### 关键文件

- 源码：`stock-sim\`、`stock-sim-aggressive\`（各含 `server.py` / `index.html` / 测试三件套
  `_test_gate.py` + `_test_api.py` + `_test.js`）
- 经验库：`deliverables\auto-trade\PLAYBOOK.md`（稳健）、`...\aggressive\PLAYBOOK.md`（激进，含两版参数对照表）
- 留档：`deliverables\auto-trade\daily-review-YYYY-MM-DD.md`、`review-YYYY-MM-DD.md`（激进版在同级 `aggressive\`）
- 交接件：本文件 `HANDOFF.md`
- 日志：`.workbuddy\memory\YYYY-MM-DD.md`；自动化执行记录：`.workbuddy\memory\automations\<自动化ID>\memory.md`

### 口径与约定（踩过坑的）

- **自动化只在 WorkBuddy 客户端运行时才触发**，实测触发时刻集中在 **19:00–23:00 与周末**。
  因此评审 prompt **禁止写"仅当盘中才执行"的守卫**——曾导致 12 次触发 0 次执行、5 天零成交。
- 云端沙箱**无人访问时会挂起**，进程内后台扫描线程随之停摆（两版都出现过 6 天无记录）。
- **`Edit` 工具遇含中文的 `old_string` 会静默不落盘** → 改用 Python 脚本做精确替换并 grep 复核。
- 两版本地测试**不能跨工具调用持有服务进程**（调用结束即被回收）→ 起服务 + 跑测试必须写进同一条命令。
- `_test_api.py` 端口：稳健版 8123、激进版 8124。
- 复盘/评审自动化读的经验库路径、NeoData 取数命令、角色定义目录，均写在各自 prompt 里（可用
  `automation_update mode=view` 取回全文）。

### v3 修复内容（本地已改完，未部署）

1. **评审与执行解耦**：`/api/review_done` 只把结论写回队列（`state=approved` + `review{verdict,confidence}`），
   不再要求评审方下单；服务端 `execute_approved()` 在交易时段按**当时最新价**成交，并把
   「评审→成交」滞后写进成交理由。
2. **队列 TTL 24h**：`expire_stale_reviews()`；waiting 按 `queuedAt`、approved 按 `reviewedAt` 起算；
   **无 `queuedAt` 的旧条目一律作废**（正好清掉激进版那 6 个僵尸）。过期/作废记入 `expiredReviews`。
3. **删掉稳健版 `prune_stale_reviews` / `reviewMaxAgeMin: 240`**，统一到 24h。
4. **修 `/api/inject` 真 bug**：原硬传 `force_trade=True`，**门控模式下投喂会绕过专家团直接下单**；
   已改为门控时只入队。
5. 前端队列行显示「已等 Xh」与「已批准·待成交」；`/api/version` 新增 `reviewDecoupled` / `reviewTTL`。
6. 两条评审自动化 prompt 已改为"触发即执行、结论写回、不自己下单"。

**已验证**：稳健版 gate 37 + api 114 + js 52 = **203**；激进版 gate 37 + api 104 + js 40 = **181**，全绿。

### 已知缺陷台账（均未修）

| 缺陷 | 影响 | 状态 |
|---|---|---|
| `settle()` 结算日与行情日错配 + 重复结算 + 休市日也结算 | `history` 超额归因不可信 | 两复盘官已报，待用户确认 |
| 技术面信号长期全量失效（每轮「技术面数据获取失败 5/20 只，记 0 分」） | 四路信号实际只有三路 | 待处理 |
| 播报自动化 `87892d61` 的 `cwds` 是 JSON 字符串 | 可能无法解析工作目录 | 待修 |
| 激进版缺 `/api/market_context` 路由与前端两块卡片 | 两版 UI 不一致，对照缺同一组宏观数据 | 待决定 |
| 稳健版 `marketContext` 数据停在 `2026-09-15` | 面板长期显示 10 天前判断，有误导性 | 待决定 |

---

### 补记（本 session 追加 · 20:50）

- 修正 `session-continuity` 技能：新建 `~/.workbuddy/skills/check_closeout.sh`（7 项硬检查点），
  并把 `SKILL.md` 中两处 `C:\Users\admini\...` 换成本机路径。
  （20:45 那次声称已修，实际未落盘 —— 又是 `Edit` 中文静默失败的坑。）
- 硬检查点实跑结果：**OK=7  FAIL=0，全绿**。
