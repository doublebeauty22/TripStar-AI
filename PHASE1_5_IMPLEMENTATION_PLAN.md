# Phase 1.5 Implementation Plan — Data & Visual Completeness

## 0. 阶段目标与边界

Phase 1.5 的目标是在不依赖小红书 Cookie 的情况下，让 Portfolio Demo 仍然具备完整、可信、可展示的景点卡片、坐标和路线体验，并为后续 Route Validator 提供可靠的地图事实输入。

本阶段只补齐地图事实与图片降级链路，不进入 Validator / Critic、Recommendation Score、Chat Patch，不引入数据库，不重构 Result 页面，也不改变 Phase 1 的 Preference Profile 和 Planner 产品逻辑。

成功标准：东京案例在只有 Google Maps Key、没有 XHS Cookie 时，能够生成计划、展示有坐标的景点、加载地图和路线、展示 Google Places 图片；当 Google 和 XHS 都不可用时，计划仍能生成，所有景点卡片使用统一 placeholder，界面不出现空白卡片或无限失败请求。

---

## 1. 当前实现盘点

### 1.1 已实现、可以直接复用

| 能力 | 当前实现 | 可复用结论 |
|---|---|---|
| Google POI 文本搜索 | `GoogleMapService.search_poi()` 使用 Places API (New) Text Search | 保留；扩充最小 FieldMask 和结果映射即可 |
| 地址转坐标 | `GoogleMapService.geocode()` 使用 Geocoding API | 直接复用 |
| 路线距离/时间 | `GoogleMapService.plan_route()` 已返回 distance / duration | 保留接口；底层建议从 Legacy Directions REST 切换到 Routes API Compute Routes |
| Place Details | `GoogleMapService.get_poi_detail()` 已请求 photos、rating 等字段 | 直接复用并补标准化解析 |
| Google 服务选择 | Planner 在配置 Google Key 时初始化 `GoogleMapsNativeTool` | 保留现有 provider 选择方式 |
| 前端 Google 地图 | `Result.vue` 已使用 Maps JavaScript API、Marker、InfoWindow、路线绘制 | 不重构，只做必要的配置和降级校正 |
| 高德降级 | Google 地图加载失败后会尝试 `initAMap()` | 保留 |
| XHS 图片获取 | `/api/poi/photo` 和 `get_photo_from_xhs()` | 从唯一来源降为第二优先级 |
| 图片占位 | `Result.vue#getAttractionImage()` 已能生成深色 placeholder | 保留视觉思路，统一成稳定 placeholder |
| 运行时配置 | Settings API、NavBar 配置弹窗、service reset | 复用配置链路，不新增配置系统 |

### 1.2 当前缺口

1. `/api/poi/photo` 写死为 XHS 图片，没有 Google Places 优先路径。
2. Places Text Search 的 FieldMask 没有请求 `photos`、rating、userRatingCount 等本阶段需要的数据。
3. `get_poi_detail()` 能拿到 photo resource name，但没有调用 Place Photos (New) media endpoint生成可展示 URL。
4. Planner 结果中的景点名称、地址、坐标主要来自 LLM 输出；没有确定性的 Place 匹配与坐标补全步骤，不能直接作为未来 Route Validator 的“地图事实”。
5. 当前后端 `plan_route()` 使用 legacy Directions REST endpoint；Result 中 `DirectionsService` 也已被 Google 标记为 deprecated。Phase 1.5 不重构 Result，但后端应优先采用 Routes API，为 Phase 2 做准备。
6. 前端虽然有 placeholder，但图片请求失败时缺少明确、统一的 source/fallback 协议。
7. 当前同一个 Google Key 同时用于服务端 Web Service 和浏览器 Maps JavaScript，难以同时应用 IP 与 HTTP referrer 两类安全限制。

---

## 2. 需要配置的 Google Maps Platform API

### 2.1 MVP 必需

1. **Places API (New)**
   - Text Search (New)：按“城市 + 景点名”匹配 Place。
   - Place Details (New)：补充 Place ID、标准名称、地址、坐标、rating、photo resource name。
   - Place Photos (New)：取得景点图片。
   - 这三项属于 Places API (New) 的能力，不需要实现新的搜索系统。

