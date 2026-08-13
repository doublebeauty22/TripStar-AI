# Phase 2 — Minimal Validator / Critic Loop Implementation Plan

> 状态：Phase 2A CLOSED；Phase 2B CLOSED / PASS；Phase 2C CLOSED / PASS
> 前置能力：Phase 1 Preference Profile、Phase 1.5 Google POI enrichment
> 本阶段边界：只实现 earliest start、mobility、budget、route feasibility 的最小 Validator/Critic 闭环；最多 revision 一次。

## Project Status（2026-08-10）

| Workstream | Status |
|---|---|
| P0 Cost Optimization | CLOSED |
| Phase 2A — Deterministic Validator | CLOSED |
| Phase 2B — Critic + Single Revision Loop | CLOSED / PASS |
| Phase 2C — Conversational Trip Editing / Local Patch | CLOSED / PASS |

Phase 2B acceptance：

- Automated Acceptance: PASS
- Real Human Acceptance: PASS
- Real OpenAI Cost Acceptance: PASS
- P0 Guardrail Integration: PASS
- Regression: PASS
- Known Limitation: Revision POI grounding stability

### Phase 2B Final Acceptance Evidence

真实人工验收案例：

- `task_id=df938d69`
- `generation_id=1ca54c14-5925-43fa-bf71-1c6234dc7ccd`
- 东京，2026-08-20 至 2026-08-22，3 天，美食偏好
- confirmed earliest start `10:30`
- mobility constraint：同行妈妈膝盖不好

实际执行链路完成一次且仅一次 Critic/Revision：

```text
POI Enrichment #1: calls=9, verified=7, partial=2, unverified=0
Validator #1: status=degraded, risks=3, route_api_calls=4
Critic: calls=1
Revision: calls=1
POI Enrichment #2: calls=9, verified=6, partial=3, unverified=0
Validator #2 / Final: revision_count=1, status=degraded, risks=2
STOP
```

Final risks 均为 `validation_unavailable` / `info` / `revisable=false`，分别表示第 2、3 天部分路线缺少 verified Google POI 或地图路线数据。该 degraded 状态是对数据不足的诚实表达，不是 Phase 2B failure，也不会触发第二轮 Critic。

P0 generation usage：

```text
preference_calls=1
xhs_calls=0
planner_calls=1
critic_calls=1
revision_calls=1
repair_calls=0
total_logical_calls=4  # <= MAX_LLM_CALLS_PER_TRIP=5
prompt_tokens=10139
completion_tokens=8456
total_tokens=18595
retry_count=0
model=gpt-5.6-luna
```

分阶段 token：Planner `4203/4514/8717`，Critic `820/422/1242`，Revision `4760/3350/8110`（prompt/completion/total）。Preference 已使用 generation-level logical call #1，因此 Planner 从 logical call #2 开始。

### Known Limitation — Revision POI Grounding Stability

Revision 后 POI grounding 可能轻微退化。本次真实案例从 `verified=7, partial=2` 变为 `verified=6, partial=3`。Revision 可能改写 POI 文本表达，使原本 verified 的地点在重新 enrichment 后变为 partial，进而令 route validation 保持 unavailable/degraded。

这不是 Phase 2B acceptance blocker。后续可考虑：

- 仅修改 transportation / schedule 时禁止改写无关 POI 名称。
- 在 `protected_elements` 中进一步保护 canonical POI names / place identity。
- 对仅交通方式修改的 revision 尽可能保持 POI identity stable。

Phase 2B 验收当时未实施这些优化；后续 Phase 2C 仍以保持未修改 POI identity 为硬约束。

### Phase 2C Automated Implementation（2026-08-10）

Phase 2C 已实现用户主动触发的局部修改链路：

```text
User instruction
→ one-call TripPatch Interpreter (stage=trip_patch, max logical calls=1)
→ strict discriminated TripPatch schema
→ deterministic Patch Engine
→ target-only POI re-enrichment when needed
→ unchanged Phase 2A Validator
→ deterministic diff + persisted result
→ STOP
```

状态：

- Automated Acceptance: PASS
- Real Human Acceptance: PASS
- Real OpenAI Cost Acceptance: PASS
- Regression: PASS
- Phase 2C: CLOSED / PASS
- Phase 3: NOT STARTED

