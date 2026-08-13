# Phase 3E Human Review Workbook

状态：Human Review Completion Gate `PASS`；initial-planning completed `6/6`、patch completed `1/1`、product failure completed `1/1`
Rubric：`human.v1`（唯一评分依据：`eval/contracts/human_rubric_v1.json`）
范围：6 个 initial-planning artifact、1 个 local-patch artifact、1 个单列 product failure artifact。
禁止：模型代评、隐藏推理、原始 secret、完整 XHS context、无关 provider raw response。

## Reviewer workflow

1. 先核对 artifact reference 与 SHA-256；hash 不一致时停止评审。
2. 阅读 TripRequest、PreferenceProfile 和逐日行程，再看 deterministic warnings，避免先入为主。
3. 对五个维度各填 `1–5`，必须采用 `human.v1` 对应 anchor，并在 `rationale_by_dimension` 写明可定位的证据。
4. 单独填写 `unsupported_fact = yes / no / uncertain` 及 rationale；它不是五维分数的替代品。
5. 有 special constraint 或 patch 的 case 完成额外问题。失败 case 不填写五维 itinerary 分数。

通用中立问题：Preference Satisfaction——计划是否真正体现兴趣、节奏及特殊要求？Itinerary Coherence——每天与跨日衔接是否连贯？Pacing——景点数量、移动、停留是否符合 requested pace？Usefulness——明天出发时是否足够可执行？Explanation——推荐理由、不确定性和限制是否清楚？

评分 anchor 必须逐字以 rubric 文件为准：1 表示严重缺陷/需大幅重做，3 表示基本可用但有明显缺口，5 表示该维度高度满足且几乎无需重排；2、4 使用相邻的正式 anchor，不允许自行改变尺度。

Unsupported-fact 检查：具体外部事实是否有来源；是否把单篇 XHS 经验泛化为平台共识；是否把一般推断写成地图或官方事实；是否出现来源不明的精确价格、时间、预约、天气或路线断言。

## gc_beijing_baseline

- Review status：`complete`（reviewer：Yi Huang）
- Artifact：`eval/pilots/phase3d2_pilot3/gc_beijing_baseline/gc_beijing_baseline.json`
- SHA-256：`0c0fe5d1422518a1e7dfc2d826d1c0840b3b5dc89f345b448d95bbfab53c525a`
- Scenario：北京 3 日、情侣 2 人、balanced、历史文化/博物馆、公共交通、舒适型酒店；无预算上限。
- Itinerary：D1 09:00 天安门广场→东交民巷→国家大剧院；D2 08:30 故宫→景山→什刹海；D3 09:00 国家博物馆→天坛→前门/大栅栏。
- Budget：总计 7,216 元；景点 136、酒店 5,400、餐饮 1,440、交通 240；无预算限制可判。
- Grounding / provenance：POI 9/9；provenance 14/14。Route coverage 4/6；已检查 4/4 feasible，其余路线 unavailable。
- Providers / weather：XHS success；Google Places success；Directions partial；Google Weather unavailable，AMap degraded fallback。
- Risks：D3 路线未完整验证。Revision `not_applicable`；Patch `not_applicable`。
- Human focus：博物馆/历史偏好是否落实；D1 关于“周一闭馆”的具体事实是否有足够来源；高酒店占比是否影响 usefulness。

## gc_kyoto_no_early_start

- Review status：`complete`（reviewer：Yi Huang）
- Artifact：`eval/pilots/phase3d3/gc_kyoto_no_early_start/gc_kyoto_no_early_start.json`
- SHA-256：`9a7292cef4403e68f95d7c078523d41efca008d0b1bb8d39fc8d3d8a80b80808`
- Scenario：京都 3 日、情侣、relaxed、历史文化；显式要求每天 10:00 后开始主要行程。
- Itinerary：三天均 10:00 开始；D1 清水寺→三年坂→八坂神社；D2 伏见稻荷→花见小路→鸭川；D3 竹林小径→岚山→渡月桥。
- Budget：总计 3,385 元；无预算限制。
- Grounding / provenance：POI 2/9；provenance 3/10。没有 eligible verified route legs；feasibility `unknown`，不是 infeasible。
- Providers / weather：XHS success；Places partial；Directions not_called；Google Weather、AMap unavailable。
- Risks：三天路线均未完整验证。Explicit start constraint deterministic 1/1。Revision/Patch `not_applicable`。
- Human focus：10:00 开始之外是否真正 relaxed；未验证 POI/路线是否降低可执行性与信任；精确交通描述是否缺来源。

