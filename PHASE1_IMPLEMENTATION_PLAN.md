# Phase 1 Implementation Plan — Preference Profile

> 项目：旅伴 AI（基于 TripStar 二次开发）
> 阶段：STEP 3 / Phase 1 规划
> 日期：2026-08-08
> 状态：等待确认，尚未开始编码
> 本阶段目标：用最小改动建立可演示的 Preference Profile，同时保证现有 Trip Planner 继续运行

## 1. Phase 1 目标与边界

### 1.1 用户价值

当前 TripStar 能接收目的地、日期、交通、住宿、兴趣和一段自由文本，但系统没有把这些信息整理为明确的用户画像。用户无法确认 AI 是否正确理解了“不想早起”“妈妈膝盖不好”“喜欢拍照”等要求，后续也无法判断行程是否违反这些约束。

Phase 1 要完成一个可感知闭环：

```text
用户填写结构化偏好和特殊要求
→ AI 解析自由文本
→ 系统合并显式选择与 AI 推断
→ 用户看到并确认“AI 对你的旅行偏好的理解”
→ 现有 Trip Planner 使用这份 Profile 生成行程
```

这一步体现的 AI PM 价值是：把模糊自然语言转化为透明、可纠正、可供后续 Validator 使用的产品状态，而不是把用户原文直接塞进一个长 Prompt。

### 1.2 本阶段包含

- 同行人类型。
- 出行人数。
- 具体预算金额，单位固定为人民币元；允许暂不填写。
- 旅行节奏：特种兵、适中、松弛度假。
- 兴趣多选。
- 特殊要求自由文本。
- LLM 对自由文本进行有限结构化解析。
- 显式选择优先于 LLM 推断的合并规则。
- 提交规划前的 Preference Profile 确认区域。
- 将确认后的 Profile 传给现有 `/api/trip/plan`。
- 让现有 Planner 读取一段 Profile 摘要，但不改变其 Agent 拓扑与输出 schema。

### 1.3 本阶段不包含

- Validator、Critic、Planner Revision。
- recommendation score/reason、RiskItem。
- Chat Patch。
- 数据库、PlanVersion、POIEvidence、`/api/v2`。
- 新状态管理库、微服务或大规模组件重构。
- 整体视觉重做；只对当前 `Landing.vue` 表单做必要扩展。
- 长期用户画像或跨旅行记忆。
- 多币种、按人/按晚预算明细或预算自动优化。

---

## 2. 具体会修改哪些文件

本阶段预计修改 8 个现有文件，新增 2 个后端文件；不拆分 `Landing.vue`，不重写现有 Planner。

| 文件路径 | 操作 | Phase 1 改动 | 原因 |
|---|---|---|---|
| `backend/app/models/schemas.py` | modify | 增加最小 `PreferenceProfile`、`PreferenceConstraints`、parse request/response；在 `TripRequest` 增加可选 `preference_profile` | 沿用当前集中 schema，避免 Phase 1 提前大拆分 |
| `backend/app/services/preference_service.py` | add | 调用现有 LLM 解析自由文本；校验结构化输出；执行显式优先 merge；失败时返回安全 fallback | 把解析和合并从 API/Planner 中隔离，便于单测 |
| `backend/app/api/routes/preferences.py` | add | 提供 `POST /api/preferences/parse` | 用户确认 Profile 后才提交原规划任务，不改写现有 trip API |
| `backend/app/api/routes/trip.py` | modify | 接收并持久化确认后的 `preference_profile`；对缺失 Profile 的旧请求保持兼容 | 历史任务可记录本次规划使用的偏好 |
| `backend/app/api/main.py` | modify | 注册 preferences router | 暴露最小解析 API |
| `backend/app/agents/trip_planner_agent.py` | modify | 仅在 `_build_planner_query()` 增加已确认 Profile 的紧凑摘要；保留现有研究、Weather/Hotel Agent、Planner 和 JSON 解析 | 确保新输入实际影响行程，同时控制改动范围 |
| `frontend/src/types/index.ts` | modify | 增加 Preference Profile、parse API 类型，并在 `TripFormData` 增加可选 Profile | 保持前后端契约一致 |
| `frontend/src/services/api.ts` | modify | 增加 `parsePreferenceProfile()`；现有 `generateTripPlan()` 不改接口行为 | 复用现有 Axios client 与运行时 API Base URL |
| `frontend/src/views/Landing.vue` | modify | 增加同行人、人数、预算、节奏、扩展兴趣；增加“AI 理解确认”状态；确认后继续走原生成逻辑 | 最小范围实现用户可见价值 |
| `frontend/src/i18n/locales/zh.json` | modify | 增加 Phase 1 中文字段、帮助文本、解析状态和错误提示 | 中文主流程所需 |

