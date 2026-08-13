# TripStar-AI 当前架构分析

> 分析范围：当前 TripStar-AI 本地仓库；该仓库为 upstream TripStar 的 GPL v2 派生项目。
> 分析日期：2026-08-08
> 当前阶段：STEP 1 — Repository Analysis
> 约束：本文只记录现状，不包含代码改造，也不代表后续方案已经实施。

## 1. Executive Summary

TripStar 是一个前后端分离的 AI 旅行规划应用：Vue 3 单页应用负责结构化旅行输入、异步任务进度、行程展示、地图、知识图谱、预算编辑、图片导出和上下文问答；FastAPI 后端负责任务编排、Agent 调用、外部数据访问、LLM 聚合、Pydantic 校验和本地 JSON 持久化。

当前核心生成链路可以概括为：

```text
结构化表单
  → 异步旅行任务
  → 按城市串行处理
      → 小红书笔记搜索 + LLM 景点提纯 + 地理编码
      → Weather SimpleAgent + 地图工具
      → Hotel SimpleAgent + 地图工具
  → Planner SimpleAgent 一次性生成完整 JSON
  → 本地 JSON 修复与 Pydantic 校验
  → 知识图谱派生
  → WebSocket 返回结果
  → 前端懒加载景点图片、绘制路线并展示
```

项目已经具备适合作为二次开发底座的工程骨架：结构化输入、真实外部数据、异步长任务、上下文 Chat、多城市、地图与持久化。但当前“Agentic”程度有限，主要是工具调用与最终聚合，不包含可验证的决策循环。预算、路线合理性、开放时间、风险、推荐依据和局部修改均没有形成独立的数据模型与闭环。

从 AI Product Manager Portfolio 角度，最值得保留的是“真实信息检索 + 结构化生成 + 长任务体验 + 可视化结果”的完整产品路径；最值得改造的是偏好模型、Planner 工作流、可信度/证据、约束验证和 Chat 的操作能力；最值得新增的是 Critic、自反思、风险检测、推荐解释、局部 What-if planning、评估与产品埋点。

---

## 2. Repository Inventory

### 2.1 根目录

| 文件 | 作用 |
|---|---|
| `README.md` / `README_en.md` / `README_ja.md` | 中、英、日项目说明、部署方式、功能与架构描述 |
| `Dockerfile` | 两阶段构建：Node 构建前端，Python 镜像运行 FastAPI/Gunicorn；运行时额外安装 Node 以执行小红书签名脚本 |
| `docker-compose.yaml` | 单服务部署、环境变量注入、端口 7860、旅行任务数据卷 |
| `start.sh` | 以单 worker Gunicorn + UvicornWorker 启动后端 |
| `LICENSE` | GNU GPL v2 |

### 2.2 前端

```text
frontend/src/
├── main.ts                 # Vue、Router、Ant Design Vue、i18n 初始化
├── App.vue                 # 根布局与 locale/title 同步
├── views/
│   ├── Landing.vue         # 当前实际首页：Hero、Trip Setup、生成进度、历史计划
│   ├── Home.vue            # 旧版/备用首页，当前路由未引用
│   └── Result.vue          # 结果页，承担绝大多数展示和本地编辑逻辑
├── components/
│   ├── NavBar.vue          # 导航、语言切换、运行时配置面板
│   ├── AIChat.vue          # 带当前行程上下文的悬浮问答
│   └── OverviewAttractionCard.vue
├── services/api.ts         # Axios、运行时配置、任务提交、WebSocket、历史查询
├── types/index.ts          # TypeScript 数据结构
├── i18n/                   # zh-CN / en-US / ja-JP 文案
└── styles/global.css       # 大量全局视觉样式
```

### 2.3 后端

```text
backend/app/
├── api/
│   ├── main.py             # FastAPI 应用、CORS、路由、静态前端托管
│   └── routes/
│       ├── trip.py         # 旅行任务、WebSocket、轮询、历史记录
│       ├── chat.py         # 当前行程上下文问答
│       ├── poi.py          # POI 搜索/详情与小红书图片
│       ├── map.py          # 高德 POI/天气/路线接口
│       └── settings.py     # 运行时 Key/模型配置
├── agents/trip_planner_agent.py
│                            # Agent 初始化、研究数据采集、Planner Prompt、JSON 修复
├── services/
│   ├── llm_service.py       # HelloAgentsLLM 单例
│   ├── chat_service.py      # OpenAI-compatible Chat Completions
│   ├── xhs_service.py       # 小红书搜索、详情、提纯、图片、地理编码
│   ├── amap_service.py      # 高德 MCP 服务包装
│   ├── google_map_service.py# Google Places/Geocoding/Directions/Weather
│   ├── map_dispatcher.py    # Google 优先、高德降级的地理编码选择
│   └── knowledge_graph_service.py
├── models/schemas.py        # Pydantic 请求/响应模型
├── config.py                # 环境变量与运行时配置持久化
└── services/xhs_sign/       # 小红书请求签名 JS/Python 实现
```