首版支持 `update_start_time`、`remove_poi`、`replace_poi`、`add_poi`、`update_transport`、`update_meal`、`update_day_pace`。城市/日期/天数/整体重设计、酒店替换及需要广泛重规划的预算修改返回 `requires_regeneration=true`，不会调用 Planner。

安全边界：

- 每次 edit 使用独立 `patch_request_id` 和 `max_calls=1`，不复用已结束的 initial generation budget。
- `plan_version` + task-level lock 防止单实例内 lost update。
- `patch_request_id` 结果持久化用于幂等去重。
- 未受影响 day 必须 deep-equal；未修改 POI 不重新 enrichment。
- 新增/替换 POI 只有重新 grounding 为 verified 后才能提交。
- Patch 后直接运行原 Phase 2A Validator 并 STOP，不进入 Phase 2B Critic/Revision。
- 所有步骤完成并成功持久化后才 commit；任何异常保留原计划。

自动化结果：backend `102 total / 101 passed / 1 skipped / 0 failed`；frontend `17 passed / 0 failed`；`vue-tsc`、production build、`git diff --check` 均通过。Skipped 为按策略禁用的真实 Google Maps E2E。本轮未调用真实 OpenAI API。

首版未实现 single-level Undo。并发锁为当前单实例内存 task lock；多进程/多实例部署需要共享锁或数据库版本 CAS。

### Phase 2C Final Acceptance Evidence（2026-08-10）

真实人工验收任务：`task_id=df938d69`。持久化版本链为：

```text
v1
→ update_start_time（Day 2，11:00）→ v2
→ replace_poi（上野公园 → 东京国立科学博物馆）→ v3
→ remove_poi（阿美横町）→ v4
→ add_poi（上野恩赐公园不忍池畔休憩区）→ v5
→ unsupported city change（东京 → 大阪，requires_regeneration=true）→ remains v5
```

每个成功 patch 的 persisted deterministic diff 均只包含 Day 2（数组索引 `[1]`），Day 1 / Day 3（索引 `[0, 2]`）在相邻持久化快照中 deep-equal；未被操作的 Day 2 POI 对象及 Google identity 也保持稳定。unsupported 请求没有 updated plan、没有 diff、没有 version increment。

POI grounding 证据：

- `replace_poi` 仅重新 grounding replacement target；东京国立科学博物馆最终为 `verified`、`map_data_source=google_places`，具有 Google `place_id=ChIJ8Vuh65yOGGARyj4L5IBFiIk`、真实坐标和地址。
- `add_poi` 仅重新 grounding added target；“上野恩赐公园不忍池畔休憩区”最终为 `verified`、`map_data_source=google_places`，具有 `place_id=ChIJw2qQRZuOGGARWmROEiM2y7E`、真实坐标和地址。该文本是用户可见 candidate/display name，grounding identity 对应 Google 上野公园实体。
- 代码在 enrichment 前将新增/替换 POI 标记为 `unverified` / `llm_unverified` 且不接受 LLM 地图字段；只有 target enrichment 返回 `verified` 才允许 Validator 与 commit。

LLM/cost 证据：每次 edit 使用独立 `stage=trip_patch`、`max_calls=1`，完成后直接 deterministic apply → optional target grounding → Phase 2A Validator → persistence → STOP，不调用 Planner、Critic 或 Revision。真实 replace case：`logical_call=1`、prompt `2824`、completion `249`、total `3073`、retry `0`。其他真实 patch 的逐次 token 明细未持久化，记为 unavailable，不作推测。

Final regression：backend `106 total / 105 passed / 1 skipped / 0 failed`；frontend Node `23 passed / 0 failed`；`vue-tsc`、Vite production build、`git diff --check` 均通过。Skipped 为按策略禁用的真实 Google Maps E2E。本次 final audit 未调用真实 OpenAI API、未生成 TripPlan。

Known limitations（非 blocker）：single-level Undo 未实现；task lock 仅保证当前单实例进程内并发安全，多进程/多实例部署仍需共享锁或数据库 CAS；逐次 patch token summary 当前只写安全日志、未写入 task persistence。

## 1. 目标与产品原则

Phase 2 的目标不是增加更多“Agent”，而是让用户看到：AI 生成的行程在展示前经过了可解释、可复现的质量检查，并在存在明确可修复问题时只进行一次受控修订。

最小闭环：

```text
Planner
→ Google POI enrichment
→ Deterministic Validator
→ 条件触发 LLM Critic
→ Planner Revision（最多一次）
→ 再次 POI enrichment
→ 再次 Deterministic Validator
→ Final Plan + Final Risks
```

