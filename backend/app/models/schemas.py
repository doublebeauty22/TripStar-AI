"""数据模型定义"""

import math
from typing import Annotated, Any, Dict, List, Optional, Union, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from datetime import date


# ============ 请求模型 ============

class CityStay(BaseModel):
    """单城市停留配置"""
    city: str = Field(..., description="城市名称")
    days: int = Field(..., description="在该城市停留天数", ge=1, le=15)


PartyType = Literal["solo", "couple", "friends", "family", "with_parents", "with_children"]
TravelPace = Literal["intensive", "balanced", "relaxed"]


class PreferenceConstraints(BaseModel):
    """从用户特殊要求中提取的最小旅行约束。"""

    avoid_early_start: bool = Field(default=False, description="是否不希望过早开始行程")
    earliest_start_time: Optional[str] = Field(
        default=None,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="用户确认的最早出发时间 HH:MM；未明确时保持为空",
    )
    mobility_notes: List[str] = Field(default_factory=list, description="行动能力相关要求")
    food_notes: List[str] = Field(default_factory=list, description="饮食相关要求")
    other_notes: List[str] = Field(default_factory=list, description="其他旅行要求")


class PreferenceProfile(BaseModel):
    """Phase 1 最小用户旅行偏好画像。"""

    party_type: PartyType = Field(..., description="同行人类型")
    party_size: int = Field(..., ge=1, le=20, description="出行总人数")
    budget_cny: Optional[int] = Field(
        default=None,
        gt=0,
        description="目的地旅行期间的当地消费总预算（人民币元），不包含往返目的地的大交通",
    )
    pace: TravelPace = Field(default="balanced", description="旅行节奏")
    interests: List[str] = Field(default_factory=list, description="用户显式选择的兴趣")
    special_requirements: str = Field(default="", description="用户特殊要求原文")
    constraints: PreferenceConstraints = Field(default_factory=PreferenceConstraints)
    inferred_interests: List[str] = Field(default_factory=list, description="AI 从特殊要求识别的兴趣")
    parsing_notes: List[str] = Field(default_factory=list, description="需要用户确认的解析说明")


class PreferenceParseRequest(BaseModel):
    """偏好解析请求；显式字段不会被 LLM 覆盖。"""

    party_type: PartyType
    party_size: int = Field(..., ge=1, le=20)
    budget_cny: Optional[int] = Field(default=None, gt=0)
    pace: TravelPace = "balanced"
    interests: List[str] = Field(default_factory=list)
    special_requirements: str = ""
    generation_id: Optional[str] = Field(default=None, max_length=128)


class PreferenceParseResponse(BaseModel):
    success: bool = True
    profile: PreferenceProfile
    used_llm: bool = False
    message: str = ""
    generation_id: Optional[str] = None


class TripRequest(BaseModel):
    """旅行规划请求"""
    city: str = Field(default="", description="目的地城市(单城市兼容)", example="北京")
    cities: List[CityStay] = Field(default=[], description="多城市行程配置")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD", example="2025-06-01")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD", example="2025-06-03")
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30, example=3)
    transportation: str = Field(..., description="交通方式", example="公共交通")
    accommodation: str = Field(..., description="住宿偏好", example="经济型酒店")
    preferences: List[str] = Field(default=[], description="旅行偏好标签", example=["历史文化", "美食"])
    free_text_input: Optional[str] = Field(default="", description="额外要求", example="希望多安排一些博物馆")
    language: Optional[str] = Field(default="zh", description="输出语言(zh/en/ja)", example="en")
    preference_profile: Optional[PreferenceProfile] = Field(
        default=None,
        description="用户确认后的结构化偏好画像；不传时保持旧版行为",
    )
    generation_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="可选的完整用户生成关联 ID；旧客户端可不传",
    )

    @model_validator(mode='after')
    def normalize_cities(self):
        """兼容处理: 如果只填了 city 没填 cities, 自动转换"""
        if not self.cities and self.city:
            self.cities = [CityStay(city=self.city, days=self.travel_days)]
        if self.cities and not self.city:
            self.city = self.cities[0].city
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "city": "北京",
                "cities": [{"city": "北京", "days": 2}, {"city": "西安", "days": 3}],
                "start_date": "2025-06-01",
                "end_date": "2025-06-05",
                "travel_days": 5,
                "transportation": "公共交通",
                "accommodation": "经济型酒店",
                "preferences": ["历史文化", "美食"],
                "free_text_input": "希望多安排一些博物馆"
            }
        }