仓库中未发现数据库迁移、正式测试目录、CI 配置、独立 Prompt 目录、analytics SDK、evaluation harness 或领域级 repository/service 分层。

---

## 3. 当前产品功能

### 3.1 首页与 Trip Setup

当前实际首页为 `Landing.vue`，而不是 `Home.vue`。它提供：

- 单城市或最多 5 个城市的动态输入；每个城市单独设置停留天数。
- 起始日期；结束日期由总天数在前端计算。
- 交通偏好：公共交通、自驾、步行、混合。
- 住宿偏好：经济型、舒适型、豪华酒店、民宿。
- 兴趣标签：历史文化、自然风光、美食、购物、艺术、休闲。
- 自由文本特殊要求。
- 中、英、日界面语言切换，并把当前语言随生成请求发送给后端。
- 分阶段生成进度：景点、天气、酒店、规划。
- 最近成功计划列表与按 Plan ID 找回。
- 在导航栏直接配置 API Base URL、地图 Key、小红书 Cookie、LLM Key/Base URL/Model。

当前输入比“一句话生成行程”更结构化，但尚未覆盖同行人、预算金额/等级、旅行节奏、行动能力、饮食禁忌等完整偏好画像；所有偏好仍是扁平字段，没有 `preference_profile`。

### 3.2 行程生成

- 支持单城市与多城市。
- 从小红书搜索景点攻略并提取真实评价、建议时长、预约需求与提示。
- 查询天气与酒店候选。
- 由最终 Planner 生成逐日景点、三餐、酒店、天气、总体建议和预算。
- 生成过程为异步后台任务，通过 WebSocket 实时返回状态；后端也保留轮询接口。
- 完成任务写入本地 JSON，可用于历史计划与服务重启后的读取。

### 3.3 结果页

`Result.vue` 是一个大型单文件组件，包含：

- Overview：城市、日期、总天数、总体建议以及景点概览轮播。
- Budget：总预算、景点/酒店/餐饮/交通/城际交通分项；支持过滤、排序、改价、删除和恢复。
- Route Map：Google Maps 或高德地图标记、信息窗、路段绘制与失败时直线降级。
- Knowledge Graph：以 ECharts 展示城市、日期、景点、酒店、餐饮、天气、预算和建议的关系图。
- Daily Plan：按天折叠展示城市、移动日、描述、交通、住宿、景点、预约提醒、酒店和餐饮。
- Edit Mode：本地修改景点地址、停留时间、描述，调整顺序或删除景点。
- Weather：逐日天气面板。
- 景点图片：结果生成后按景点名异步从小红书加载。
- 导出：将计划、预算、地图、天气、酒店等拼成 HTML，再用 `html2canvas` 导出图片。
- AI Chat：悬浮式上下文问答。

这些编辑只更新前端状态和 `sessionStorage`，没有保存为后端的新版本，也不会触发路线、预算、天气或其他约束的重新计算。

### 3.4 AI Chat

当前 Chat 不是完全独立的通用机器人：请求会携带完整当前 `trip_plan` 与会话历史。因此它能够回答“为什么、多少钱、是否适合”等行程相关问题。

但它目前是只读问答：

- 不返回结构化 patch。
- 不识别受影响 itinerary nodes。
- 不调用路线、天气、预算或 POI 工具。
- 不修改当前行程。
- 不持久化聊天记录。
- System Prompt 固定要求中文，即使主界面选择英语或日语。

---

## 4. 前端架构

### 4.1 技术栈

- Vue 3.5 + Composition API + TypeScript。
- Vite 6。
- Vue Router 4，只有 `/` 和 `/result` 两条实际路由。
- Ant Design Vue 4 作为基础组件库。
- Vue I18n 9，支持 `zh-CN`、`en-US`、`ja-JP`。
- Axios 作为 HTTP 客户端，原生 WebSocket 接收任务事件。
- ECharts 展示知识图谱。
- AMap JS API 与 Google Maps JS API 展示地图和路线。
- Swiper 用于首页/概览交互。
- html2canvas 用于图片导出。

### 4.2 页面与组件关系

```text
App.vue
└── router-view
    ├── Landing.vue
    │   └── NavBar.vue
    │       └── Runtime Settings Modal
    └── Result.vue
        ├── OverviewAttractionCard.vue
        ├── Google Map / AMap
        ├── ECharts Knowledge Graph
        └── AIChat.vue
```

`Home.vue` 没有被路由使用，代表仓库保留了旧版页面实现。它和 `Landing.vue` 存在重复的 Trip Setup 与生成逻辑，后续应明确废弃或迁移，避免双实现漂移。

### 4.3 状态管理与持久化

项目没有 Pinia/Vuex。状态分布在组件的 `ref/reactive`、浏览器存储和后端任务文件中：

- `sessionStorage`：`tripPlan`、`graphData`、`planId`。
- `localStorage`：locale、API Base URL、高德 JS Key、Google Maps Key。
- 组件内状态：编辑模式、预算删除恢复栈、Chat history、图片缓存、当前 Tab。
- 后端 JSON：任务请求、状态、结果。

