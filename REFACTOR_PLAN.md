# 旅伴 AI：TripStar 产品重构计划

> 基于：`CURRENT_ARCHITECTURE.md` 与当前本地 TripStar repository
> 阶段：STEP 2 — Product Refactor Planning
> 日期：2026-08-08
> 本文性质：产品与技术实施蓝图，不代表任何功能已经开发
> 本阶段约束：只新增本文档，不修改现有业务逻辑、不安装依赖、不删除功能、不实现 UI

## 0. Executive Decision

新的产品暂定名为 **「旅伴 AI」**，目标不是把 TripStar 汉化，也不是增加更多看起来像 Agent 的类，而是把现有的“AI 生成旅行攻略”升级为“AI 辅助用户做旅行决策”。

核心重构原则：

1. 先建立可解释、可校验、可修改的领域数据，再增加 Agent。
2. 事实查询、计算和约束检测优先使用 deterministic service；LLM 负责理解、权衡、解释和生成候选方案。
3. Planner 不能直接把第一版结果交给用户，必须经过规则校验和 Critic，并保留修改依据。
4. Chat 不再只回答问题，而要能产生结构化 PlanPatch；后端决定 patch 是否安全、需要重算哪些节点。
5. 推荐指数是产品内部 heuristic score，不宣称为模型概率。
6. 所有推荐理由必须能回指用户偏好、路线事实或来源证据；没有依据时应显式降级。
7. P0 只证明一个闭环：**理解用户 → 有依据地规划 → 检查问题 → 修正 → 解释 → 接受修改**。

目标 P0 数据流：

```text
Trip Setup
  → PreferenceProfile
  → POI Research + POIEvidence
  → Draft ItineraryNodes
  → Deterministic Rule Validator
  → LLM Travel Critic
  → Planner Revision（最多一次）
  → Final Plan + Reasons + Scores + Risks
  → Travel Copilot
  → PlanPatch
  → Validate + Apply + Return Diff
```

---

## 1. 产品目标

### 1.1 要解决的核心用户问题

目标用户已经能从小红书、地图、大众点评、酒店平台和官方渠道找到大量信息。真正的问题不是“没有攻略”，而是：

- 信息分散在多个平台，整理成本高。
- 热门攻略互相矛盾，用户无法判断适不适合自己。
- 景点列表容易获得，但开放时间、预约、交通、预算、天气和同行人限制很难一起权衡。
- 通用 AI 可以快速生成 itinerary，但事实不透明、理由不清楚、修改一次常常整份重写。
- 用户在规划中会不断改变主意，却无法预知某个改变对路线、预算和其他日期的连锁影响。

旅伴 AI 要解决的是：

> 把分散旅行信息转化为符合个人约束、能够解释、能够检查、能够局部修改的旅行决策。

### 1.2 与当前 TripStar 的差异

| 维度 | 当前 TripStar | 旅伴 AI 目标 |
|---|---|---|
| 产品任务 | 生成一份完整行程 | 帮助用户理解、比较、选择和调整方案 |
| 用户输入 | 城市、日期、交通、住宿、兴趣、自由文本 | 完整 PreferenceProfile，区分硬约束与软偏好 |
| 研究结果 | 小红书内容提纯为 Planner 文本上下文 | 保存 POI evidence、来源、时间、可信度和冲突信息 |
| 规划方式 | 单次 Planner 输出完整 JSON | Draft → Rule Validator → Critic → Revision |
| 路线 | Prompt 要求考虑距离，前端事后绘图 | 距离/时间服务先计算，Planner 基于可验证数据决策 |
| 风险 | 预约提醒字段 | 时间、交通、开放时间、预算、天气、预约、偏好冲突统一 RiskItem |
| 推荐解释 | 景点描述 | 基于偏好、路线和证据的 RecommendationReason |
| 推荐指数 | 无 | 有透明分项的 heuristic RecommendationScore |
| Chat | 读取当前计划的只读问答 | 生成 PlanPatch，验证后局部修改并返回 diff |
| 修改 | 前端本地字段编辑 | 版本化、可回滚、可追踪影响范围 |
| 产品学习 | 无 analytics/evaluation | 指标、离线评估和 LLM observability |

### 1.3 目标用户

核心用户：

- 20–35 岁中文年轻自由行用户。
- 熟悉小红书、大众点评、地图和酒店平台。
- 愿意自己做决定，但不愿花数小时整合信息。
- 常见场景是情侣、朋友、独自旅行和年轻家庭；P0 也支持带父母、带儿童的基本约束。
- 关注真实体验、路线效率、预算和可拍照/可分享性。

非首要用户：

- 需要专业签证、保险、医疗、极限运动安全建议的高风险旅行者。
- 需要企业差旅审批或复杂团队协作的用户。
- 希望系统代订机酒、代付款或保证实时票务的用户。

### 1.4 核心价值主张

主价值主张：

> 不只是帮你排景点，而是结合你的预算、节奏、同行人和真实旅行信息，解释为什么这样安排，并在你改变主意时只调整受影响的部分。

三个可感知的产品承诺：

1. **更适合我**：把自然语言特殊需求转成结构化约束，并在结果中逐项体现。
2. **更可信**：展示推荐依据、数据来源和风险，不把 heuristic score 伪装成模型概率。
3. **更好改**：用户说“第二天轻松一点”时返回局部变更与影响说明，而非整份重写。

---

## 2. MVP 范围与优先级

### 2.1 P0：证明核心决策闭环

P0 必须能在一条完整演示路径中被用户感知。

| 能力 | P0 边界 | 明确不做 |
|---|---|---|
| 中文主界面 | 中文为默认；重写首页与结果页的核心文案和信息层级 | 不删除现有 i18n，不追求所有语言同步完成 |
| Structured Preference Profile | 同行人、预算等级/金额、节奏、兴趣、饮食、行动能力、avoid tags、自由文本解析 | 不做长期跨旅行用户画像或 embedding 推荐 |
| Preference Parsing | LLM 将自由文本补充为结构化字段；用户提交前可确认/纠正 | 不自动覆盖用户显式选择 |
| Planner + Critic | 生成 draft；规则校验；Critic 判断体验问题；最多一次修订；保留 audit summary | 不做无限反思循环或多个 Critic 辩论 |
| Recommendation Reason | 每个核心 POI 至少一个偏好理由和一个路线/证据理由；缺失时明确显示 | 不生成无法追溯的营销文案 |
| Recommendation Score | 0–100 heuristic score；展示偏好、路线、时间、预算、证据分项 | 不称“准确率”“概率”或“模型信心” |
| Travel Risk / Conflict Detection | 时间、交通、开放时间、预约、天气、预算和偏好冲突使用统一 RiskItem | 无可靠数据时不假装检测通过 |
| AI Chat 修改行程 | 支持删除 POI、换 POI、调整当日强度、增加室内/购物节点等有限意图；返回 patch preview | 不支持任意自然语言直接写入整份 JSON |
| 结果页 | Overview、AI Summary、Map、Daily Timeline、Budget、Risk、Suggestions、Copilot | 不在 P0 重做高级知识图谱 |

P0 成功定义：一个代表性测试用例能从结构化偏好生成计划，暴露至少一种可解释的风险，经过 Critic 修订，并通过 Chat 对某一天执行可预览、可确认的局部修改。

### 2.2 P1：增强决策深度和可修改性

| 能力 | 范围 |
|---|---|
| Budget constraint | 明确人数、币种、总预算/每日预算；deterministic 汇总；超限时提出可执行降级策略 |
| What-if local replanning | 识别天气、预算、疲劳、删除 POI 等影响图，只重算目标日和相邻路线 |
| 多方案比较 | 经典、小众、松弛三个候选，共用一致指标：预算、步行量、景点数、热度、交通时间、风险 |
| Plan versioning | 初版、Critic 修订版、用户 patch 版；支持 diff、回滚和当前版本指针 |
| 证据增强 | 官方开放时间/预约信息优先；保存来源新鲜度和冲突标记 |

### 2.3 P2：建立可运营、可评估、可扩展能力