## gc_osaka_places_partial

- Review status：`complete`（reviewer：Yi Huang）
- Artifact：`eval/pilots/phase3d3/gc_osaka_places_partial/gc_osaka_places_partial.json`
- SHA-256：`4bde4636f96f3134bdef8ca5f9bcd8b61578fffa47b87ffcb45a29710e2dd380`
- Scenario：大阪 3 日、3 位朋友、balanced、美食/购物、公共交通、经济型酒店。
- Itinerary：D1 08:30 USJ/飞天恐龙；D2 10:00 黑门市场→难波千日前→心斋桥；D3 09:30 新世界→通天阁→梅田。
- Budget：总计 5,080 元；无预算限制。
- Grounding / provenance：POI 1/8；provenance 2/9；无 eligible verified route legs，feasibility `unknown`。
- Providers / weather：XHS success；Places partial；Directions not_called；Google Weather、AMap unavailable。
- Risks：三天路线均未完整验证。Revision/Patch `not_applicable`。
- Human focus：购物/美食是否具体而非仅地名；USJ 全天与后两天密度是否 balanced；Express、交通时长等事实是否有来源。

## gc_shenzhen_overbudget_revision

- Review status：`complete`（reviewer：Yi Huang）
- Artifact：`eval/pilots/phase3d4/gc_shenzhen_overbudget_revision/gc_shenzhen_overbudget_revision.json`
- SHA-256：`85a59d603ad37a69f12c13646fe7a3e4201ba48b594fb5fd17f55a5cc6dfbf52`
- Scenario：深圳 3 日、solo、balanced、城市探索；当地消费上限 2,000 元。
- Itinerary：artifact 的 day_index 为 1/2/3（而非 0/1/2），日期仍为三天；D1 万象天地→世界之窗→深圳湾，D2 仙湖植物园→水贝→东门，D3 大鹏半岛三点并要求 08:00 出发。
- Budget：总计 1,510 元，算术一致且低于上限。
- Grounding / provenance：POI 7/9；provenance 12/14；route coverage 2/4，已查 2/2 feasible。
- Providers / weather：XHS success；Places/Directions partial；Google Weather unavailable，AMap degraded fallback。
- Risks：D2/D3 路线未完整验证；`date_day_consistency = 0`。尽管 case 标记 revision_trigger，artifact 显示 revision 未触发。
- Human focus：day index 错位是否影响可用性；D3 08:00 和长途移动是否与 balanced pace 相称；预算达标是否掩盖行程质量问题。

## gc_chengdu_budget

- Review status：`complete`（reviewer：Yi Huang；food constraint：`satisfied with caveats`）
- Artifact：`eval/pilots/phase3d42/gc_chengdu_budget/gc_chengdu_budget.json`
- SHA-256：`f5426654890cae844f8d65291987cb2c36c9157c84b5d3142b0897274044784f`
- Scenario：成都 4 日、2 位朋友、balanced、美食/城市探索；当地消费不超过 3,500 元；不吃花生。
- Itinerary：D1 10:00 春熙路/太古里/大慈寺/九眼桥/望平街；D2 07:30 熊猫基地/文殊院/奎星楼；D3 成都博物馆/杜甫草堂/武侯祠；D4 都江堰方向五点。
- Budget：总计 2,530 元，算术一致且低于上限。
- Grounding / provenance：POI 14/16；provenance 19/21；route coverage 6/8，已查 6/6 feasible。
- Providers / weather：XHS/hotel success；Places/Directions partial；Google Weather unavailable，AMap degraded fallback。
- Risks：D1/D2/D4 路线未完整验证。Revision/Patch `not_applicable`。
- Special constraint review：不能因多次出现“花生”关键词就判满足。检查每餐是否可实际规避、是否存在交叉接触风险、系统是否清楚区分“不吃”与“过敏”、提醒是否安全且不过度保证。