优点是 MVP 简单；问题是行程修改没有版本模型，刷新/跨设备/多人场景不可追踪，也无法计算“修改率”“接受率”等产品指标。

### 4.4 API 客户端行为

`api.ts` 每次请求动态读取 API Base URL。生成流程为：

1. `POST /api/trip/plan` 获得 `task_id` 和 `ws_url`。
2. 连接 `/api/trip/ws/{task_id}`。
3. 监听阶段事件，完成时解析 `TripPlanResponse`。
4. 写入 `sessionStorage`，跳转结果页。

虽然存在 `pollTaskStatus()`，当前 `generateTripPlan()` 在 WebSocket 出错或关闭时直接失败，没有自动切换到轮询。因此“轮询兼容”是后端能力，不是完整的前端降级闭环。

### 4.5 结果页的架构边界

`Result.vue` 同时承担数据恢复、编辑、预算计算、图片加载、地图 provider 选择、路线绘制、知识图谱渲染、天气派生、导出和页面 UI。它功能丰富，但职责过多，是后续迭代的主要复杂度风险。

前端 TypeScript `Attraction` 类型还缺少后端已有的 `reservation_required` 与 `reservation_tips` 字段；模板直接使用这些字段，说明类型定义与实际 API 已发生漂移，构建时可能出现类型问题。Docker 构建也明确跳过 `vue-tsc`，进一步掩盖此类问题。

---

## 5. 后端架构

### 5.1 FastAPI 应用层

`backend/app/api/main.py`：

- 注册 Trip、POI、Map、Chat、Settings 五组 API。
- 配置 CORS。
- 提供 `/health`、Swagger `/docs` 与 ReDoc。
- 兼容某些代理给路径加前缀的场景，将含 `/api/` 的路径重写为真实 API 路径。
- Docker 生产环境中直接托管前端 `dist`，并提供 SPA fallback。

### 5.2 任务系统

`trip.py` 使用：

- 进程内 `_tasks` 字典保存活动任务。
- `asyncio.create_task()` 启动长时间规划。
- 每个订阅者使用 `asyncio.Queue` 接收 WebSocket 广播。
- 本地 `backend/data/trip_tasks/{task_id}.json` 持久化请求、状态与完整结果。
- 服务重启后，未完成任务被标记为失败，不会恢复执行。
- 历史列表直接扫描 JSON 文件并按修改时间排序。

这是适合单实例 Demo 的轻量方案。由于部署使用单 Gunicorn worker，它与当前内存订阅模型一致；但不支持多 worker、横向扩容、可靠队列、任务取消、幂等、租户隔离或用户权限。

### 5.3 配置系统

`config.py` 从环境变量和本地 `runtime_settings.json` 读取配置，并允许前端更新。保存后会重置 LLM、地图服务和 Planner 单例以热生效。

当前配置 API 会把 API Key 和小红书 Cookie 原样返回给前端，并以明文 JSON 持久化；API 没有认证。这适合纯本地 Demo，不适合公网部署。`google_maps_proxy` 存在于返回结构和前端类型中，但 `RuntimeSettingsPayload` 未定义该字段，因此前端提交的 proxy 会被 Pydantic 忽略，存在前后端契约不一致。

### 5.4 服务层

- `llm_service.py`：创建 `HelloAgentsLLM` 单例，并替换底层 OpenAI client 以设置浏览器 User-Agent，支持 OpenAI-compatible provider。
- `xhs_service.py`：小红书 API 签名、搜索、详情、SSR 降级、LLM 提纯、搜图、地理编码。
- `amap_service.py`：包装 `amap-mcp-server`；部分通用 API 的返回解析仍为 TODO，因此 `/api/map/poi`、`/weather`、`/route` 可能返回空结构。
- `google_map_service.py`：同步 HTTP 客户端封装 Places、Geocoding、Directions、Weather。
- `map_dispatcher.py`：有 Google Key 时优先 Google；一次地理编码失败后全局短路并改用高德。
- `knowledge_graph_service.py`：从最终 TripPlan 确定性派生图数据，不调用 LLM。
- `chat_service.py`：直接通过 `httpx` 调用 `/chat/completions`，没有复用 `HelloAgentsLLM` 或 Agent tools。

### 5.5 并发与性能现状

当前 `plan_trip()` 是“按城市串行、每个城市内部也依次执行景点 → 天气 → 酒店”。README 的架构图写有 `asyncio.gather` 并发阶段，但当前本地实现没有对这三类研究任务并发，也没有跨城市并发。阻塞调用通过 `asyncio.to_thread` 移出事件循环。

每次城市研究最多读取 4 篇小红书笔记；每个景点再进行地理编码；最终 Planner 是长 JSON 生成，默认规划超时 180 秒，超时仅重试一次。整体延迟和 LLM 成本没有结构化记录。

---

## 6. Agent / LLM 架构

### 6.1 实际 Agent 拓扑