| 能力 | 范围 |
|---|---|
| Analytics | 事件规范、匿名 session、漏斗、推荐接受/删除、Chat 修改完成率 |
| Offline evaluation | 固定测试集、自动规则评估、LLM judge 仅用于主观维度、人工抽检 |
| LLM observability | prompt version、model、latency、token、cost、重试、解析失败、Critic 修订率 |
| 高级 Knowledge Graph | 只有在能支撑 impact analysis、实体去重或 explainability 时升级；否则保留为展示 |
| 复杂个性化 | 跨旅行偏好记忆、行为学习、协同信号、用户分群推荐 |
| 更可靠持久化 | 从本地 JSON 迁移到正式数据库、队列和对象存储，支持多用户与横向扩展 |

### 2.4 明确延后

- 自动预订、付款或票务保证。
- 实时导航与位置后台追踪。
- 完整社交内容聚合平台。
- 为“多 Agent”而增加无独立决策价值的 Agent。
- 在 P0 迁移技术栈或引入微服务。

---

## 3. 数据模型改造

### 3.1 建模原则

- 稳定 ID 优先：所有可修改节点必须有 `node_id`，不能再用数组下标定位。
- 事实与判断分离：POIEvidence 保存来源事实；RecommendationReason 保存产品解释；RiskItem 保存校验结果。
- 原始输入与解析结果并存：PreferenceProfile 必须保留用户原文，方便纠错和评估。
- 硬约束与软偏好分离：违反硬约束应阻止或强提醒；软偏好用于评分和解释。
- 不确定性显式化：`unknown` 比虚假的默认值更安全。
- 版本不可变：PlanVersion 创建后不原地覆盖，修改产生新版本。

以下字段为建议设计，暂不实现。时间统一使用带时区的 ISO 8601；金额使用整数最小货币单位或 Decimal，不能使用 float。

### 3.2 PreferenceProfile

用途：作为研究、规划、评分、Critic 和 Chat 修改的共同用户约束来源。

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `profile_id` | `str` | 系统生成 | 稳定引用与评估 |
| `destinations` | `list[CityStay]` | 表单 | 目的地及停留天数 |
| `date_range` | `DateRange` | 表单 | 行程日期硬约束 |
| `party_type` | `enum` | 表单 | 独自、情侣、朋友、家庭、父母、儿童 |
| `party_size` | `int` | 表单 | 预算与酒店计算 |
| `budget_level` | `enum` | 表单 | 经济、舒适、高品质 |
| `budget_amount` | `Money | null` | 表单 | 总预算硬/软约束 |
| `pace` | `enum` | 表单 | 特种兵、适中、松弛 |
| `interest_tags` | `list[str]` | 表单 + LLM | 检索、排序、解释 |
| `food_preferences` | `list[str]` | 表单 + LLM | 餐饮筛选 |
| `mobility_requirements` | `list[str]` | 表单 + LLM | 步行量、楼梯、无障碍约束 |
| `avoid_tags` | `list[str]` | 表单 + LLM | 排除拥挤、早起、辣食等 |
| `daily_start_after` | `time | null` | LLM 解析后用户确认 | “不想早起”等时间约束 |
| `max_daily_steps` | `int | null` | 表单/LLM | 强度校验 |
| `transportation_preference` | `enum` | 表单 | 路线模式 |
| `accommodation_preference` | `str` | 表单 | 酒店检索与预算 |
| `special_requirements_raw` | `str` | 用户原文 | 可追溯与重新解析 |
| `hard_constraints` | `list[Constraint]` | 表单 + 解析 | Rule Validator 必须检查 |
| `soft_preferences` | `list[WeightedPreference]` | 表单 + 解析 | Planner/Score 权衡 |
| `parse_warnings` | `list[str]` | Preference Parser | 暧昧或冲突输入提示 |
| `schema_version` | `str` | 系统 | 兼容迁移 |

与现有 schema 的关系：扩展并最终替代 `TripRequest` 中的 `transportation`、`accommodation`、`preferences`、`free_text_input` 等扁平字段；过渡期 `TripRequest` 同时保留旧字段和可选 `preference_profile`，通过 adapter 生成 Profile，避免破坏旧客户端。

### 3.3 POIEvidence

用途：保存一个 POI 相关事实或观点的来源，使推荐理由、风险与评分可追溯。

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `evidence_id` | `str` | 系统 | 唯一引用 |
| `poi_id` | `str` | POI normalization | 关联规范化 POI |
| `source_type` | `enum` | connector | `official/map/xhs/review/weather/manual` |
| `source_name` | `str` | connector | 来源展示名称 |
| `source_url` | `str | null` | connector | 用户查看原始来源 |
| `source_item_id` | `str | null` | connector | 笔记/Place/页面 ID |
| `claim_type` | `enum` | extractor | 开放时间、评价、预约、价格、时长等 |
| `claim_text` | `str` | 原文/提纯 | 展示或 Critic 输入 |
| `structured_value` | `dict | null` | extractor/parser | 可计算事实 |
| `observed_at` | `datetime | null` | 来源 | 内容发生时间 |
| `retrieved_at` | `datetime` | 系统 | 数据新鲜度 |
| `reliability_level` | `enum` | 来源策略 | `official/high/medium/low/unknown` |
| `freshness_status` | `enum` | deterministic | `fresh/stale/unknown` |
| `conflicts_with` | `list[str]` | evidence resolver | 相互矛盾的 evidence IDs |
| `excerpt` | `str | null` | 来源，受版权长度限制 | 简短证据摘要 |
| `language` | `str` | 来源 | 翻译与显示 |

与现有 schema 的关系：替代 `xhs_service.py` 返回给 Planner 的纯文本拼接；现有 `Attraction.description`、预约字段和评分可以从 evidence 派生，但 evidence 不应塞入 description。`source_url` 缺失时推荐理由必须标示“来源链接不可用”。

### 3.4 RecommendationReason

用途：回答“为什么推荐这个地点/酒店/安排”，并支持 UI 展开依据。

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `reason_id` | `str` | 系统 | 唯一引用 |
| `target_node_id` | `str` | ItineraryNode | 绑定推荐对象 |
| `summary` | `str` | LLM 基于结构化输入生成 | 用户可读主理由 |
| `preference_matches` | `list[ReasonFactor]` | deterministic + LLM | 指向 Profile 字段 |
| `route_factors` | `list[ReasonFactor]` | route service | 同日距离/交通便利 |
| `time_factors` | `list[ReasonFactor]` | validator | 开放时间和节奏适配 |
| `budget_factors` | `list[ReasonFactor]` | budget service | 预算适配 |
| `evidence_ids` | `list[str]` | research | 引用 POIEvidence |
| `limitations` | `list[str]` | reason builder | 数据不足/不确定性 |
| `generated_by` | `enum` | 系统 | `template/llm/hybrid` |
| `prompt_version` | `str | null` | LLM layer | 可复现与评估 |

与现有 schema 的关系：新增到 `Attraction`/`Hotel` 或更通用的 `ItineraryNode`；不复用当前 `description`，因为 description 讲地点是什么，reason 讲为什么适合该用户。

### 3.5 RiskItem

用途：统一承载规则冲突、事实不确定性和 Critic 发现的问题。

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `risk_id` | `str` | 系统 | 唯一标识 |
| `risk_type` | `enum` | validator/critic | 时间、交通、开放时间、预算、天气、预约、偏好、数据质量 |
| `severity` | `enum` | 规则表/LLM 受限输出 | `info/warning/blocking` |
| `status` | `enum` | workflow | `open/resolved/accepted/unknown` |
| `day_index` | `int | null` | itinerary | 定位日期 |
| `node_ids` | `list[str]` | itinerary | 受影响节点 |
| `title` | `str` | deterministic/template | 简短提示 |
| `description` | `str` | validator/critic | 具体问题 |
| `evidence_ids` | `list[str]` | validator | 事实依据 |
| `measured_value` | `dict | null` | service | 如抵达 16:40、闭馆 17:00 |
| `threshold` | `dict | null` | rule | 判定阈值 |
| `suggested_actions` | `list[RiskAction]` | rule/critic | 调整上午、替换室内等 |
| `detected_by` | `enum` | 系统 | `rule/critic/manual` |
| `rule_id` | `str | null` | validator | 可测试性 |
| `resolved_in_version_id` | `str | null` | version workflow | 审计闭环 |

与现有 schema 的关系：替代只有预约布尔提示的碎片化风险表达；现有 `reservation_required/tips` 仍可保留用于兼容，但同时生成标准 RiskItem。

### 3.6 ItineraryNode