以下文件明确不改：

- `frontend/src/views/Result.vue`：Phase 1 只负责输入和 Planner 消费，不改结果页。
- `frontend/src/views/Home.vue`：它不是当前路由页面，不同步维护旧实现。
- `backend/app/services/llm_service.py`：直接复用现有 LLM client，不在本阶段统一 structured output 基础设施。
- `backend/app/services/xhs_service.py`：继续读取现有 `preferences` 搜索景点。
- `backend/app/models` 目录结构：不在 Phase 1 拆分 `schemas.py`。
- `Dockerfile`、依赖清单、部署文件：不安装新依赖。

### 2.1 可选测试文件

编码时建议新增但不引入测试依赖：

| 文件路径 | 操作 | 内容 |
|---|---|---|
| `backend/tests/test_preference_service.py` | add | 使用标准库/unittest 或现有环境可用测试方式，覆盖 merge 与 fallback |
| `backend/tests/test_preference_schemas.py` | add | 覆盖人数、预算、枚举和旧请求兼容 |

如果当前环境没有现成 test runner，不安装依赖；可以使用直接运行的最小验证脚本或 FastAPI OpenAPI/schema 检查替代，但不把临时脚本提交为业务代码。

---

## 3. 复用哪些现有逻辑

### 3.1 前端复用

- 复用 `Landing.vue` 当前三段式表单、Ant Design Vue 表单控件和 `reactive` 状态。
- 复用现有城市列表、日期计算、多城市校验、交通方式和住宿选择。
- 复用 `interestOptions` 和 `togglePreference()`，只补充“拍照、博物馆、城市探索、夜生活、小众景点”等选项。
- 复用 `generateTripPlan()`、WebSocket 进度、sessionStorage 和结果页跳转。
- 复用 `api.ts` 的 Axios client、错误处理和运行时 Base URL。
- 复用 Vue I18n；Phase 1 新体验以中文为主，不删除英日文能力。

### 3.2 后端复用

- 复用现有 `TripRequest` 和 `/api/trip/plan`，只增加一个可选嵌套字段。
- 复用 `get_llm()` 的模型、Key、Base URL 和运行时热配置。
- 复用现有异步任务、WebSocket、持久化和历史计划逻辑。
- 复用 `MultiAgentTripPlanner`、天气 Agent、酒店 Agent、小红书研究、Planner Prompt 和 JSON 修复链路。
- 复用现有 `preferences` 字段供小红书搜索使用；确认后的 Profile interests 会同步投影到这个旧字段。
- 复用 `free_text_input` 作为原始用户文本，保证旧 Planner 和历史任务仍能看到用户原话。

### 3.3 明确不复用的做法

- 不让前端自行解释“妈妈膝盖不好”；语义解析由后端 LLM 完成。
- 不让 LLM 决定用户已显式选择的同行人、人数、预算或节奏。
- 不把解析后的 Profile 仅拼成一段不可见 Prompt；必须返回给前端供用户确认。
- 不把 Preference Parser 包装成长期自治 Agent。Phase 1 它只是一次受 schema 约束的 LLM extraction task。

---

## 4. 最小 PreferenceProfile 数据结构

### 4.1 后端建议结构

```python
class PreferenceConstraints(BaseModel):
    avoid_early_start: bool = False
    earliest_start_time: Optional[str] = None
    mobility_notes: List[str] = []
    food_notes: List[str] = []
    other_notes: List[str] = []


class PreferenceProfile(BaseModel):
    party_type: Literal[
        "solo", "couple", "friends", "family", "with_parents", "with_children"
    ]
    party_size: int
    budget_cny: Optional[int] = None
    pace: Literal["intensive", "balanced", "relaxed"]
    interests: List[str] = []
    special_requirements: str = ""
    constraints: PreferenceConstraints = PreferenceConstraints()
    inferred_interests: List[str] = []
    parsing_notes: List[str] = []
```