```text
MultiAgentTripPlanner（Python orchestration）
├── Weather SimpleAgent
│   └── Google native adapter 或 AMap MCP tool
├── Hotel SimpleAgent
│   └── Google native adapter 或 AMap MCP tool
├── XHS research pipeline（服务，不是 SimpleAgent）
│   ├── 小红书 Search/Detail
│   ├── Inline LLM extraction
│   └── Google/AMap geocoding
└── Planner SimpleAgent
    └── 无工具；接收前三类结果文本并一次性输出完整 TripPlan JSON
```

旧的 Attraction Agent 常量保留为空字符串，但已经弃用。路线没有独立 Route Agent；预算没有独立 Budget Agent；Research 也不是一个具备工具选择/循环/状态的 Agent。

### 6.2 Agent 工作方式

- Weather Agent 和 Hotel Agent 的 Prompt 强制模型输出 HelloAgents 特定的单行 `[TOOL_CALL:...]` 格式。
- Google provider 使用代码内的鸭子类型适配器模拟 MCPTool，并暴露 text search、weather、geo 三个子工具。
- 高德 provider 启动外部 `uvx amap-mcp-server`。
- Planner Agent 不调用工具，只消费预先拼好的上下文。
- Planner 输出经过多轮字符串清洗、引号修复、截断补全、正则提取；全部失败时再调用 LLM 修复 JSON。
- 最终由 Pydantic `TripPlan` 做结构校验。

### 6.3 当前并非完整 Agentic Planning 的原因

当前工作流有多 Agent 名称和工具调用，但决策闭环仍是单向的：

```text
Research → Planner → Parse → Display
```

不存在：

- Preference Agent 与结构化用户画像。
- 显式 task graph / itinerary node graph。
- Planner → Critic → Planner 反思循环。
- 基于规则或工具的事实验证。
- 预算超限后的自动回调与重规划。
- 路线距离矩阵或优化算法。
- 开放时间、预约、天气、时间冲突的统一 constraint engine。
- Chat 驱动的局部 patch 和受影响节点重算。
- 证据、来源、抓取时间或数据新鲜度模型。

因此更准确的产品描述应是“多源研究 + 多个工具型 Agent + LLM 聚合规划”，而不是已经完成可验证、自反思的自治旅行决策系统。

---

## 7. Prompt 设计

### 7.1 Weather Prompt

职责单一，强制调用指定 provider 的天气工具，不允许直接编造。Prompt 对工具名称和单行调用格式约束很强，适合当前 HelloAgents 的文本协议。

局限：只传城市，没有旅行日期范围；结果是否覆盖用户日期依赖外部服务与最终 Planner 自行映射。

### 7.2 Hotel Prompt

要求调用地图 text search，关键词固定为“酒店/宾馆”。用户的 accommodation 偏好出现在自然语言 query 中，但工具调用示例与系统约束没有预算、位置、评分、设施、同行人等结构化过滤条件。

### 7.3 小红书提纯 Prompt

从最多 4 篇真实笔记中提取：

- `name`
- `name_zh`
- `name_en`
- `reason`
- `duration`
- `reservation_required`
- `reservation_tips`

它要求严格 JSON 数组，并对目标语言做翻译，同时保留中英文官方名以适配地图地理编码。

这是当前最接近“真实旅行经验结构化”的 Prompt，也是未来推荐理由与证据链的重要资产。但提取结果没有 source URL、note ID、发布时间、原文证据、热度、样本数量或冲突观点；`reason` 在后续 Planner 中只作为上下文文本，最终 Attraction schema 也没有独立保存该字段，因此真实评价的可追溯性会丢失。

### 7.4 Planner Prompt

Planner System Prompt 给出完整 JSON 示例并重点约束：

- 每天 2–3 个景点。
- 每天早中晚三餐。
- 酒店、景点、天气、预算字段。
- 数值字段必须是纯数字。
- 预约信息需要透传。
- 多城市移动日与城际交通预算。
- 禁止编造具体车次、班次和时间。

运行时 User Prompt 拼接表单数据以及每个城市的景点、天气和酒店文本，并要求考虑距离、交通和真实坐标。

主要问题：

- 单次 Prompt 同时承担选点、排序、时间分配、餐饮、酒店、预算和文案，认知负担高。
- 没有 itinerary 时间字段，无法真正验证时间与开放时间冲突。
- 没有用户预算输入，预算只能由模型估算，无法判断是否超预算。
- “考虑距离”只是自然语言要求，没有距离矩阵或路线工具结果。
- “真实准确坐标”由模型和研究上下文共同保证，但没有最终校验。
- 允许在信息不足时用“保守、通用建议补齐”，这会提升完成率，也会引入无法追溯的内容。
- Prompt 要求预约字段透传，但前后端数据契约不完整，来源也没有保留。

### 7.5 Chat Prompt

System Prompt 要求基于完整行程 JSON 回答；若上下文没有信息，可以基于常识推断，但必须声明。这一边界表达是合理的。

局限是固定中文、200 字限制、无工具、无 citation、无结构化 action；因此它只能解释当前 JSON，不能安全地执行行程修改或实时决策。

### 7.6 JSON 修复 Prompt

当本地修复全部失败时，系统只给模型 JSON 开头 500 字符和尾部 2000 字符，要求补全为完整 JSON。这可能恢复语法，却无法恢复被省略的中间完整语义；修复后的内容也没有和原始约束再次做业务级校验。