用途：把景点、餐饮、酒店、交通和休息统一成可定位、可排序、可 patch 的节点。

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `node_id` | `str` | 系统 | patch、diff、风险关联的稳定 ID |
| `node_type` | `enum` | Planner | `attraction/meal/hotel/transport/rest/free_time` |
| `day_index` | `int` | Planner | 所属日期 |
| `sequence` | `int` | Planner/route optimizer | 当日顺序 |
| `start_time` | `datetime | null` | Planner/scheduler | Timeline 与冲突检测 |
| `end_time` | `datetime | null` | scheduler | Timeline 与开放时间检测 |
| `duration_minutes` | `int` | research/Planner | 调度 |
| `title` | `str` | POI/Planner | 展示 |
| `poi_id` | `str | null` | POI normalization | 关联 POI |
| `location` | `Location | null` | map service | 路线计算 |
| `cost` | `MoneyEstimate | null` | budget service | 预算计算 |
| `opening_hours` | `OpeningHours | null` | evidence resolver | 开放时间校验 |
| `reservation` | `ReservationInfo | null` | evidence resolver | 预约风险 |
| `indoor_outdoor` | `enum` | research | 天气替换 |
| `mobility_load` | `MobilityLoad | null` | route/research | 步行与行动能力 |
| `travel_from_previous` | `TravelLeg | null` | route service | 距离、时长、模式 |
| `recommendation_reason` | `RecommendationReason | null` | reason builder | Explainability |
| `recommendation_score` | `RecommendationScore | null` | scoring service | 推荐依据分项 |
| `evidence_ids` | `list[str]` | research | 可追溯 |
| `locked` | `bool` | 用户/Chat | 局部重规划时禁止改动 |
| `source` | `enum` | workflow | `generated/user_added/patch` |

与现有 schema 的关系：逐步替代 `DayPlan.attractions + meals + hotel + transportation` 的并列结构。过渡期由 adapter 在旧 DayPlan 与 nodes 之间双向转换，先允许景点成为 node，再扩展餐饮和酒店，避免一次迁移全部结果页。

### 3.7 PlanVersion

用途：保存初版、Critic 修订版和用户修改版，提供 diff、回滚和指标基础。

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `version_id` | `str` | 系统 | 唯一版本 |
| `plan_id` | `str` | 任务系统 | 归属旅行 |
| `version_number` | `int` | persistence | 排序 |
| `parent_version_id` | `str | null` | workflow | 版本链 |
| `created_at` | `datetime` | 系统 | 审计 |
| `created_by` | `enum` | 系统 | `planner/critic/user/chat/system` |
| `change_reason` | `str` | workflow/用户意图 | 解释版本变化 |
| `preference_profile_id` | `str` | workflow | 固定约束快照 |
| `nodes` | `list[ItineraryNode]` | Planner/Patch | 完整不可变快照 |
| `risks` | `list[RiskItem]` | validation | 当前版本风险 |
| `budget_summary` | `BudgetSummary | null` | budget service | 版本预算 |
| `quality_summary` | `PlanQualitySummary` | validator/critic | blocking/warning 数量等 |
| `patch_id` | `str | null` | patch workflow | 产生该版本的 patch |
| `workflow_trace_id` | `str` | orchestration | 调试与 observability |
| `schema_version` | `str` | 系统 | 数据迁移 |

与现有 schema 的关系：当前 `TripPlan` 可作为 PlanVersion 的兼容 projection；当前 `task.result.data` 最终应保存当前版本 ID 和展示 projection，而不是唯一可变计划。

### 3.8 PlanPatch

用途：让 Chat 和手动操作通过有限、安全、可预览的操作修改计划。

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `patch_id` | `str` | 系统 | 审计 |
| `plan_id` | `str` | context | 归属计划 |
| `base_version_id` | `str` | 客户端 | 乐观并发与防止覆盖 |
| `user_intent` | `str` | 用户原文 | 可解释与评估 |
| `intent_type` | `enum` | LLM classifier | relax_day、remove、replace、add、budget、weather 等 |
| `operations` | `list[PatchOperation]` | LLM 结构化输出/手动 UI | `add/remove/replace/move/update/lock` |
| `affected_node_ids` | `list[str]` | impact analyzer | 限定重算范围 |
| `affected_day_indices` | `list[int]` | impact analyzer | 局部规划 |
| `recompute_scopes` | `set[enum]` | deterministic | route/budget/time/risk/reason |
| `constraints_added` | `list[Constraint]` | intent parser | 如“当天更轻松” |
| `preview_diff` | `PlanDiff` | patch service | 用户确认前展示 |
| `validation_result` | `PatchValidation` | deterministic | 是否可应用及风险 |
| `requires_confirmation` | `bool` | policy | 防止高影响操作直接执行 |
| `status` | `enum` | workflow | draft/validated/applied/rejected |
| `created_at` | `datetime` | 系统 | 审计 |

与现有 schema 的关系：替代 Result 中直接按数组下标改对象的方式；短期保留前端手动编辑，但逐步让编辑操作也生成 PlanPatch。

### 3.9 RecommendationScore

用途：提供透明的产品排序信号，不冒充模型真实概率。

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `score` | `int`（0–100） | scoring service | UI 推荐指数 |
| `score_type` | literal `heuristic` | 系统常量 | 防止误解 |
| `preference_match` | `float`（0–1） | profile matcher | 兴趣/同行人/节奏适配 |
| `route_convenience` | `float`（0–1） | route service | 距离与交通效率 |
| `time_fit` | `float`（0–1） | scheduler/validator | 开放时间与时段适配 |
| `budget_fit` | `float`（0–1） | budget service | 预算适配 |
| `evidence_strength` | `float`（0–1） | evidence resolver | 来源数量、可靠性、新鲜度 |
| `popularity_signal` | `float | null` | data source | 热度，仅在有可比数据时使用 |
| `penalties` | `list[ScorePenalty]` | risk service | 风险与缺失数据扣分 |
| `weights` | `dict[str, float]` | versioned config | 可解释、可调参 |
| `explanation` | `list[str]` | deterministic template | UI 分项说明 |
| `formula_version` | `str` | scoring config | 评估与迭代 |

建议公式：先将每个可用分项标准化，再按版本化权重加权；缺失数据不应默认满分，而应降低 `evidence_strength` 并展示“不确定”。硬约束冲突可以封顶分数或直接判为不可推荐。

与现有 schema 的关系：新增至 ItineraryNode，不复用当前可选 `rating`。`rating` 是外部地点评分，`recommendation_score` 是对当前用户和当前行程的内部适配评分，两者必须在 UI 上明确区分。

---

## 4. Agent / Workflow 重构

### 4.1 新流程

```text
User Input
  ↓
Input Validation                         [deterministic]
  ↓
Preference Parsing                      [LLM + deterministic merge]
  ↓ user confirmation when ambiguous
Research Query Builder                  [deterministic]
  ↓
Destination Research                    [connectors + LLM extraction]
  ↓
POI Normalization / Evidence Resolution [deterministic]
  ↓
Draft Planner                           [LLM]
  ↓
Route / Schedule / Budget Enrichment    [deterministic services]
  ↓
Rule Validator                          [deterministic]
  ↓
Travel Critic                           [LLM, bounded structured output]
  ↓
Revision Decision                       [deterministic policy]
  ├── no blocking/material risk → finalize
  └── revision needed → Planner Revision [LLM, once]
                            ↓
                     Rule Validator again
                            ↓
Recommendation Score / Reason Builder   [deterministic + LLM wording]
  ↓
Final PlanVersion
  ↓
Chat Intent → PlanPatch → Impact Analysis → Local Replan → Validate → Confirm → New PlanVersion
```

### 4.2 步骤职责与输入输出