2. **Geocoding API**
   - 当 Place 搜索没有坐标、只有地址时作为坐标补全。
   - 也用于城市中心点等现有逻辑。

3. **Routes API**
   - 使用 Compute Routes 获取相邻景点的真实距离与预计时间。
   - Phase 1.5 先保证 service 输出稳定；Phase 2 Route Validator 直接消费同一结果。
   - 不新增 Route Agent。

4. **Maps JavaScript API**
   - Result 页地图、marker、info window 和路线可视化。
   - 当前 Result 已经接入，无需重做地图组件。

### 2.2 条件启用

5. **Weather API**
   - 不属于本阶段图片/路线目标，但当前 Google provider 已调用 Google Weather。
   - 如果希望配置 Google 后继续使用现有 Google 天气路径，需要一并启用；否则应明确继续使用现有天气 fallback。

### 2.3 Key 与安全限制建议

推荐使用两个 Key，而不是把一个无限制 Key 同时暴露给浏览器和后端：

- `GOOGLE_MAPS_SERVER_API_KEY`：仅供后端 Places / Geocoding / Routes / Weather，设置 API restriction，并按部署环境设置 IP restriction。
- `VITE_GOOGLE_MAPS_BROWSER_KEY`：仅供 Maps JavaScript API，设置 HTTP referrer restriction。

为保持兼容，现有 `GOOGLE_MAPS_API_KEY` 继续作为 server key 和本地开发 fallback；不强制旧配置立即迁移。若 Phase 1.5 只做本地 Demo，也可以暂时使用一个 Key，但必须设置 API restriction、quota 和 billing alert，不能把未限制的 Key 提交到仓库。

Google Maps Platform 需要绑定 billing account，具体免费用量和单价应以实施时的官方 Pricing 页面为准，不在代码或 README 中写死金额。

---

## 3. 最小数据设计

不创建完整 `POIEvidence`，只在现有模型上补足地图识别和图片展示所需字段。

### 3.1 POIInfo 建议补充

| 字段 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `id` | `string` | Google Place ID | 稳定匹配 Place Details / Photo |
| `name` | `string` | Google displayName | 地图事实名称 |
| `address` | `string` | Google formattedAddress | 地图事实地址 |
| `location` | `Location` | Google Places / Geocoding | 地图 marker、路线计算 |
| `rating` | `number?` | Google Places | 可选展示，不参与 Phase 1 score |
| `user_rating_count` | `integer?` | Google Places | 说明 rating 样本量 |
| `photo_name` | `string?` | Google Places photos[0].name | 临时请求 Place Photo；不长期持久化 |
| `photo_attributions` | `array?` | Google Places | 满足图片署名要求 |
| `data_source` | `"google_places"` | deterministic | 防止与 LLM/XHS 内容混淆 |

### 3.2 Attraction 最小补充

| 字段 | 类型 | 说明 |
|---|---|---|
| `place_id` | `string?` | 匹配成功的 Google Place ID |
| `map_data_source` | `"google_places" \| "amap" \| "unverified"` | 标记坐标/地址的事实来源 |
| `image_url` | `string?` | 只允许确定性图片服务写入；Planner prompt 继续禁止 LLM 填写 |
| `image_source` | `"google_places" \| "xhs" \| "placeholder"` | 明确图片来源 |
| `image_attributions` | `array?` | Google Photo 需要时展示署名 |

如果为了更小范围而不扩充 `Attraction`，上述图片 source/attribution 可先只存在 `/api/poi/photo` 返回值和前端内存中；但 `place_id` 与 `map_data_source` 建议进入结果 schema，因为 Phase 2 Route Validator 需要区分“已验证坐标”和“LLM 未验证坐标”。

---

## 4. 数据来源边界

### 4.1 地图事实（Google Places / Geocoding / Routes）

允许作为地图事实展示：

- Place ID、标准名称、地址、经纬度、Place types。
- Google rating / userRatingCount（明确标注来源）。
- 路线距离、预计交通时间、路线 polyline。
- Google Place photo 和对应 attribution。

这些字段必须来自 API response，不由 LLM补写。如果地图查询失败，对应字段标记为 unavailable / unverified，不用 LLM 猜测来伪装成功。

