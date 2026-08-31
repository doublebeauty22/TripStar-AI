"""有界顺序式 LLM 旅行规划编排与确定性服务集成。"""

import json
import asyncio
import copy
import os
import re
from typing import Dict, Any, List, Callable, Awaitable, Optional
from hello_agents import SimpleAgent
from pydantic import ValidationError
from ..services.llm_service import (
    LLMCallBudgetExceeded,
    StructuredOutputLimitReached,
    TaskScopedLLM,
    create_chat_completion,
    get_last_structured_output,
    get_llm,
    log_structured_output_event,
    record_application_retry,
    structured_output_metadata,
)
from ..models.schemas import TripRequest, TripPlan, DayPlan, Attraction, Meal, WeatherResult, XHSResearchResult, Location, Hotel, has_valid_verified_coordinates
from ..config import get_google_maps_server_api_key, get_settings
from ..services.timing import timed_stage

# ============ Agent提示词 (动态模版化，支持 amap / google 双供应商) ============


def _build_weather_agent_prompt(tool_prefix: str) -> str:
    """构建天气查询 Agent 的系统提示词。

    Args:
        tool_prefix: "amap" 或 "google"
    """
    tool_name = f"{tool_prefix}_maps_weather"
    return f"""你是天气查询专家。你的任务是查询指定城市的天气信息。

**重要提示:**
1. 你必须使用工具来查询天气!不要自己编造天气信息!
2. 系统为你绑定的真实工具名称叫做 `{tool_name}`，你**只能而且必须**原样输出这个名字。

**工具调用格式:**
使用天气工具时,必须严格按照以下单行格式输出，**不要带任何多余的字符或JSON block**:
`[TOOL_CALL:{tool_name}:city=城市名]`

**示例:**
用户: "查询北京天气"
你的回复: [TOOL_CALL:{tool_name}:city=北京]

用户: "上海的天气怎么样"
你的回复: [TOOL_CALL:{tool_name}:city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 必须输出 `{tool_name}` 作为工具名。
"""


def _build_hotel_agent_prompt(tool_prefix: str) -> str:
    """构建酒店推荐 Agent 的系统提示词。"""
    tool_name = f"{tool_prefix}_maps_text_search"
    return f"""你是酒店推荐专家。你的任务是根据城市和景点位置推荐合适的酒店。

**重要提示:**
1. 你必须使用工具来搜索酒店!不要自己编造酒店信息!
2. 系统为你绑定的真实工具名称叫做 `{tool_name}`，你**只能而且必须**原样输出这个名字。

**工具调用格式:**
使用text_search工具搜索酒店时,必须严格按照以下单行格式输出，**不要带任何多余的字符或JSON block**:
`[TOOL_CALL:{tool_name}:keywords=酒店,city=城市名]`

**示例:**
用户: "搜索北京的酒店"
你的回复: [TOOL_CALL:{tool_name}:keywords=酒店,city=北京]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 关键词使用"酒店"或"宾馆"
4. 必须输出 `{tool_name}` 作为工具名。
"""


# 保留原有静态常量作为向后兼容 alias（部分外部代码可能引用到）
ATTRACTION_AGENT_PROMPT = ""  # 已弃用，景点改走小红书
WEATHER_AGENT_PROMPT = _build_weather_agent_prompt("amap")
HOTEL_AGENT_PROMPT = _build_hotel_agent_prompt("amap")

BASELINE_PLANNER_VERSION = "planner_baseline_v1"
BASELINE_PLANNER_PROMPT_VERSION = "planner_prompt_v1"
PLANNER_VERSION = "planner_pacing_v1"
PLANNER_PROMPT_VERSION = "planner_prompt_pacing_v1"


PLANNER_AGENT_PROMPT = """你是行程规划专家。你的任务是根据景点信息和天气信息,生成详细的旅行计划。支持单城市和多城市行程。

请严格按照以下JSON格式返回旅行计划:
```json
{
  "city": "首个城市名称(兼容字段)",
  "cities": ["城市1", "城市2"],
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "start_time": "09:30",
      "city": "当天所在城市",
      "is_transfer_day": false,
      "transfer_info": "",
      "description": "用一句话概括当天主题",
      "transportation": "地铁+步行",
      "accommodation": "经济型酒店",
      "hotel": {
        "name": "酒店名称",
        "address": "酒店地址",
        "price_range": "300-500元",
        "distance": "距离景点2公里",
        "type": "经济型酒店",
        "estimated_cost": 400
      },
      "attractions": [
        {
          "name": "景点名称",
          "address": "详细地址",
          "location": {"longitude": 0, "latitude": 0},
          "visit_duration": 120,
          "description": "一句话说明核心游玩价值。",
          "category": "景点类别",
          "ticket_price": 60,
          "reservation_required": false,
          "reservation_tips": ""
        }
      ],
      "meals": [
        {"type": "breakfast", "name": "早餐推荐", "estimated_cost": 30},
        {"type": "lunch", "name": "午餐推荐", "estimated_cost": 50},
        {"type": "dinner", "name": "晚餐推荐", "estimated_cost": 80}
      ]
    }
  ],
  "weather_info": [],
  "overall_suggestions": "最多三条简明建议",
  "budget": {
    "total_attractions": 180,
    "total_hotels": 1200,
    "total_meals": 480,
    "total_transportation": 200,
    "total_inter_city_transport": 0,
    "total": 2060
  }
}
```

**⚠️ JSON 格式关键约束（违反将导致系统崩溃）：**
- budget 中所有费用字段（total_attractions、total_hotels、total_meals、total_transportation、total_inter_city_transport、total）必须是**纯数字**，绝对禁止出现算术表达式！
  - ✅ 正确: "total_attractions": 324
  - ❌ 错误: "total_attractions": 30+54+120+120=324
  - ❌ 错误: "total_attractions": "324元"
- ticket_price、estimated_cost 等所有价格字段也必须是纯数字，不带单位

**重要提示:**
1. weather_info 必须固定输出空数组 []；权威天气由系统在 Planner 完成后确定性写入，禁止重复生成天气行
2. 每天安排2-3个景点(城际移动日可减少为1-2个)
   - start_time 表示当天第一个主要活动预计开始时间，不是起床或早餐时间，格式必须为 HH:MM
3. 考虑景点之间的距离和游览时间
4. 每天必须包含早中晚三餐
5. 提供实用的旅行建议
6. **必须包含预算信息**:
   - 景点门票价格(ticket_price)
   - 餐饮预估费用(estimated_cost)
   - 酒店预估费用(estimated_cost)
   - 预算汇总(budget)包含各项总费用
7. **预约信息透传**: 如果景点搜索数据中包含 reservation_required 和 reservation_tips 字段，请务必将它们完整保留在对应景点的JSON中。需要预约的景点请在 description 中也提醒游客提前预约
8. **景点图片与地图事实**: attraction.location 固定使用 {"longitude":0,"latitude":0} 作为 schema 占位；不要填写 rating、photos、image_url、poi_id、place_id、poi_match_status 或 map_data_source。这些事实由系统通过地图 API 确定性补全，禁止编造。
9. **紧凑文字要求**:
    - attraction.description 尽量只写一句约20-45个中文字符的说明，保留核心游玩价值或活动类型；不要重复地址、城市、评分、票价或交通，不得编造事实
    - day.description 只用一句话概括当天主题，不重复景点清单、天气、餐饮、酒店或交通
    - meal.description 为可选字段，默认省略；确有必要时仅写一个极短短语。meal 的 type、name、estimated_cost 必须保留
    - transportation 只写简短方式，如“地铁+步行”“步行”“公共交通”“出租车”；accommodation 只写简短住宿类型，不重复酒店详情
    - 非换乘日 transfer_info 输出空字符串；换乘日仅保留交通方式建议和大致时长
    - overall_suggestions 最多三条简明建议，不重复行程、天气表、预算表或景点描述
10. **多城市行程要求**:
    - 每个 day 对象中必须包含 "city" 字段标明当天所在城市
    - 城市切换当天设置 "is_transfer_day": true，并在 "transfer_info" 中**仅给出交通方式建议和大致时长**（如"建议乘坐高铁，约2-3小时"），**禁止编造具体车次、班次号、出发时间、到达时间等不可验证的信息**
    - 城际移动日的景点数量可适当减少为1-2个
    - budget 中的 "total_inter_city_transport" 统计城际交通费用(单城市时为0)
    - "cities" 数组列出所有途经城市(单城市时只有一个元素)
"""