---

## 8. API 与第三方数据源

### 8.1 API 清单

| Method | Endpoint | 作用 | 当前主要调用方 |
|---|---|---|---|
| `GET` | `/health` | 应用健康检查 | API client 可调用 |
| `POST` | `/api/trip/plan` | 创建异步旅行任务 | 首页 |
| `WS` | `/api/trip/ws/{task_id}` | 推送任务进度与结果 | 首页、结果恢复 |
| `GET` | `/api/trip/status/{task_id}` | 查询任务状态/结果 | 结果页、兼容轮询 |
| `GET` | `/api/trip/history` | 最近成功计划摘要 | 首页 |
| `GET` | `/api/trip/health` | Planner/Agent 健康检查 | 运维/调试 |
| `POST` | `/api/chat/ask` | 基于当前行程的问答 | AIChat |
| `GET` | `/api/poi/photo` | 按景点名获取小红书图片 | 结果页 |
| `GET` | `/api/poi/search` | POI 搜索 | 当前主生成链路未使用 |
| `GET` | `/api/poi/detail/{poi_id}` | POI 详情 | 当前主生成链路未使用 |
| `GET` | `/api/map/poi` | 高德 POI 搜索 | 当前前端未直接使用；解析仍 TODO |
| `GET` | `/api/map/weather` | 高德天气 | 当前前端未直接使用；解析仍 TODO |
| `POST` | `/api/map/route` | 高德路线 | 当前结果地图使用前端 JS SDK，不调用此接口；解析仍 TODO |
| `GET` | `/api/map/health` | 地图 MCP 健康检查 | 运维/调试 |
| `GET` | `/api/settings` | 获取运行时配置 | NavBar |
| `PUT` | `/api/settings` | 保存并热应用配置 | NavBar |

### 8.2 第三方数据与服务

| 来源/服务 | 用途 | 集成方式 | 关键边界 |
|---|---|---|---|
| OpenAI-compatible LLM | Agent、景点提纯、Planner、Chat、JSON 修复 | HelloAgentsLLM 或直接 HTTP Chat Completions | 依赖第三方模型结构化输出；无 token/cost/latency 记录 |
| HelloAgents | SimpleAgent、MCPTool、文本工具调用协议 | Python package | 当前主要用于天气、酒店和 Planner 封装 |
| 小红书 | 攻略正文、真实评价、预约提示、景点图片 | 本地 JS 签名直连 API，详情失败时 SSR | Cookie/风控不稳定；合规、来源展示、缓存与数据新鲜度需后续评估 |
| 高德地图 Web Service | 地理编码降级、天气 HTTP 降级 | REST | 无 Key 时返回北京默认坐标，可能产生静默错误 |
| 高德 MCP | POI、酒店、天气、路线工具 | `uvx amap-mcp-server` | 通用 service 层多个解析函数仍是 TODO |
| 高德 JS API | 结果页地图、Marker、路线、截图 | 浏览器 SDK | 依赖前端 Key；浏览器端路线与后端规划逻辑分离 |
| Google Maps Platform | Places、Geocoding、Directions、Weather | 后端 REST + 前端 JS SDK | 有 Key 就优先；provider 选择未按目的地自动判断 |
| ECharts | 知识图谱 | 前端渲染 | 图是 TripPlan 的可视化派生，不是推理用知识图谱 |
| qrserver.com | 导出图片中的 GitHub QR code | 外部图片 URL | 导出依赖外部服务 |
| Google Fonts / Creative Tim cloud asset | 字体、首页云雾素材 | 外链 | 离线与可用性依赖外部资源 |

### 8.3 数据可信度现状

- 小红书是唯一包含真实用户经验的研究来源。
- 官方旅游网站、开放时间、门票官方渠道、天气风险和预约状态没有统一接入。
- POI rating 字段存在，但主生成链路未稳定填充。
- 没有来源 URL、引用片段、抓取时间或数据版本。
- 小红书搜图默认拿首个有效笔记的第一张图，不能保证版权、地点准确性或图片质量。
- 没有对不同来源的矛盾信息进行比较或置信度计算。

---

## 9. 核心数据结构

### 9.1 TripRequest

```text
city                  单城市兼容字段
cities[]              { city, days }
start_date / end_date YYYY-MM-DD 字符串
travel_days           1–30
transportation        字符串
accommodation         字符串
preferences[]         扁平兴趣标签
free_text_input       自由文本要求
language              zh/en/ja 等
```

Pydantic validator 会在 `city` 和 `cities` 之间做单城市兼容，但没有校验：城市天数总和是否等于 `travel_days`、结束日期是否与天数一致、起止日期是否合法、目的地是否为空。

### 9.2 TripPlan

```text
TripPlan
├── city / cities[]
├── start_date / end_date
├── days[]
│   ├── date / day_index / city
│   ├── is_transfer_day / transfer_info
│   ├── description / transportation / accommodation
│   ├── hotel?
│   ├── attractions[]
│   └── meals[]
├── weather_info[]
├── overall_suggestions
└── budget?
```