## gc_lijiang_places_unavailable

- Review status：`complete`（reviewer：Yi Huang）
- Artifact：`eval/pilots/phase3d42/gc_lijiang_places_unavailable/gc_lijiang_places_unavailable.json`
- SHA-256：`20080b6627e4e197a582f35bb81b9e2b1c3fb20f7aa8666dd13fe7d00eb24e08`
- Scenario：丽江 3 日、情侣、relaxed、自然风光、混合交通、民宿。
- Itinerary：D1 10:00 黑龙潭/丽江古城/束河；D2 08:00 云杉坪/玉龙雪山/蓝月谷；D3 09:30 玉湖村/龙女湖/九鼎龙谭。
- Budget：总计 3,050 元；无预算限制。
- Grounding / provenance：POI 8/9；provenance 13/14；route coverage 0/4，feasibility `unknown`。
- Providers / weather：XHS/hotel success；Places partial；Directions unavailable；Google Weather unavailable，AMap degraded fallback。
- Risks：三天路线未完整验证。Revision/Patch `not_applicable`。
- Human focus：route unavailable 是否显著降低 usefulness；“relaxed”与 D2 08:00、高海拔全天活动是否冲突；高海拔/交通精确描述是否有来源。

## gc_nanjing_local_patch

- Review status：`complete`（reviewer：Yi Huang）
- Artifact：`eval/pilots/phase3d43/gc_nanjing_local_patch/gc_nanjing_local_patch.json`
- SHA-256：`88cd804a7373cdd12a54b8190b858eb33165ff20a0ac1a350dbf4bf27e0113c1`
- Request：只把第三天最后一个景点“玄武湖”换成“南京博物院”，其他日期保持不变。
- Final itinerary：D1 老门东→夫子庙→秦淮河；D2 中山陵→音乐台→明孝陵；D3 鸡鸣寺→明城墙台城→南京博物院；D4 南京博物院→总统府→遇难同胞纪念馆。
- Budget：总计 4,775 元；无预算限制。
- Patch state：plan v1→v2；affected day `[2]`；protected `[0,1,3]`；protected 3/3 deep-equal；scope drift false。
- Grounding / provenance：12/12、17/17。Route coverage 8/8，但 feasibility 5/8；automatic badcase `route_infeasible`。
- Risks：D1、D2、D4 存在长路线/高负荷 warnings。Revision 不参与 patch path。
- Patch human questions：要求是否准确执行；替换是否自然融入 D3；未修改日期虽字节保持，逻辑是否仍一致；D3 与 D4 重复南京博物院是否降低 coherence/usefulness；新 POI 是否恶化路线或节奏。Deterministic preservation 不能替代这些判断。

## gc_beijing_xian_multi_city — Product Failure Review

- Review status：`complete`（reviewer：Yi Huang；severity：`high`；recoverability：`difficult`；retry guidance：`unknown`；user impact：`high`）
- Artifact：`eval/pilots/phase3d43/gc_beijing_xian_multi_city/gc_beijing_xian_multi_city.failure.json`
- SHA-256：`a953c3ab912d6500a8908ca4806a06e8c8f0e51457cd2131b96c9bd1bdcd5872`
- Request：北京 2 日 + 西安 3 日、家庭 3 人；历史/博物馆/美食；切换日不要太满。
- Failure：`planner_output_parse_failure` at `planner`；3 calls；27,283 prompt + 20,001 completion = 47,284 tokens；161,127 ms；0 retry。
- User impact：等待约 161 秒后无可用计划。正常五维 itinerary score 不适用。
- Reviewer fields：severity、recoverability、retry guidance 是否存在，以及“真实用户遇到该失败有多严重”的 rationale。

## Completion gate

完成状态：6/6 initial-planning records、1/1 patch record 均有 reviewer、timestamp、五项分数、逐项 rationale、unsupported-fact verdict/rationale；1/1 failure review 有 severity、recoverability、retry guidance、user impact 及逐项 rationale。Human Review Completion Gate = `PASS`。Product Quality Synthesis 尚未执行。