这是 Portfolio MVP 的最小结构，不增加 `profile_id`、版本号、权重、Money 对象、通用 Constraint DSL 或持久化实体表。

### 4.2 字段说明

| 字段 | 类型 | 来源 | Phase 1 用途 |
|---|---|---|---|
| `party_type` | enum string | 用户显式选择 | Planner 理解同行关系 |
| `party_size` | int，1–20 | 用户显式输入 | Planner 估算总体安排和预算语境 |
| `budget_cny` | optional int，> 0 | 用户显式输入 | Planner 获得总预算上限；Phase 2 Validator 使用 |
| `pace` | enum string | 用户显式选择 | Planner 控制每日强度 |
| `interests` | list[string] | 用户多选 | 研究关键词和 Planner 偏好 |
| `special_requirements` | string | 用户原文 | 可追溯；继续兼容 `free_text_input` |
| `constraints.avoid_early_start` | bool | LLM 从原文推断 | Phase 1 Planner 输入；Phase 2 Validator 直接使用 |
| `constraints.earliest_start_time` | optional `HH:MM` | LLM 推断 | “不想早起”默认解释为 09:30；若用户给具体时间则使用具体值 |
| `constraints.mobility_notes` | list[string] | LLM 推断 | 如膝盖不好、少爬楼、减少步行 |
| `constraints.food_notes` | list[string] | LLM 推断 | 如不吃辣、素食、过敏信息 |
| `constraints.other_notes` | list[string] | LLM 推断 | 不适合其他最小字段的要求 |
| `inferred_interests` | list[string] | LLM 推断 | 如“喜欢拍照”→“拍照”；合并后供 Planner 使用 |
| `parsing_notes` | list[string] | 解析/合并服务 | 告知用户系统做了哪些解释或存在何种歧义 |

### 4.3 为什么保留 `interests` 和 `inferred_interests` 两组

- `interests` 是用户点击确认的显式偏好，是 authoritative value。
- `inferred_interests` 是从自由文本补充的建议。
- UI 会把两者合并展示，但给推断项标注“AI 识别”。
- 用户可以删除推断项；确认后生成 `effective_interests`，同步回旧的 `TripRequest.preferences`。
- LLM 永远不能移除或降权用户显式选择。

不在最终 Profile 增加独立 `effective_interests` 字段，避免三份列表漂移；提交前由 merge service 以稳定顺序去重得到。

### 4.4 默认值

- `party_type`: `friends` 不适合作为静默默认。UI 必须要求选择；可根据人数提示但不自动提交。
- `party_size`: `1`，当选择情侣时 UI 自动建议 `2`，用户仍可修改。
- `budget_cny`: `null`，不填写即“不设置明确总预算”，不能转换为 0。
- `pace`: `balanced`。
- `avoid_early_start`: `false`。
- “不想早起”但未给时间：解析为 `avoid_early_start=true`、`earliest_start_time="09:30"`，并在 `parsing_notes` 中明确展示该解释，允许用户修改。

---

## 5. 显式选择优先于 LLM 的规则

### 5.1 权威字段

以下字段只能来自用户表单，LLM 输出即使包含也必须丢弃：

- `party_type`
- `party_size`
- `budget_cny`
- `pace`
- 用户显式选择的 `interests`

LLM 只允许输出：

```text
avoid_early_start
earliest_start_time
mobility_notes
food_notes
other_notes
inferred_interests
parsing_notes
```

### 5.2 合并算法

```text
1. 用用户表单建立 Profile base。
2. 如果 special_requirements 为空：跳过 LLM，使用空 constraints。
3. 如果不为空：调用 LLM extraction。
4. 对 LLM 输出做 Pydantic 校验和枚举/时间格式清洗。
5. 只取允许推断的字段。
6. interests = 用户显式 interests，保持原顺序。
7. inferred_interests 去除与显式 interests 重复的项目。
8. 返回完整 Profile + parsing_notes 给用户确认。
9. 用户修改/删除推断项后，提交最终 Profile。
```

如果自由文本与显式选择冲突，例如用户选择“松弛度假”但写“每天尽量多去几个景点”：

- `pace` 仍为 `relaxed`。
- `parsing_notes` 提示“特殊要求与已选择的松弛节奏可能冲突，将以松弛节奏为准”。
- Phase 1 不自动弹出复杂冲突解决器。

### 5.3 LLM 失败策略