class POISearchRequest(BaseModel):
    """POI搜索请求"""
    keywords: str = Field(..., description="搜索关键词", example="故宫")
    city: str = Field(..., description="城市", example="北京")
    citylimit: bool = Field(default=True, description="是否限制在城市范围内")


class RouteRequest(BaseModel):
    """路线规划请求"""
    origin_address: str = Field(..., description="起点地址", example="北京市朝阳区阜通东大街6号")
    destination_address: str = Field(..., description="终点地址", example="北京市海淀区上地十街10号")
    origin_city: Optional[str] = Field(default=None, description="起点城市")
    destination_city: Optional[str] = Field(default=None, description="终点城市")
    route_type: str = Field(default="walking", description="路线类型: walking/driving/transit")


# ============ 响应模型 ============

class Location(BaseModel):
    """地理位置"""
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")


def has_valid_verified_coordinates(location: Any) -> bool:
    """Canonical trust gate for external map coordinates."""
    if location is None:
        return False
    if isinstance(location, dict):
        longitude, latitude = location.get("longitude"), location.get("latitude")
    else:
        longitude = getattr(location, "longitude", None)
        latitude = getattr(location, "latitude", None)
    if isinstance(longitude, bool) or isinstance(latitude, bool):
        return False
    try:
        longitude_value, latitude_value = float(longitude), float(latitude)
    except (TypeError, ValueError):
        return False
    return bool(
        math.isfinite(longitude_value)
        and math.isfinite(latitude_value)
        and -180 <= longitude_value <= 180
        and -90 <= latitude_value <= 90
        and not (longitude_value == 0 and latitude_value == 0)
    )


class Attraction(BaseModel):
    """景点信息"""
    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    visit_duration: int = Field(..., description="建议游览时间(分钟)")
    description: str = Field(..., description="景点描述")
    category: Optional[str] = Field(default="景点", description="景点类别")
    rating: Optional[float] = Field(default=None, description="评分")
    photos: Optional[List[str]] = Field(default_factory=list, description="景点图片URL列表")
    poi_id: Optional[str] = Field(default="", description="POI ID")
    place_id: Optional[str] = Field(default="", description="Google Place ID")
    poi_match_status: Literal["verified", "partial_match", "unverified"] = Field(
        default="unverified",
        description="地图 POI 匹配状态",
    )
    map_data_source: Literal["google_places", "amap", "llm_unverified"] = Field(
        default="llm_unverified",
        description="地址和坐标的数据来源",
    )
    image_url: Optional[str] = Field(default=None, description="图片URL")
    ticket_price: int = Field(default=0, description="门票价格(元)")
    reservation_required: Optional[bool] = Field(default=False, description="是否需要提前预约")
    reservation_tips: Optional[str] = Field(default="", description="预约提示信息")


