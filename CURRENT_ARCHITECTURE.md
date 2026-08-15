# TripStar 当前架构说明

> 本文件保留在仓库根目录，作为旧链接和历史入口的兼容页。当前架构的规范参考是 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，能力边界与验证状态以 [`docs/PROJECT_BASELINE.md`](docs/PROJECT_BASELINE.md) 和当前代码为准。本文件不再作为未来功能规划或历史阶段状态的来源。

## 1. 当前系统定位

TripStar 是 Vue 3 单页前端与 FastAPI 后端组成的旅行规划应用。它接收结构化行程信息与自然语言偏好，通过**有界的顺序式 LLM 编排与确定性服务**生成、补全、验证和展示行程。

仓库中的 `MultiAgentTripPlanner` 是现有类名，`backend/app/agents/` 是现有模块路径；这些名称不代表系统存在自治的多 Agent 协商、独立 Agent 调度器或无限自反思循环。实际运行时主要包含：

- 一个 HelloAgents `SimpleAgent` 执行核心 Planner 模型调用；
- 可选且有界的偏好解析、XHS 提取、Critic/Revision、JSON repair、Patch interpretation 和 Chat LLM 调用；
- 地图、天气、酒店检索、POI enrichment、pacing、validation、patch application、knowledge graph、持久化与进度管理等确定性服务；
- Google Maps、AMap、XHS 和 OpenAI-compatible LLM 等外部 provider 适配器。

## 2. 目录与职责

```text
frontend/src/
├── views/Landing.vue              # 表单、偏好确认、任务提交、进度与恢复
├── views/Result.vue               # 后端结果恢复、行程/预算/风险/地图/图谱/导出
├── components/AIChat.vue          # 问答模式与局部修改模式
├── services/api.ts                # HTTP API 与运行时配置访问
├── services/tripTaskLifecycle.ts  # WebSocket + HTTP polling 恢复
└── types/index.ts                 # 前端数据契约

backend/app/
├── api/main.py                    # FastAPI、异常处理、健康检查、静态前端
├── api/routes/                    # trip/preferences/chat/poi/map/settings/demo
├── agents/trip_planner_agent.py   # 顺序式规划编排、Planner prompt 与解析
├── models/schemas.py              # Pydantic 请求、计划、验证、Patch、图谱契约
├── services/                      # Provider、grounding、validation、revision、patch 等
└── evaluation/                    # 离线评估、capture 与 report 工具
```

Prompts 没有独立目录，主要内嵌在 Planner、preference、chat、revision、pacing revision 和 patch service 中。

## 3. 前端架构

### 3.1 输入与偏好

`Landing.vue` 收集城市/多城市天数、日期、同行类型与人数、预算、节奏、兴趣、交通、住宿和自由文本要求。自由文本通过 `/api/preferences/parse` 尝试结构化；显式字段保持优先，模型或解析失败时保留显式字段与原始要求，不阻塞后续生成。

### 3.2 长任务体验

前端通过 `POST /api/trip/plan` 获取 task ID。`tripTaskLifecycle.ts` 同时使用：

- WebSocket `/api/trip/ws/{task_id}` 接收阶段、进度和最终结果；
- HTTP `/api/trip/status/{task_id}` polling 作为持续启用的恢复路径；
- `sessionStorage` 保存活动 task ID，支持刷新后恢复查询。

WebSocket 提前关闭或失败后会回退到 polling。此恢复能力只恢复任务状态/结果读取，不会在后端进程重启后恢复未完成的执行。

### 3.3 结果与交互

`Result.vue` 在存在 `plan_id` 时以后端任务结果为规范数据，渲染行程、预算、天气与来源状态、validation risks、POI 照片、地图路线和确定性生成的展示型 knowledge graph。当前导出能力是基于 html2canvas 的图片导出，不包含 PDF、日历或分享链接。

`AIChat.vue` 提供两条独立路径：

- Q&A：将当前 TripPlan 与有限对话历史发送给 LLM；
- Patch：由 LLM 将修改意图解释为类型化操作，再由确定性 patch engine 执行、补全、验证、生成 diff 并提升 plan version。

Patch 支持受限的局部修改，但没有 Undo、用户权限或跨进程并发控制。

## 4. 后端 API 与任务模型

主要 API：

