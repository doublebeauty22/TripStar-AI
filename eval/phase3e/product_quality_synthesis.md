# TripStar Phase 3E — Final Product Quality Synthesis

状态：`ESTABLISHED`
Human Review Completion Gate：`PASS`
证据范围：6 个 initial-planning artifacts、1 个 local-patch artifact、1 个 product-failure artifact；全部 immutable hash 已复核。
限制：小样本 descriptive analysis；不计算 overall quality score，不声明统计显著性或因果关系。

## 1. Evidence integrity

- Initial-planning review：6/6 complete。
- Patch review：1/1 complete。
- Product failure review：1/1 complete。
- 8/8 review records 通过对应 schema；8/8 artifact references 的当前 SHA-256 与 record 完全一致。
- 无 required pending field；没有重新运行 case、API 或 LLM。

## 2. Human review aggregate（initial planning only）

| Dimension | Mean | Median | Range | Lowest case(s) |
|---|---:|---:|---:|---|
| Preference Satisfaction | 3.83 | 4 | 3–4 | Shenzhen（3） |
| Itinerary Coherence | 3.67 | 4 | 3–4 | Shenzhen、Lijiang（3） |
| Pacing Quality | 3.00 | 3 | 2–4 | Shenzhen、Chengdu、Lijiang（2） |
| Usefulness | 2.83 | 3 | 2–3 | Shenzhen（2） |
| Explanation Quality | 3.33 | 3 | 3–4 | Beijing、Kyoto、Osaka、Shenzhen（3） |

Patch 不混入均值：Nanjing = Preference 4、Coherence 2、Pacing 3、Usefulness 2、Explanation 2。Product failure 不参与五维评分。

## 3. Dimensional synthesis

### Preference Satisfaction

这是最稳定的人类维度（mean 3.83；无 case 低于 3）。京都 10:00 后开始的明确约束得到满足；成都“不吃花生”被人工判为 `satisfied with caveats`，依据是可执行的点餐/配料检查，而非关键词。深圳预算达标但主题聚焦和节奏削弱整体满足度。结论仅限 request-level 显式偏好，不代表深度个性化。

### Itinerary Coherence

Initial mean 3.67，多数计划在主题和区域层面可理解；但路线证据不足使执行连贯性不确定。深圳存在 day_index 1/2/3 与 contract 不一致。南京 patch 更清楚地暴露结构与语义分离：protected days 3/3 保持，但 Day 3 文本仍指向玄武湖，且 Day 3/4 重复南京博物院，human coherence 仅 2。

### Pacing Quality

这是跨场景最明确的 Planner behavior weakness。深圳、成都、丽江均为 2/5，覆盖 balanced 与 relaxed pace、城市/近郊/自然场景：早出发、单日 450–510 分钟 POI、跨区或跨城移动、接驳/排队/休息未充分计入。3/6 initial cases 明显偏弱，因此不是单一异常；样本仍不足以估计总体发生率。

### Usefulness

这是均值最低的维度（2.83），且 6/6 均未达到 4。共同问题不是“没有 itinerary”，而是距离“明天直接执行”仍缺路线验证、逐段交通、预约/价格依据或一致文本。Grounding 高并不保证 usefulness 高：丽江 grounding 8/9、provenance 13/14，但 route coverage 0/4，usefulness 仍为 3；南京 grounding/provenance 100%，usefulness 仍为 2。

### Explanation Quality

Mean 3.33。成都、丽江因约束与降级披露较清楚获 4；其余多为 3。主要缺口是未把 provider degradation、route unknown、价格来源和 patch 副作用转化为用户可理解的边界。南京 patch 为 2，说明执行成功与解释成功是两件事。

### Grounding

`ungrounded_poi` 出现在 5/6 initial cases。北京 9/9；京都 2/9；大阪 1/8；深圳 7/9；成都 14/16；丽江 8/9。问题最集中在京都与大阪 Places partial/not-called-route 场景。Grounding gap 会限制 route eligibility，但当前 evidence 不能证明所有 route unavailable 都由 grounding 导致，也不能把 provider coverage 问题归因于 Planner。

### Provenance and unsupported facts

`provenance_missing` 出现在 5/6 initial cases；覆盖从大阪 2/9、京都 3/10 到北京 14/14。6 个 initial reviewer 均给出 `unsupported_fact=uncertain`：这不是确认 hallucination，而是现有 review surface 无法逐项验证价格、预约、营业、路线等 claim。南京为 `yes`，因为 patch 后玄武湖旧文本与结构化 POI 冲突，且同一南京博物院 reservation flag 前后矛盾。

### Route availability and feasibility

`route_unavailable` 为最常见 automatic badcase：6/6 initial cases。Coverage：北京 4/6、京都 N/A（无 eligible verified legs）、大阪 N/A、深圳 2/4、成都 6/8、丽江 0/4。只有存在 checked legs 才能讨论 feasibility；京都、大阪、丽江为 unknown，不能记作 infeasible。Route availability 对 usefulness 的影响显著可见：低/零覆盖 cases 均需用户重新核实路线，但在 6-case 小样本中不能量化独立因果效应。南京 patch coverage 8/8，却只有 5/8 feasible，说明 availability 与 feasibility 也必须分开。

### Constraint satisfaction and budget consistency