设计原则：

- 地图事实只使用 `verified` POI 和地图 API 返回值。
- LLM Critic 不负责判定距离、预算是否超标等事实，只负责根据 Validator 证据提出修订指令和权衡。
- 不把每一个步骤包装成 Agent。Validator、路线检查、预算求和均为普通 deterministic service。
- Revision 最多一次，避免循环、延迟和 LLM 成本失控。
- 所有新增 schema 字段保持 optional/default，兼容旧请求、旧任务 JSON 和旧 `/api/trip/plan`。
- 数据不足应显示“未能完整验证”，不能把未知伪装成安全或冲突。

## 2. 当前架构只读审计

### 2.1 Preference Profile

当前 `PreferenceProfile` 已提供：

- `budget_cny: Optional[int]`
- `constraints.earliest_start_time: Optional[HH:MM]`
- `constraints.avoid_early_start: bool`
- `constraints.mobility_notes: List[str]`
- `party_size`
- `pace`

当前能力边界：

- `earliest_start_time` 是结构化值，可成为硬约束。
- `avoid_early_start=true` 但时间为空时，不存在可计算阈值，只能提示用户补充，不能判定违规。
- `mobility_notes` 仍是自然语言数组，尚无 `max_walking_distance`、`max_steps` 或无障碍等级。因此 P0 不能从 note 中确定性推导任意精确数字。
- `budget_cny` 定义为整个同行群体在目的地旅行期间的当地消费总预算，不含往返目的地的大交通。

### 2.2 TripPlan / Planner 输出

当前 `TripPlan` 包含：

- 每日有序 `attractions`
- 景点 `visit_duration`、`ticket_price`
- 酒店和餐饮 `estimated_cost`
- 汇总 `Budget`
- POI enrichment 后的 `place_id`、`poi_match_status`、`map_data_source`

当前缺口：

- `DayPlan` 没有 `start_time`。
- Attraction 没有计划抵达/开始时间。
- 路线 leg 没有持久化在 TripPlan 中。
- `Budget.total` 和各分项来自 Planner，数值可计算但不等于已验证的真实价格。
- 酒店价格是“每晚”还是整个团队房费、餐费是否已乘人数，目前 Prompt 约束不够明确。

因此第一版必须给 `DayPlan` 增加可选 `start_time`，否则 earliest-start Validator 没有 deterministic 输入。暂不引入完整 Timeline/ItineraryNode。

### 2.3 地图和路线能力

当前 `GoogleMapService.plan_route()` 使用 Directions REST，返回：

- `distance`: 米
- `duration`: 秒
- `distance_text`
- `duration_text`
- `route_type`
- `data_source=google_maps`

可用于确定性检查的前提：

- 起终点均为 `poi_match_status=verified`
- `map_data_source=google_places`（未来也可接受可靠的 `amap`）
- 地址/坐标来自地图 API
- Directions 调用成功

当前 `plan_route()` 接收地址。Phase 2 不迁移 Routes API，不改路线实现；按每天景点顺序检查相邻 attraction legs，并做本次 validation 内的内存去重。

### 2.4 可 deterministic validation 的约束

| 约束 | 第一版能否 deterministic | 依据 | 限制 |
|---|---|---|---|
| earliest start | 可以，但需新增 `DayPlan.start_time` | 用户确认的 HH:MM 与日计划 HH:MM 比较 | 用户未选时间时只能 info |
| budget | 可以检查计划数值和用户上限 | `Budget` 数值字段、`budget_cny` | 只能验证计划估算，不代表真实价格准确 |
| route feasibility | 可以，限 verified POI | Directions distance/duration、visit duration | 未验证 POI 或 API 失败时为 unknown |
| mobility | 可以做保守 heuristic | mobility note 是否存在 + verified walking legs | 不能从自然语言推导精确个人能力 |

不应在本阶段做 deterministic hard constraint 的内容：开放时间、天气风险、真实无障碍设施、预约可用性、餐厅营业时间、真实步数、实时交通。这些缺少稳定事实字段或数据源。

## 3. 最小 Schema 设计

### 3.1 DayPlan 增量

```python
start_time: Optional[str] = Field(
    default=None,
    pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    description="当天主要行程计划开始时间 HH:MM",
)
```

规则：