### 4.2 XHS 用户经验

用于增强：

- 用户体验摘要。
- 常见避坑、排队、拍照体验等主观信息。
- Google 图片不可用时的可选图片。

必须标记为 XHS 来源；没有 Cookie 时返回 degraded mode，不阻断行程。

### 4.3 LLM 推断

用于：

- 行程编排、景点取舍和节奏安排。
- 根据 Preference Profile 解释规划逻辑。
- 对已有地图事实和 XHS 经验做摘要。

LLM 不得声称自己生成的地址、坐标、距离、rating、图片或用户评价来自 Google/XHS。Planner prompt 中继续明确 `image_url` 不由模型生成。

---

## 5. 建议的数据流

### 5.1 POI 与坐标补全

```text
Planner 生成 TripPlan
→ 对主要 Attraction 执行确定性 Place enrichment
→ Places Text Search("城市 + 景点名")
→ 选择名称/地址最相关的候选
→ 写入 place_id、标准地址、可靠坐标、map_data_source
→ 搜索无结果时尝试 Geocoding(address + city)
→ 仍失败则保留原字段，但标记 map_data_source=unverified
→ 返回 Result
```

P0.5 不做复杂 entity resolution。匹配规则保持透明：优先完全/包含名称匹配，其次首个同城候选；低置信匹配不覆盖原名称，只补 Place ID/坐标，并记录日志。

### 5.2 图片 fallback

```text
Result 请求 /api/poi/photo?name=浅草寺&city=东京&place_id=...
→ 1. Google Places
     place_id 存在：Place Details → photo resource name
     place_id 不存在：Text Search → Place ID + photo resource name
     Place Photos media → photoUri + attribution
→ Google 没配置 / 无匹配 / 无 photo / 请求失败
→ 2. XHS photo
     Cookie 可用：尝试现有 get_photo_from_xhs()
     Cookie 缺失或失败：继续降级
→ 3. placeholder
→ 始终返回 success + source + photo_url/placeholder 标记，不因图片失败返回 500
```

建议 `/api/poi/photo` 返回：

```json
{
  "success": true,
  "data": {
    "name": "浅草寺",
    "place_id": "...",
    "photo_url": "...",
    "source": "google_places",
    "attributions": []
  },
  "degraded": false
}
```

两类来源都不可用时：

```json
{
  "success": true,
  "data": {
    "name": "浅草寺",
    "photo_url": "",
    "source": "placeholder",
    "attributions": []
  },
  "degraded": true
}
```

前端用统一 placeholder 渲染空 URL。不得让 LLM生成图片 URL；不得把 Google photo resource name 当成永久可缓存 URL；需要展示 Google 返回的 author attribution。

---

## 6. XHS 的新定位

### 有 Cookie 且可用

- 保留现有 XHS research。
- 提供真实攻略、用户体验摘要、避坑信息。
- 仅在 Google Places 没有图片时尝试 XHS 图片。
- XHS 失败仍进入 degraded mode，不影响地图事实与 Planner。

### 无 Cookie、过期或请求失败

- research 阶段沿用现有 XHS fallback 日志和空 research context。
- 景点候选、标准地址、坐标和图片优先来自 Google Places。
- Planner 正常生成。
- 图片 endpoint 不抛 500，直接进入 placeholder。
- task progress/log 明确记录“XHS 用户经验不可用”，但不把它描述为产品不可用。

产品定位因此变为：Google Maps 是 POI/空间事实底座，XHS 是可选的用户经验增强层，LLM 是决策与规划层。

---

## 7. 无 Key / 无 Cookie 时的产品行为矩阵

| Google Key | XHS Cookie | 预期行为 |
|---|---|---|
| 有效 | 有效 | Google 地图事实和图片；XHS research/避坑增强；Google 无图时才尝试 XHS 图 |
| 有效 | 无效 | 完整 Google POI、坐标、路线、地图和图片；XHS 标记 degraded；计划正常完成 |
| 无效 | 有效 | 保留现有 AMap/LLM Planner 路径；可用 XHS research 和图片；Google 地图不可用时尝试 AMap |
| 无效 | 无效 | Planner 继续运行；若 AMap 可用则使用 AMap 坐标/地图；否则明确显示地图数据不可用；所有景点使用统一 placeholder，不出现空白卡片 |