class MultiAgentTripPlanner:
    """顺序编排行程研究、LLM Planner、grounding、validation 与 revision。"""

    def __init__(self):
        """初始化有界规划编排器及确定性 provider 适配器。"""
        print("🔄 开始初始化多智能体旅行规划系统...")

        try:
            settings = get_settings()
            self.llm = get_llm()

            # ---------- 判断地图供应商 ----------
            from ..services.map_dispatcher import get_map_provider
            self.map_provider = get_map_provider()
            print(f"  - 地图供应商: {self.map_provider.upper()}")

            if self.map_provider == "google":
                tool_prefix = "google"
                self._init_google_tools(settings)
            else:
                tool_prefix = "amap"
                self._init_amap_tools(settings)

            # Weather and hotel retrieval are deterministic. Planner agents are
            # created per plan_trip call so no conversation history is shared.
            self.planner_agent_name = "行程规划专家"

            print(f"✅ 多智能体系统初始化成功 (供应商={self.map_provider})")
            print("   天气/酒店: 确定性直连工具（LLM calls=0）")

        except Exception as e:
            print(f"❌ 多智能体系统初始化失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    def _init_amap_tools(self, settings):
        """初始化无 MCP/uvx 依赖的高德 REST 适配器。"""
        print("  - 创建高德 REST 适配器...")
        from ..services.amap_service import get_amap_service

        self._amap_service = get_amap_service()
        self.amap_tool = None  # deprecated compatibility marker
        self._active_tool = None

    def _init_google_tools(self, settings):
        """初始化 Google Maps 本地适配器工具。"""
        print("  - 创建 Google Maps 本地适配器工具...")
        from ..services.google_map_service import GoogleMapService

        # 创建一个轻量级的本地工具适配器
        google_svc = GoogleMapService(
            api_key=get_google_maps_server_api_key(),
            proxy=settings.google_maps_proxy,
        )
        self._google_service = google_svc

        class GoogleMapsNativeTool:
            """将 Google Maps API 封装为 hello_agents 可注册的工具。

            通过鸭子类型模拟 MCPTool 的接口（name, description, expandable,
            _available_tools, run），无需继承任何基类。

            注册后在 Agent 的可用工具列表中暴露为:
              - google_maps_text_search
              - google_maps_weather
              - google_maps_geo
            """

            def __init__(self):
                self.name = "google"
                self.description = "Google Maps 服务 (POI搜索/天气/地理编码)"
                self.expandable = True
                self._google_svc = google_svc
                # 模拟 MCP 子工具列表，使 hello_agents 能自动展开
                self._available_tools = [
                    {"name": "google_maps_text_search", "description": "Google POI文本搜索"},
                    {"name": "google_maps_weather", "description": "Google 天气查询"},
                    {"name": "google_maps_geo", "description": "Google 地理编码"},
                ]

            def get_expanded_tools(self):
                """返回展开的子工具列表，满足 hello_agents ToolRegistry 的接口要求。"""
                parent = self

                class _SubTool:
                    def __init__(self, name, description):
                        self.name = name
                        self.description = description
                        self.expandable = False

                    def run(self, input_data):
                        return parent.run(input_data)

                    def get_expanded_tools(self):
                        return [self]

                return [_SubTool(t["name"], t["description"]) for t in self._available_tools]

            def run(self, input_data):
                """分发 [TOOL_CALL:google_maps_*:...] 格式调用。"""
                import re as _re
                if isinstance(input_data, dict):
                    tool_name = input_data.get("tool_name", "")
                    arguments = input_data.get("arguments", {})
                elif isinstance(input_data, str):
                    # 解析 [TOOL_CALL:google_maps_xxx:key=val,...] 格式
                    match = _re.search(
                        r'\[TOOL_CALL:(\w+):(.*?)\]', input_data
                    )
                    if match:
                        tool_name = match.group(1)
                        args_str = match.group(2)
                        arguments = dict(
                            kv.split("=", 1)
                            for kv in args_str.split(",")
                            if "=" in kv
                        )
                    else:
                        return f"无法解析工具调用: {input_data}"
                else:
                    return f"不支持的输入类型: {type(input_data)}"

                return self._dispatch(tool_name, arguments)

            def _dispatch(self, tool_name: str, arguments: dict) -> str:
                import json as _json
                try:
                    if tool_name == "google_maps_text_search":
                        kw = arguments.get("keywords", "")
                        city = arguments.get("city", "")
                        results = self._google_svc.search_poi(kw, city)
                        return _json.dumps(
                            [r.model_dump() for r in results],
                            ensure_ascii=False,
                        )
                    elif tool_name == "google_maps_weather":
                        city = arguments.get("city", "")
                        results = self._google_svc.get_weather(city)
                        return _json.dumps(results.model_dump(mode="json"), ensure_ascii=False)
                    elif tool_name == "google_maps_geo":
                        address = arguments.get("address", "")
                        city = arguments.get("city", "")
                        loc = self._google_svc.geocode(address, city)
                        if loc:
                            return _json.dumps(loc.model_dump(), ensure_ascii=False)
                        return '{"error": "地理编码失败"}'
                    else:
                        return f'未知的 Google Maps 工具: {tool_name}'
                except Exception as e:
                    return f'Google Maps 工具调用失败: {e}'

        self._google_tool = GoogleMapsNativeTool()
        self._active_tool = self._google_tool

    def _new_planner_agent(self) -> SimpleAgent:
        """Create a history-empty planner for exactly one Trip task."""
        return SimpleAgent(
            name=self.planner_agent_name,
            llm=TaskScopedLLM(self.llm, "planner"),
            system_prompt=PLANNER_AGENT_PROMPT,
        )

    @staticmethod
    def _compact_json(value: Any, limit: int = 5000) -> str:
        def serialize(item: Any) -> Any:
            if hasattr(item, "model_dump"):
                return item.model_dump(mode="json")
            raise TypeError(f"Unsupported context value: {type(item).__name__}")

        text = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=serialize,
        )
        return text if len(text) <= limit else text[:limit] + "..."

    async def _retrieve_weather_context(self, city: str) -> str:
        """Retrieve weather facts directly, without an LLM tool-selection hop."""
        if not hasattr(self, "_weather_results"):
            self._weather_results = {}
        try:
            if self.map_provider == "google":
                result = await asyncio.to_thread(self._google_service.get_weather, city)
                from ..services.planner_observation import observe_weather
                observe_weather("google_weather", city, result)
                if isinstance(result, WeatherResult) and result.data_available:
                    result.city = city
                    self._weather_results[city] = result
                    return self._compact_json(result)
                # Compatibility for test doubles only.
                if isinstance(result, list) and result:
                    return self._compact_json(result)
                fallback = await self._fallback_amap_weather(city)
                fallback.city = city
                if isinstance(result, WeatherResult):
                    fallback.primary_failure_reason = result.reason
                self._weather_results[city] = fallback
                return self._compact_json(fallback)

            result = await self._fallback_amap_weather(city)
            result.city = city
            self._weather_results[city] = result
            return self._compact_json(result)
        except Exception as exc:
            print(f"⚠️ [WEATHER_DEGRADED] {city}: {exc}")
            if self.map_provider == "google":
                from ..services.planner_observation import observe_weather
                observe_weather("google_weather", city, WeatherResult(
                    provider="google_weather", city=city, request_success=False,
                    data_available=False, degraded=True, reason="network_error",
                ))
                fallback = await self._fallback_amap_weather(city)
                fallback.city = city
                fallback.primary_failure_reason = "network_error"
                self._weather_results[city] = fallback
                return self._compact_json(fallback)
            unavailable = WeatherResult(provider="unavailable", city=city, request_success=False, data_available=False, degraded=True, reason="network_error")
            self._weather_results[city] = unavailable
            return self._compact_json(unavailable)

    async def _retrieve_hotel_context(self, city: str, accommodation: str) -> str:
        """Retrieve and compact bounded hotel candidates without an LLM."""
        from ..services.planner_observation import observe_hotel

        keywords = f"{accommodation}酒店" if accommodation else "酒店"
        try:
            if self.map_provider == "google":
                candidates = await asyncio.to_thread(
                    self._google_service.search_poi, keywords, city
                )
                observe_hotel("hotel_google_places", city,
                              status="success" if candidates else "unavailable",
                              candidate_count=min(len(candidates or []), 5),
                              reason=None if candidates else "no_hotel_candidates")
                return self._compact_json(candidates[:5]) if candidates else "酒店候选暂不可用"

            result = await asyncio.to_thread(
                self._amap_service.search_poi, keywords, city, True,
            )
            observe_hotel("hotel_amap", city,
                          status=("degraded" if result.data_available and result.degraded else
                                  "success" if result.data_available else "unavailable"),
                          candidate_count=min(len(result.data or []), 5),
                          reason=str(result.reason) if not result.data_available else None)
            return self._compact_json(result.data[:5]) if result.data_available else "酒店候选暂不可用"
        except Exception as exc:
            print(f"⚠️ [HOTEL_DEGRADED] {city}: {exc}")
            observe_hotel(f"hotel_{self.map_provider}", city, status="unavailable",
                          candidate_count=0, reason="provider_execution_error")
            return "酒店候选暂不可用，请使用保守安排。"

    async def _emit_progress(
        self,
        progress_callback: Optional[Callable[[str, str, int], Awaitable[None] | None]],
        stage: str,
        message: str,
        progress: int,
    ) -> None:
        """向上层回调任务进度（支持同步/异步回调）。"""
        if progress_callback is None:
            return
        result = progress_callback(stage, message, progress)
        if asyncio.iscoroutine(result):
            await result

    async def _fallback_amap_weather(self, city: str) -> WeatherResult:
        """Use the shared typed AMap REST adapter as Google weather fallback."""
        from ..services.amap_service import get_amap_service

        service = getattr(self, "_amap_service", None) or get_amap_service()
        result = await asyncio.to_thread(service.get_weather, city, degraded=True)
        from ..services.planner_observation import observe_weather
        observe_weather("amap", city, result)
        return result

    async def _search_attractions_with_xhs_fallback(
        self,
        city: str,
        keywords: str,
        language: str,
        progress_callback: Optional[Callable[[str, str, int], Awaitable[None] | None]],
        progress: int,
        search_func: Optional[Callable[[str, str, str], str]] = None,
    ) -> str:
        """执行 XHS 景点研究；任何不可用状态都降级为空研究上下文继续规划。

        ``search_func`` 仅作为测试接缝；生产环境保持调用现有
        ``search_xhs_attractions``，不修改其正常逻辑。
        """
        if search_func is None:
            from ..services.xhs_service import search_xhs_attractions
            search_func = search_xhs_attractions

        fallback_message = "小红书数据不可用，已使用降级方案继续生成。"
        fallback_context = (
            "小红书研究数据不可用，本次没有来自小红书的景点候选或真实评价。"
            "请结合当前可用的地图、酒店、天气信息和保守的通用旅行知识继续规划；"
            "不得声称候选来自小红书，不得编造小红书热度、评价或预约依据。"
        )
        if not hasattr(self, "_xhs_results"):
            self._xhs_results = {}

        try:
            response = await asyncio.to_thread(search_func, city, keywords, language)
            if isinstance(response, XHSResearchResult):
                self._xhs_results[city] = response
                if response.status == "unavailable" or not response.evidence or not response.context:
                    raise RuntimeError(response.reason or "小红书研究不可用")
                return response.context
            # xhs_service 的 LLM 提纯失败目前会返回说明文本而不是抛异常。
            # 将该已知失败信号也纳入 fail-open，避免把失败文本当作研究证据。
            if not response or response.startswith("尝试提取小红书结构化数据失败"):
                raise RuntimeError(response or "小红书研究返回空结果")
            return response
        except Exception as exc:
            if city not in self._xhs_results:
                self._xhs_results[city] = XHSResearchResult(
                    status="unavailable", verification_status="unavailable", degraded=True,
                    reason=getattr(exc, "reason", None) or "request_failed",
                    evidence=[], context="",
                )
            print(f"⚠️ [XHS_FALLBACK] {city}: {fallback_message} 原因: {exc}")
            await self._emit_progress(
                progress_callback,
                "attraction_search",
                fallback_message,
                progress,
            )
            return fallback_context
    
    async def plan_trip(
        self,
        request: TripRequest,
        progress_callback: Optional[Callable[[str, str, int], Awaitable[None] | None]] = None
    ) -> TripPlan:
        """
        使用有界顺序式 LLM 编排与确定性服务生成旅行计划（支持多城市）。

        按 request.cities 逐城市搜集景点/天气/酒店信息，
        然后统一交给 LLM 生成跨城行程。
        单城市场景下 cities 只有一个元素，行为与原版一致。

        Args:
            request: 旅行请求

        Returns:
            旅行计划
        """
        try:
            cities = request.cities  # List[CityStay] — 已由 normalize_cities 保证非空
            total_cities = len(cities)
            city_names = [cs.city for cs in cities]

            print(f"\n{'='*60}")
            print(f"🚀 开始多智能体协作规划旅行...")
            print(f"途经城市: {' → '.join(city_names)}")
            print(f"日期: {request.start_date} 至 {request.end_date}")
            print(f"天数: {request.travel_days}天")
            print(f"偏好: {', '.join(request.preferences) if request.preferences else '无'}")
            print(f"{'='*60}\n")

            keywords = request.preferences[0] if request.preferences else "景点"
            _lang = (getattr(request, 'language', 'zh') or 'zh').strip().lower().split('-')[0]
            _lang_hint = "" if _lang == "zh" else f" Please respond in {'English' if _lang == 'en' else _lang}."

            # ========== 按城市逐一搜集信息 ==========
            all_attractions: Dict[str, str] = {}
            all_weather: Dict[str, str] = {}
            all_hotels: Dict[str, str] = {}
            self._weather_results: Dict[str, WeatherResult] = {}
            self._xhs_results: Dict[str, XHSResearchResult] = {}

            for idx, city_stay in enumerate(cities):
                city = city_stay.city
                # 计算当前城市对应的进度区间: 10 ~ 75 之间按城市均分
                progress_base = int(10 + (idx / total_cities) * 65)
                progress_step = max(int(65 / total_cities / 3), 3)
                city_label = f" ({idx+1}/{total_cities})" if total_cities > 1 else ""

                # [1] 景点搜索
                print(f"  [{idx+1}/{total_cities}] 正在搜索 {city} 的景点...")
                await self._emit_progress(
                    progress_callback, "attraction_search",
                    f"正在搜索 {city} 的景点...{city_label}",
                    progress_base
                )
                with timed_stage("trip_stage_timing", "xhs_research"):
                    attraction_response = await self._search_attractions_with_xhs_fallback(
                        city,
                        keywords,
                        _lang,
                        progress_callback,
                        progress_base,
                    )
                all_attractions[city] = attraction_response
                print(f"📍 {city} 景点搜索结果: {attraction_response[:150]}...")

                # [2] 天气查询
                print(f"  [{idx+1}/{total_cities}] 正在查询 {city} 的天气...")
                await self._emit_progress(
                    progress_callback, "weather_search",
                    f"正在查询 {city} 的天气...{city_label}",
                    progress_base + progress_step
                )
                with timed_stage("trip_stage_timing", "weather"):
                    weather_response = await self._retrieve_weather_context(city)
                print(f"🌤️  {city} 天气查询结果: {weather_response[:150]}...")
                all_weather[city] = weather_response

                # [3] 酒店搜索
                print(f"  [{idx+1}/{total_cities}] 正在搜索 {city} 的酒店...")
                await self._emit_progress(
                    progress_callback, "hotel_search",
                    f"正在搜索 {city} 的酒店...{city_label}",
                    progress_base + progress_step * 2
                )
                with timed_stage("trip_stage_timing", "hotel_search"):
                    hotel_response = await self._retrieve_hotel_context(
                        city, request.accommodation
                    )
                all_hotels[city] = hotel_response
                print(f"🏨 {city} 酒店搜索结果: {hotel_response[:150]}...")

            print(f"\n✅ 全部 {total_cities} 个城市基础信息搜集完成\n")

            # ========== 统一规划阶段 ==========
            planning_label = "正在生成多城市行程计划..." if total_cities > 1 else "正在生成旅行计划..."
            print(f"📋 步骤4: {planning_label}")
            await self._emit_progress(progress_callback, "planning", planning_label, 85)

            with timed_stage("trip_stage_timing", "planner"):
                planner_response = await self._run_planner_with_retry(
                    request,
                    all_attractions,
                    all_weather,
                    all_hotels,
                )
            print("Planner 响应已返回，开始结构化校验。\n")

            # 解析最终计划
            trip_plan = self._parse_response_with_timing(planner_response, request)
            trip_plan = self._sanitize_external_facts(trip_plan)
            trip_plan.xhs_research = [
                self._xhs_results.get(city, XHSResearchResult(
                    status="unavailable", verification_status="unavailable", degraded=True,
                    reason="request_failed", evidence=[], context="",
                ))
                for city in city_names
            ]
            trip_plan.weather_results = [
                self._weather_results.get(city, WeatherResult(
                    provider="unavailable", request_success=False, data_available=False,
                    degraded=True, reason="empty_forecast",
                ))
                for city in city_names
            ]
            trip_plan.weather_info = [
                day
                for city in city_names
                for day in trip_plan.weather_results[city_names.index(city)].days
            ]

            # 补全 cities 字段（LLM 可能遗漏）
            if not trip_plan.cities:
                trip_plan.cities = city_names
            # 补全每日 city 字段（单城市场景 LLM 可能遗漏）
            if total_cities == 1:
                for day in trip_plan.days:
                    if not day.city:
                        day.city = city_names[0]

            # Phase 1.5: Planner 完成后用 Google Places 确定性补全地图事实。
            # 此步骤 fail-open，不改变景点选择，也不让地图故障阻断既有 Planner。
            with timed_stage("trip_stage_timing", "poi_enrichment"):
                trip_plan = await self._enrich_trip_plan_pois(trip_plan)

            # Phase 2A: deterministic validation only. Fail-open preserves the
            # existing Planner result when validation infrastructure is unavailable.
            try:
                from ..services.trip_validator_service import get_trip_validator_service

                await self._emit_progress(
                    progress_callback,
                    "validating",
                    "正在检查行程约束、计划预算估算和地图路线...",
                    92,
                )
                from ..services.planner_observation import observe_revision, validation_pass
                with validation_pass("validation.initial", "initial"):
                    with timed_stage("trip_stage_timing", "validator"):
                        validation = await get_trip_validator_service().validate(
                            request, trip_plan
                        )
                trip_plan.risks = validation.risks
                trip_plan.validation_status = validation.status
                trip_plan.pacing_policy_version = validation.pacing_policy_version
                trip_plan.daily_load_assessments = validation.daily_load_assessments
                observe_revision(
                    "initial_validation",
                    plan=trip_plan.model_copy(deep=True),
                    validation_result=validation.model_dump(mode="json"),
                    risks=[risk.model_copy(deep=True) for risk in validation.risks],
                )
                print(
                    "✅ [VALIDATOR_COMPLETED] "
                    f"status={validation.status}, risks={len(validation.risks)}, "
                    f"route_api_calls={validation.route_api_calls}"
                )

                # Phase 2B: deterministic trigger -> optional critic -> at most
                # one revision -> fresh enrichment -> Validator #2 -> STOP.
                from ..services.trip_revision_service import (
                    filter_actionable_risks,
                    get_trip_revision_service,
                )
                from ..services.pacing_revision_service import (
                    get_pacing_revision_service, select_pacing_revision_risks,
                )

                actionable_risks = filter_actionable_risks(validation.risks)
                pacing_risks = select_pacing_revision_risks(validation.risks)
                pacing_attempted = False
                if trip_plan.revision_count == 0 and pacing_risks:
                    pacing_attempted = True
                    pacing_service = get_pacing_revision_service()
                    before_pacing = trip_plan.model_copy(deep=True)
                    try:
                        await self._emit_progress(
                            progress_callback, "pacing_revision",
                            "检测到单日节奏过载，正在进行受影响日期内的调整...", 95,
                        )
                        with timed_stage("trip_stage_timing", "revision"):
                            proposal = await pacing_service.propose(
                                request, trip_plan, pacing_risks
                            )
                        observe_revision(
                            "pacing_revision_proposal",
                            target_risk_ids=list(proposal.target_risk_ids),
                            affected_day_indices=list(proposal.affected_day_indices),
                            protected_day_indices=list(proposal.protected_day_indices),
                            revision_instructions=[
                                item.model_dump(mode="json") for item in proposal.operations
                            ],
                        )

                        async def enrich_affected(candidate, affected):
                            partial = candidate.model_copy(deep=True)
                            partial.days = [candidate.days[index].model_copy(deep=True) for index in affected]
                            with timed_stage("trip_stage_timing", "poi_enrichment"):
                                partial = await self._enrich_trip_plan_pois(partial)
                            enriched = candidate.model_copy(deep=True)
                            for position, day_index in enumerate(affected):
                                enriched.days[day_index] = partial.days[position]
                            return enriched

                        async def revalidate(req, candidate):
                            with validation_pass("validation.post_pacing_revision", "post_revision"):
                                with timed_stage("trip_stage_timing", "validator"):
                                    return await get_trip_validator_service().validate(
                                        req, candidate
                                    )

                        outcome = await pacing_service.execute(
                            request, trip_plan, validation.risks, proposal,
                            enricher=enrich_affected, validator=revalidate,
                        )
                        observe_revision(
                            "pacing_revision_result", before=before_pacing,
                            candidate=outcome.candidate_plan,
                            after=outcome.committed_plan.model_copy(deep=True),
                            status=outcome.status, failure_reason=outcome.failure_reason,
                            target_risk_ids=outcome.target_risk_ids,
                            affected_day_indices=outcome.affected_day_indices,
                            protected_day_indices=outcome.protected_day_indices,
                            protected_day_equality=outcome.protected_day_equality,
                            post_validation=outcome.post_validation,
                            post_pacing_risk_ids=outcome.post_pacing_risk_ids,
                            resolution_outcome=outcome.resolution_outcome,
                            metrics=outcome.metrics,
                            pacing_policy_version=outcome.pacing_policy_version,
                        )
                        trip_plan = outcome.committed_plan
                        if outcome.status != "success":
                            print(
                                "⚠️ [PACING_REVISION_REJECTED] "
                                f"status={outcome.status} reason={outcome.failure_reason}; 保留原计划"
                            )
                        else:
                            print(
                                "✅ [PACING_REVISION_COMPLETED] affected_days="
                                f"{outcome.affected_day_indices} protected_preserved=true"
                            )
                    except Exception as exc:
                        observe_revision(
                            "pacing_revision_result", before=before_pacing, candidate=None,
                            after=before_pacing, status="rejected",
                            failure_reason="invalid_revision_output",
                            target_risk_ids=[risk.id for risk in pacing_risks],
                            affected_day_indices=sorted({risk.day_index for risk in pacing_risks if risk.day_index is not None}),
                            protected_day_indices=[index for index in range(len(trip_plan.days))
                                                   if index not in {risk.day_index for risk in pacing_risks}],
                            protected_day_equality={}, post_validation=None,
                            post_pacing_risk_ids=[risk.id for risk in pacing_risks],
                            resolution_outcome="rejected", metrics={},
                            pacing_policy_version=trip_plan.pacing_policy_version,
                        )
                        print(f"⚠️ [PACING_REVISION_FAILED_CLOSED] 保留原计划: {exc}")

                if trip_plan.revision_count == 0 and actionable_risks and not pacing_attempted:
                    revision_service = get_trip_revision_service()
                    try:
                        await self._emit_progress(
                            progress_callback, "critic",
                            "发现可优化问题，正在分析...", 94,
                        )
                        critic = await revision_service.run_critic(
                            request, trip_plan, actionable_risks
                        )
                        observe_revision(
                            "critic",
                            target_risk_ids=[
                                risk_id for risk_id in critic.target_risk_ids
                                if risk_id in {risk.id for risk in actionable_risks}
                            ],
                            protected_elements=list(critic.protected_elements),
                            revision_instructions=list(critic.revision_instructions),
                        )
                        if critic.should_revise:
                            await self._emit_progress(
                                progress_callback, "revising",
                                "正在进行一次针对性调整...", 96,
                            )
                            compact_research = {
                                "attractions": {
                                    city: self._compact_json(value, limit=1500)
                                    for city, value in all_attractions.items()
                                },
                                "weather": {
                                    city: self._compact_json(value, limit=600)
                                    for city, value in all_weather.items()
                                },
                                "hotels": {
                                    city: self._compact_json(value, limit=600)
                                    for city, value in all_hotels.items()
                                },
                            }
                            with timed_stage("trip_stage_timing", "revision"):
                                revised_plan = await revision_service.run_revision(
                                    request,
                                    trip_plan,
                                    actionable_risks,
                                    critic,
                                    compact_research,
                                )
                            # Weather/XHS provenance comes from deterministic
                            # connectors, never from the revision model.
                            revised_plan.weather_results = [
                                item.model_copy(deep=True) for item in trip_plan.weather_results
                            ]
                            revised_plan.weather_info = [
                                item.model_copy(deep=True) for item in trip_plan.weather_info
                            ]
                            revised_plan.xhs_research = [
                                item.model_copy(deep=True) for item in trip_plan.xhs_research
                            ]
                            # Never reuse old map facts: the parsed revision has
                            # deterministic fields stripped and is fully enriched again.
                            with timed_stage("trip_stage_timing", "poi_enrichment"):
                                revised_plan = await self._enrich_trip_plan_pois(revised_plan)
                            observe_revision(
                                "post_revision_enrichment",
                                state="complete",
                                plan=revised_plan.model_copy(deep=True),
                            )
                            await self._emit_progress(
                                progress_callback, "revalidating",
                                "正在重新验证调整后的行程...", 98,
                            )
                            with validation_pass("validation.post_revision", "post_revision"):
                                with timed_stage("trip_stage_timing", "validator"):
                                    validation_2 = await get_trip_validator_service().validate(
                                        request, revised_plan
                                    )
                            revised_plan.risks = validation_2.risks
                            revised_plan.validation_status = validation_2.status
                            revised_plan.pacing_policy_version = validation_2.pacing_policy_version
                            revised_plan.daily_load_assessments = validation_2.daily_load_assessments
                            observe_revision(
                                "post_revision_validation",
                                plan=revised_plan.model_copy(deep=True),
                                validation_result=validation_2.model_dump(mode="json"),
                                risks=[risk.model_copy(deep=True) for risk in validation_2.risks],
                            )
                            trip_plan = revised_plan
                            print(
                                "✅ [REVISION_COMPLETED] revision_count=1 "
                                f"status={validation_2.status}, risks={len(validation_2.risks)}"
                            )
                            # Deliberately no trigger after Validator #2.
                    except Exception as exc:
                        # Phase 2B is an enhancement. Critic/revision/quota/parse/
                        # timeout failures all preserve the original plan and risks.
                        print(f"⚠️ [PHASE_2B_FAIL_OPEN] 保留原计划和首次验证结果: {exc}")
            except Exception as exc:
                from ..models.schemas import RiskItem

                print(f"⚠️ [VALIDATOR_DEGRADED] 基础行程检查不可用，保留原计划: {exc}")
                trip_plan.validation_status = "degraded"
                trip_plan.risks = [RiskItem(
                    id="validation_unavailable:service",
                    type="validation_unavailable",
                    severity="info",
                    title="基础行程检查暂不可用",
                    message="行程已经生成，但本次未能完成约束、计划预算估算和地图路线检查。",
                    evidence={},
                    suggestion="稍后可重新生成或手动核对关键约束。",
                    revisable=False,
                )]

            print(f"{'='*60}")
            print(f"✅ 旅行计划生成完成!")
            print(f"{'='*60}\n")

            return trip_plan

        except Exception as e:
            print(f"❌ 生成旅行计划失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise RuntimeError(f"旅行计划生成失败: {str(e)}") from e

    async def _enrich_trip_plan_pois(self, trip_plan: TripPlan) -> TripPlan:
        """Attach verified Google Place IDs, addresses and coordinates.

        Only a high-confidence name match may replace LLM-provided map fields.
        Partial and unverified matches retain the original display data and are
        explicitly marked so downstream route logic cannot treat them as facts.
        """
        name_evidence_counter_maps = {
            "best_name_score_band": {
                "lt_020": "name_low_score_band_lt_020",
                "020_039": "name_low_score_band_020_039",
                "040_049": "name_low_score_band_040_049",
                "050_059": "name_low_score_band_050_059",
            },
            "best_name_match_method": {
                "exact": "name_low_method_exact",
                "alias": "name_low_method_alias",
                "substring": "name_low_method_substring",
                "sequence": "name_low_method_sequence",
                "none": "name_low_method_none",
            },
            "script_relationship": {
                "same_script": "name_low_script_same",
                "cross_script": "name_low_script_cross",
                "mixed_script": "name_low_script_mixed",
                "unknown": "name_low_script_unknown",
            },
            "best_candidate_language_count": {
                1: "name_low_language_count_1",
                2: "name_low_language_count_2",
                3: "name_low_language_count_3",
            },
            "multilingual_same_place_id": {
                True: "name_low_multilingual_same_place_id_true",
                False: "name_low_multilingual_same_place_id_false",
            },
            "localized_name_variant_count_bucket": {
                "1": "name_low_variant_count_1",
                "2": "name_low_variant_count_2",
                "3plus": "name_low_variant_count_3plus",
            },
            "planner_address_hint_present": {
                True: "name_low_address_hint_present",
                False: "name_low_address_hint_absent",
            },
            "candidate_count_bucket": {
                "1": "name_low_candidate_count_1",
                "2_3": "name_low_candidate_count_2_3",
                "4plus": "name_low_candidate_count_4plus",
            },
        }
        name_evidence_fields = tuple(
            counter
            for mapping in name_evidence_counter_maps.values()
            for counter in mapping.values()
        )
        requested_language_labels = {"zh-CN": "zh", "ja": "ja", "en": "en"}
        returned_language_values = ("zh", "ja", "en", "other", "missing")
        script_values = ("same", "cross", "mixed", "unknown")
        score_values = ("lt_020", "020_039", "040_049", "050_059", "060plus")
        per_language_fields = tuple(
            field
            for label in requested_language_labels.values()
            for field in (
                f"name_low_{label}_present",
                f"name_low_{label}_absent",
                *(f"name_low_{label}_returned_language_{value}"
                  for value in returned_language_values),
                *(f"name_low_{label}_script_{value}" for value in script_values),
                *(f"name_low_{label}_score_{value}" for value in score_values),
                f"name_low_{label}_top_true",
                f"name_low_{label}_top_false",
            )
        )
        summary_fields = (
            "attractions", "unique_lookups", "text_search_calls", "candidate_found",
            "verified", "partial", "unverified", "no_candidates",
            "provider_failure", "name_mismatch", "city_mismatch", "type_mismatch",
            "scope_conflict", "invalid_place_id", "invalid_coordinates",
            "insufficient_evidence", "ambiguous",
            "city_identity_not_attempted", "city_identity_unresolved",
            "city_identity_conflicting",
            "city_trusted_name_absent_containment_empty",
            "city_trusted_name_absent_containment_nonmatching",
            "identity_not_attempted_name_below_threshold",
            "identity_not_attempted_type_incompatible",
            "identity_not_attempted_scope_conflict",
            "identity_not_attempted_invalid_place_id",
            "identity_not_attempted_provider_untrusted",
            "identity_not_attempted_invalid_coordinates",
            "autocomplete_shadow_attempted",
            "autocomplete_shadow_provider_failure",
            "autocomplete_shadow_malformed",
            "autocomplete_shadow_no_prediction",
            "autocomplete_shadow_place_id_match",
            "autocomplete_shadow_place_id_mismatch",
            "autocomplete_shadow_main_text_missing",
            "autocomplete_shadow_name_strong",
            "autocomplete_shadow_name_weak",
            "autocomplete_shadow_type_compatible",
            "autocomplete_shadow_type_incompatible",
            "autocomplete_shadow_eligible",
            "autocomplete_shadow_score_band_lt020",
            "autocomplete_shadow_score_band_020_039",
            "autocomplete_shadow_score_band_040_059",
            "autocomplete_shadow_score_band_060_087",
            "autocomplete_shadow_score_band_088plus",
        ) + name_evidence_fields + per_language_fields
        summary = {field: 0 for field in summary_fields}
        summary_complete = False
        try:
            from ..services.google_map_service import (
                get_google_map_service,
                observe_generation_grounding,
            )

            service = get_google_map_service()
            if service is None:
                summary_complete = True
                return trip_plan

            cache: Dict[tuple[str, str], Dict[str, Any]] = {}
            verified_count = 0
            partial_count = 0
            unverified_count = 0

            for day in trip_plan.days:
                city = day.city or trip_plan.city
                for attraction in day.attractions:
                    summary["attractions"] += 1
                    cache_key = (city.strip().casefold(), attraction.name.strip().casefold())
                    if cache_key not in cache:
                        summary["unique_lookups"] += 1
                        with observe_generation_grounding() as grounding_observation:
                            cache[cache_key] = await asyncio.to_thread(
                                service.match_poi,
                                attraction.name,
                                city,
                                attraction.address,
                                attraction.category or "",
                            )
                        unique_match = cache[cache_key]
                        evidence = unique_match.get("evidence") or {}
                        summary["text_search_calls"] += int(evidence.get("search_calls") or 0)
                        if unique_match.get("poi") is not None:
                            summary["candidate_found"] += 1
                        unique_status = unique_match.get("status", "unverified")
                        shadow = grounding_observation.get("autocomplete_shadow")
                        if isinstance(shadow, dict) and shadow.get("attempted") is True:
                            summary["autocomplete_shadow_attempted"] += 1
                            outcome_counter = {
                                "provider_failure": "autocomplete_shadow_provider_failure",
                                "malformed": "autocomplete_shadow_malformed",
                                "no_prediction": "autocomplete_shadow_no_prediction",
                                "place_id_mismatch": "autocomplete_shadow_place_id_mismatch",
                                "main_text_missing": "autocomplete_shadow_main_text_missing",
                                "name_weak": "autocomplete_shadow_name_weak",
                                "type_incompatible": "autocomplete_shadow_type_incompatible",
                                "eligible": "autocomplete_shadow_eligible",
                            }.get(shadow.get("outcome"))
                            if outcome_counter is not None:
                                summary[outcome_counter] += 1
                            if shadow.get("place_id_match") is True:
                                summary["autocomplete_shadow_place_id_match"] += 1
                            if shadow.get("name_strong") is True:
                                summary["autocomplete_shadow_name_strong"] += 1
                            if shadow.get("type_compatible") is True:
                                summary["autocomplete_shadow_type_compatible"] += 1
                            score_counter = {
                                "lt020": "autocomplete_shadow_score_band_lt020",
                                "020_039": "autocomplete_shadow_score_band_020_039",
                                "040_059": "autocomplete_shadow_score_band_040_059",
                                "060_087": "autocomplete_shadow_score_band_060_087",
                                "088plus": "autocomplete_shadow_score_band_088plus",
                            }.get(shadow.get("score_band"))
                            if score_counter is not None:
                                summary[score_counter] += 1
                        if unique_status == "verified":
                            summary["verified"] += 1
                        elif unique_status == "partial_match":
                            summary["partial"] += 1
                        else:
                            summary["unverified"] += 1
                            reason = grounding_observation.get("terminal_category")
                            if reason not in summary:
                                reason = service.grounding_terminal_category(unique_match)
                            if reason in summary:
                                summary[reason] += 1
                            if reason == "city_mismatch":
                                city_resolution = grounding_observation.get(
                                    "city_resolution_category"
                                )
                                city_counter = {
                                    "identity_not_attempted": "city_identity_not_attempted",
                                    "identity_unresolved": "city_identity_unresolved",
                                    "identity_conflicting": "city_identity_conflicting",
                                    "trusted_name_absent_containment_empty": (
                                        "city_trusted_name_absent_containment_empty"
                                    ),
                                    "trusted_name_absent_containment_nonmatching": (
                                        "city_trusted_name_absent_containment_nonmatching"
                                    ),
                                }.get(city_resolution)
                                if city_counter is not None:
                                    summary[city_counter] += 1
                                if city_resolution == "identity_not_attempted":
                                    prerequisite = grounding_observation.get(
                                        "city_identity_prerequisite_category"
                                    )
                                    prerequisite_counter = {
                                        "name_below_threshold": (
                                            "identity_not_attempted_name_below_threshold"
                                        ),
                                        "type_incompatible": (
                                            "identity_not_attempted_type_incompatible"
                                        ),
                                        "scope_conflict": (
                                            "identity_not_attempted_scope_conflict"
                                        ),
                                        "invalid_place_id": (
                                            "identity_not_attempted_invalid_place_id"
                                        ),
                                        "provider_untrusted": (
                                            "identity_not_attempted_provider_untrusted"
                                        ),
                                        "invalid_coordinates": (
                                            "identity_not_attempted_invalid_coordinates"
                                        ),
                                    }.get(prerequisite)
                                    if prerequisite_counter is not None:
                                        summary[prerequisite_counter] += 1
                                    if prerequisite == "name_below_threshold":
                                        name_evidence = grounding_observation.get(
                                            "name_low_evidence"
                                        )
                                        if isinstance(name_evidence, dict):
                                            for field, mapping in (
                                                name_evidence_counter_maps.items()
                                            ):
                                                counter = mapping.get(
                                                    name_evidence.get(field)
                                                )
                                                if counter is not None:
                                                    summary[counter] += 1
                                            per_language = name_evidence.get(
                                                "per_language"
                                            )
                                            if not isinstance(per_language, dict):
                                                per_language = {}
                                            for request_language, label in (
                                                requested_language_labels.items()
                                            ):
                                                item = per_language.get(request_language)
                                                if not isinstance(item, dict):
                                                    summary[f"name_low_{label}_absent"] += 1
                                                    continue
                                                summary[f"name_low_{label}_present"] += 1
                                                returned_language = item.get(
                                                    "returned_language"
                                                )
                                                if returned_language not in returned_language_values:
                                                    returned_language = "missing"
                                                summary[
                                                    f"name_low_{label}_returned_language_{returned_language}"
                                                ] += 1
                                                script = {
                                                    "same_script": "same",
                                                    "cross_script": "cross",
                                                    "mixed_script": "mixed",
                                                    "unknown": "unknown",
                                                }.get(item.get("script_relationship"), "unknown")
                                                summary[f"name_low_{label}_script_{script}"] += 1
                                                score_band = item.get("score_band")
                                                if score_band not in score_values:
                                                    score_band = "lt_020"
                                                summary[f"name_low_{label}_score_{score_band}"] += 1
                                                top = item.get("top") is True
                                                summary[
                                                    f"name_low_{label}_top_{str(top).lower()}"
                                                ] += 1

                    match = cache[cache_key]
                    status = match.get("status", "unverified")
                    poi = match.get("poi")
                    attraction.poi_match_status = status

                    if (
                        status == "verified"
                        and poi is not None
                        and poi.id
                        and has_valid_verified_coordinates(poi.location)
                    ):
                        attraction.place_id = poi.id
                        attraction.poi_id = poi.id  # existing compatibility field
                        attraction.address = poi.address
                        attraction.location = poi.location
                        attraction.map_data_source = "google_places"
                        if poi.rating is not None:
                            attraction.rating = poi.rating
                        verified_count += 1
                    elif status == "partial_match":
                        attraction.place_id = ""
                        attraction.poi_id = ""
                        attraction.rating = None
                        attraction.map_data_source = "llm_unverified"
                        partial_count += 1
                    else:
                        attraction.poi_match_status = "unverified"
                        attraction.place_id = ""
                        attraction.poi_id = ""
                        attraction.rating = None
                        attraction.map_data_source = "llm_unverified"
                        unverified_count += 1

            print(
                "🗺️ [POI_ENRICHMENT] "
                f"Google Places calls={len(cache)}, verified={verified_count}, "
                f"partial={partial_count}, unverified={unverified_count}"
            )
            summary_complete = True
        except Exception as exc:
            print(f"⚠️ [POI_ENRICHMENT] 地图事实补全失败，保留原计划继续: {exc}")
        finally:
            if summary_complete:
                fields = " ".join(f"{field}={summary[field]}" for field in summary_fields)
                print(f"event=poi_grounding_summary {fields}", flush=True)

        return trip_plan

    @staticmethod
    def _sanitize_external_facts(trip_plan: TripPlan) -> TripPlan:
        """Strip every external-looking fact emitted by the initial Planner.

        Textual addresses remain useful as unverified match hints. Coordinates are
        legacy-required by the schema, so a zero sentinel is used until a verified
        connector replaces it.
        """
        for day in trip_plan.days:
            for attraction in day.attractions:
                attraction.place_id = ""
                attraction.poi_id = ""
                attraction.poi_match_status = "unverified"
                attraction.map_data_source = "llm_unverified"
                attraction.location = Location(longitude=0.0, latitude=0.0)
                attraction.rating = None
                attraction.photos = []
                attraction.image_url = None
        return trip_plan
    
    def _build_attraction_query(self, request: TripRequest) -> str:
        """构建景点搜索查询 - 直接包含工具调用"""
        keywords = []
        if request.preferences:
            # 只取第一个偏好作为关键词
            keywords = request.preferences[0]
        else:
            keywords = "景点"

        # 直接返回工具调用格式，使用正确的工具名和严格的格式
        query = f"请使用amap_maps_text_search工具搜索{request.city}的{keywords}相关的景点。\n非常重要：你必须直接输出 `[TOOL_CALL:amap_maps_text_search:keywords={keywords},city={request.city}]`，不要附带任何多余的 JSON 或文字说明！"
        return query


    async def _run_planner_with_retry(
        self,
        request: TripRequest,
        attractions: Dict[str, str],
        weather: Dict[str, str],
        hotels: Dict[str, str],
    ) -> str:
        """规划阶段使用更长超时，并在超时后重试一次。
        
        Args:
            attractions: {city_name: 景点搜索结果文本}
            weather: {city_name: 天气查询结果文本}
            hotels: {city_name: 酒店搜索结果文本}
        """
        timeout = int(os.getenv("TRIP_PLANNER_TIMEOUT", "180"))
        with timed_stage("planner_stage_timing", "planner_input"):
            planner_query = self._build_planner_query(request, attractions, weather, hotels)
            planner_agent = self._new_planner_agent()

        try:
            return await asyncio.to_thread(
                planner_agent.run,
                planner_query,
                timeout=timeout,
                temperature=0.2,
                max_tokens=6000,
            )
        except Exception as exc:
            err_text = str(exc).lower()
            if "timeout" not in err_text and "timed out" not in err_text:
                raise

            print("⚠️  首次行程规划超时，正在重试一次...")
            record_application_retry()
            planner_query += (
                "\n\n**补充要求:** 如果部分辅助信息不足，请使用保守、常见、可执行的建议补齐，"
                "但必须输出完整合法的 JSON，不要输出解释性文字。"
            )
            return await asyncio.to_thread(
                planner_agent.run,
                planner_query,
                timeout=timeout,
                temperature=0.2,
                max_tokens=6000,
            )

    def _build_planner_query(
        self,
        request: TripRequest,
        attractions: Dict[str, str],
        weather: Dict[str, str],
        hotels: Dict[str, str],
    ) -> str:
        """构建行程规划查询（支持多城市）
        
        Args:
            attractions: {city_name: 景点搜索结果文本}
            weather: {city_name: 天气查询结果文本}
            hotels: {city_name: 酒店搜索结果文本}
        """
        cities = request.cities
        total_cities = len(cities)
        is_multi_city = total_cities > 1

        # 构建城市停留计划描述
        if is_multi_city:
            cities_info_lines = []
            day_offset = 0
            for cs in cities:
                cities_info_lines.append(
                    f"- {cs.city}: 停留 {cs.days} 天 (第{day_offset+1}天 ~ 第{day_offset+cs.days}天)"
                )
                day_offset += cs.days
            cities_desc = "\n".join(cities_info_lines)
            title = f"跨城市旅行计划（{' → '.join(cs.city for cs in cities)}）"
        else:
            cities_desc = f"- {cities[0].city}: {cities[0].days} 天"
            title = f"{cities[0].city}的{request.travel_days}天旅行计划"

        query = f"""请根据以下信息生成{title}:

**基本信息:**
- 途经城市及天数分配:
{cities_desc}
- 总天数: {request.travel_days}天
- 日期: {request.start_date} 至 {request.end_date}
- 交通方式: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}
"""
        profile = getattr(request, "preference_profile", None)
        if profile:
            party_labels = {
                "solo": "独自旅行", "couple": "情侣", "friends": "朋友",
                "family": "家庭", "with_parents": "带父母", "with_children": "带儿童",
            }
            pace_labels = {"intensive": "特种兵", "balanced": "适中", "relaxed": "松弛度假"}
            effective_interests = list(dict.fromkeys(profile.interests + profile.inferred_interests))
            profile_lines = [
                f"- 同行: {party_labels.get(profile.party_type, profile.party_type)}，共 {profile.party_size} 人",
                (
                    f"- 目的地旅行期间当地消费总预算: {profile.budget_cny} 元人民币"
                    "（不包含往返目的地的大交通）"
                    if profile.budget_cny is not None
                    else "- 目的地旅行期间当地消费总预算: 未设置（不包含往返目的地的大交通）"
                ),
                f"- 旅行节奏: {pace_labels.get(profile.pace, profile.pace)}",
                f"- 兴趣: {', '.join(effective_interests) if effective_interests else '无'}",
            ]
            constraints = profile.constraints
            if constraints.avoid_early_start:
                if constraints.earliest_start_time:
                    profile_lines.append(f"- 不早起约束: 每天主要行程不早于 {constraints.earliest_start_time}")
                else:
                    profile_lines.append("- 不早起偏好: 用户不希望过早出发，但未指定具体最早时间")
            if constraints.mobility_notes:
                profile_lines.append(f"- 行动需求: {'；'.join(constraints.mobility_notes)}")
            if constraints.food_notes:
                profile_lines.append(f"- 饮食需求: {'；'.join(constraints.food_notes)}")
            if constraints.other_notes:
                profile_lines.append(f"- 其他要求: {'；'.join(constraints.other_notes)}")
            query += "\n**已由用户确认的 Preference Profile:**\n" + "\n".join(profile_lines) + "\n"
            from ..services.pacing_policy import compact_planner_contract
            pacing_contract = compact_planner_contract(profile.pace)
            query += (
                "\n**Pacing Contract（proposed product policy）:**\n"
                + json.dumps(pacing_contract, ensure_ascii=False, separators=(",", ":"))
                + "\n生成每天安排时同时为景点、移动、用餐、出入口和休息留出容量；"
                  "未知路线必须保守预留时间，不得按零分钟处理。\n"
            )
        # 为每个城市附上搜集到的信息
        for cs in cities:
            city = cs.city
            if is_multi_city:
                query += f"""
--- {city} ({cs.days}天) ---
**{city} 景点信息:**
{attractions.get(city, '无')}
**{city} 天气信息:**
{weather.get(city, '无')}
**{city} 酒店信息:**
{hotels.get(city, '无')}
"""
            else:
                query += f"""
**景点信息:**
{attractions.get(city, '无')}

**天气信息:**
{weather.get(city, '无')}

**酒店信息:**
{hotels.get(city, '无')}
"""

        query += """
**本次规划补充要求:**
1. 每天从已提供的信息中选择一个具体酒店，并保留酒店名称和 estimated_cost
2. attraction 的 name、address、category、visit_duration、ticket_price 及有证据的预约字段必须保留，顺序即实际游览顺序
3. weather_info 固定输出 []；系统将在 Planner 完成后写入权威 Provider 天气。不得在其他字段复述天气表或编造具体温度、逐日晴雨、风速
4. 严格返回系统提示定义的完整 JSON 结构
"""
        if request.free_text_input:
            query += f"\n**额外要求:** {request.free_text_input}"

        # 如果用户选择了非中文语言，指示模型用目标语言输出所有文字内容
        _lang = (getattr(request, 'language', 'zh') or 'zh').strip().lower().split('-')[0]
        if _lang != 'zh':
            _lang_names = {"en": "English", "ja": "Japanese", "ko": "Korean", "fr": "French", "de": "German", "es": "Spanish"}
            _target_lang = _lang_names.get(_lang, _lang)
            query += f"""\n\n**语言要求 (Language Requirement):**
请用 {_target_lang} 语言输出所有文字内容（包括 description, overall_suggestions, meals 中的 name/description, hotel 中的 name/address, attractions 中的 name/address/description 等）。
JSON 的 key 名称保持英文不变，只翻译 value 中的文字。"""

        return query
    
    def _sanitize_json_str(self, json_str: str) -> str:
        """清理大模型输出中常见的 JSON 格式污染"""
        import re as _re
        # 1. 移除可能包裹在外面的 ```json ... ``` 标记
        json_str = _re.sub(r'^```(?:json)?\s*', '', json_str.strip())
        json_str = _re.sub(r'```\s*$', '', json_str.strip())
        # 2. 移除 JS 风格注释 // ... 和 /* ... */
        json_str = _re.sub(r'//[^\n]*', '', json_str)
        json_str = _re.sub(r'/\*.*?\*/', '', json_str, flags=_re.DOTALL)
        # 3. 移除 JSON 值中的控制字符
        json_str = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', json_str)
        # 4. 修复尾部逗号: },] 或 },}
        json_str = _re.sub(r',\s*([\]\}])', r'\1', json_str)
        # 5. 修复中文引号和全角标点
        #    注意: 中文双引号""必须替换为单引号，因为它们通常出现在 JSON 字符串值内部
        #    如果替换为标准双引号会破坏 JSON 结构！
        json_str = json_str.replace('\u201c', "'").replace('\u201d', "'")
        json_str = json_str.replace('\u2018', "'").replace('\u2019', "'")
        json_str = json_str.replace('\uff1a', ':')
        json_str = json_str.replace('\uff0c', ',')
        # 6. 修复 LLM 在 budget 等数值字段中输出算术表达式的问题
        #    例如: "total_attractions": 30+54+120+120=324 → "total_attractions": 324
        #    模式: 冒号后面跟着 数字[+-*/]数字...=最终结果
        def _fix_arithmetic_expr(m):
            """将算术表达式替换为等号后的最终结果，若无等号则尝试 eval"""
            expr = m.group(1).strip()
            if '=' in expr:
                # 取等号后面的最终结果
                return m.group(0).replace(m.group(1), expr.split('=')[-1].strip())
            else:
                # 没有等号，尝试安全计算
                try:
                    result = eval(expr, {"__builtins__": {}}, {})
                    return m.group(0).replace(m.group(1), str(result))
                except Exception:
                    return m.group(0)
        # 匹配 JSON 键值对中冒号后的算术表达式（含 +、-、*、= 且以数字开头）
        json_str = _re.sub(
            r':\s*(\d+(?:\s*[+\-*/]\s*\d+)+(?:\s*=\s*\d+)?)',
            _fix_arithmetic_expr,
            json_str
        )
        return json_str
    
    def _fix_unescaped_quotes(self, json_str: str) -> str:
        """修复 JSON 字符串值内部未转义的双引号
        
        例如: "description": "这是"好的"景点" 
        修复为: "description": "这是'好的'景点"
        """
        import re as _re
        result = []
        i = 0
        in_string = False
        escape_next = False
        
        while i < len(json_str):
            ch = json_str[i]
            
            if escape_next:
                result.append(ch)
                escape_next = False
                i += 1
                continue
            
            if ch == '\\' and in_string:
                escape_next = True
                result.append(ch)
                i += 1
                continue
            
            if ch == '"':
                if not in_string:
                    in_string = True
                    result.append(ch)
                else:
                    # 看下一个非空白字符是否是 JSON 结构字符
                    rest = json_str[i+1:].lstrip()
                    if rest and rest[0] in (',', '}', ']', ':'):
                        # 这是真正的字符串结尾引号
                        in_string = False
                        result.append(ch)
                    elif not rest:
                        # 到末尾了，也是结尾引号
                        in_string = False
                        result.append(ch)
                    else:
                        # 内嵌的未转义引号，替换为单引号
                        result.append("'")
            else:
                result.append(ch)
            
            i += 1
        
        return ''.join(result)

    def _repair_truncated_json(self, json_str: str) -> str:
        """修复被 max_tokens 截断的不完整 JSON。

        策略：
        1. 如果最后一个字符在字符串值内部，先关闭该字符串。
        2. 移除最后一个不完整的键值对（trailing comma 之后的碎片）。
        3. 根据打开/关闭的括号差额，补齐缺失的 ] 和 }。
        """
        import re as _re

        s = json_str.rstrip()
        if not s:
            return s

        # --- Step 1: 关闭未终止的字符串 ---
        in_str = False
        escape = False
        for ch in s:
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
        if in_str:
            # 去掉尾部可能的碎片转义符
            s = s.rstrip('\\')
            s += '"'

        # --- Step 2: 移除尾部不完整的键值对碎片 ---
        # 常见模式: 值字符串闭合后紧跟着换行但后面没有逗号/括号
        # 或者尾部是 "key": 但缺少值
        # 尝试反复去除尾部碎片直到以合法的 JSON 结构字符结尾
        for _ in range(10):
            stripped = s.rstrip()
            if not stripped:
                break
            last = stripped[-1]
            if last in ('}', ']', '"', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
                        'e', 'l', 's'):
                # 'e' for true/false, 'l' for null, 's' unlikely but safe
                break
            # 当前尾部是非法字符(如冒号、逗号、空键名开头等)，回退一个 token
            s = stripped[:-1]

        # 移除尾部悬挂的逗号
        s = _re.sub(r',\s*$', '', s)

        # --- Step 3: 补齐缺失的闭合括号 ---
        open_braces = s.count('{') - s.count('}')
        open_brackets = s.count('[') - s.count(']')

        # 更精确: 扫描非字符串中的括号
        stack = []
        in_str2 = False
        esc2 = False
        for ch in s:
            if esc2:
                esc2 = False
                continue
            if ch == '\\' and in_str2:
                esc2 = True
                continue
            if ch == '"':
                in_str2 = not in_str2
                continue
            if in_str2:
                continue
            if ch in ('{', '['):
                stack.append(ch)
            elif ch == '}' and stack and stack[-1] == '{':
                stack.pop()
            elif ch == ']' and stack and stack[-1] == '[':
                stack.pop()

        # 用精确的 stack 逆序关闭
        closing = [']' if c == '[' else '}' for c in reversed(stack)]
        if closing:
            s += '\n' + ''.join(closing)

        return s

    def _llm_repair_json(self, broken_json: str) -> str:
        """使用 LLM 修复无法自动修复的 JSON（最后手段）"""
        llm = get_llm()

        repair_prompt = f"""以下是一段被截断的旅行计划 JSON，请你补全它使其成为合法的 JSON。
只输出修复后的完整 JSON，不要输出任何解释文字。

需要修复的完整 JSON:
{broken_json}
"""
        try:
            response = create_chat_completion(
                stage="json_repair",
                model=llm.model,
                messages=[{"role": "user", "content": repair_prompt}],
                llm_instance=llm,
                temperature=0.0,
                max_tokens=6000,
                stage_max_token_exposure=6000,
            )
            metadata = structured_output_metadata(response, 6000)
            if metadata["finish_reason"] == "length":
                log_structured_output_event(
                    stage="json_repair", category="output_limit_reached",
                    metadata=metadata, success=False,
                )
                raise StructuredOutputLimitReached("json repair output limit reached")
            content = response.choices[0].message.content or ""
            # 从修复结果中提取 JSON
            import re as _re
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                if end > start:
                    return content[start:end].strip()
            if "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                if end > start:
                    return content[start:end].strip()
            match = _re.search(r'\{[\s\S]*\}', content)
            if match:
                return match.group()
            return content
        except LLMCallBudgetExceeded:
            print("⚠️  LLM 调用预算已用尽，跳过 JSON repair")
            return broken_json
        except StructuredOutputLimitReached:
            raise
        except Exception:
            log_structured_output_event(
                stage="json_repair", category="repair_exhausted",
                metadata={
                    "finish_reason": "missing", "configured_output_limit": 6000,
                    "completion_tokens": None,
                }, success=False,
            )
            return broken_json

    @staticmethod
    def _safe_schema_errors(exc: ValidationError, limit: int = 5) -> List[Dict[str, str]]:
        """Extract bounded Pydantic metadata without values, messages, or context."""
        safe_errors: List[Dict[str, str]] = []
        for item in exc.errors(
            include_url=False, include_context=False, include_input=False,
        )[:max(0, min(limit, 5))]:
            parts = []
            for component in item.get("loc", ()):
                raw = "*" if isinstance(component, int) else str(component)
                cleaned = re.sub(r"[^A-Za-z0-9_*-]", "_", raw)[:48]
                parts.append(cleaned or "unknown")
            field = ".".join(parts)[:200] or "root"
            error_type = re.sub(
                r"[^A-Za-z0-9_.-]", "_", str(item.get("type") or "unknown"),
            )[:80]
            safe_errors.append({"field": field, "error_type": error_type or "unknown"})
        return safe_errors

    @staticmethod
    def _log_schema_errors(stage: str, errors: List[Dict[str, str]]) -> None:
        safe_stage = stage if stage in {"planner_parse", "schema_repair"} else "planner_parse"
        for item in errors[:5]:
            print(
                "event=planner_schema_event "
                f"stage={safe_stage} field={item['field']} "
                f"error_type={item['error_type']} success=false",
                flush=True,
            )

    @staticmethod
    def _normalize_planner_schema_data(data: Any) -> Any:
        """Apply only repository-proven, semantics-preserving normalization."""
        normalized = copy.deepcopy(data)
        if not isinstance(normalized, dict):
            return normalized
        days = normalized.get("days")
        if not isinstance(days, list):
            return normalized
        for day in days:
            if isinstance(day, dict) and day.get("start_time") == "":
                # Optional start_time consumers already treat empty as absent.
                day["start_time"] = None
        return normalized

    def _llm_repair_schema(
        self, data: Any, errors: List[Dict[str, str]],
    ) -> Any:
        """Perform one bounded schema-only repair on already-decoded JSON."""
        llm = get_llm()
        bounded_errors = errors[:5]
        repair_prompt = (
            "Repair the decoded TripPlan JSON so it satisfies the existing schema.\n"
            "Preserve all factual content and change only fields required by the listed errors.\n"
            "Do not add unsupported facts. Do not invent weather, coordinates, POI IDs, "
            "routes, prices, attractions, or itinerary days. Return JSON only.\n"
            f"Schema errors: {json.dumps(bounded_errors, ensure_ascii=False, separators=(',', ':'))}\n"
            "Complete decoded JSON:\n"
            f"{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}"
        )
        response = create_chat_completion(
            stage="schema_repair",
            model=llm.model,
            messages=[{"role": "user", "content": repair_prompt}],
            llm_instance=llm,
            temperature=0.0,
            max_tokens=6000,
            stage_max_token_exposure=6000,
        )
        metadata = structured_output_metadata(response, 6000)
        if metadata["finish_reason"] == "length":
            log_structured_output_event(
                stage="schema_repair", category="output_limit_reached",
                metadata=metadata, success=False,
            )
            raise StructuredOutputLimitReached("schema repair output limit reached")
        content = response.choices[0].message.content or ""
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            content = content[start:end].strip() if end > start else content[start:].strip()
        elif "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            content = content[start:end].strip() if end > start else content[start:].strip()
        else:
            match = re.search(r"\{[\s\S]*\}", content)
            content = match.group() if match else content
        try:
            return json.loads(self._sanitize_json_str(content))
        except json.JSONDecodeError:
            log_structured_output_event(
                stage="schema_repair", category="json_decode_failed",
                metadata=metadata, success=False,
            )
            raise ValueError("行程 schema repair decode failed") from None

    def _validate_or_repair_planner_schema(
        self, data: Any, planner_metadata: Dict[str, Any],
    ) -> TripPlan:
        try:
            return TripPlan.model_validate(data)
        except ValidationError as exc:
            errors = self._safe_schema_errors(exc)
            self._log_schema_errors("planner_parse", errors)
            log_structured_output_event(
                stage="planner_parse", category="schema_validation_failed",
                metadata=planner_metadata, success=False,
            )

        normalized = self._normalize_planner_schema_data(data)
        if normalized != data:
            try:
                return TripPlan.model_validate(normalized)
            except ValidationError as exc:
                errors = self._safe_schema_errors(exc)
                self._log_schema_errors("planner_parse", errors)

        with timed_stage("planner_stage_timing", "planner_schema_repair"):
            repaired = self._llm_repair_schema(normalized, errors)
        try:
            return TripPlan.model_validate(repaired)
        except ValidationError as exc:
            repair_errors = self._safe_schema_errors(exc)
            self._log_schema_errors("schema_repair", repair_errors)
            metadata = get_last_structured_output("schema_repair")
            log_structured_output_event(
                stage="schema_repair", category="schema_validation_failed",
                metadata=metadata, success=False,
            )
            raise ValueError("行程 schema repair validation failed") from None

    def _parse_response_with_timing(self, response: str, request: TripRequest) -> TripPlan:
        """Measure existing local Planner parsing without changing its result."""
        with timed_stage("planner_stage_timing", "planner_parse_validate"):
            return self._parse_response(response, request)

    def _parse_response(self, response: str, request: TripRequest) -> TripPlan:
        """
        解析Agent响应，带有多层容错清理
        
        Args:
            response: Agent响应文本
            request: 原始请求
            
        Returns:
            旅行计划
        """
        import re as _re
        try:
            planner_metadata = get_last_structured_output("planner")
            if planner_metadata["finish_reason"] == "length":
                log_structured_output_event(
                    stage="planner_parse", category="output_limit_reached",
                    metadata=planner_metadata, success=False,
                )
                raise StructuredOutputLimitReached("planner output limit reached")
            # 尝试从响应中提取JSON
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                # 如果没有找到闭合的 ```，说明输出被截断，取到末尾
                if json_end == -1 or json_end <= json_start:
                    json_str = response[json_start:].strip()
                else:
                    json_str = response[json_start:json_end].strip()
            elif "```" in response:
                json_start = response.find("```") + 3
                json_end = response.find("```", json_start)
                if json_end == -1 or json_end <= json_start:
                    json_str = response[json_start:].strip()
                else:
                    json_str = response[json_start:json_end].strip()
            elif "{" in response:
                json_start = response.find("{")
                json_end = response.rfind("}")
                if json_end > json_start:
                    json_str = response[json_start:json_end + 1]
                else:
                    # 没有闭合的 }，取到末尾（截断场景）
                    json_str = response[json_start:]
            else:
                raise ValueError("响应中未找到JSON数据")
            
            # ====== 第1轮：基础清理 + 解析 ======
            json_str = self._sanitize_json_str(json_str)
            
            parse_attempts = [
                ("基础清理", json_str),
            ]

            # 预生成各轮修复候选
            fixed_quotes = self._fix_unescaped_quotes(json_str)
            parse_attempts.append(("修复未转义引号", fixed_quotes))

            # 截断修复
            repaired = self._repair_truncated_json(json_str)
            if repaired != json_str:
                parse_attempts.append(("截断修复", repaired))
                # 截断修复 + 引号修复
                repaired_fixed = self._fix_unescaped_quotes(repaired)
                if repaired_fixed != repaired:
                    parse_attempts.append(("截断+引号修复", repaired_fixed))

            # 暴力正则提取
            match = _re.search(r'\{[\s\S]*\}', json_str)
            if match:
                brutal = self._sanitize_json_str(match.group())
                brutal = self._fix_unescaped_quotes(brutal)
                parse_attempts.append(("正则提取", brutal))
                # 对正则提取的结果也做截断修复
                brutal_repaired = self._repair_truncated_json(brutal)
                if brutal_repaired != brutal:
                    parse_attempts.append(("正则+截断修复", brutal_repaired))

            # 依次尝试每种本地语法修复。JSON syntax 和 schema validation
            # 必须分开：语法有效但 schema 无效时不调用通用 JSON repair。
            decoded_any = False
            for attempt_name, candidate in parse_attempts:
                try:
                    data = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                decoded_any = True
                return self._validate_or_repair_planner_schema(data, planner_metadata)

            if not decoded_any:
                log_structured_output_event(
                    stage="planner_parse", category="json_decode_failed",
                    metadata=planner_metadata, success=False,
                )

            # ====== 最终手段：LLM 修复 ======
            with timed_stage("planner_stage_timing", "planner_json_repair"):
                llm_fixed = self._llm_repair_json(json_str)
            llm_fixed = self._sanitize_json_str(llm_fixed)
            try:
                data = json.loads(llm_fixed)
            except json.JSONDecodeError:
                repair_metadata = get_last_structured_output("json_repair")
                log_structured_output_event(
                    stage="json_repair", category="json_decode_failed",
                    metadata=repair_metadata, success=False,
                )
                raise ValueError("行程 JSON repair decode failed") from None
            try:
                return TripPlan.model_validate(data)
            except ValidationError:
                repair_metadata = get_last_structured_output("json_repair")
                log_structured_output_event(
                    stage="json_repair", category="schema_validation_failed",
                    metadata=repair_metadata, success=False,
                )
                raise ValueError("行程 JSON repair schema validation failed") from None
            
        except ValueError:
            raise
        except StructuredOutputLimitReached:
            raise
        except Exception:
            raise ValueError("行程 JSON 解析失败") from None
    
    def _create_fallback_plan(self, request: TripRequest) -> TripPlan:
        """创建备用计划(当Agent失败时)"""
        from datetime import datetime, timedelta
        
        # 解析日期
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        
        # 创建每日行程
        days = []
        for i in range(request.travel_days):
            current_date = start_date + timedelta(days=i)
            
            day_plan = DayPlan(
                date=current_date.strftime("%Y-%m-%d"),
                day_index=i,
                description=f"第{i+1}天行程",
                transportation=request.transportation,
                accommodation=request.accommodation,
                attractions=[
                    Attraction(
                        name=f"{request.city}景点{j+1}",
                        address=f"{request.city}市",
                        location=Location(longitude=116.4 + i*0.01 + j*0.005, latitude=39.9 + i*0.01 + j*0.005),
                        visit_duration=120,
                        description=f"这是{request.city}的著名景点",
                        category="景点"
                    )
                    for j in range(2)
                ],
                meals=[
                    Meal(type="breakfast", name=f"第{i+1}天早餐", description="当地特色早餐"),
                    Meal(type="lunch", name=f"第{i+1}天午餐", description="午餐推荐"),
                    Meal(type="dinner", name=f"第{i+1}天晚餐", description="晚餐推荐")
                ]
            )
            days.append(day_plan)
        
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=[],
            overall_suggestions=f"这是为您规划的{request.city}{request.travel_days}日游行程,建议提前查看各景点的开放时间。"
        )


# 全局规划编排器实例（保留既有类名以维持兼容）
_multi_agent_planner = None


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    """获取旅行规划编排器单例。"""
    global _multi_agent_planner

    if _multi_agent_planner is None:
        _multi_agent_planner = MultiAgentTripPlanner()

    return _multi_agent_planner


def reset_trip_planner_agent() -> None:
    """重置旅行规划编排器实例（用于运行时配置更新后热生效）。"""
    global _multi_agent_planner
    _multi_agent_planner = None