| API | 当前职责 |
|---|---|
| `POST /api/preferences/parse` | 可选 LLM 偏好结构化，失败时使用显式字段 |
| `POST /api/trip/plan` | 创建异步规划任务并立即返回 task ID |
| `GET /api/trip/status/{task_id}` | polling 查询任务状态和最终结果 |
| `WS /api/trip/ws/{task_id}` | 推送进度与最终结果 |
| `POST /api/trip/{task_id}/patch` | 版本化局部修改、验证与 diff |
| `GET /api/trip/history` | 读取本地已完成任务摘要；公开模式可禁用 |
| `POST /api/chat/ask` | 当前行程上下文问答 |
| `/api/map/*`, `/api/poi/*` | 地图、天气、路线、POI 和图片适配器接口 |
| `GET/PUT /api/settings` | 浏览器安全的运行时配置；公开部署只读 |
| `GET /api/example-trip` | 不调用 Planner/provider 的只读示例行程 |

任务、订阅 queue、去重 fingerprint、patch lock、LLM usage 和公开演示限流都位于单进程内存。任务快照写入 `backend/data/trip_tasks/*.json`；自由文本偏好在持久化请求副本中被清理。服务重启后，未完成任务会被标记为失败，不会恢复执行。

## 5. 实际 AI 执行路径

1. 可选偏好解析：自由文本触发一次受预算控制的 `preference` 调用；失败时 fail open。
2. 任务创建：请求语义 fingerprint 用于活动任务去重，随后以 `asyncio.create_task` 启动规划。
3. Provider context：逐城市收集 XHS 景点研究，并通过 Google/AMap 确定性服务获取天气与酒店候选；不可用时保留明确降级状态。
4. Planner：新建 HelloAgents `SimpleAgent`，将 TripRequest、PreferenceProfile、provider context 与 pacing contract 组合为 prompt，调用 OpenAI-compatible 模型。
5. Structured parsing：确定性清理/修复 JSON 并构造 `TripPlan`；最后手段可使用一次 `json_repair` LLM 调用。
6. Grounding：地图 provider enrichment 移除或覆盖不可信地图事实，写入 verified/partial/unverified/unavailable 等状态。
7. Validation：确定性检查预算、最早出发、路线/行动能力、grounding 和 pacing，生成类型化 risks/degraded 状态。
8. Revision：只有符合条件的风险才进入有界 Critic/Revision 或类型化 pacing revision；安全门和重验失败时保留原计划，不进行开放式循环。
9. Finalization：构建展示型 knowledge graph，持久化 `TripPlanResponse`，并通过 WebSocket/polling 返回。

LLM 调用统一经过 `llm_service.create_chat_completion()`，记录 stage、model、可获得的 token、duration 与 retry，并执行逻辑调用上限。默认每次完整生成最多 5 次逻辑 LLM 调用；只有明确分类为瞬时错误的 provider 调用才允许一次重试。

## 6. Preference、Validation 与 Follow-up 的当前状态

旧架构文档中“没有结构化同行/预算/节奏偏好”“没有独立 validation”“Chat 不能修改行程”等描述已经过时。当前实现包括：

- `PreferenceProfile` 与 `PreferenceConstraints`，覆盖 party、budget、pace、interests、earliest start、mobility、food 和其他要求；
- `TripValidatorService` 与类型化 `ValidationResult`/`RiskItem`；
- 确定性 daily-load/pacing policy；
- 有界 Critic/Revision 与受保护约束；
- 版本化 `TripPatch`、受影响日修改、diff、锁、grounding 和重验。

边界仍然存在：推荐没有统一评分模型；开放时间和所有叙述/费用字段并非全面验证；局部修改不支持 Undo 或分布式并发；Q&A 允许标注后的常识性建议，因此并非完全 grounded。

## 7. Provider、地图与可观测性

### 7.1 Provider 集成

- Google service 封装 Places、Geocoding、Directions、Weather 和 Photo 路径；前端可使用 Google Maps JavaScript API。
- AMap service 使用 REST adapter 处理 POI、geocoding、route 和 weather，并有对应的解析/失败分类测试；旧文档中“依赖外部 AMap MCP 且解析仍为 TODO”的描述已过时。
- XHS adapter 包含签名、搜索、详情/SSR 降级、LLM 提取和图片路径；它依赖 Cookie、网络和第三方接口稳定性。
- `map_dispatcher.py` 根据后端配置选择地图 provider 并提供降级路径。

这些是仓库中已实现/可配置的适配器，并不证明本地或生产环境的 Key/Cookie 有效、API 已启用、quota/billing 可用、provider 稳定或内容实时准确。STEP 0 的 provider 验证主要来自 mock 测试，没有完成 live provider smoke test。

### 7.2 Grounding 与 evidence