| 步骤 | 类型 | 输入 | 输出 | 为什么 |
|---|---|---|---|---|
| Input Validation | deterministic service | 原始表单 | 合法 TripSetup、字段错误 | 日期、人数、金额无需 LLM |
| Preference Parsing | LLM task，不必长期自治 | 表单 + 特殊需求原文 | `PreferenceProfileDraft`、歧义和提取证据 | 需要语义理解，但结果必须和显式字段 deterministic merge |
| User Confirmation | UI/product policy | ProfileDraft | 已确认 PreferenceProfile | 高影响歧义不能让模型擅自决定 |
| Query Builder | deterministic service | PreferenceProfile | 多组 research queries | 可测试，确保多个兴趣均被覆盖 |
| Research Connectors | service | query | raw source items | 外部 API/抓取不是 Agent 决策 |
| Research Extraction | LLM task | raw items + schema | POI candidates + POIEvidence | 适合非结构化信息抽取 |
| POI Normalization | deterministic service | candidates | canonical POIs、去重、坐标、evidence links | 实体去重和字段合并应可复现 |
| Draft Planner | LLM Agent | Profile、POIs、evidence summaries、规则 | Draft nodes + planning rationale | 需要多目标权衡与候选生成 |
| Route Service | deterministic/API | 有序 locations、模式 | TravelLeg、距离、时长 | 应使用地图数据而非模型猜测 |
| Scheduler | deterministic service | nodes、duration、hours、legs | start/end time | 时间计算必须可验证 |
| Budget Calculator | deterministic service | costs、人数、天数 | BudgetSummary、超限量 | 算术不应交给 LLM |
| Rule Validator | deterministic service | Profile + enriched plan | RiskItem[] | 开放时间、重叠、预算、交通阈值可测试 |
| Travel Critic | LLM Agent | Profile、plan、evidence summary、rule risks | semantic risks、suggestions、revision brief | 适合判断节奏、体验、偏好冲突，但不可覆盖规则事实 |
| Revision Decision | deterministic policy | risk severity + Critic output | revise/finalize/fail-soft | 防止无限循环和成本失控 |
| Planner Revision | LLM Agent | draft + risks + revision brief | revised nodes + change rationale | 只改指出的问题；最多一次 P0 |
| Score Service | deterministic service | Profile、metrics、evidence、risks | RecommendationScore | 透明权重优于模型随意给分 |
| Reason Builder | hybrid | score factors + evidence + route | RecommendationReason | 因子选择 deterministic，中文表达可由 LLM 润色 |
| Patch Intent Parser | LLM task | user message + current version summary | PlanPatch draft | 将自然语言限制成允许的 operation schema |
| Impact Analyzer | deterministic service | patch + dependency graph | affected nodes/days/scopes | 控制局部重算范围 |
| Patch/Replanner | service + bounded LLM | patch、局部上下文 | candidate new version | 简单 remove/move deterministic；替换/松弛需要 LLM 候选 |
| Patch Validator | deterministic + existing Critic policy | candidate version | risks、preview diff | 用户确认前保证计划一致性 |

### 4.3 应该是 deterministic service 的部分

- 日期、总天数和城市停留一致性。
- 距离、路线时长、步行量估算。
- 时间线排程与重叠检查。
- 开放时间窗口检查。
- 预算汇总、超预算量和分类占比。
- 预约字段转风险。
- 天气类型与室内/室外标签匹配的基础规则。
- RecommendationScore 计算。
- Patch schema、权限、目标 node 存在性和 base version 检查。
- 影响范围分析、版本 diff 和持久化。
- 重试上限、是否触发 Revision 的 workflow policy。

### 4.4 应该使用 LLM 的部分

- 从特殊需求理解隐含偏好与约束。
- 从非结构化游记抽取 POI 观点与避坑信息。
- 在多个合格候选间做软目标权衡并生成 draft。
- Critic 判断“太累”“体验单一”“与同行人不匹配”等语义问题。
- 把结构化 recommendation factors 转成自然、克制的中文理由。
- 将 Chat 自然语言解析为有限 PlanPatch。
- 必须替换节点时，从合格候选中提出局部方案。

### 4.5 不应该包装成 Agent 的部分

- API connector、HTTP client、Cookie 签名和 provider dispatcher。
- POI 去重、坐标校验和 schema adapter。
- 预算加总、百分比和币种处理。
- 距离矩阵、路线 API 和 timetable arithmetic。
- 风险规则执行器。
- scoring formula。
- JSON 持久化、版本管理和 analytics emitter。
- 前端展示、导出和图片缓存。

把这些包装为 Agent 会增加延迟、成本和不确定性，却不增加产品判断价值。

### 4.6 Planner–Critic 控制策略

P0 只允许一次 revision：

1. Draft Planner 生成候选。
2. Rule Validator 生成确定性风险。
3. Critic 只能补充语义风险和修订建议，不得删除规则风险。
4. 出现 blocking risk，或 warning 超过配置阈值，才触发 Revision。
5. Revision 后再次运行 Rule Validator。
6. 若仍有 blocking risk，返回“计划已生成但需要用户确认”的 fail-soft 结果，不继续无限循环。
7. 保存 draft risks、critic suggestions、changed node IDs 和 unresolved risks，供作品集展示和评估。

### 4.7 Prompt 管理策略

- 将 Prompt 从 Agent 文件移到版本化模板目录。
- 每个 Prompt 固定 input/output schema、禁止事项、fallback 和示例。
- Prompt 版本写入 workflow trace 与 PlanVersion。
- Critic 输出必须是结构化 JSON，不接收自由散文作为控制信号。
- 长上下文只传相关证据摘要和 IDs，不重复发送整份原始笔记。
- 不依赖“修复坏 JSON”作为正常路径；优先使用 provider 的 structured output，保留现有修复仅作兼容 fallback。

---

## 5. 前端改造计划

### 5.1 `Landing.vue`

保留：

- 现有 `/` 路由和表单提交骨架。
- 多城市输入能力。
- 日期选择、历史计划入口、异步进度接收。
- NavBar 的基本位置和 i18n 基础。

拆分：

- `TripSetupForm`：表单编排与验证。
- `DestinationDateStep`：目的地和日期。
- `TravelPartyStep`：同行人和人数。
- `BudgetPaceStep`：预算与旅行节奏。
- `InterestStep`：兴趣选择。
- `SpecialRequirementsStep`：自然语言需求与示例。
- `PreferenceReview`：LLM 解析后展示硬约束/软偏好，允许纠正。
- `GenerationProgress`：展示 Preference、Research、Draft、Checking、Optimizing，而不是只展示技术 Agent 名称。
- `TripHistoryList`：历史计划。

中文用户流程建议：

```text
Hero：一句话描述你想要的旅行
  ↓ 点击“开始规划旅行”
Step 1 去哪里、什么时候
Step 2 和谁去、预算与节奏
Step 3 兴趣与特殊需求
  ↓
AI 理解确认：
“我理解你不想早起、每天最多走 15000 步、偏好拍照和美食”
  ↓ 用户确认/修改
开始生成
```

Hero 下的三个示例用于填充表单/特殊需求，而不是直接跳过结构化设置。

`Home.vue`：标记 deprecate，不在本阶段删除；后续确认 Landing 完成后再移除，避免继续维护两个首页。

### 5.2 `Result.vue`

保留：

- 路由与 plan ID 恢复能力。
- 地图 provider 初始化和现有路线绘制能力。
- 图片懒加载、导出、历史结果兼容。
- 天气可视化、基础预算展示。
- AIChat 的悬浮/侧栏交互基础。

拆分为页面容器与领域组件：

```text
TripResultPage.vue
├── TripOverview.vue
├── AISummary.vue
├── RouteMap.vue
├── DayTimeline.vue
│   └── ItineraryNodeCard.vue
│       ├── RecommendationScoreBadge.vue
│       ├── RecommendationReasonPanel.vue
│       ├── EvidenceDrawer.vue
│       └── ReservationAlert.vue
├── BudgetPanel.vue
├── TravelRiskPanel.vue
├── AISuggestions.vue
├── PlanChangePreview.vue
├── PlanVersionBar.vue                # P1
└── TravelCopilot.vue
```

Composable 建议：

- `useTripPlan`：加载、当前版本和 projection。
- `useTripTask`：WebSocket + 轮询降级。
- `useTripMap`：Google/AMap 生命周期和路线。
- `usePlanEditing`：patch preview、confirm、undo。
- `useAttractionPhotos`：图片加载与缓存。
- `usePlanExport`：导出。

### 5.3 新增页面/状态

P0 不需要增加大量路由，建议仅增加一个确认态：

- `/`：Landing + Trip Setup。
- `/setup/review` 或同页 step：Preference Review。
- `/result/:planId?`：结果页，兼容现有 query 参数。

P1 再考虑：

- `/compare/:planId`：三方案对比。
- `/plan/:planId/versions`：版本历史；也可先做结果页 drawer。

### 5.4 推荐结果页信息架构

