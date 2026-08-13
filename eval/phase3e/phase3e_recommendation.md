# Phase 4 Recommendation — Pacing-aware Planner Optimization

## Decision

Phase 4 推荐方向：**Pacing-aware Planner Optimization**。

最高优先用户问题：用户选择 balanced/relaxed pace 后，计划仍可能把 POI 停留、跨区/跨城移动、接驳、排队、用餐与休息拆开处理，交付主题合理但实际过密的 itinerary。

证据：Shenzhen、Chengdu、Lijiang 的 pacing 均为 2/5，覆盖不同城市和 trip shapes；initial usefulness mean 仅 2.83。该问题有直接 human rationale、现有 route/visit-duration evidence、validator/revision seam 和可复测指标。

## Candidate ranking

评分：1 低、5 高；Engineering Effort 与 Implementation Risk 的 5 表示成本/风险高，不是正向分。不存在综合总分。

| Rank | Candidate | User Value | Severity Reduction | Frequency | Differentiation | AI PM Portfolio | Measurability | Eng. Effort | Impl. Risk | Architecture Fit |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | B. Pacing-aware Planner Optimization | 5 | 4 | 3 | 5 | 5 | 5 | 3 | 3 | 5 |
| 2 | A. Route / Grounding Reliability | 5 | 4 | 5 | 5 | 5 | 5 | 5 | 5 | 3 |
| 3 | E. Multi-city Structured Output Robustness | 5 | 5 | 1 | 4 | 5 | 5 | 3 | 3 | 4 |
| 4 | D. Patch Semantic Consistency | 4 | 4 | 1 | 5 | 5 | 5 | 3 | 3 | 5 |
| 5 | C. Provenance & Trust UX | 4 | 3 | 5 | 5 | 5 | 4 | 3 | 2 | 4 |
| 6 | F. Budget / Cost Realism | 4 | 3 | 2 | 3 | 4 | 3 | 4 | 4 | 3 |

## Why this direction

- 它针对 3/6 initial cases 的直接人工低分，而非仅 technical coverage。
- 问题属于可控的 Planner behavior；不要求先解决所有 provider availability。
- 可利用已存在的 visit duration、route checks、pace profile、Validator 和 Revision。
- 能形成清晰 AI PM story：从 deterministic coverage 走向 human-perceived feasibility，并用同一 frozen cases 做 before/after comparison。

## Why not the others now

- Route/Grounding：频率最高，但混合 provider/data、entity matching、routing 与 UX 多层问题，作为一个 Phase 4 scope 过大；且 unavailable 不能归因 Planner。
- Multi-city robustness：单次最严重，应进入紧邻 backlog；但只有 1 个尝试样本，当前先做完整主方向会让决策过度依赖一个 failure。
- Patch consistency：证据很强且 readiness 高，但只有 1 个 patch case；适合作为后续窄修复。
- Provenance UX：信任价值高，但不能直接解决过密和不可执行路线。
- Budget realism：存在真实 gap，但当前 arithmetic/limit satisfaction 稳定，优先级低于直接可执行性。

## Minimum useful scope

1. 定义 `daily_load`：POI visit minutes + known route minutes + configurable meal/rest buffer；unknown route 保持 unknown，不估成 0。
2. 为 `relaxed`、`balanced`、`intensive` 建立 provisional、可配置的 daily-load policy；阈值不宣称普适真理。
3. Planner prompt/context 明确消费 pace 与 daily-load budget，生成阶段限制单日活动密度。
4. Validator 新增 pace-aware actionable risk，明确区分 verified overload 与 route-unknown uncertainty。
5. Revision 只重排/删减受影响日期，并重新验证日期、预算、route 与约束。
6. 用现有 6 initial artifacts/cases 做 baseline；candidate 只运行经单独批准的 controlled evaluation，并人工复评 pacing/usefulness。

## Non-goals

- 不重构 Google/AMap/XHS provider；不保证 route availability 100%。
- 不建设 dashboard、在线 A/B、用户 memory 或 LLM judge。
- 不同时重写 patch engine 或 multi-city architecture。
- 不把 unknown route 当作 0 分钟；不凭空制定“20%改善”目标。

## Success metrics

- Human `pacing_quality`：candidate 相对同 case baseline 的逐 case变化。
- Human `usefulness`：确保 pacing 改善没有降低可执行信息质量。
- Verified overload day count / actionable pacing risk count。
- Explicit pace-policy satisfaction rate。
- Route-check coverage 与 unknown coverage：不得因删数据制造假改善。
- Preference satisfaction、budget arithmetic、date/day consistency：non-regression。
- Revision risk resolution rate 与 unaffected-day preservation。
- Total tokens、logical calls、latency：报告 delta，不设无依据阈值。

## Acceptance criteria

1. 在 Shenzhen、Chengdu、Lijiang 三个已知 pacing badcases 上，candidate 不再出现同类 verified daily-load violation，或对 route unknown 给出明确不可判状态。
2. 三个 case 的 human pacing 均不得回归，且至少两个有 reviewer 可解释的改善；这是 provisional 小样本 gate，不是统计显著性声明。
3. Preference satisfaction、schema、date/day、budget arithmetic 无 regression。
4. 不通过删除 route checks、POI provenance 或用户要求制造改善。
5. 所有 candidate artifacts 与 baseline paired comparison 使用相同 case/fixture policy，并报告 token/latency delta。
6. Human rationale 与 deterministic evidence 均完成后才可关闭 Phase 4。

## Resume value

可讲述：使用 8-case controlled evidence 将“行程看起来合理”拆解为 constraint、grounding、route、pacing 与 usefulness；根据 human evidence 选择 pacing-aware feasibility；设计可测、可回归、保留 unknown 语义的 Planner/Validator/Revision experiment。

不得讲述：已提升用户满意度、已证明 Planner V2 更优、已获得统计显著改善或 route success 提升。