### 9.3 Attraction

已有字段：名称、地址、坐标、建议停留分钟、描述、类别、评分、图片、POI ID、票价、预约布尔值、预约提示。

对新产品定位而言，目前缺少：

- start/end time 与开放时间。
- 来源与证据。
- 推荐理由。
- heuristic recommendation score 及其分项。
- 用户偏好匹配标签。
- 前后景点交通时间/距离。
- 室内/室外、天气敏感性、步行负担、适合人群。
- 预约渠道、截止时间与事实状态。
- 风险项与替代 POI。

### 9.4 DayPlan

当前是“日级描述 + 景点数组 + 餐饮数组 + 酒店”，不是 timeline graph。景点和餐饮没有统一 node ID、明确时间、依赖关系或变更来源，因此无法准确做局部重规划和冲突检测。

### 9.5 Budget

只有景点、酒店、餐饮、市内交通、城际交通和总额。没有币种、人数、预算上限、购物预留、价格区间、估算依据、置信区间或按人/按房/按晚语义。

结果页会依据行程明细重新求和，但生成时的总预算来自 Planner LLM，而不是独立预算服务。

### 9.6 KnowledgeGraphData

`nodes + edges + categories` 是 ECharts 展示结构。节点由 TripPlan 确定性生成；它没有图数据库、检索能力、语义 embedding、实体消歧或推理用途，因此当前应称为“行程关系可视化”，不应等同于产品路线图中的 Knowledge Graph 能力。

### 9.7 Task 与 Chat

- Task 保存 `task_id / plan_id / status / stage / progress / message / result / error / request_payload`。
- Chat request 保存本次 message、完整 trip_plan 和前端提供的 history；Chat 不绑定 `plan_id`，后端不持久化。

---

## 10. 完整数据流

### 10.1 创建旅行计划

1. 用户在 `Landing.vue` 填写城市、日期、交通、住宿、兴趣和特殊要求。
2. 前端计算总天数和结束日期，组装 `TripFormData`，附带当前语言。
3. `POST /api/trip/plan` 通过 Pydantic 创建 `TripRequest`。
4. 后端生成 8 位 UUID 任务 ID，保存初始状态和请求 JSON。
5. FastAPI 用 `asyncio.create_task` 启动后台规划，并立即返回 task/plan ID 和 WS URL。
6. 前端建立 WebSocket，按阶段更新 loading stepper。
7. 后端获取或初始化单例 `MultiAgentTripPlanner`，根据是否配置 Google Maps Key 选择 provider。
8. 对每个城市依次执行：
   1. 小红书搜索“城市 + 第一个兴趣标签 + 旅游 + 景点攻略”。
   2. 读取最多 4 篇笔记，尝试原生详情，失败则 SSR。
   3. Inline LLM 将笔记提取为景点 JSON。
   4. 每个景点通过 Google 或高德补坐标。
   5. Weather Agent 调真实工具；Google 失败时可降级高德 REST。
   6. Hotel Agent 调地图 text search 工具。
9. 后端把所有城市研究结果和用户请求拼成 Planner query。
10. Planner 一次性生成完整行程 JSON；超时重试一次。
11. 后端执行本地 JSON 清理/修复；必要时用 LLM 修复；Pydantic 转为 `TripPlan`。
12. 后端补齐遗漏的 `cities` 与单城市 `day.city`。
13. `knowledge_graph_service` 从 TripPlan 构建图数据。
14. 任务结果写入本地 JSON，并通过 WS 推送 `TripPlanResponse`。
15. 前端写入 sessionStorage 后进入 `/result`。

### 10.2 结果展示

1. Result 优先读取 route query/sessionStorage；若只有 Plan ID，则调用状态接口恢复完整结果。
2. 前端从 TripPlan 派生 Overview、Budget details、Weather UI 与各 Tab。
3. 对全部景点并发调用 `/api/poi/photo`，将 URL 缓存在组件内。
4. 地图初始化时优先使用配置的 Google Key，否则使用高德；浏览器端根据当日景点顺序请求 Directions/Driving/Walking。
5. 知识图谱从后端返回的 graph data 渲染。
6. 用户手动编辑时只改变前端 TripPlan 副本并写回 sessionStorage。

### 10.3 上下文问答

1. 用户打开 `AIChat.vue`，输入问题或选择快捷问题。
2. 前端发送问题、完整当前 TripPlan 和此前 history。
3. Chat service 把 TripPlan 作为一个 user context message 注入 Chat Completions。
4. LLM 返回自然语言，前端追加到内存 history。
5. 对话不会改变 TripPlan，也不会保存到任务文件。

### 10.4 手动修改与预算

1. 景点移动、删除和字段编辑由 Result 本地操作。
2. 预算项目编辑/删除会在浏览器中重新汇总总额；删除项进入临时恢复列表。
3. 这些变更不回传后端，不重新计算地图研究结果、天气适配或 Planner 约束，也没有变更日志。

---

## 11. 当前项目优势

### 11.1 产品层