```text
Top bar：目的地 / 日期 / 同行人 / 当前版本 / 导出

Trip Overview
  - 方案定位、节奏、预算、关键偏好

AI Summary
  - AI 如何理解你
  - 本次计划的 3 个核心取舍

Route Map

Daily Timeline
  - 明确时间
  - 节点卡片
  - 推荐指数（heuristic）
  - 为什么推荐
  - 来源依据
  - 开放时间 / 停留 / 交通 / 预算 / 预约

Budget

Travel Risk
  - blocking / warning / info
  - 已由 Critic 修正 / 仍待用户确认

AI Suggestions

Travel Copilot（右侧固定）
  - 提问
  - 修改意图
  - Patch Preview
  - 确认应用
```

Knowledge Graph 在 P0 降为次级 Tab，不删除；避免与用户核心决策信息竞争首屏。

### 5.5 UX 边界

- 分数旁固定显示“基于偏好与行程条件计算，并非模型概率”。
- 来源不足时显示“依据有限”，不能隐藏不确定性。
- Chat 修改默认 preview-first，高影响修改必须确认。
- 用 diff 表达“改了什么、为什么、影响预算/交通多少”。
- 风险不能只用颜色；同时提供 severity 文案和建议动作。
- AI 解析的偏好必须允许用户纠正。
- P0 默认中文，但保留 locale 数据结构和旧结果兼容。

---

## 6. 后端改造计划

### 6.1 API

建议版本化新增 `/api/v2`，旧接口保持兼容，P0 不立即删除：

| API | 作用 | 优先级 |
|---|---|---|
| `POST /api/v2/preferences/parse` | 解析自由文本并返回 ProfileDraft | P0 |
| `POST /api/v2/preferences/confirm` | 校验并确认 PreferenceProfile | P0 |
| `POST /api/v2/trips` | 创建基于 Profile 的规划任务 | P0 |
| `GET /api/v2/trips/{plan_id}` | 获取当前版本与展示 projection | P0 |
| `GET /api/v2/trips/{plan_id}/events` 或 WS | 推送 workflow 阶段 | P0 |
| `POST /api/v2/trips/{plan_id}/chat` | 问答或生成 patch draft | P0 |
| `POST /api/v2/trips/{plan_id}/patches/preview` | 校验 patch 并返回 diff | P0 |
| `POST /api/v2/trips/{plan_id}/patches/{patch_id}/apply` | 应用已确认 patch | P0 |
| `GET /api/v2/trips/{plan_id}/risks` | 获取结构化风险 | P0，可合并主响应 |
| `GET /api/v2/trips/{plan_id}/versions` | 版本列表 | P1 |
| `POST /api/v2/trips/{plan_id}/versions/{version_id}/restore` | 回滚生成新版本 | P1 |
| `POST /api/v2/trips/compare` | 生成/读取三个方案 | P1 |
| `POST /api/v2/events` | 产品埋点接收 | P2 |

### 6.2 Service

新增或重构：

- `preference_service`：显式字段与 LLM 抽取结果 merge，冲突时以用户显式字段为准。
- `research_service`：编排多个 connector，返回规范 POI 与 evidence，不直接返回拼接文本。
- `poi_normalization_service`：去重、canonical ID、坐标质量、名称别名。
- `route_service`：统一 Google/AMap 距离、时长和路线质量，禁止默认北京坐标静默通过。
- `schedule_service`：根据 duration、travel leg 和 opening hours 计算 timeline。
- `budget_service`：确定性费用汇总和约束检测。
- `risk_service`：规则注册、运行和 RiskItem 标准化。
- `recommendation_service`：score 和 reason factors；中文润色与因子计算分开。
- `patch_service`：validate、impact analyze、preview、apply、diff。
- `plan_version_service`：不可变版本、当前指针和 adapter。
- `workflow_service`：显式状态机/阶段编排，替代 `trip_planner_agent.py` 中的巨型职责。

暂时保留：

- `xhs_service` 的签名与抓取能力，但拆出 connector 和 extractor 接口。
- `google_map_service`、`amap_service`、`map_dispatcher`，先统一返回结构再考虑替换。
- `knowledge_graph_service`，P0 只做兼容 projection。

### 6.3 Schema

建议从单个 `schemas.py` 按领域拆分：

```text
backend/app/models/
├── common.py
├── preference.py
├── evidence.py
├── itinerary.py
├── risk.py
├── recommendation.py
├── versioning.py
├── patch.py
├── api.py
└── legacy.py
```

P0 初期可以先新增文件并由 `schemas.py` re-export，避免一次修改全部 import。

### 6.4 Workflow

建议显式维护以下 stage：

```text
submitted
preference_parsing
preference_confirmed
researching
normalizing
draft_planning
route_enrichment
validating
critic_review
revising
finalizing
completed
failed
```

每个 stage 记录：开始/结束时间、输入摘要、输出 ID、错误、模型/prompt version、重试次数。P0 仍可使用当前 `asyncio.create_task` 和单 worker，不必先引入队列。

### 6.5 Persistence

P0：

- 继续使用本地 JSON，避免数据库迁移抢占产品验证资源。
- 目录按实体分开：profiles、plans、versions、patches、tasks、evidence cache。
- 采用 schema version 和原子写入。
- 现有 task JSON 通过 legacy adapter 可读取。
- 避免把完整密钥写入旅行数据。

P1：

- 引入正式数据库前先定义 repository interface。
- PlanVersion 不可变；Plan 保存 `current_version_id`。
- Patch 使用 `base_version_id` 防止并发覆盖。

P2：

- 再评估关系型数据库、任务队列、缓存与对象存储。

### 6.6 Evaluation support

虽然完整 Offline Evaluation 属于 P2，P0 数据结构必须预留：

- `workflow_trace_id`、`prompt_version`、`model_id`。
- Draft 和 revised version。
- Rule risks before/after revision。
- Critic suggestions 与实际 changed nodes。
- Profile raw input 和 confirmed output。
- 推荐分数公式版本。

建议测试层级：

- 单元规则：日期、预算、开放时间、travel leg、patch operation。
- contract test：每个 LLM task 的 JSON schema。
- golden cases：20 条代表性中文旅行需求。
- regression snapshot：同一输入的结构和关键约束，不要求逐字一致。
- 人工 rubric：POI 相关性、体验节奏、解释质量。

---

## 7. 文件级修改计划

> 下表是后续实施清单。本阶段没有执行这些操作。操作值严格限定为 `keep / modify / split / deprecate / add`。

### 7.1 根目录与文档

| 文件路径 | 操作 | 修改内容 | 优先级 | 风险 |
|---|---|---|---|---|
| `CURRENT_ARCHITECTURE.md` | keep | 保留为重构前基线 | P0 | 低 |
| `REFACTOR_PLAN.md` | keep | 保留为实施与验收依据 | P0 | 低 |
| `README.md` | modify | 后续改为旅伴 AI Case Study，明确上游项目与 GPL v2 | P1 | 中：License 表述必须准确 |
| `README_en.md` | keep | P0 保留，不要求同步重写 | P2 | 低 |
| `README_ja.md` | keep | P0 保留，不要求同步重写 | P2 | 低 |
| `LICENSE` | keep | 保留 GNU GPL v2 | P0 | 高：不可误删或隐去上游来源 |
| `docs/PRD.md` | add | 产品需求与非目标 | P1 | 低 |
| `docs/user_persona.md` | add | 用户画像与 JTBD | P1 | 低 |
| `docs/user_flow.md` | add | 核心流程和异常流程 | P1 | 低 |
| `docs/agent_architecture.md` | add | Agent/service 边界与序列图 | P1 | 低 |
| `docs/ai_evaluation.md` | add | 评估 rubric 与 20 cases | P2 | 中：避免只用 LLM 自评 |
| `docs/product_metrics.md` | add | North Star、漏斗、口径 | P2 | 中：事件口径需一致 |
| `docs/roadmap.md` | add | P0/P1/P2 路线图 | P1 | 低 |

### 7.2 前端