class Meal(BaseModel):
    """餐饮信息"""
    type: str = Field(..., description="餐饮类型: breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐饮名称")
    address: Optional[str] = Field(default=None, description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    description: Optional[str] = Field(default=None, description="描述")
    estimated_cost: int = Field(default=0, description="预估费用(元)")


class Hotel(BaseModel):
    """酒店信息"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = Field(default=None, description="酒店位置")
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")
    distance: str = Field(default="", description="距离景点距离")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: int = Field(default=0, description="预估费用(元/晚)")


class DayPlan(BaseModel):
    """单日行程"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_index: int = Field(..., description="第几天(从0开始)")
    start_time: Optional[str] = Field(
        default=None,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="当天第一个主要活动预计开始时间 HH:MM；不是起床或早餐时间",
    )
    city: str = Field(default="", description="当日所在城市")
    is_transfer_day: bool = Field(default=False, description="是否为城际移动日")
    transfer_info: Optional[str] = Field(default="", description="城际交通信息")
    description: str = Field(..., description="当日行程描述")
    transportation: str = Field(..., description="交通方式")
    accommodation: str = Field(..., description="住宿")
    hotel: Optional[Hotel] = Field(default=None, description="推荐酒店")
    attractions: List[Attraction] = Field(default=[], description="景点列表")
    meals: List[Meal] = Field(default=[], description="餐饮列表")


class WeatherInfo(BaseModel):
    """天气信息"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    city: str = Field(default="", description="所在城市")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: Optional[int] = Field(default=None, description="白天温度；provider 未提供时未知")
    night_temp: Optional[int] = Field(default=None, description="夜间温度；provider 未提供时未知")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")
    precipitation_probability: Optional[int] = Field(
        default=None, ge=0, le=100, description="降水概率百分比；provider 未提供时未知",
    )
    data_source: Literal["google_weather", "amap", "llm_general"] = "llm_general"
    verification_status: Literal["verified", "partial", "unverified", "unavailable"] = "unverified"
    degraded: bool = False

    @field_validator('day_temp', 'night_temp', mode='before')
    @classmethod
    def parse_temperature(cls, v):
        """Parse a finite provider temperature without inventing a sentinel."""
        if v is None:
            return None
        if isinstance(v, str):
            v = v.replace('°C', '').replace('℃', '').replace('°', '').strip()
            if not v:
                return None
        if isinstance(v, bool):
            return None
        try:
            value = float(v)
        except (TypeError, ValueError):
            return None
        return int(round(value)) if math.isfinite(value) else None


WeatherFailureReason = Literal[
    "key_missing", "authentication_failed", "permission_denied", "rate_limited",
    "timeout", "network_error", "malformed_response", "empty_forecast",
    "unsupported_location",
]

AmapFailureReason = Literal[
    "key_missing", "authentication_failed", "permission_denied", "rate_limited",
    "timeout", "network_error", "malformed_response", "business_error",
    "empty_result", "unsupported_mode",
]


class WeatherResult(BaseModel):
    """Provider result that separates request success from usable forecast data."""
    provider: Literal["google_weather", "amap", "unavailable"]
    city: str = ""
    request_success: bool
    data_available: bool
    degraded: bool
    reason: Optional[WeatherFailureReason] = None
    days: List[WeatherInfo] = Field(default_factory=list)


class XHSEvidence(BaseModel):
    note_id: str
    title: str = ""
    source_url: str = ""
    status: Literal["metadata_only", "detail_available", "detail_unavailable"]
    extracted_text: str = ""


class XHSEvidenceSupport(BaseModel):
    """Short source excerpts proving one note supports one extracted item."""
    evidence_id: str
    identity_quote: str = Field(..., min_length=1, max_length=240)
    recommendation_quote: str = Field(..., min_length=1, max_length=240)


class XHSExtractedItem(BaseModel):
    """One recommendation supported by specific XHS notes."""
    name: str = Field(..., min_length=1, max_length=240)
    identity_text: str = Field(default="", max_length=240)
    name_zh: str = ""
    name_en: str = ""
    evidence_summary: str = Field(default="", max_length=1000)
    recommendation: str = Field(default="", max_length=1000)
    duration: Optional[int] = None
    reservation_required: Optional[bool] = None
    reservation_tips: str = ""
    evidence_ids: List[str] = Field(default_factory=list)
    evidence_support: List[XHSEvidenceSupport] = Field(default_factory=list)
    location: Optional[dict] = None
    location_status: Literal["available", "unavailable"] = "unavailable"


class XHSResearchResult(BaseModel):
    status: Literal["available", "degraded", "unavailable"]
    data_source: Literal["xhs"] = "xhs"
    verification_status: Literal["verified", "partial", "unavailable"] = "unavailable"
    degraded: bool = True
    reason: Optional[str] = None
    evidence: List[XHSEvidence] = Field(default_factory=list)
    extracted_items: List[XHSExtractedItem] = Field(default_factory=list)
    context: str = ""


class Budget(BaseModel):
    """预算信息"""
    total_attractions: int = Field(default=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, description="酒店总费用")
    total_meals: int = Field(default=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, description="交通总费用")
    total_inter_city_transport: int = Field(default=0, description="城际交通总费用")
    total: int = Field(default=0, description="总费用")


RiskSeverity = Literal["info", "warning", "blocking"]
RiskType = Literal[
    "earliest_start",
    "mobility",
    "budget",
    "route_feasibility",
    "validation_unavailable",
    "pacing",
]


class RiskItem(BaseModel):
    """Deterministic trip validation finding."""

    id: str
    type: RiskType
    severity: RiskSeverity
    day_index: Optional[int] = None
    related_poi_names: List[str] = Field(default_factory=list)
    title: str
    message: str
    evidence: Dict[str, Any] = Field(default_factory=dict)
    suggestion: str = ""
    source: Literal["rule_validator"] = "rule_validator"
    revisable: bool = True


class ValidationResult(BaseModel):
    """Output of the deterministic Phase 2A validator."""

    status: Literal["passed", "issues_found", "degraded"]
    risks: List[RiskItem] = Field(default_factory=list)
    checked_rules: List[str] = Field(default_factory=list)
    unavailable_checks: List[str] = Field(default_factory=list)
    route_api_calls: int = 0
    pacing_policy_version: Optional[str] = None
    daily_load_assessments: List[Dict[str, Any]] = Field(default_factory=list)
    validation_pass_scope: Optional[str] = None


class CriticResult(BaseModel):
    """Compact, advisory-only output from the Phase 2B critic."""

    should_revise: bool
    revision_instructions: List[str] = Field(default_factory=list)
    protected_elements: List[str] = Field(default_factory=list)
    summary: str = ""
    target_risk_ids: List[str] = Field(default_factory=list)


class TripPlan(BaseModel):
    """旅行计划"""
    city: str = Field(..., description="主城市(兼容)/首个城市")
    cities: List[str] = Field(default=[], description="所有途经城市列表")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(..., description="每日行程")
    weather_info: List[WeatherInfo] = Field(default=[], description="天气信息")
    weather_results: List[WeatherResult] = Field(default_factory=list, description="按城市记录天气供应商状态")
    xhs_research: List[XHSResearchResult] = Field(default_factory=list, description="可追溯的小红书研究状态")
    overall_suggestions: str = Field(..., description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")
    risks: List[RiskItem] = Field(default_factory=list, description="确定性检查发现的问题")
    validation_status: Optional[Literal["passed", "issues_found", "degraded"]] = Field(
        default=None,
        description="基础规则检查状态",
    )
    revision_count: int = Field(default=0, ge=0, le=1)
    revision_summary: Optional[str] = None
    plan_version: int = Field(default=1, ge=1)
    pacing_policy_version: Optional[str] = None
    daily_load_assessments: List[Dict[str, Any]] = Field(default_factory=list)


# ============ Phase 2C local trip patch ============

class StrictPatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatchPOIInput(StrictPatchModel):
    """User-requested POI identity without any map/verification facts."""

    name: str = Field(..., min_length=1, max_length=160)
    visit_duration: int = Field(default=60, ge=15, le=600)
    description: str = Field(default="", max_length=1000)
    category: str = Field(default="景点", max_length=80)
    ticket_price: int = Field(default=0, ge=0)


class PatchMealInput(StrictPatchModel):
    type: Literal["breakfast", "lunch", "dinner", "snack"]
    name: str = Field(..., min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    estimated_cost: int = Field(default=0, ge=0)


class ReplacePOIOperation(StrictPatchModel):
    operation: Literal["replace_poi"]
    day_index: int = Field(..., ge=0)
    target_id: str = Field(..., min_length=1, max_length=200)
    target_name: str = Field(..., min_length=1, max_length=160)
    new_poi: PatchPOIInput
    user_instruction: str = Field(default="", max_length=500)


class RemovePOIOperation(StrictPatchModel):
    operation: Literal["remove_poi"]
    day_index: int = Field(..., ge=0)
    target_id: str = Field(..., min_length=1, max_length=200)
    target_name: str = Field(..., min_length=1, max_length=160)
    user_instruction: str = Field(default="", max_length=500)


class AddPOIOperation(StrictPatchModel):
    operation: Literal["add_poi"]
    day_index: int = Field(..., ge=0)
    new_poi: PatchPOIInput
    user_instruction: str = Field(default="", max_length=500)


class UpdateStartTimeOperation(StrictPatchModel):
    operation: Literal["update_start_time"]
    day_index: int = Field(..., ge=0)
    old_value: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    new_value: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    user_instruction: str = Field(default="", max_length=500)


class UpdateTransportOperation(StrictPatchModel):
    operation: Literal["update_transport"]
    day_index: int = Field(..., ge=0)
    old_value: Optional[str] = Field(default=None, max_length=1000)
    new_value: str = Field(..., min_length=1, max_length=1000)
    user_instruction: str = Field(default="", max_length=500)


class UpdateMealOperation(StrictPatchModel):
    operation: Literal["update_meal"]
    day_index: int = Field(..., ge=0)
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"]
    target_name: Optional[str] = Field(default=None, max_length=160)
    new_meal: PatchMealInput
    user_instruction: str = Field(default="", max_length=500)


class UpdateDayPaceOperation(StrictPatchModel):
    operation: Literal["update_day_pace"]
    day_index: int = Field(..., ge=0)
    new_value: Literal["lighter"]
    user_instruction: str = Field(default="", max_length=500)


PatchOperation = Annotated[
    Union[
        ReplacePOIOperation,
        RemovePOIOperation,
        AddPOIOperation,
        UpdateStartTimeOperation,
        UpdateTransportOperation,
        UpdateMealOperation,
        UpdateDayPaceOperation,
    ],
    Field(discriminator="operation"),
]


class TripPatch(StrictPatchModel):
    intent: str = Field(..., min_length=1, max_length=300)
    operations: List[PatchOperation] = Field(default_factory=list, max_length=8)
    affected_day_indices: List[int] = Field(default_factory=list)
    protected_day_indices: List[int] = Field(default_factory=list)
    summary: str = Field(default="", max_length=300)
    requires_regeneration: bool = False
    regeneration_reason: Optional[str] = Field(default=None, max_length=500)


class TripPatchRequest(StrictPatchModel):
    instruction: str = Field(..., min_length=1, max_length=1000)
    current_plan_version: int = Field(..., ge=1)
    patch_request_id: str = Field(..., min_length=8, max_length=128)


class TripChangeDiff(BaseModel):
    changed_day_indices: List[int] = Field(default_factory=list)
    changed_fields: List[str] = Field(default_factory=list)
    added_pois: List[str] = Field(default_factory=list)
    removed_pois: List[str] = Field(default_factory=list)
    replaced_pois: List[str] = Field(default_factory=list)
    unchanged_day_indices: List[int] = Field(default_factory=list)


class TripPatchResult(BaseModel):
    success: bool
    updated_plan: Optional[TripPlan] = None
    graph_data: Optional[Dict[str, Any]] = None
    patch: Optional[TripPatch] = None
    changed_day_indices: List[int] = Field(default_factory=list)
    change_summary: List[str] = Field(default_factory=list)
    diff: Optional[TripChangeDiff] = None
    validation_status: Optional[Literal["passed", "issues_found", "degraded"]] = None
    risks: List[RiskItem] = Field(default_factory=list)
    requires_regeneration: bool = False
    regeneration_reason: Optional[str] = None
    error: Optional[str] = None
    plan_version: int = Field(default=1, ge=1)
    patch_request_id: str = ""


# ============ 知识图谱数据模型 ============

class GraphNode(BaseModel):
    """图谱节点"""
    id: str = Field(..., description="节点ID")
    name: str = Field(..., description="节点名称")
    category: int = Field(default=0, description="分类索引")
    symbolSize: int = Field(default=30, description="节点大小")
    itemStyle: Optional[dict] = Field(default=None, description="节点样式")
    value: Optional[str] = Field(default="", description="附加信息")


class GraphEdge(BaseModel):
    """图谱边"""
    source: str = Field(..., description="源节点ID")
    target: str = Field(..., description="目标节点ID")
    label: str = Field(default="", description="关系标签")


class GraphCategory(BaseModel):
    """图谱分类"""
    name: str = Field(..., description="分类名称")


class KnowledgeGraphData(BaseModel):
    """知识图谱数据"""
    nodes: List[GraphNode] = Field(default=[], description="节点列表")
    edges: List[GraphEdge] = Field(default=[], description="边列表")
    categories: List[GraphCategory] = Field(default=[], description="分类列表")


class TripPlanResponse(BaseModel):
    """旅行计划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    plan_id: Optional[str] = Field(default=None, description="计划ID（与后端任务ID对齐）")
    data: Optional[TripPlan] = Field(default=None, description="旅行计划数据")
    graph_data: Optional[KnowledgeGraphData] = Field(default=None, description="知识图谱数据")


class POIInfo(BaseModel):
    """POI信息"""
    id: str = Field(..., description="POI ID")
    name: str = Field(..., description="名称")
    type: str = Field(..., description="类型")
    address: str = Field(..., description="地址")
    district: str = Field(default="", description="行政区")
    location: Location = Field(..., description="经纬度坐标")
    tel: Optional[str] = Field(default=None, description="电话")
    rating: Optional[float] = Field(default=None, description="Google Places 评分")
    user_rating_count: Optional[int] = Field(default=None, description="Google Places 评分数量")
    photo_name: Optional[str] = Field(default=None, description="Google Place Photo 临时资源名")
    photo_attributions: List[dict] = Field(default_factory=list, description="图片署名信息")
    data_source: str = Field(default="google_places", description="POI 数据来源")
    verification_status: Literal["verified", "partial", "unverified"] = "verified"


class AmapPOISearchResult(BaseModel):
    provider: Literal["amap", "unavailable"]
    request_success: bool
    data_available: bool
    degraded: bool = False
    reason: Optional[AmapFailureReason] = None
    data: List[POIInfo] = Field(default_factory=list)


class AmapGeocodeResult(BaseModel):
    provider: Literal["amap", "unavailable"]
    request_success: bool
    data_available: bool
    degraded: bool = False
    reason: Optional[AmapFailureReason] = None
    location: Optional[Location] = None
    poi_id: str = ""
    formatted_address: str = ""
    resolution_path: Literal["geocoding", "poi_search", "unavailable"] = "unavailable"


class AmapRouteResult(BaseModel):
    provider: Literal["amap", "unavailable"]
    request_success: bool
    data_available: bool
    degraded: bool = False
    reason: Optional[AmapFailureReason] = None
    distance: Optional[float] = None
    duration: Optional[int] = None
    route_mode: str = ""


class POISearchResponse(BaseModel):
    """POI搜索响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[POIInfo] = Field(default=[], description="POI列表")
    provider: Literal["amap", "unavailable"] = "unavailable"
    request_success: bool = False
    data_available: bool = False
    degraded: bool = True
    reason: Optional[AmapFailureReason] = None


class RouteInfo(BaseModel):
    """路线信息"""
    distance: float = Field(..., description="距离(米)")
    duration: int = Field(..., description="时间(秒)")
    route_type: str = Field(..., description="路线类型")
    description: str = Field(..., description="路线描述")


class RouteResponse(BaseModel):
    """路线规划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[RouteInfo] = Field(default=None, description="路线信息")
    provider: Literal["amap", "unavailable"] = "unavailable"
    request_success: bool = False
    data_available: bool = False
    degraded: bool = True
    reason: Optional[AmapFailureReason] = None


class WeatherResponse(BaseModel):
    """天气查询响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: WeatherResult = Field(..., description="天气查询结果与来源状态")


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")


# ============ AI 行程问答模型 ============

class ChatMessage(BaseModel):
    """单条对话消息"""
    role: str = Field(..., description="角色: user / assistant")
    content: str = Field(..., description="消息内容")


class TripChatRequest(BaseModel):
    """行程问答请求"""
    message: str = Field(..., description="用户提问内容")
    trip_plan: dict = Field(..., description="当前旅行计划(JSON对象)")
    history: Optional[List[ChatMessage]] = Field(default=[], description="历史对话记录")


class TripChatResponse(BaseModel):
    """行程问答响应"""
    success: bool = Field(default=True, description="是否成功")
    reply: str = Field(..., description="AI回复内容")