- 已经从纯聊天输入向结构化 Trip Setup 迈进。
- 覆盖“输入 → 等待 → 结果 → 追问 → 手动编辑 → 导出”的完整 Demo 路径。
- 小红书真实游记与预约提醒解决了一部分“纯模型凭空生成”的问题。
- 多城市、天气、酒店、预算、地图和历史计划让结果比简单 itinerary 更完整。
- 长任务有明确阶段反馈，降低用户等待焦虑。
- Chat 已读取当前行程上下文，为后续 Copilot 操作模式打下基础。

### 11.2 AI/数据层

- 将小红书非结构化内容提纯为结构化候选，再交给 Planner，而不是让 Planner 完全依赖参数知识。
- Prompt 明确禁止编造具体车次，并对缺失信息、数字字段和预约透传做了边界约束。
- Pydantic schema 和多层 JSON 容错提高了模型输出可用率。
- 地图双 provider 与天气降级增强了 Demo 可用性。

### 11.3 工程层

- 前后端分离，领域 schema 相对清晰。
- 异步任务规避长 LLM 请求的网关超时。
- WebSocket 推送和 JSON 持久化支持进度与历史恢复。
- Docker 可单容器交付前后端。
- 国际化已经贯穿 UI、Planner 输出和知识图谱标签。

---

## 12. 当前问题与风险

### 12.1 产品问题

- 用户偏好过薄：没有同行人、预算、节奏、饮食、行动能力和 avoid tags 的结构化模型。
- 结果是“看起来完整的攻略”，不是显式的决策过程；用户看不到为何选、为何排序、证据是什么。
- 没有多方案比较、推荐得分、冲突提示或替代方案。
- Chat 只能解释，不能修改；手动编辑也不会触发 AI 重算。
- 没有将用户接受、删除、修改、导出等行为记录为 analytics event。

### 12.2 AI 能力问题

- Planner 单次生成承担过多职责，错误难定位、难评估、难局部重试。
- 没有 Critic 或 deterministic validator；Pydantic 只验证形状，不验证旅行逻辑。
- 没有 route matrix，所谓路线优化主要依赖模型判断和结果页事后绘图。
- 没有开放时间字段，无法检测闭馆、迟到或时间冲突。
- 没有用户预算上限，无法真正执行“超预算自动调整”。
- 天气和酒店 Agent 返回的是文本上下文，缺少统一规范化和质量检查。
- 研究只使用第一个 preference 作为小红书关键词，其他兴趣可能被弱化。
- 地理编码失败时高德返回固定北京坐标，可能让错误静默进入计划和地图。
- JSON 修复优先保证可解析，不保证修复后仍忠于事实和原始约束。
- Chat 允许常识推断，但没有工具或来源，实时问题存在过时与幻觉风险。

### 12.3 数据与可信度问题

- 没有 source/citation schema，真实小红书评价在最终计划中不可追溯。
- 没有官方开放时间、预约、门票、天气等事实的来源优先级。
- 没有数据新鲜度、缓存、去重、冲突合并与来源可靠性评分。
- 推荐和预算没有 uncertainty 表达。
- 小红书直连依赖 Cookie 与签名，存在稳定性、平台条款、内容版权和生产合规风险。

### 12.4 工程问题

- `Result.vue` 和 `global.css` 体积过大，业务逻辑与展示高度耦合。
- 当前路由未使用 `Home.vue`，形成重复实现。
- 前后端类型已经漂移，Docker 构建跳过类型检查。
- 多个 AmapService 返回解析是 TODO，部分公开 API 名义可用但实际返回空数据。
- WebSocket 前端没有轮询降级。
- 本地 JSON + 内存队列只能支撑单实例 Demo。
- 没有自动化测试、evaluation、CI、observability、结构化日志、trace 或 LLM usage 统计。
- Settings API 未认证且回传明文密钥；运行时配置也明文落盘。
- `RuntimeSettingsPayload` 与前端 settings 类型不一致，Google proxy 无法正常保存。
- 同步 HTTP client、线程池、外部 MCP 子进程和单例资源缺少统一生命周期管理。

### 12.5 README 与代码差异

- README 表示研究阶段使用 `asyncio.gather` 并发；当前代码实际按城市、按研究类型串行。
- README 称“路线编排计算两两景点距离和最优顺序”；当前 Planner 没有显式距离矩阵，路线 API 绘制主要发生在结果页。
- README 将 Knowledge Graph 描述为“图数据库雏形”；当前实现是从 TripPlan 派生的 ECharts 展示数据。
- README 强调轮询系统；当前主前端使用 WebSocket，轮询只作为接口存在，未在 WebSocket 失败时自动接管。

---

## 13. 从 AI PM Portfolio 角度的取舍

### 13.1 值得保留