| 文件路径 | 操作 | 修改内容 | 优先级 | 风险 |
|---|---|---|---|---|
| `frontend/src/views/Landing.vue` | split | 拆 Trip Setup、Review、Progress、History | P0 | 高：现有生成流程不能回归 |
| `frontend/src/views/Home.vue` | deprecate | 标记旧版未路由页面，暂不删除 | P0 | 低 |
| `frontend/src/views/Result.vue` | split | 拆结果信息架构、地图、Timeline、Budget、Risk、Copilot | P0 | 高：当前功能高度耦合 |
| `frontend/src/App.vue` | modify | 中文产品品牌与新路由容器 | P0 | 低 |
| `frontend/src/main.ts` | modify | 增加 review/result 参数化路由，保持旧路由兼容 | P0 | 中 |
| `frontend/src/components/NavBar.vue` | modify | 中文品牌；设置入口与生产安全提示 | P0 | 中：运行时配置兼容 |
| `frontend/src/components/AIChat.vue` | split | TravelCopilot、ChatMessages、PatchPreview | P0 | 高：问答与修改需分状态 |
| `frontend/src/components/OverviewAttractionCard.vue` | modify | 适配 score/reason/evidence | P0 | 中 |
| `frontend/src/components/setup/TripSetupForm.vue` | add | 新结构化表单容器 | P0 | 中 |
| `frontend/src/components/setup/PreferenceReview.vue` | add | 展示并纠正 AI 解析 | P0 | 中 |
| `frontend/src/components/setup/GenerationProgress.vue` | add | 用户语言描述 workflow 进度 | P0 | 低 |
| `frontend/src/components/trip/TripOverview.vue` | add | Overview 和偏好摘要 | P0 | 低 |
| `frontend/src/components/trip/AISummary.vue` | add | 核心取舍说明 | P0 | 中：避免无依据生成 |
| `frontend/src/components/trip/RouteMap.vue` | add | 迁移双地图与路线逻辑 | P0 | 高 |
| `frontend/src/components/trip/DayTimeline.vue` | add | 时间线容器 | P0 | 中 |
| `frontend/src/components/trip/ItineraryNodeCard.vue` | add | 节点展示与操作入口 | P0 | 中 |
| `frontend/src/components/trip/RecommendationReasonPanel.vue` | add | 原因、分数分项和 evidence | P0 | 中 |
| `frontend/src/components/trip/TravelRiskPanel.vue` | add | 风险分级、状态和建议 | P0 | 中 |
| `frontend/src/components/trip/BudgetPanel.vue` | add | 迁移现有预算，后接 constraint | P0 | 中 |
| `frontend/src/components/trip/PlanChangePreview.vue` | add | Patch diff 与确认 | P0 | 高 |
| `frontend/src/components/trip/PlanVersionBar.vue` | add | 版本查看、回滚 | P1 | 中 |
| `frontend/src/composables/useTripTask.ts` | add | WS + polling fallback | P0 | 中 |
| `frontend/src/composables/useTripPlan.ts` | add | 计划加载与 projection | P0 | 中 |
| `frontend/src/composables/usePlanEditing.ts` | add | Patch preview/apply | P0 | 高 |
| `frontend/src/composables/useTripMap.ts` | add | 双地图生命周期 | P0 | 高 |
| `frontend/src/composables/usePlanExport.ts` | add | 迁移导出逻辑 | P1 | 中 |
| `frontend/src/composables/useAttractionPhotos.ts` | add | 图片加载和缓存 | P1 | 低 |
| `frontend/src/services/api.ts` | split | 拆 settings/trips/chat/patch API clients | P0 | 高：兼容旧 API |
| `frontend/src/services/trips.ts` | add | v2 trip/task/version API | P0 | 中 |
| `frontend/src/services/preferences.ts` | add | Preference parse/confirm API | P0 | 中 |
| `frontend/src/services/patches.ts` | add | Patch preview/apply API | P0 | 中 |
| `frontend/src/types/index.ts` | split | 按领域拆类型并修复预约字段漂移 | P0 | 高：全前端引用范围大 |
| `frontend/src/types/preference.ts` | add | PreferenceProfile types | P0 | 中 |
| `frontend/src/types/itinerary.ts` | add | Node/reason/score/risk types | P0 | 中 |
| `frontend/src/types/versioning.ts` | add | PlanVersion/PlanPatch/PlanDiff | P0 | 中 |
| `frontend/src/i18n/locales/zh.json` | modify | 中文产品主文案和解释边界 | P0 | 中 |
| `frontend/src/i18n/locales/en.json` | keep | P0 保持兼容 | P2 | 低 |
| `frontend/src/i18n/locales/ja.json` | keep | P0 保持兼容 | P2 | 低 |
| `frontend/src/styles/global.css` | split | 页面/组件样式逐步迁移 | P1 | 高：视觉回归范围大 |

### 7.3 后端

| 文件路径 | 操作 | 修改内容 | 优先级 | 风险 |
|---|---|---|---|---|
| `backend/app/api/main.py` | modify | 注册 v2 路由，保留现有路由 | P0 | 中 |
| `backend/app/api/routes/trip.py` | split | 保留 legacy API，任务/历史逻辑迁至 service/repository | P0 | 高 |
| `backend/app/api/routes/chat.py` | modify | 兼容问答并转入 Copilot/patch workflow | P0 | 高 |
| `backend/app/api/routes/poi.py` | modify | 返回 evidence/source metadata，保持图片接口 | P1 | 中 |
| `backend/app/api/routes/map.py` | modify | 修复空解析或标注 deprecated，不再作为伪可用 API | P1 | 中 |
| `backend/app/api/routes/settings.py` | modify | 修复 proxy schema，限制密钥回显 | P1 | 高：本地使用体验与安全权衡 |
| `backend/app/api/routes/v2/preferences.py` | add | Preference parse/confirm | P0 | 中 |
| `backend/app/api/routes/v2/trips.py` | add | 创建/读取新计划 | P0 | 高 |
| `backend/app/api/routes/v2/patches.py` | add | Patch preview/apply | P0 | 高 |
| `backend/app/models/schemas.py` | split | legacy re-export + 新领域 schema 迁移 | P0 | 高 |
| `backend/app/models/common.py` | add | Money、DateRange、Constraint 等 | P0 | 中 |
| `backend/app/models/preference.py` | add | PreferenceProfile | P0 | 中 |
| `backend/app/models/evidence.py` | add | POIEvidence、canonical POI | P0 | 中 |
| `backend/app/models/itinerary.py` | add | ItineraryNode、Plan projection | P0 | 高 |
| `backend/app/models/risk.py` | add | RiskItem、RiskAction | P0 | 中 |
| `backend/app/models/recommendation.py` | add | Reason 与 Score | P0 | 中 |
| `backend/app/models/versioning.py` | add | PlanVersion、PlanDiff | P1 | 高 |
| `backend/app/models/patch.py` | add | PlanPatch、PatchOperation | P0 | 高 |
| `backend/app/agents/trip_planner_agent.py` | split | 保留 legacy facade；拆 workflow、planner、critic | P0 | 高 |
| `backend/app/agents/preference_parser.py` | add | 结构化偏好解析 LLM task | P0 | 中 |
| `backend/app/agents/planner.py` | add | Draft 与 Revision Planner | P0 | 高 |
| `backend/app/agents/travel_critic.py` | add | 语义 Critic，结构化输出 | P0 | 高 |
| `backend/app/agents/patch_intent_parser.py` | add | Chat 意图转 Patch draft | P0 | 高 |
| `backend/app/prompts/preference.py` | add | Preference Prompt v1 | P0 | 中 |
| `backend/app/prompts/planner.py` | add | Draft/Revision Prompt v1 | P0 | 高 |
| `backend/app/prompts/critic.py` | add | Critic Prompt v1 | P0 | 高 |
| `backend/app/prompts/patch.py` | add | Patch Prompt v1 | P0 | 高 |
| `backend/app/services/llm_service.py` | modify | 统一 structured output、usage metadata | P0 | 高：provider 兼容 |
| `backend/app/services/chat_service.py` | split | 问答与 patch intent 分离，共享上下文构建 | P0 | 高 |
| `backend/app/services/xhs_service.py` | split | connector、extractor、photo 分离，输出 evidence | P0 | 高：Cookie/签名链路脆弱 |
| `backend/app/services/amap_service.py` | modify | 统一返回结构，完成或禁用 TODO API | P1 | 高 |
| `backend/app/services/google_map_service.py` | modify | 标准 route/evidence result、错误状态 | P0 | 中 |
| `backend/app/services/map_dispatcher.py` | modify | 按能力降级，移除默认北京坐标静默成功 | P0 | 高：现有成功率可能下降但可信度提高 |
| `backend/app/services/preference_service.py` | add | Profile merge/validation | P0 | 中 |
| `backend/app/services/research_service.py` | add | 查询编排与 evidence 汇总 | P0 | 高 |
| `backend/app/services/poi_normalization_service.py` | add | 去重、canonical IDs | P0 | 中 |
| `backend/app/services/route_service.py` | add | 距离、时长、TravelLeg | P0 | 高：API 配额与 provider 差异 |
| `backend/app/services/schedule_service.py` | add | Timeline 排程 | P0 | 高 |
| `backend/app/services/risk_service.py` | add | 确定性风险规则 | P0 | 高：阈值产品决策 |
| `backend/app/services/recommendation_service.py` | add | Score、reason factors | P0 | 高：权重需解释和评估 |
| `backend/app/services/budget_service.py` | add | 费用汇总与 constraint | P1 | 中 |
| `backend/app/services/patch_service.py` | add | patch/impact/diff/apply | P0 | 高 |
| `backend/app/services/plan_version_service.py` | add | 当前版本与不可变快照 | P1 | 高 |
| `backend/app/services/workflow_service.py` | add | 显式编排与状态 | P0 | 高 |
| `backend/app/services/knowledge_graph_service.py` | keep | P0 维持展示兼容 | P2 | 低 |
| `backend/app/repositories/plan_repository.py` | add | 隔离本地 JSON/未来 DB | P0 | 中 |
| `backend/app/repositories/task_repository.py` | add | 隔离任务持久化 | P0 | 中 |
| `backend/app/repositories/evidence_repository.py` | add | evidence/cache 存储 | P1 | 中 |
| `backend/tests/unit/` | add | deterministic service 单测 | P0 | 低 |
| `backend/tests/contracts/` | add | LLM schema contract tests | P0 | 中 |
| `backend/tests/evaluation/` | add | golden cases 与 runner | P2 | 中 |
| `backend/app/services/xhs_sign/` | keep | 保留签名实现，不在产品重构中改写 | P0 | 高：改动易破坏数据源 |