- Planner 新输出必须尽量填写。
- 旧计划缺失时仍可解析。
- 第一版只检查每日主要行程开始时间，不增加每个景点 Timeline 时间。

### 3.2 RiskItem

```python
RiskSeverity = Literal["info", "warning", "blocking"]
RiskType = Literal[
    "earliest_start",
    "mobility",
    "budget",
    "route_feasibility",
    "validation_unavailable",
]

class RiskItem(BaseModel):
    id: str                         # deterministic stable id，如 budget:trip
    type: RiskType
    severity: RiskSeverity
    day_index: Optional[int] = None
    related_poi_names: List[str] = []
    title: str
    message: str
    evidence: Dict[str, Any] = {}   # 结构化数字/来源，不放 LLM 推测
    suggestion: str = ""
    source: Literal["rule_validator", "critic"] = "rule_validator"
    revisable: bool = True
```

`evidence` 示例：

```json
{
  "constraint": "10:00",
  "planned_start": "08:30"
}
```

```json
{
  "budget_limit_cny": 5000,
  "plan_total_cny": 5680,
  "over_by_cny": 680,
  "budget_scope": "destination_local_spend_excluding_round_trip"
}
```

```json
{
  "origin": "浅草寺",
  "destination": "明治神宫",
  "distance_m": 12400,
  "duration_s": 3100,
  "route_type": "transit",
  "data_source": "google_maps"
}
```

### 3.3 ValidationResult

```python
class ValidationResult(BaseModel):
    status: Literal["passed", "issues_found", "degraded"]
    risks: List[RiskItem] = []
    checked_rules: List[str] = []
    unavailable_checks: List[str] = []
    route_api_calls: int = 0
    should_trigger_critic: bool = False
```

### 3.4 CriticResult

```python
class CriticResult(BaseModel):
    should_revise: bool
    revision_instructions: List[str] = []
    protected_elements: List[str] = []
    summary: str = ""
```

Critic 不生成 distance/budget 等新事实，不新增 RiskItem 数字，不输出完整 TripPlan。

### 3.5 TripPlan 增量

```python
risks: List[RiskItem] = []
validation_status: Optional[Literal["passed", "issues_found", "degraded"]] = None
revision_count: int = 0
```

这些字段均有默认值，保证旧任务恢复和旧 API 调用兼容。

## 4. Deterministic Validator 规则

### 4.1 Earliest start

输入：

- `PreferenceProfile.constraints.earliest_start_time`
- 每个 `DayPlan.start_time`

规则：

- 用户时间存在且 `day.start_time < earliest_start_time`：`blocking`。
- 用户时间存在但某天 `start_time` 缺失：`warning`，表示无法确认约束满足。
- `avoid_early_start=true` 且时间为空：单个 trip-level `info`，提示用户确认具体时间；不触发 revision。
- 没有相关偏好：跳过检查。

### 4.2 Budget

输入：

- `PreferenceProfile.budget_cny`
- `TripPlan.budget`

先做内部一致性检查：

```text
component_sum = attractions + hotels + meals + transportation + inter_city_transport
```

规则：

- `Budget` 缺失或字段不可用：`warning`，validation degraded。
- `total != component_sum`：`warning`，revision 应先修正汇总。
- `total > budget_cny`：`blocking`。
- 超预算 evidence 必须注明预算口径“不含往返目的地大交通”。
- 未提供 `budget_cny`：不判断超预算，但仍可检查内部加总一致性。

本阶段不重新估价、不联网验证票价，也不推断汇率。

### 4.3 Route feasibility

输入：每天按顺序排列的 verified attractions。

流程：

```text
Day attractions
→ 相邻两两成 leg
→ 双方 verified 才调用 plan_route
→ 累计 distance/duration
→ 与固定 MVP policy 比较
```

建议 MVP policy（必须作为具名常量并写测试，不宣称行业标准）：

- 单段交通 `duration > 90 分钟`：`warning`
- 单段交通 `duration > 150 分钟`：`blocking`
- 单日景点间交通累计 `duration > 180 分钟`：`warning`
- `visit_duration + verified route duration > 10 小时`：`warning`

route mode：

- request transportation 明确为步行时用 walking。
- 公共交通/地铁/公交用 transit。
- 自驾/出租车用 driving。
- 无法识别时默认 transit，并在 evidence 标注 policy default。

未知处理：