解析失败不能阻止旅行规划：

- 返回由显式字段组成的 Profile。
- `special_requirements` 原文仍保留并传给 Planner。
- `parsing_notes` 显示“AI 未能结构化解析，已保留你的原始要求”。
- 前端允许用户直接确认继续。
- 不进行自动重试循环；最多使用现有 LLM timeout 下的一次请求。

这保证“Profile 解析是增强能力，不是现有 Planner 的单点故障”。

---

## 6. API 与数据流

### 6.1 新增解析接口

```http
POST /api/preferences/parse
```

建议请求：

```json
{
  "party_type": "with_parents",
  "party_size": 3,
  "budget_cny": 5000,
  "pace": "balanced",
  "interests": ["美食"],
  "special_requirements": "不想早起，妈妈膝盖不好，喜欢拍照"
}
```

建议响应：

```json
{
  "success": true,
  "profile": {
    "party_type": "with_parents",
    "party_size": 3,
    "budget_cny": 5000,
    "pace": "balanced",
    "interests": ["美食"],
    "special_requirements": "不想早起，妈妈膝盖不好，喜欢拍照",
    "constraints": {
      "avoid_early_start": true,
      "earliest_start_time": "09:30",
      "mobility_notes": ["减少长距离步行", "避免连续爬楼或陡坡"],
      "food_notes": [],
      "other_notes": []
    },
    "inferred_interests": ["拍照"],
    "parsing_notes": ["已将“不想早起”理解为每天 09:30 后开始主要行程"]
  }
}
```

### 6.2 原规划接口的兼容扩展

现有接口保持：

```http
POST /api/trip/plan
```

`TripRequest` 只增加：

```python
preference_profile: Optional[PreferenceProfile] = None
```

前端提交确认后的 Profile 时，同时继续提交现有字段：

- `preferences` = 显式 interests + 用户确认保留的 inferred interests。
- `free_text_input` = 原始 special requirements。
- `transportation`、`accommodation` 保持现有值。
- `preference_profile` = 新结构。

旧客户端不传 `preference_profile` 时，后端行为完全不变。

### 6.3 Phase 1 完整数据流

```text
Landing Form
  ├── existing trip fields
  └── explicit preference fields
          ↓
POST /api/preferences/parse
          ↓
PreferenceService
  ├── build explicit base
  ├── optional LLM extraction
  ├── schema validation
  └── explicit-first merge
          ↓
Preference Review UI
  ├── show explicit values
  ├── label AI-inferred values
  └── user confirm/edit
          ↓
POST /api/trip/plan
  ├── legacy fields retained
  └── preference_profile included
          ↓
existing task / WebSocket / research workflow
          ↓
existing Planner + compact profile summary
          ↓
existing TripPlan response and Result page
```

---

## 7. 用户界面变化

### 7.1 当前 `Landing.vue` 表单调整

保留当前目的地/日期和交通/住宿布局，在兴趣与特殊要求附近增加：

1. **和谁一起去**
   - 独自旅行
   - 情侣
   - 朋友
   - 家庭
   - 带父母
   - 带儿童

2. **出行人数**
   - 数字输入，1–20。

3. **本次旅行总预算**
   - 人民币金额输入。
   - 帮助文案：“选填，不含往返目的地的大交通；后续行程会尽量按此预算安排。”
   - Phase 1 只把预算提供给 Planner，不承诺精确控制；Phase 2 才做超预算 Validator。

4. **旅行节奏**
   - 特种兵：一天希望体验更多地点。
   - 适中：景点与休息平衡。
   - 松弛度假：减少赶路，留出自由时间。

5. **兴趣**
   - 保留历史文化、自然风光、美食、购物、艺术、休闲。
   - 增加拍照、博物馆、城市探索、夜生活、小众景点。

6. **特殊要求**
   - 保留 textarea。
   - 示例文案：“例如：不想早起、妈妈膝盖不好、不吃辣、每天最多走 15000 步、喜欢拍照。”

### 7.2 提交流程变化

当前按钮从直接“生成旅行计划”调整为两个状态：

```text
第一次点击：AI 理解我的需求
  ↓
同页显示 Preference Review Card
  ↓
用户点击：确认并生成行程
  ↓
进入现有生成进度
```

为减少摩擦：