### 7.4 部署

| 文件路径 | 操作 | 修改内容 | 优先级 | 风险 |
|---|---|---|---|---|
| `Dockerfile` | keep | P0 不引入依赖与部署重构 | P0 | 低 |
| `docker-compose.yaml` | keep | P0 保留单实例本地 Demo | P0 | 低 |
| `start.sh` | keep | 保持单 worker 与内存任务模型一致 | P0 | 低 |
| `backend/requirements.txt` | keep | 本规划阶段及首轮模型设计不加依赖 | P0 | 低 |
| `frontend/package.json` | keep | 优先用现有 Vue/AntD 能力 | P0 | 低 |

---

## 8. 开发顺序与独立验收

### Phase 0：兼容基线与契约

用户可见结果：现有 TripStar 行程生成和结果页行为不变。

后端变化：

- 固化 legacy API 样例。
- 增加 adapter 和新 schema 空壳。
- 为当前请求/结果建立 contract fixture。

前端变化：

- 修复 TypeScript 与后端预约字段契约。
- 抽离 `useTripTask`，加入 WebSocket → polling fallback，但不改 UI。

验收标准：

- 当前单城市、多城市请求仍能加载旧结果。
- 构建运行类型检查，不再依赖 Docker 跳过错误。
- WebSocket 失败时能通过轮询获得结果。

是否影响现有功能：不应影响；这是后续重构的安全网。

### Phase 1：Structured Preference Profile

用户可见结果：

- 首页新增同行人、人数、预算、节奏、兴趣、行动和饮食需求。
- 用户看到“AI 如何理解我”，可在生成前纠正。

后端变化：

- PreferenceProfile schema。
- `/preferences/parse` 与 deterministic merge。
- Profile 本地持久化和 legacy TripRequest adapter。

前端变化：

- Landing 拆分为 Setup steps。
- 新增 PreferenceReview。
- 中文为默认主流程。

验收标准：

- “东京 3 天，不想早起，喜欢拍照，预算 5000 元”解析出 start-after、interest、budget。
- 显式表单值优先于 LLM 推断。
- 模糊约束被标记而不是静默决定。
- 用户修改后最终 Profile 与确认界面一致。

是否影响现有功能：扩展请求；通过 adapter 保持现有 Planner 可继续工作。

### Phase 2：POI Evidence 与研究标准化

用户可见结果：核心景点卡片可以看到“依据来自哪里”和“信息更新时间”；来源不足时有提示。

后端变化：

- XHS raw connector 与 LLM extractor 分离。
- POIEvidence、canonical POI、地理编码质量状态。
- 多兴趣 query，不再只用第一个 preference。

前端变化：

- Evidence drawer/minimal source chips。
- 暂不重做完整卡片视觉。

验收标准：

- 每个由小红书提取的 POI 至少保留 source item ID 或明确标记缺失。
- 地理编码失败返回 unknown/error，不能使用北京默认坐标假装成功。
- 同一 POI 的别名可以归并，证据仍可追溯。

是否影响现有功能：研究返回结构改变；需要 projection 回旧 Planner 文本。

### Phase 3：Planner + Rule Validator + Critic

用户可见结果：生成进度出现“检查行程”和“优化方案”；结果页可显示“AI 已修正的风险”和仍待确认问题。

后端变化：

- Draft Planner、schedule/route enrichment、Rule Validator、Travel Critic、Revision Planner。
- ItineraryNode 和 RiskItem。
- 单次 revision policy 和 before/after trace。

前端变化：

- GenerationProgress 新阶段。
- Result 先加入 Risk Panel 和 revision summary。

验收标准：

- 固定测试中可识别一天景点过多、跨区距离过远、到达接近闭馆、早于用户起床约束、预约缺失。
- Critic 不覆盖 deterministic risk。
- Revision 最多一次，且修改后的节点可列出。
- 仍有 blocking risk 时明确展示，不伪装“全部通过”。

是否影响现有功能：核心生成链路变化最大；保留 feature flag/legacy workflow 便于对照和回退。

### Phase 4：Recommendation Reason + Score + 新结果页骨架

用户可见结果：

- 每个核心 POI 显示推荐指数、分项依据和“为什么推荐”。
- 结果页调整为 Overview → AI Summary → Map → Timeline → Budget → Risk → Suggestions → Copilot。

后端变化：

- RecommendationScore deterministic formula v1。
- RecommendationReason factors 与中文 reason builder。

前端变化：

- 拆 Result、Timeline、NodeCard、Reason、Risk。
- Knowledge Graph 移到次级位置但保留。

验收标准：

- score 明确标注 heuristic。
- 每个 reason 至少引用一个 Profile factor；有路线/证据时提供对应 ID。
- 修改权重可以通过 formula version 复现。
- 无 evidence 时分数和 UI 体现不确定性。

是否影响现有功能：结果 UI 改动大；地图、天气、预算、导出需回归。

### Phase 5：Chat Patch（P0 最后一环）

用户可见结果：

- 用户输入“第二天不要这么累”或“不想去迪士尼了”。
- Copilot 展示受影响节点、变更前后、预算/交通变化，用户确认后应用。

后端变化：

- Patch intent parser、PlanPatch、impact analyzer、preview/apply。
- P0 可以用轻量 version snapshot，即使完整 PlanVersion UI 留到 P1。

前端变化：

- AIChat 拆为 TravelCopilot。
- Patch preview、confirm、reject 和 apply state。

验收标准：

- remove 操作只影响目标 node 和必要的相邻 route legs。
- relax-day 只修改目标日，除非系统明确说明必须影响相邻日。
- base version 不一致时拒绝应用并提示刷新。
- Patch 应用后重新运行 route/time/risk/score 必需范围。
- 普通解释问题仍可回答，不误触发修改。

是否影响现有功能：Chat 从只读扩展为双模式；旧 `/api/chat/ask` 保持兼容。

### Phase 6：P1 决策增强

用户可见结果：预算超限自动给调整建议；下雨等 What-if 可局部替换；可比较经典/小众/松弛；可查看版本并回滚。

后端变化：Budget constraint、局部 replanner、方案生成策略、正式 PlanVersion repository。

前端变化：Compare 页面/面板、Version Bar、预算约束反馈。

验收标准：三个方案使用同一指标口径；局部重规划保留 locked nodes；版本可回滚且不覆盖历史；预算汇总可由明细复算。

是否影响现有功能：新增能力；需要关注 LLM 成本和候选生成延迟。

### Phase 7：P2 度量与评估