当没有任何地图 Key 时，不应把 LLM 输出坐标描述为“真实路线”。Result 可保留行程文本，但地图区域显示配置提示或 unavailable 状态；不得用直线图伪装成已计算路线。

---

## 8. 文件级修改计划

| 文件路径 | 操作 | 计划内容 | 风险 |
|---|---|---|---|
| `backend/app/services/google_map_service.py` | modify | 扩充 Places FieldMask/解析；新增 Place Photo 方法；`plan_route()` 内部切到 Routes API，保持返回结构兼容 | Google FieldMask 会影响 SKU；需严格控制字段 |
| `backend/app/api/routes/poi.py` | modify | `/photo` 改为 Google → XHS → placeholder 协调器；返回 source、attribution、degraded，不再因图片源失败返回 500 | 外部请求延迟；需要超时和降级日志 |
| `backend/app/models/schemas.py` | modify | 最小增加 place/source/attribution 字段，全部 optional，保持旧任务 JSON 兼容 | 避免把 Phase 1.5 扩成完整 evidence schema |
| `backend/app/agents/trip_planner_agent.py` | modify | Planner parse 后增加轻量 deterministic Place enrichment；保留 Planner prompt 与主流程 | API 调用量；限制为主要景点且避免重复搜索 |
| `backend/app/config.py` | modify | 可选增加 server/browser Google key，保留 `google_maps_api_key` fallback | 配置兼容与敏感 key 暴露 |
| `backend/app/api/routes/settings.py` | modify | 支持新增 key 配置并在变更后 reset Google service | 不能把 server key 无条件返回浏览器 |
| `frontend/src/services/api.ts` | modify | 同步浏览器地图 key；不改变 Trip Planner API | 避免将 server key 当 browser key |
| `frontend/src/types/index.ts` | modify | 添加最小 source/attribution 类型 | 保持字段 optional |
| `frontend/src/views/Result.vue` | modify | 仅调整图片响应消费、attribution、placeholder 与 map unavailable 提示；不拆组件、不改结果信息架构 | 文件大，修改必须局部且有回归测试 |
| `frontend/src/components/OverviewAttractionCard.vue` | keep / minimal modify | 保持卡片；必要时增加图片 attribution slot/小字 | 避免视觉重构 |
| `frontend/public/.../attraction-placeholder.svg` | add | 统一稳定的本地 placeholder，不依赖远程 URL | 无外部成本 |
| `backend/tests/test_google_map_service.py` | add | mock Google Text Search/Details/Photo/Routes 响应 | 不访问真实收费 API |
| `backend/tests/test_poi_photo_fallback.py` | add | 覆盖 Google、XHS、placeholder 三条路径 | 确认任一图片源失败不返回 500 |
| `frontend/tests/...` | add/modify | 覆盖空图始终落到 placeholder、source 显示 | 使用现有无新增依赖测试方式 |
| `.env.example` | modify | 列出需启用 API、两类 Key、restriction 和成本提醒 | 不提交真实 Key |

后端 `trip.py`、Preference Profile、LLM compatibility、XHS research fallback 主逻辑不需要修改。

---

## 9. 建议实施顺序

### Step 1 — 配置与 Google service 验证

- 在 Google Cloud 项目启用 Places API (New)、Geocoding API、Routes API、Maps JavaScript API；按需启用 Weather API。
- 设置 billing、quota、budget alert 和 key restriction。
- 用 mock 测试确认现有 service 输出；再做一次东京真实 smoke test。

验收：`东京 浅草寺` 能返回 Place ID、标准地址和非零坐标；Routes 能返回浅草寺到东京晴空塔的 distance/duration。

### Step 2 — Google Place Photo

- Text Search / Details 请求 `photos`。
- 通过 Place Photos media endpoint获取 photo URI。
- 返回 attribution，失败返回空而非异常。

验收：浅草寺能返回 Google 图片或明确 `no_photo`，不出现 LLM URL。

### Step 3 — 图片 fallback endpoint

- `/api/poi/photo` 实现 Google → XHS → placeholder。
- 添加 source/degraded 日志与测试。