- 任一 POI 非 verified：不调用 Directions；记录该 leg 未验证。
- 部分 legs 未验证：route check 为 degraded，可产生一个合并的 `info`，不能说路线合理或不合理。
- Directions 失败：记录 `validation_unavailable/info`，不阻塞 Planner 主链路。

### 4.4 Mobility

第一版不解析 mobility note 中的任意数字，也不声称知道用户医学能力。

当 `mobility_notes` 非空时，启用保守 walking policy：

- verified walking leg `distance > 800m`：`warning`
- verified walking leg `distance > 1500m`：`blocking`
- 单日 verified walking distance 累计 `> 4000m`：`warning`

要求：

- 阈值是产品 MVP heuristic，必须在 UI/文档中说明，不是医疗建议。
- 如果主交通方式是 transit，仍可用 walking 查询检查相邻 POI 的“纯步行替代距离”，但结果只用于风险提示；Revision 建议优先增加交通工具、减少跨区，不直接删除用户核心兴趣。
- 地图数据不可用时只返回 info，Critic 不得自行估算步行距离。

## 5. Critic 职责与触发条件

Critic 使用 LLM，但不是事实校验器。

### 5.1 输入

- `PreferenceProfile`
- 原始 `TripPlan` 的紧凑摘要
- Validator `RiskItem[]`
- 明确的不可改变约束
- revision 次数（必须为 0 才允许建议 revision）

### 5.2 输出

- 是否值得 revision
- 按优先级排列的最小修订指令
- 应保护的内容，例如用户显式兴趣、旅行天数、城市顺序
- 简短权衡说明

### 5.3 触发条件

触发 Critic：

- 至少一个 `blocking`；或
- 至少一个 `revisable=true` 的 `warning`。

不触发：

- 只有 `info`；
- 风险全部来自数据不可用；
- 已经 revision 一次；
- 没有 Preference Profile 且不存在可修订的 deterministic warning。

Critic 可返回 `should_revise=false`，例如问题来自地图不可用、无法通过改行程解决，或 revision 可能损害更高优先级显式偏好。

## 6. 最多一次 Revision 状态流

```text
INITIAL_PLAN
  ↓ parse
POI_ENRICHMENT_1
  ↓
VALIDATION_1
  ├─ passed / info only ───────────────→ FINALIZE
  ├─ validator degraded only ─────────→ FINALIZE_WITH_INFO
  └─ actionable warning/blocking
          ↓
       CRITIC
          ├─ fail / should_revise=false → FINALIZE_ORIGINAL_WITH_RISKS
          └─ should_revise=true
                    ↓
               REVISION_1
                    ├─ fail/parse fail → FINALIZE_ORIGINAL_WITH_RISKS
                    ↓
               POI_ENRICHMENT_2
                    ↓
               VALIDATION_2
                    ↓
               FINALIZE_REVISED_WITH_FINAL_RISKS
```

硬性状态规则：

- `revision_count` 只能为 0 或 1。
- 第二次 Validation 即为最终结果，仍有 blocking 也不再循环。
- Revision 不重新运行 XHS、天气、酒店 research；使用原有 research context、原计划、Validator evidence 和 Critic 指令。
- Revision 必须返回完整 TripPlan JSON，以复用现有解析和 Result 数据流。
- Revision 后重新 enrichment，因为景点顺序或景点本身可能变化。
- 最终 `risks` 必须来自第二次 Validator；原始风险只进入日志/调试，不展示为当前风险。

## 7. Planner Revision Prompt 边界

Revision Prompt 应要求：

- 仅修复列出的 deterministic risks。
- 保持城市、日期、天数、用户显式兴趣和未受影响日程。
- 不编造地图 distance/duration。
- 不填写 `place_id`、地图地址来源或风险字段。
- earliest start 问题只调整 `DayPlan.start_time` 和当天安排。
- mobility/route 问题优先重排同日景点、减少跨区或改变交通建议。
- budget 问题优先替换高费用项目、降低住宿/餐饮估算，并重算全部预算分项。
- 输出完整合法 JSON。

Critic 的 revision instructions 作为建议；Planner 必须遵守 Validator 给出的结构化约束和 protected elements。

## 8. 失败降级设计