用户可见结果：产品界面变化较少，但 Case Study 可展示真实指标、失败案例和迭代结果。

后端变化：analytics events、evaluation runner、LLM trace/usage、测试集。

前端变化：关键交互发送事件；仅开发环境提供诊断视图。

验收标准：能计算 Successful Trip Plan Rate、Plan Acceptance Rate、Recommendation Acceptance Rate、Modification Rate、Chat Usage、latency 和 cost；20 条 case 可重复运行并输出维度分数。

是否影响现有功能：低；必须控制埋点隐私与性能。

---

## 9. Portfolio 叙事

### 9.1 用户问题定义

可讲述：发现“AI 能生成内容”不是核心价值，真正痛点是多源信息下的决策成本和信任问题。因此把成功标准从“生成一份 itinerary”改成“用户接受、理解并能调整一份可执行计划”。

证据形式：用户旅程、现状产品差距、Trip Setup 字段与推荐解释之间的映射。

体现能力：从 feature request 回到 JTBD，并明确非目标。

### 9.2 Structured Preference Profile

可讲述：把“妈妈膝盖不好、不想早起”从 prompt 文本变成可验证的约束；同时保留原文、解析结果和用户确认，避免 LLM 悄悄误解。

AI 边界：LLM 做语义解析，用户做高影响确认，规则服务做约束执行。

指标/评估：Preference Understanding Accuracy、correction rate、constraint satisfaction。

UX：AI 理解确认页让模型推断可见、可改。

Trade-off：多一步确认增加摩擦，但减少整份计划返工；可以只在有歧义或高风险约束时展开。

### 9.3 Planner–Critic 自反思

可讲述：不是无限“让模型再想想”，而是用 deterministic rules 发现可计算错误，再让 Critic 处理体验和偏好权衡，最多一次修订。

AI 边界：规则事实优先，Critic 不能覆盖距离、时间和预算事实。

指标/评估：首次风险率、修订后风险下降、blocking risk escape rate、额外延迟和成本。

UX：展示“发现并修复了什么”，而不暴露冗长 chain-of-thought。

Trade-off：质量提升会增加延迟/成本，因此设置触发阈值和单次循环。

### 9.4 Recommendation Reason + Heuristic Score

可讲述：用户不需要一个神秘的 92%，而需要知道 92 是由兴趣匹配、路线便利、时间、预算和证据强度如何组成。

AI 边界：分数由可版本化公式计算；LLM 只润色理由，不自行决定数字。

指标/评估：recommendation click/keep/remove、reason expansion、score calibration by acceptance bucket。

UX：先给一句核心理由，需要时展开分项和来源。

Trade-off：过多解释增加信息负担，所以采用 progressive disclosure。

### 9.5 Travel Risk / Conflict Detection

可讲述：从“生成后看起来合理”转向“生成前经过可审计检查”，并且对未知数据不假装安全。

AI 边界：时间、预算、路线和开放时间由规则检查；体验疲劳由 Critic 辅助。

指标/评估：constraint satisfaction、risk precision/recall、用户接受建议率、未解决 blocking risk 数量。

UX：按 blocking/warning/info 分级，提供具体动作。

Trade-off：信息源不完整会产生 unknown；宁可展示依据不足，也不返回虚假绿灯。

### 9.6 Chat Patch 与局部重规划

可讲述：将 Chat 从装饰性问答变成真正的 Copilot；自然语言先变成有限 patch，再由系统分析影响、预览并确认。

AI 边界：LLM 解释意图和提出替换候选；系统控制允许的操作、影响范围与一致性。

指标/评估：patch parse accuracy、preview acceptance、modified nodes count、unintended change rate、time to acceptable plan。

UX：显示 diff，不让 AI 静默重写用户已满意的部分。

Trade-off：局部最优不一定全局最优，因此重新校验目标日和相邻路线，并在需要扩大范围时征求确认。

### 9.7 Evaluation 与 Metrics

North Star 建议沿用并严格定义 **Successful Trip Plan Rate**：成功生成、无未确认 blocking risk，且用户未全量重新生成并执行至少一个接受信号的计划占比。

Portfolio 中应同时展示：

- 生成成功率不等于产品成功率。
- 离线约束满足与线上接受行为需要结合。
- LLM judge 只能评估部分主观质量，不能代替规则和人工检查。
- 成本与延迟是 AI 产品体验的一部分。
- 失败案例和不确定性处理比只展示最佳 Demo 更能说明 PM 判断力。

### 9.8 风险与权衡

| 决策 | 收益 | 成本/风险 | 缓解 |
|---|---|---|---|
| 引入 Critic | 提升计划质量 | 延迟和 LLM 成本 | 规则先筛、条件触发、最多一次 |
| 展示证据 | 增强信任 | 来源不稳定、版权与合规 | 短摘要、来源链接、缓存/新鲜度、平台条款评估 |
| 结构化 Profile | 更精准可评估 | 表单变长 | 分步、默认值、自然语言补充、按歧义确认 |
| heuristic score | 易比较 | 容易被误解为概率 | 固定免责声明、展示分项和公式版本 |
| 局部 patch | 保留用户成果 | 可能破坏全局一致性 | impact analysis + revalidation + preview |
| 本地 JSON 延续到 P0 | 交付快 | 不可扩展 | repository abstraction，P1 后迁移 |
| 中文优先 | 聚焦目标用户 | 多语言暂时不一致 | 保留 i18n 架构和 legacy locale |

---

## 10. 约束、Guardrails 与 Definition of Done

### 10.1 实施约束

- 不一次性重写整个项目。
- 每个 Phase 必须能独立运行、独立验收、可回退。
- P0 不引入微服务、复杂队列或新数据库，除非当前架构无法安全完成目标。
- 不删除现有功能；需要降级的功能先移动到次级入口并记录原因。
- 保留原项目来源和 GNU GPL v2，不弱化署名。
- 不把 deterministic 计算包装成 Agent。
- 不允许无限 Planner–Critic 循环。
- 不允许 LLM 自行产生 RecommendationScore 数字。
- 不允许没有 source/evidence 的事实被 UI 呈现为已验证。
- 不允许 Chat 直接覆盖当前计划；必须使用 base version、preview 和 confirm。
- 不把 chain-of-thought 暴露给用户；展示的是结构化检查结果和简明修订说明。
- 密钥、Cookie 和用户计划数据不进入 analytics payload。
- 所有 AI 输出必须通过 schema validation；所有 patch 必须通过业务规则 validation。

### 10.2 P0 Definition of Done

P0 只有同时满足以下条件才算完成：

1. 用户能建立并确认 PreferenceProfile。
2. 计划使用稳定 ItineraryNode IDs。
3. 至少五类风险可以用结构化 RiskItem 展示，其中可计算风险由 deterministic rules 产生。
4. Planner draft 经 Validator 和 Critic，必要时最多修订一次。
5. 每个核心 POI 有 RecommendationReason，分数明确为 heuristic。
6. 用户能通过 Chat 预览并确认至少四类有限 patch intent。
7. Patch 后仅重算声明的影响范围，并再次校验。
8. 旧 TripStar 计划仍可读取，旧 API 有明确兼容策略。
9. 有代表性测试覆盖“不想早起、预算限制、行动不便、下雨、预约”场景。
10. 文档能够解释产品问题、AI 边界、评估方法、指标和 trade-off。

### 10.3 每个功能交付时的固定汇报模板

后续每完成一个 Phase 或功能，应汇报：

1. 做了什么。
2. 为什么这样设计，对应哪个用户问题。
3. 修改了哪些文件。
4. 数据流和 LLM/deterministic 边界。
5. 如何测试及结果。
6. 已知限制、风险和下一步建议。

---

## 11. 推荐的立即执行顺序

如果下一步进入 coding，建议从以下顺序开始，而不是先做视觉汉化：

1. **Phase 0 兼容契约 + Phase 1 PreferenceProfile**：先让系统知道“为谁规划、什么不能违反”。
2. **Phase 3 的最小 Rule Validator + Critic**：先证明计划会被检查和修正，再扩充漂亮展示。
3. **Phase 4 RecommendationReason/Risk UI**：把 AI 的决策依据与能力边界变成用户能感知的价值。

Chat Patch 必须建立在稳定 node ID、风险校验和基础版本快照之后，否则“局部修改”只能退化为整份 JSON 重写。

本文完成后，STEP 2 停止，等待下一步指令。