POI 具有坐标信任边界、provider source、match status 和降级状态；XHS 数据具有 evidence ID、quote 和 support；照片路径暴露 Google/XHS/placeholder 来源及可用 attribution。Planner prose、餐饮/酒店估价、票价、预算与一般建议仍未做到逐字段统一来源引用。

### 7.3 Observability

当前实现记录：

- generation/task ID 与 LLM stage；
- 逻辑调用数、stage calls、model、retry；
- provider 返回 usage 时的 prompt/completion/total tokens；
- 单次 LLM duration 与安全的 generation summary；
- provider failure category、grounding/photo events；
- validation、route、hotel、weather 和 revision observation，用于 evaluation capture。

这不是完整生产 observability：仓库没有集中式日志/trace/metric backend、可靠产品埋点、生产 dashboard 或已验证的 uptime/latency 指标。

## 8. 配置与安全边界

后端 secret 通过环境变量加载，包括 LLM、Google server key、AMap server key 和 XHS Cookie。当前 settings API 不返回这些 server secrets，只返回 browser-safe 配置与是否已配置的布尔元数据。Google browser key 与 AMap browser key 必须使用域名/API 限制。

公开部署模式会将 runtime settings 设为只读、关闭共享 history（默认）、清理 provider 错误并启用单进程 concurrency/cooldown guard。应用仍没有 authentication、authorization、用户模型或 task ownership enforcement；UUID 和 client identity guard 不是权限系统。

## 9. 持久化与运行时限制

当前 state model 适合单实例演示：

- process memory：活动任务、WebSocket subscriber、lock、dedupe 和 rate guard；
- local JSON：任务结果与版本/patch 元数据；
- browser storage：活动 task、结果过渡状态、locale 和 browser-safe runtime settings；
- Docker named volume：`/app/backend/data`。

仓库没有 SQL/NoSQL database、Redis、broker、durable job queue、migration、backup 证明或多租户数据模型。因此不支持安全多 worker、水平扩容、可靠恢复、跨设备账号、任务取消或强一致分布式修改。

## 10. 部署模型与验证边界

`Dockerfile` 通过 Node 18 构建 Vue，再以 Python 3.10、Gunicorn 和一个 Uvicorn worker 运行 FastAPI；FastAPI 同源托管 `frontend/dist`。`start.sh` 绑定平台提供的 `PORT`，Compose 暴露 7860 并挂载任务数据卷。

`/health` 只证明应用进程可返回配置/示例文件状态，不探测 LLM、Google、AMap、XHS、browser key 或 volume 可写性。仓库证明的是 Docker/Render-compatible 的部署配置，不证明存在健康的线上服务、有效生产配置、TLS/WSS、uptime、流量或 provider 可靠性。README 当前仍标记 public demo URL 为 coming soon。

## 11. 质量基线

STEP 0 在 application baseline `449d0fa584b99f61386b980fe76aac53848de871` 上记录：

- Backend：327 tests，325 passed，1 failed（过时 commit SHA assertion），1 skipped；
- Frontend：34/34 Node tests passed；
- Frontend `npm run build`：通过，但存在 unresolved legacy asset/font 与 oversized chunk warnings；
- `git diff --check`：通过。

这些测试多数不访问真实 provider，前端测试不是完整 browser E2E。详细命令和边界见 `docs/PROJECT_BASELINE.md` 与 `docs/METRICS.md`。

STEP 2C 在 2026-08-16 修复了该过时的 repository-revision test assertion，并重新验证 Backend 327 tests：326 passed、0 failed、1 skipped。STEP 0 结果仍作为历史审计记录保留。

## 12. 当前重要限制

1. 单进程任务架构，不支持安全多 worker/水平扩容。
2. 无 authentication/authorization、用户账户、database 或 durable queue。
3. 进程重启后未完成任务不能恢复。
4. 外部 provider 与线上部署没有 live verification。
5. Grounding 为部分覆盖，不是所有 recommendation/price/prose 的逐字段证据系统。
6. 没有 booking、inventory、authoritative live price、Undo 或可靠产品 analytics。
7. 大型集中模块增加维护风险；没有完整 browser E2E。
8. 测试 import 风格混合，完整 Backend suite 仍需显式设置 `PYTHONPATH=.:backend`。

## 13. 维护规则

未来修改架构说明时，按以下证据顺序核对：当前代码与 Git、`docs/PROJECT_BASELINE.md`、`docs/PROJECT_MASTER.md`、`docs/ARCHITECTURE.md`，最后才是历史文档/注释。任何 provider 或 deployment 描述都必须区分“代码支持/已配置”与“live verified”。任何量化结论必须记录方法、样本、日期和证据位置。