验收：关闭 XHS Cookie 后仍有 Google 图；关闭 Google 后可尝试 XHS；两者都关闭时返回 placeholder 状态且 HTTP 200。

### Step 4 — 轻量 POI enrichment

- TripPlan 解析完成后，按景点名和所属城市补 Place ID/坐标。
- 不改变 Planner 的景点选择，不做路线校验。
- 对同名景点请求去重，并限制单次 trip 调用量。

验收：东京主要景点坐标来自 Google 并标记 source；无匹配景点标记 unverified。

### Step 5 — Result 局部接入

- 消费新的 photo source/attribution。
- 图片失败统一 fallback。
- 地图无 key 时显示明确 unavailable/configuration 提示。
- 不改变 Result 页面业务结构。

验收：任何数据源组合下都没有空白图片卡片；地图事实不可用时不伪装路线。

---

## 10. 东京案例测试方案

固定案例：`东京 3 天，喜欢拍照和日本文化，不想早起`。至少验证浅草寺、东京晴空塔、涩谷十字路口等知名 POI。

### 10.1 Service 测试

1. Text Search 返回 Place ID、地址、坐标。
2. Place Details 返回 photos、rating、userRatingCount。
3. Place Photo 返回可访问 URI 和 attribution。
4. Routes 返回浅草寺 → 东京晴空塔的正数 distance/duration。
5. Google 401/403/429/timeout 时返回可降级结果，不中断 Planner。

### 10.2 Fallback 矩阵测试

1. Google 有图、XHS 无 Cookie：source=`google_places`。
2. Google 无图、XHS 有图：source=`xhs`。
3. Google 和 XHS 都不可用：source=`placeholder`，HTTP 200。
4. 单张图片 URL 加载失败：前端 `onerror` 切换 placeholder，不反复请求。

### 10.3 E2E 手工验收

1. 只配置 Google Key，清空 XHS Cookie。
2. 生成东京案例并进入 Result。
3. 确认 Overview 每张卡片都有 Google 图片或统一 placeholder。
4. 确认地图 marker 与景点名称对应、坐标不为 0。
5. 确认同一天相邻景点显示真实路线，而不是默认直线。
6. 检查浏览器 Network：图片来源来自 Google Place Photo endpoint；没有凭空生成的 URL。
7. 检查 UI/日志：XHS degraded 不阻塞任务，地图事实与 XHS 经验没有混标。
8. 清空 Google Key 和 XHS Cookie 再测一次，确认计划仍完成、地图显示 unavailable、图片全部 placeholder。

---

## 11. API 成本与控制

会产生额外 Google Maps Platform 成本，主要来自：

- Maps JavaScript API 的动态地图加载。
- Places Text Search / Place Details。
- Place Photos。
- Geocoding。
- Routes Compute Routes。
- 若启用，Weather API。

成本控制策略：

1. 使用最小 FieldMask，禁止 `*`。
2. 每个 unique attraction 每次 trip 最多做一次 Place Search；已有 Place ID 后直接 Details。
3. 只为 Result 当前计划的主要景点加载一张图片，不预取多张。
4. 路线只计算同一天相邻节点，不做全量 N×N matrix。
5. 设置项目 quota、每日上限和 billing budget alert。
6. 浏览器 key 使用 referrer restriction，server key 使用 API/IP restriction。
7. 单元测试全部 mock；真实 API 只做受控 smoke test。
8. 遵守 Google Places 数据缓存和 attribution 规则，尤其不长期缓存可能过期的 photo resource name。

具体价格和免费用量会变化，实施时以 Google Maps Platform 官方 pricing SKU 为准。

---

## 12. 本阶段明确不做

- 不实现 Rule Validator、Travel Critic 或 Planner Revision。
- 不实现 Recommendation Score / Reason。
- 不实现 Chat Patch。
- 不创建完整 POIEvidence 或数据库。
- 不做多方案比较。
- 不重构 Result 页面或地图组件。
- 不将 XHS 恢复为核心依赖。
- 不允许 LLM 生成或猜测图片 URL、Google rating、坐标或路线事实。

Phase 1.5 完成后应暂停，由产品验收确认视觉完整性和数据来源边界，再决定是否进入 Phase 2。