| 失败点 | 产品行为 | 是否中断原 Planner |
|---|---|---:|
| Validator 内部非地图异常 | 保留计划，`validation_status=degraded`，返回 info | 否 |
| Google/Directions 未配置或失败 | 跳过对应 legs，明确“路线未完整验证” | 否 |
| POI 未 verified | 不使用其坐标/地址做路线事实 | 否 |
| Critic LLM 失败/超时 | 返回原计划和 deterministic risks | 否 |
| Critic JSON 解析失败 | 同上 | 否 |
| Revision LLM 失败/超时 | 返回原计划和 deterministic risks | 否 |
| Revision TripPlan 解析失败 | 返回原计划和 deterministic risks | 否 |
| Revision 后仍有 blocking | 返回 revised plan + final blocking risks，不再循环 | 否 |
| 初始 Planner 失败 | 沿用现有 task failed 行为 | 是 |

日志必须区分：

- `VALIDATOR_COMPLETED`
- `VALIDATOR_DEGRADED`
- `CRITIC_SKIPPED`
- `CRITIC_FAILED_FALLBACK`
- `REVISION_APPLIED`
- `REVISION_FAILED_FALLBACK`
- `FINAL_VALIDATION_COMPLETED`

不得在日志中输出 API Key 或完整敏感 Preference 原文。

## 9. 完整数据流

```text
TripRequest
  ├─ PreferenceProfile
  └─ existing trip inputs
        ↓
Existing research services
        ↓
Planner LLM → TripPlan(start_time optional)
        ↓
Google Places enrichment
        ↓
RuleValidator
  ├─ time comparison
  ├─ budget arithmetic/comparison
  ├─ verified adjacent POI route calls
  └─ mobility walking heuristics
        ↓ ValidationResult
Actionable risks?
  ├─ no → attach final risks
  └─ yes → Critic LLM → CriticResult
                         ↓ if should_revise
                    Planner revision once
                         ↓
                    enrichment + validation
        ↓
TripPlan(risks, validation_status, revision_count)
        ↓
Existing TripPlanResponse / task persistence / WebSocket / status API
        ↓
Result minimal Travel Risk section
```

## 10. 文件级修改计划

| 文件路径 | 操作 | 计划内容 | 优先级 | 风险 |
|---|---|---|---|---|
| `backend/app/models/schemas.py` | modify | 增加 `DayPlan.start_time`、`RiskItem`、`ValidationResult`、`CriticResult`；给 TripPlan 增加可选 validation 字段 | P0 | schema 兼容；必须全部有默认值 |
| `backend/app/services/trip_validator_service.py` | add | 四类 deterministic rules、route leg 检查、阈值常量、ValidationResult | P0 | 地图调用延迟、未知数据处理 |
| `backend/app/services/travel_critic_service.py` | add | 使用现有 LLM adapter，生成严格 CriticResult；超时/解析 fail-open | P0 | LLM 输出稳定性与成本 |
| `backend/app/agents/trip_planner_agent.py` | modify | Planner 后编排 enrichment → validator → critic → 最多一次 revision → revalidate；Prompt 增加 start_time | P0 | 当前主链路核心文件，需小步修改 |
| `backend/app/services/google_map_service.py` | keep | 复用现有 `plan_route()`；本阶段不迁移路线 API | P0 | Directions 可用性 |
| `backend/app/services/llm_service.py` | keep | 复用当前兼容 adapter，不改模型参数逻辑 | P0 | 无 |
| `backend/app/api/routes/trip.py` | modify | 增加 validating/critic/revising progress stage 映射；响应结构沿用 TripPlanResponse | P0 | WebSocket/polling stage 兼容 |
| `frontend/src/types/index.ts` | modify | 对齐 start_time、RiskItem、TripPlan validation 字段和新增 task stages | P0 | TS 类型兼容 |
| `frontend/src/views/Landing.vue` | modify | 仅补充新 progress stage 的中文 loading 文案 | P0 | 低 |
| `frontend/src/views/Result.vue` | modify | 最小展示“AI 检查到的问题”；不重构结果页 | P0 | 控制 UI 范围 |
| `frontend/src/i18n/locales/zh.json` | modify | Validator/Critic progress 与风险区中文文案 | P0 | 低 |
| `frontend/src/i18n/locales/en.json` | modify | 英文 fallback 文案 | P0 | 低 |
| `backend/tests/test_trip_validator_service.py` | add | 四类规则、degraded、route call 去重测试 | P0 | 阈值断言需稳定 |
| `backend/tests/test_travel_critic_service.py` | add | 触发条件、JSON 解析、fail-open | P0 | mock LLM，不调用真实模型 |
| `backend/tests/test_planner_validation_workflow.py` | add | 无风险、revision 一次、revision 失败、二次仍失败等状态流 | P0 | orchestration mock 设计 |
| `frontend/tests/validatorRiskDisplay.test.cjs` | add | 风险渲染和新 stage 静态/最小行为检查 | P0 | 不引入测试依赖 |