- 没有特殊要求时不调用 LLM，但仍生成 Profile Review。
- Review Card 只展示简短摘要，不做新页面。
- 用户修改任何核心输入后，将旧 review 标记为“需要重新确认”。
- AI 推断的兴趣和约束使用“AI 识别”标签。
- 解析失败时显示提示，但允许继续生成。

### 7.3 Preference Review Card 建议展示

```text
AI 对这次旅行的理解

同行：带父母 · 3 人
预算：¥5,000
节奏：适中
兴趣：美食、拍照（AI 识别）

特别注意
• 不安排过早出发（按 09:30 后理解）
• 妈妈膝盖不适，减少长距离步行和连续爬楼

[返回修改] [确认并生成行程]
```

不展示模型 chain-of-thought，只展示结构化结论和必要的解释。

### 7.4 本阶段不修改结果页的原因

Phase 1 的验收重点是“AI 是否正确理解用户，并把理解传给 Planner”。Recommendation Reason、Risk 和 Profile 在结果页的长期展示属于 Phase 2/3。现在修改 `Result.vue` 会扩大回归范围，降低 MVP 交付确定性。

为了演示 Profile 确实生效，Planner 生成的 `overall_suggestions` 和每日安排应能体现 Profile；编码验收时使用固定案例做人工检查，但不在 Phase 1 增加新的结果组件。

---

## 8. 如何保证现有 Trip Planner 仍然运行

### 8.1 向后兼容原则

1. `TripRequest.preference_profile` 是可选字段，默认 `None`。
2. 所有现有必填字段保持名称、类型和语义不变。
3. `/api/trip/plan`、WebSocket URL、任务状态和 `TripPlanResponse` 不变。
4. `TripPlan`、`DayPlan`、`Attraction` 等输出 schema 不变。
5. Planner 初始化、研究顺序、Agent 数量、LLM JSON 解析和知识图谱构建不变。
6. 旧任务 JSON 和历史结果不需要 migration。

### 8.2 Legacy projection

确认 Profile 后，前端和后端均做兼容投影：

```text
PreferenceProfile.interests
  + confirmed inferred_interests
  → TripRequest.preferences

PreferenceProfile.special_requirements
  → TripRequest.free_text_input

PreferenceProfile.pace / party / budget / constraints
  → Planner query 中新增的“已确认用户偏好”段落
```

即使 Planner 暂时忽略新嵌套对象，旧字段仍能维持原有研究与生成行为。

### 8.3 Planner 改动限制

只修改 `_build_planner_query()`：在现有基本信息之后追加类似内容：

```text
**已确认用户偏好:**
- 同行类型: 带父母，共 3 人
- 总预算: 5000 元
- 旅行节奏: 适中
- 兴趣: 美食、拍照
- 不早起: 每天主要行程不早于 09:30
- 行动需求: 减少长距离步行，避免连续爬楼
```

不修改：

- Planner System Prompt 的 JSON schema。
- `_run_planner_with_retry()`。
- `_parse_response()` 和修复逻辑。
- 小红书、天气、酒店、地图服务。
- 返回给结果页的数据结构。

### 8.4 Feature fallback

- Preference parse API 失败：使用显式字段 + 原始自由文本继续规划。
- Profile 未确认：前端不提交；用户可以返回修改。
- 旧客户端/历史重试不含 Profile：完全走现有逻辑。
- LLM 返回未知兴趣：只保留允许列表中的标准兴趣，其余写入 `other_notes`，不污染研究关键词。
- 预算为空：Planner query 显示“未设置明确预算”，不传 0。

---

## 9. LLM 解析设计

### 9.1 Parser 输入

- 用户显式选择的 party、size、budget、pace 和 interests。
- `special_requirements` 原文。
- 允许的标准兴趣列表。
- 固定目标 schema。

显式字段作为冲突参照，不要求 LLM 重新推断这些值。

### 9.2 Parser 输出约束

LLM 只能输出 JSON：

```json
{
  "avoid_early_start": true,
  "earliest_start_time": "09:30",
  "mobility_notes": ["减少长距离步行"],
  "food_notes": [],
  "other_notes": [],
  "inferred_interests": ["拍照"],
  "parsing_notes": ["将不想早起理解为 09:30 后开始主要行程"]
}
```

Prompt 要求：

- 不覆盖显式字段。
- 不诊断疾病，不给医疗建议；只抽取旅行相关行动限制。
- 不扩写用户未表达的限制。
- `earliest_start_time` 使用 `HH:MM`。
- 推断兴趣必须来自允许列表。
- 无法确定时写入 parsing note，不强行推断。