| 能力 | 保留原因 |
|---|---|
| Vue + FastAPI 前后端分离 | 架构易理解、适合快速迭代和作品演示 |
| 结构化 Trip Setup | 比一句话 Planner 更能体现需求理解与产品设计 |
| 异步任务 + 分阶段进度 | 直接解决 AI 长延迟体验问题 |
| Pydantic 结构化结果 | 是 Agent 分工、评估和局部修改的基础 |
| 小红书研究管线 | 体现真实用户经验与中国旅行决策场景的差异化 |
| Google/高德双地图 | 兼顾境内外目的地与可视化验证 |
| 预约信息字段 | 与“可执行行程”价值直接相关 |
| 上下文 Chat | 可演进为 Travel Copilot，而不是另建孤立机器人 |
| 本地任务历史 | 可扩展为 plan version、实验与 analytics 基础 |
| 多语言骨架 | 保留工程能力，但 MVP 产品界面可优先中文 |

### 13.2 值得改造

| 当前能力 | 应改造方向 | 产品价值 |
|---|---|---|
| 扁平 TripRequest | Structured Preference Profile | 明确用户约束，支持解释与评估 |
| 单次 Planner | 分阶段可观测 workflow | 降低错误耦合，便于局部重试和 PM 展示 |
| 文本研究结果 | 标准化 POI evidence model | 保留来源、时间与观点，建立可信度 |
| LLM 路线判断 | Route service + constraint checks | 将“看起来合理”变为可验证 |
| LLM 预算 | Budget calculator + user budget | 支持超预算检测与自动调整 |
| 只读 Chat | Intent → impacted nodes → patch → recompute | 实现真正 What-if planning |
| 前端本地编辑 | Plan version / change log | 支持回滚、指标和解释 |
| 关系图展示 | 降级为辅助可视化或后置 P2 | 避免为技术展示牺牲核心用户价值 |
| 运行时明文配置 | 本地开发与生产配置分离 | 提高作品的安全可信度 |
| 大型 Result.vue | 领域组件与 composable 拆分 | 支撑后续高频产品迭代 |

### 13.3 值得新增

以下均为后续方向，当前仓库尚未实现：

- Preference Agent：自然语言特殊需求转结构化 profile，并保留原文与解析置信信息。
- Research evidence：官方来源、地图 POI、真实评价的统一结构与引用。
- Route Agent：距离矩阵、开放时间、节奏和移动成本约束。
- Budget Agent/Calculator：人数、币种、预算上限、分类与调整策略。
- Travel Critic：规则校验 + LLM 语义校验，输出结构化 risk list。
- Planner → Critic → Planner：限制循环次数，并记录修正前后差异。
- Recommendation reason：绑定 preference match、route logic 和 evidence。
- Recommendation score：明确为 heuristic score，展示分项而非伪概率。
- Conflict engine：时间、交通、开放时间、预算、天气、预约风险。
- What-if patch：节点级影响分析和局部重规划。
- 三方案决策模式：经典、小众、松弛的可比较指标。
- Analytics：生成、接受、删除、修改、Chat、导出等事件。
- Offline evaluation：偏好理解、POI 相关性、路线、预算、约束和幻觉。
- LLM observability：模型、prompt version、延迟、token、成本、失败阶段与重试。

---

## 14. 面向后续重构的关键架构结论

1. 不需要推翻技术栈。现有 Vue/FastAPI/Schema/异步任务可以继续作为 MVP 底座。
2. 主要问题不在“Agent 数量少”，而在中间决策对象缺失。优先建立 Preference、POI Evidence、Itinerary Node、Constraint/Risk、Plan Version，而不是先增加更多自由对话 Agent。
3. Critic 应结合确定性规则与 LLM。日期、距离、预算、开放时间等优先规则计算；偏好冲突、体验节奏等再交给模型判断。
4. Planner 输出不应继续一次性承担全部事实与计算。研究事实、预算、路线和风险应分别结构化，再由 Planner 做选择与解释。
5. Chat 的核心升级不是换一个更强模型，而是赋予读取当前 plan version、生成 patch、触发局部重算和返回 diff 的能力。
6. “为什么推荐”和 score 必须能追溯到用户偏好、路线指标与来源证据，不能只是模型生成的一段文案。
7. Knowledge Graph 视觉效果强，但当前对核心决策价值有限，符合 P2；不要让它抢占 P0 的约束、Critic 和修改闭环资源。
8. 为 Portfolio 叙事，应保留每次决策的输入、输出、风险、修正和指标，而不只是最终漂亮页面。

---

## 15. 本阶段结论

TripStar 已经是一个可运行的 AI 旅行规划 Demo，而不是空壳：它有真实小红书研究、地图工具、天气、酒店、异步生成、结构化行程、预算、历史、地图、知识图谱、导出与上下文问答。

但它离“AI 个性化旅行决策助手”的差距也很明确：当前系统主要优化“生成一份完整行程”，尚未真正优化“帮助用户做可信、可比较、可调整的决策”。后续产品升级的核心不应是堆叠更多 UI 或 Agent 名称，而应围绕以下闭环：

```text
理解约束
→ 收集可追溯证据
→ 生成候选决策
→ 用规则和 Critic 验证
→ 修正并解释
→ 接受用户局部修改
→ 只重算受影响部分
→ 记录效果并持续评估
```

本文完成后，本阶段停止；未修改任何现有业务代码，未安装依赖，未执行后续功能开发。