不新增数据库、repository abstraction、队列、缓存系统或 `/api/v2`。

## 11. 测试案例

### 11.1 Earliest start

1. 用户确认 `10:00`，Day 1 `08:30` → blocking。
2. 用户确认 `10:00`，Day 1 `10:00` → pass。
3. 用户确认 `10:00`，Day 1 缺 start_time → warning/degraded。
4. `avoid_early_start=true`、时间为空 → info，不触发 Critic。

### 11.2 Mobility

5. mobility notes 存在，verified walking leg 600m → pass。
6. walking leg 1000m → warning。
7. walking leg 1800m → blocking。
8. 当日 verified walking 总距离 4500m → warning。
9. POI unverified 或 Directions 失败 → info/unknown，不制造距离。

### 11.3 Budget

10. 用户预算 5000，计划 4800，分项加总正确 → pass。
11. 用户预算 5000，计划 5600 → blocking，over_by=600。
12. total 与分项和不一致 → warning。
13. Budget 缺失 → warning/degraded。
14. 用户未设预算但分项正确 → 不判断超预算。

### 11.4 Route feasibility

15. verified 相邻 POI 30 分钟 → pass。
16. 单段 100 分钟 → warning。
17. 单段 160 分钟 → blocking。
18. 单日 route 累计超过 180 分钟 → warning。
19. visit + route 超过 10 小时 → warning。
20. 同一 validation 中重复 leg → 复用内存结果，不重复 API call。

### 11.5 Critic / Revision workflow

21. 只有 info → Critic 不调用。
22. blocking → Critic 调用一次，revision 一次。
23. Critic `should_revise=false` → 原计划 + risks。
24. Critic 超时 → 原计划 + risks，任务 completed。
25. Revision JSON 无效 → 原计划 + risks，任务 completed。
26. Revision 后解决问题 → final risks 清空/只剩 info，revision_count=1。
27. Revision 后仍 blocking → 返回 final blocking，绝不二次 revision。
28. 新景点出现在 revision → 再 enrichment 后才做路线检查。

### 11.6 Compatibility / regression

29. 不带 PreferenceProfile 的旧 `/api/trip/plan` 仍成功。
30. 不带 start_time/risks 的旧任务 JSON 可恢复。
31. XHS fallback、Google key separation、POI enrichment/photo fallback 不回归。
32. WebSocket completed、polling fallback、刷新恢复不回归。
33. 前端 build 与现有全部测试通过。

## 12. 实施阶段与独立验收

### Phase 2A — Schema + Deterministic Validator（CLOSED）

用户可见结果：计划携带结构化风险，但暂不自动 revision。

验收：四类规则可独立测试；地图未知明确 degraded；旧 API 兼容。

### Phase 2B — Critic + One Revision（CLOSED / PASS）

用户可见结果：存在可修复风险时，系统最多自动修订一次，并展示最终风险。

验收：所有状态流可 mock；Critic/Revision 失败不会使原 Planner 失败；无循环。

### Phase 2C — Conversational Trip Editing / Local Patch（AUTOMATED IMPLEMENTATION PASS）

用户可见结果：Result 页面 AI Chat 可切换到局部修改模式，提交自然语言修改并展示 deterministic change summary、未修改天数以及最终风险。

验收：自动化实现已通过；真实人工验收尚未开始。

## 13. 阶段边界：明确不做

本 Phase 2 不做：

- Recommendation Score / Recommendation Reason
- Chat Patch / What-if local replanning
- 多方案比较
- PlanVersion / PlanPatch
- 数据库与正式 persistence migration
- Analytics backend
- Offline evaluation 平台实现
- 开放时间、天气、预约的完整事实校验
- Knowledge Graph 改造
- Routes API 迁移
- 通用 POI entity resolution
- 实时交通、真实步数或医学级 mobility 判断
- 多轮 self-reflection 或无限 revision

完成 Phase 2C 后应停止并做端到端验收，再决定是否进入 Recommendation Reason 或 Chat Patch。