### 9.3 能力边界文案

Preference Review 下显示：

> AI 会尝试理解你的特殊要求，可能存在偏差。请确认后再生成行程。

这既是风险提示，也让 Portfolio 能展示“人类确认高影响模型推断”的产品设计。

---

## 10. 验收与测试计划

### 10.1 功能验收

| Case | 输入 | 预期 |
|---|---|---|
| 显式字段 | 带父母、3 人、5000 元、适中、美食 | Profile 原样保留，不被 LLM 改写 |
| 不早起 | “不想早起” | `avoid_early_start=true`，默认 09:30，并提示解释 |
| 明确时间 | “每天十点以后再出门” | earliest start 为 10:00 |
| 行动限制 | “妈妈膝盖不好” | mobility notes 提示减少步行/爬楼，不输出医学诊断 |
| 推断兴趣 | “喜欢拍照” | inferred interests 包含“拍照”并标注 AI 识别 |
| 饮食限制 | “不吃辣” | food notes 包含“不吃辣” |
| 显式冲突 | pace=松弛，文本“每天尽量多去几个景点” | pace 保持松弛，显示冲突 note |
| 空自由文本 | 空 | 不调用 LLM，立即生成显式 Profile |
| LLM 失败 | timeout/非法 JSON | 保留显式 Profile 和原文，允许继续 |
| 旧请求 | 不含 preference_profile | `/api/trip/plan` 行为与当前一致 |

### 10.2 Planner 兼容验收

- 单城市旧请求仍能完成。
- 多城市旧请求仍能完成。
- 新请求能持久化 `preference_profile`。
- Planner query 能看到确认后的 party、budget、pace、constraints。
- `TripPlanResponse` JSON 结构不变，当前 Result 页面无需修改即可打开。
- 小红书搜索仍得到非空 `preferences`；推断兴趣去重后同步进入旧字段。
- Profile parse 失败不影响 `/api/trip/plan`。

### 10.3 前端验收

- 所有新增输入在移动端和桌面端可操作。
- 人数和预算有合法范围校验。
- 输入改变后旧 Profile Review 自动失效。
- 解析中有 loading，失败有可继续路径。
- 用户可以返回修改，也可以确认并进入现有进度页。
- 浏览器刷新不会破坏已有历史计划入口。

### 10.4 不以此作为 Phase 1 成功标准

- 预算一定不超标：Phase 2 Validator 才保证。
- 每天一定 09:30 后开始：现有 TripPlan 没有正式 timeline 字段；Phase 1 只能通过 Planner 指令尽量体现。
- 距离、预约和强度一定合理：Phase 2 处理。

清楚区分“模型收到偏好”与“系统验证偏好得到满足”，是本阶段最重要的能力边界。

---

## 11. 实施顺序

编码获批后按以下小步进行：

1. 增加后端 schema 和纯 deterministic merge，先不接 LLM。
2. 增加 Preference Parser service 和 `/api/preferences/parse`。
3. 用 curl/API contract 验证空文本、正常解析、失败 fallback。
4. 增加前端类型与 API 方法。
5. 扩展 Landing 表单和 Preference Review。
6. 将确认 Profile 随现有 TripRequest 提交。
7. 在 Planner query 中追加紧凑 Profile 摘要。
8. 运行旧请求、新请求、单城市、多城市兼容测试。
9. 记录实际修改文件、数据流、测试结果和已知限制。

任何一步若破坏现有 Planner，都先恢复兼容路径，不扩大成架构重写。

---

## 12. Phase 1 完成标准

Phase 1 只有同时满足以下条件才完成：

1. 用户可选择同行人、人数、预算、节奏和兴趣，并填写特殊要求。
2. 特殊要求可被 LLM 解析为有限结构化字段。
3. 显式字段不会被 LLM 覆盖。
4. 用户能看到、修改并确认 AI 的理解。
5. 解析失败时仍能继续使用现有 Planner。
6. 确认后的 Profile 被保存在任务请求中并进入 Planner query。
7. 旧请求、旧任务与当前 Result 页面保持兼容。
8. 没有新增依赖、数据库、v2 API 或大规模文件重构。

本文完成后停止，等待用户确认后再开始 Phase 1 编码。