结构化显式约束在覆盖样本中稳定：京都 earliest-start 1/1；深圳与成都 budget limit 通过；成都饮食约束人工判 `satisfied with caveats`。Schema 6/6 valid；date/day 5/6（深圳失败）；budget arithmetic 6/6。预算算术稳定不等于预算真实：深圳结构化 1,510 元与文案 1,495 元不一致；成都 inter-city transport 为 0；价格来源仍可能 unknown。

### Patch quality

Deterministic scope correctness 强：protected 3/3、scope drift false、grounding 12/12、provenance 17/17、route coverage 8/8。Whole-plan semantic correctness 弱：旧文本未同步、跨日重复 POI、reservation conflict、3/8 route legs infeasible、7 个 revisable warnings 后无 revision。产品结论：`Local patch correctness ≠ global plan correctness`。

### Multi-city robustness

唯一 multi-city case 在 planner output-contract boundary 失败：3 calls、47,284 tokens、161,127 ms、0 retries，无 TripPlan、partial result、checkpoint 或 recovery metadata。Human assessment：severity high、recoverability difficult、user impact high、retry guidance unknown。这是最严重的单次 failure，但只有 1 个尝试样本，不能据此估计频率。

### Cost and latency

6 个成功 initial cases：每 case 均 2 logical calls；total tokens mean 25,354、median 26,984、range 15,609–30,914；latency mean 105,649 ms、median 108,966 ms、range 80,356–115,369 ms。南京 patch：1 call、3,456 tokens、10,329 ms。Multi-city failure：47,284 tokens、161,127 ms 后零结果。没有 baseline/candidate experiment，因此不能设定合理提升百分比或声称成本优化。

## 4. Direct product answers

1. 最稳定的三个维度：显式 preference/constraint satisfaction、schema/budget arithmetic、initial itinerary 的主题级 coherence。
2. 最弱的三个维度：直接 usefulness、pacing、route availability/可执行性。
3. 最常见 automatic badcase：`route_unavailable`（6/6 initial）。
4. 最常见真实体验问题：计划看起来完整，但移动、停留、预约或 evidence boundary 不足以直接执行；其中 3/6 明确出现过密 pacing。
5. 最严重单次 failure：北京—西安 multi-city parse failure，161 秒、47,284 tokens 后无结果。
6. Grounding 主要问题：京都与大阪最突出；其他 3 个非北京 initial case 也有局部缺口。
7. Route unavailable 的影响：所有 initial case 均受影响，且 reviewer 反复将其列为 usefulness/执行不确定性来源；不能从本样本计算独立效应或因果大小。
8. Pacing：是跨三个不同场景重复出现的产品问题，不只是单 case；尚不能宣称总体系统发生率。
9. Patch 最大问题：只保证字段级 scope，不保证关联文本、跨日重复、事实和全局约束一致。
10. Multi-city 最大问题：高成本长等待后在 structured-output boundary 完全失败，且无可恢复 partial result。
11. 当前优先用户问题：用户需要符合所选 pace、把移动/停留/缓冲纳入负荷的可执行计划，而非仅主题合理的 POI 列表。
12. 暂缓：大规模 provider 重构、Provenance dashboard、全面 patch engine 重写、单凭一个样本开展 multi-city 平台重构、未校准的价格预测。

## 5. System problem classification

| Problem | Primary type | Boundary note |
|---|---|---|
| Route unavailable | Provider/data coverage + product transparency | 不自动归因 Planner；Planner 可更好降级与披露 |
| POI grounding gaps | Provider/data coverage | 会限制 route eligibility |
| Unsupported-fact uncertainty | Product UX/transparency + evaluation-only limitation | 现有 review surface 不能逐 claim 审核 |
| Overpacked pacing | Planner behavior problem | 可由现有 validator/revision seam 测量和改进 |
| Patch stale text/duplicate POI | Patch consistency problem | deterministic scope pass 仍可能 semantic fail |
| Multi-city parse failure | Structured-output robustness problem | capture 正常，产品输出边界失败 |
| Budget realism gaps | Planner behavior + provenance limitation | arithmetic 正确不代表 estimate 完整/可信 |
| Weak degradation explanation | Product UX/transparency problem | backend state 未充分转成用户边界 |

## 6. Priority conclusion

正式 badcase matrix 见 `badcase_priority_matrix.json`。最高优先产品问题是 pacing-aware feasibility：系统会生成主题合理、约束表面满足，但没有把 POI 停留、跨区移动、接驳/排队和休息统一纳入 selected pace 的计划。它不是频次最高的 technical badcase，却有 3/6 直接 human evidence、明确用户影响、现有 validator/revision architecture fit 与可重复指标。

## 7. Resume-safe claims

可以说：建立并完成 8-case controlled product-quality review（6 initial、1 patch、1 failure）；将 deterministic metrics 与 human.v1 分开分析；发现 route unavailable 6/6、pacing 3/6 为 2/5、initial usefulness mean 2.83；识别 deterministic patch preservation 与 semantic correctness 的差距；记录 multi-city 161 秒/47,284-token 无结果 failure；基于证据选择 pacing-aware optimization 为下一步实验。

不能说：大规模用户研究、统计显著、用户满意度提升、Planner V2 优于 V1、route success 提升 X%、总体 failure rate、grounding 与 usefulness 的因果关系、Phase 4 已取得改善。
