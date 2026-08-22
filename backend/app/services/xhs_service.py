"""小红书搜索服务 - 基于 Spider_XHS 原生签名引擎

彻底替换第三方 xhs 库，使用本地 JS 签名直连小红书 Search/Detail API，
解决 300011 账号异常风控误杀问题。
"""

import json
import re
import math
import random
import logging
import threading
import uuid
import requests
import httpx
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
from ..config import get_settings
from ..models.schemas import (
    XHSEvidence, XHSEvidenceSupport, XHSExtractedItem, XHSResearchResult,
)
from .timing import timed_stage
from .llm_service import (
    get_llm, log_structured_output_event, structured_output_metadata,
)
logger = logging.getLogger(__name__)
_XHS_SIGNING_LOCK = threading.Lock()
_XHS_IMAGE_FALLBACK_CONCURRENCY = 2
_XHS_IMAGE_FALLBACK_SEMAPHORE = threading.Semaphore(_XHS_IMAGE_FALLBACK_CONCURRENCY)
_XHS_EXTRACTION_NOTE_CHARS = 500
_XHS_EXTRACTION_MAX_TOKENS = 4000
_FABRICATED_CONSENSUS_TERMS = (
    "大家都推荐", "小红书普遍认为", "小红书用户普遍推荐", "热门必去",
)


class XHSCookieExpiredError(Exception):
    """小红书 Cookie 过期致命异常，用于向前端报警"""
    pass


class XHSRequestError(Exception):
    def __init__(
        self, reason: str, message: str, *, retryable: Optional[bool] = None,
        business_code: Optional[str] = None, http_status: Optional[int] = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable
        self.business_code = business_code
        self.http_status = (
            http_status
            if reason == "http_error"
            and type(http_status) is int and 100 <= http_status <= 599
            else None
        )


_XHS_RETRYABLE = {
    "missing_config": False,
    "authentication_failed": False,
    "permission_denied": False,
    "rate_limited": True,
    "risk_control": False,
    "sign_error": False,
    "timeout": True,
    "network_error": True,
    "malformed_response": False,
    "no_result": False,
    "unexpected_error": False,
    "http_error": False,
    "business_rejected": False,
    "request_error": False,
}


def _log_xhs_event(
    *, request_id: str, stage: str, category: str,
    retryable_override: Optional[bool] = None,
    business_code: Optional[str] = None,
    http_status: Optional[int] = None,
) -> None:
    """Emit only stable diagnostic metadata; never provider or request data."""
    safe_request_id = request_id if re.fullmatch(r"[0-9a-f]{12}", request_id or "") else "unknown"
    retryable = (
        retryable_override
        if retryable_override is not None
        else _XHS_RETRYABLE.get(category, False)
    )
    safe_status = (
        http_status
        if category == "http_error"
        and type(http_status) is int and 100 <= http_status <= 599
        else None
    )
    print(
        "XHS_EVENT "
        f"request_id={safe_request_id} stage={stage} category={category} "
        f"{f'business_code={business_code} ' if business_code is not None else ''}"
        f"{f'status={safe_status} ' if safe_status is not None else ''}"
        f"retryable={str(retryable).lower()}",
        flush=True,
    )


def _sanitize_xhs_business_code(code: Any) -> str:
    """Return a bounded token safe for logs, never arbitrary provider content."""
    if code is None:
        return "missing"
    if type(code) is int:
        return str(code)
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", code):
        return code
    return "redacted"


def _sign_request(cookies_str: str, api: str, data: dict):
    """Keep signing failures separate from HTTP/provider failures."""
    try:
        from .xhs_sign.sign_util import generate_request_params
        return generate_request_params(cookies_str, api, data, "POST")
    except Exception as exc:
        raise XHSRequestError("sign_error", "XHS request signing failed") from exc


def _new_search_id() -> str:
    try:
        from .xhs_sign.sign_util import generate_x_b3_traceid
        return generate_x_b3_traceid(21)
    except Exception as exc:
        raise XHSRequestError("sign_error", "XHS request signing failed") from exc


# ============ Cookie 处理 ============

def normalize_xhs_cookie(cookie: str) -> str:
    """兼容 Cookie 请求头字符串和浏览器导出的 JSON Cookie 列表。"""
    normalized = cookie.strip()
    if not normalized:
        return normalized

    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        normalized = normalized[1:-1].strip()

    cookie_items = None
    if normalized.startswith("[") and normalized.endswith("]"):
        try:
            cookie_items = json.loads(normalized)
        except json.JSONDecodeError:
            cookie_items = None
    elif normalized.startswith("{") and '"name"' in normalized and '"value"' in normalized:
        try:
            cookie_items = json.loads(f"[{normalized}]")
        except json.JSONDecodeError:
            cookie_items = None

    if isinstance(cookie_items, list):
        pairs = []
        for item in cookie_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            value = str(item.get("value", "")).strip()
            if name:
                pairs.append(f"{name}={value}")
        if pairs:
            print("已将 JSON 格式的小红书 Cookie 转换为请求头字符串格式。")
            return "; ".join(pairs)

    return normalized


# ============ 原生小红书 API 客户端 ============

class XhsNativeClient:
    """
    使用 Spider_XHS 签名引擎直连小红书 API 的原生客户端。
    不依赖任何第三方 xhs Python 库，通过 PyExecJS 调用本地 JS
    生成 x-s / x-t / x-s-common 等完整签名，彻底绕过 300011 风控。
    """
    BASE_URL = "https://edith.xiaohongshu.com"
    SEARCH_BASE_URL = "https://so.xiaohongshu.com"
    SEARCH_API = "/api/sns/web/v2/search/notes"
    SEARCH_AUTHORITY = "so.xiaohongshu.com"

    def __init__(self, cookies_str: str):
        self.cookies_str = cookies_str
        # Backend analogue of one browser page/search session. This value is
        # private, process-local, never persisted, and never accepted from a
        # caller or emitted in logs.
        self._search_session_id = str(uuid.uuid4())

    def search_notes(self, keyword: str, page: int = 1, sort_type: int = 0,
                     page_size: int = 20) -> dict:
        """
        搜索笔记 - 直连 /api/sns/web/v2/search/notes
        
        Args:
            keyword: 搜索关键词
            page: 页码
            sort_type: 排序方式 0综合 1最新 2最多点赞
            page_size: 每页数量
            
        Returns:
            API 响应 JSON
        """
        # Retain sort_type in the internal API for caller compatibility. The
        # evidence-supported minimal v2 contract uses general sorting; current
        # production callers already pass the default value.
        _ = sort_type

        api = self.SEARCH_API
        data = {
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
            "search_id": _new_search_id(),
            "sort": "general",
            "note_type": 0,
            "ext_flags": [],
            "geo": "",
            "image_formats": ["jpg"],
            "session_id": self._search_session_id,
        }

        # The imported PyExecJS contexts are process-global and do not document
        # concurrent-call safety. Serialize signing only; HTTP waits remain free
        # to overlap across the bounded research workers.
        with _XHS_SIGNING_LOCK:
            headers, cookies, serialized_data = _sign_request(self.cookies_str, api, data)
        # Header construction is shared with the unchanged Detail API and
        # defaults to edith. Override only the trusted Search authority here.
        headers = dict(headers)
        headers["authority"] = self.SEARCH_AUTHORITY
        response = requests.post(
            self.SEARCH_BASE_URL + api,
            headers=headers,
            data=serialized_data.encode("utf-8"),
            cookies=cookies,
            timeout=15,
        )
        if response.status_code == 401:
            raise XHSRequestError("authentication_failed", "小红书认证失败")
        if response.status_code == 403:
            raise XHSRequestError("permission_denied", "小红书访问被拒绝")
        if response.status_code == 429:
            raise XHSRequestError("rate_limited", "小红书请求频率受限")
        if 400 <= response.status_code < 600:
            raise XHSRequestError(
                "http_error", "小红书搜索 HTTP 请求失败",
                retryable=response.status_code >= 500,
                http_status=response.status_code,
            )
        response.raise_for_status()
        res_json = response.json()

        if not res_json.get("success"):
            code = res_json.get("code")
            msg = res_json.get("msg", "")
            if code == 300011 or code == "300011" or "异常" in msg:
                raise XHSCookieExpiredError(
                    f"小红书 Cookie 已被风控拦截 (code={code}): {msg}。请更换 Cookie 后重试。"
                )
            raise XHSRequestError(
                "business_rejected", "小红书搜索业务请求被拒绝",
                business_code=_sanitize_xhs_business_code(code),
            )

        return res_json

    def get_note_detail(self, note_id: str, xsec_token: str = "",
                        xsec_source: str = "pc_search") -> dict:
        """
        获取笔记详情 - 直连 /api/sns/web/v1/feed
        
        Args:
            note_id: 笔记 ID
            xsec_token: 安全令牌（来自搜索结果）
            xsec_source: 来源标识
            
        Returns:
            笔记详情 JSON
        """
        api = "/api/sns/web/v1/feed"
        data = {
            "source_note_id": note_id,
            "image_formats": ["jpg", "webp", "avif"],
            "extra": {"need_body_topic": "1"},
            "xsec_source": xsec_source,
            "xsec_token": xsec_token,
        }

        with _XHS_SIGNING_LOCK:
            headers, cookies, serialized_data = _sign_request(self.cookies_str, api, data)
        response = requests.post(
            self.BASE_URL + api,
            headers=headers,
            data=serialized_data,
            cookies=cookies,
            timeout=15,
        )
        if response.status_code == 401:
            raise XHSRequestError("authentication_failed", "小红书认证失败")
        if response.status_code == 403:
            raise XHSRequestError("permission_denied", "小红书访问被拒绝")
        if response.status_code == 429:
            raise XHSRequestError("rate_limited", "小红书请求频率受限")
        if 400 <= response.status_code < 600:
            raise XHSRequestError(
                "http_error", "小红书详情 HTTP 请求失败",
                retryable=response.status_code >= 500,
                http_status=response.status_code,
            )
        response.raise_for_status()
        res_json = response.json()

        if not res_json.get("success"):
            code = res_json.get("code", "")
            msg = res_json.get("msg", "")
            if code == 300011 or "异常" in msg:
                raise XHSCookieExpiredError(
                    f"小红书 Cookie 已被风控拦截 (code={code}): {msg}"
                )
            raise XHSRequestError("detail_unavailable", f"小红书详情失败 (code={code}): {msg}")

        return res_json


# ============ 客户端工厂 ============

def get_xhs_client() -> XhsNativeClient:
    """初始化并返回原生小红书客户端"""
    settings = get_settings()
    if not settings.xhs_cookie:
        raise XHSCookieExpiredError("XHS_COOKIE 未在后端环境中配置")
    cookie_str = normalize_xhs_cookie(settings.xhs_cookie)
    if not cookie_str:
        raise XHSCookieExpiredError("XHS_COOKIE 未在后端环境中配置")
    return XhsNativeClient(cookie_str)


# ============ 高德地理编码 ============

def _geocode_amap_raw(address: str, city: str) -> Optional[dict]:
    """纯高德 Web 服务地理编码（供 map_dispatcher 降级调用）。

    返回: {"longitude": float, "latitude": float}
    """
    from .amap_service import get_amap_service

    result = get_amap_service().resolve_place(address, city, prefer_poi=True)
    if not result.data_available or result.location is None:
        print(f"⚠️ [AMAP_UNAVAILABLE] geocode reason={result.reason}: {address}")
        return None
    return result.location.model_dump()


def geocode_amap(address: str, city: str, *, name_zh: str = "", name_en: str = "") -> Optional[dict]:
    """统一地理编码入口 — 自动路由到 Google / 高德。

    内部通过 map_dispatcher 判断当前活跃供应商，
    并根据供应商自动选择最合适语言的地址进行编码：
    - Google Maps: 优先使用英文名称 (name_en)
    - 高德地图: 优先使用中文名称 (name_zh)
    """
    from .map_dispatcher import geocode_unified
    return geocode_unified(address, city, address_zh=name_zh, address_en=name_en)


# ============ SSR 降级方案（备用） ============

def get_note_detail_ssr(note_id: str) -> dict:
    """通过网页抓取 SSR 状态提取笔记详情，作为原生 API 的降级备选"""
    url = f"https://www.xiaohongshu.com/explore/{note_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    with timed_stage("xhs_stage_timing", "research_ssr") as timing:
        try:
            resp = httpx.get(url, headers=headers, timeout=8)
            match = re.search(r'window\.__INITIAL_STATE__=({.*?})</script>', resp.text)
            if match:
                state_json = json.loads(match.group(1).replace('undefined', 'null'))
                return state_json.get("note", {}).get("noteDetailMap", {}).get(note_id, {}).get("note", {})
        except Exception as e:
            timing.mark_failed()
            print(f"SSR详情提取失败 {note_id}: {e}")
    return {}


def _normalize_evidence_text(value: Any) -> str:
    """Normalize only superficial formatting; never guess aliases or entities."""
    return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)


def _research_note(
    client: XhsNativeClient,
    indexed_note: tuple[int, dict],
) -> tuple[Optional[XHSEvidence], str, str]:
    """Process one selected note while preserving its existing detail fallback."""
    index, note = indexed_note
    note_card = note.get("note_card", {})
    title = note_card.get("display_title", "")
    note_id = note.get("id", "")
    source_url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""

    desc = ""
    try:
        xsec_token = note.get("xsec_token", "")
        if note_id:
            with timed_stage("xhs_stage_timing", "research_detail"):
                detail_res = client.get_note_detail(note_id, xsec_token)
            detail_items = detail_res.get("data", {}).get("items", [])
            if detail_items:
                note_data = detail_items[0].get("note_card", {})
                desc = note_data.get("desc", "")
    except Exception:
        try:
            if note_id:
                detail = get_note_detail_ssr(note_id)
                desc = detail.get("desc", "")
        except Exception:
            desc = ""

    evidence = None
    source_text = ""
    if note_id:
        source_text = f"{title}\n{desc}"
        evidence = XHSEvidence(
            note_id=note_id,
            title=title,
            source_url=source_url,
            status="detail_available" if desc else "detail_unavailable",
            extracted_text=desc[:500] if desc else "",
        )

    combined = ""
    if title or desc:
        extraction_desc = desc[:_XHS_EXTRACTION_NOTE_CHARS]
        combined = (
            f"\n笔记{index + 1}:\nnote_id: {note_id}\n标题: {title}"
            f"\n正文内容: {extraction_desc}\n来源: {source_url}\n"
        )
    return evidence, source_text, combined


def _validate_xhs_extracted_items(
    extracted: Any,
    evidence: List[XHSEvidence],
    evidence_source_text: Dict[str, str],
) -> List[dict]:
    """Validate IDs and verbatim support excerpts without claiming semantic entailment."""
    if not isinstance(extracted, list):
        return []
    evidence_lookup = {item.note_id: item for item in evidence}
    validated: List[dict] = []
    for item in extracted:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        identity_text = str(item.get("identity_text") or "").strip()
        recommendation = str(item.get("recommendation") or item.get("reason") or "").strip()
        raw_ids = item.get("evidence_ids")
        raw_support = item.get("evidence_support")
        if (
            not name or not identity_text or not recommendation
            or not isinstance(raw_ids, list) or not raw_ids
            or not isinstance(raw_support, list) or not raw_support
            or any(term in recommendation for term in _FABRICATED_CONSENSUS_TERMS)
        ):
            continue
        # ``name`` is the evidence-native identity. Translations belong only in
        # name_zh/name_en, so no language-specific alias table is required.
        if _normalize_evidence_text(name) != _normalize_evidence_text(identity_text):
            continue

        requested_ids = list(dict.fromkeys(str(note_id) for note_id in raw_ids))
        valid_support: List[dict] = []
        for support in raw_support:
            if not isinstance(support, dict):
                continue
            evidence_id = str(support.get("evidence_id") or "")
            identity_quote = str(support.get("identity_quote") or "").strip()
            recommendation_quote = str(support.get("recommendation_quote") or "").strip()
            source = evidence_source_text.get(evidence_id, "")
            normalized_source = _normalize_evidence_text(source)
            if (
                evidence_id not in evidence_lookup
                or evidence_id not in requested_ids
                or not identity_quote or not recommendation_quote
                or _normalize_evidence_text(identity_quote) not in normalized_source
                or _normalize_evidence_text(recommendation_quote) not in normalized_source
                or _normalize_evidence_text(identity_text) not in _normalize_evidence_text(identity_quote)
            ):
                continue
            valid_support.append({
                "evidence_id": evidence_id,
                "identity_quote": identity_quote,
                "recommendation_quote": recommendation_quote,
            })

        valid_ids = list(dict.fromkeys(item["evidence_id"] for item in valid_support))
        if not valid_ids:
            continue
        validated.append({
            **item,
            "name": name,
            "identity_text": identity_text,
            "recommendation": recommendation,
            "evidence_ids": valid_ids,
            "evidence_support": valid_support,
        })
    return validated


# ============ 景点搜索核心函数 ============

def search_xhs_attractions(city: str, keywords: str, language: str = "zh") -> XHSResearchResult:
    """
    搜索小红书笔记，使用大模型极速提纯出结构化景点，
    并静默拼装经纬度和真实图片，回传给Planner。

    Args:
        city: 城市名称
        keywords: 搜索关键词
        language: 目标输出语言 (zh/en/ja 等)
    """
    print(f"🔍 [XHS_SERVICE] 正在呼叫小红书 API 搜索: {city} {keywords}")
    client = get_xhs_client()
    query = f"{city} {keywords} 旅游 景点攻略"

    try:
        # 使用原生签名客户端搜索
        with timed_stage("xhs_stage_timing", "research_search"):
            res_json = client.search_notes(keyword=query)
        items = res_json.get("data", {}).get("items", [])[:4]

        combined_text = ""
        evidence: List[XHSEvidence] = []
        evidence_source_text: Dict[str, str] = {}
        selected_notes = [
            (index, note)
            for index, note in enumerate(items)
            if note.get("model_type") == "note"
        ]
        if selected_notes:
            # executor.map preserves input order even when detail calls finish
            # out of order. Extraction therefore sees the same evidence order.
            with ThreadPoolExecutor(
                max_workers=min(2, len(selected_notes)),
                thread_name_prefix="xhs-research-detail",
            ) as executor:
                note_results = executor.map(
                    lambda indexed_note: _research_note(client, indexed_note),
                    selected_notes,
                )
                for note_evidence, source_text, combined in note_results:
                    if note_evidence is not None:
                        evidence_source_text[note_evidence.note_id] = source_text
                        evidence.append(note_evidence)
                    combined_text += combined

    except XHSCookieExpiredError:
        raise
    except XHSRequestError:
        raise
    except requests.Timeout as e:
        raise XHSRequestError("timeout", "小红书搜索超时") from e
    except requests.ConnectionError as e:
        raise XHSRequestError("network_error", "小红书网络连接失败") from e
    except (json.JSONDecodeError, ValueError) as e:
        raise XHSRequestError("malformed_response", "小红书响应无法解析") from e
    except Exception as e:
        print(f"❌ 小红书接口抓取崩盘: {e}")
        raise XHSRequestError("request_failed", "小红书请求失败，已降级继续规划") from e

    if not combined_text or not evidence:
        return XHSResearchResult(
            status="unavailable", verification_status="unavailable", degraded=True,
            reason="empty_search", evidence=[], context="",
        )
    if not any(item.status == "detail_available" for item in evidence):
        return XHSResearchResult(
            status="unavailable", verification_status="unavailable", degraded=True,
            reason="detail_unavailable", evidence=evidence, context="",
        )

    # ======== 轻量级提取过程 ========
    print(f"🧠 [XHS_SERVICE] 正在调用内联模型提纯小红书游记参数...")
    llm = get_llm()

    # 根据目标语言构建翻译附加指令
    _lang = (language or "zh").strip().lower().split("-")[0]
    _lang_names = {"en": "English", "ja": "Japanese", "ko": "Korean", "fr": "French", "de": "German", "es": "Spanish"}
    if _lang != "zh" and _lang in _lang_names:
        translation_instruction = f"""
**极其重要的翻译要求:**
目标语言为 {_lang_names[_lang]}。
- "name" 和 "identity_text" 必须保留证据原文中的名称，不得翻译；译名仅写入 name_zh/name_en。
- **注意**: "name_zh" 必须始终保持简体中文名称，"name_en" 必须始终保持英文名称，不受目标语言影响！
- 严格保持 JSON schema 格式不变！
"""
    else:
        translation_instruction = ""

    extract_prompt = f"""
请从以下真实的素人小红书打卡游记中，提纯出真实存在的【游玩景点】。
要求返回严格的 JSON 数组格式(哪怕只提取到了1个)，切勿返回除了JSON以外的任何冗余 markdown 文字！
{translation_instruction}
数组中每个对象必须包含以下字段:
"name": 必须逐字使用对应笔记中出现的景点名称，不得翻译、扩写或具体化
"identity_text": 与 name 完全相同的证据原始名称
"name_zh": 景点的中文简体名称(必须是简体中文，例如 "故宫博物院"。此字段始终为中文，不受目标语言影响)
"name_en": 景点的英文名称(必须是英文，使用景点在国际上通用的官方英文名，例如 "The Palace Museum"。此字段始终为英文，不受目标语言影响)
"evidence_ids": 支持该景点和评价引文的 note_id 数组，至少包含一个值
"evidence_support": 数组；每个 evidence_id 对应一个对象，包含 "evidence_id"、"identity_quote"、"recommendation_quote"。两个 quote 都必须是该笔记正文中的简短逐字片段

**证据约束（必须遵守）:**
- evidence_ids 只能从上方输入笔记明确提供的 note_id 中选择，不得生成或改写 note_id。
- attraction identity 必须由对应笔记明确支持。不得把模糊/umbrella 名称具体化成某个分店、园区、场馆或变体。
- evidence 只写泛称时，name 和 identity_text 必须保持同样粒度；无法明确确认 identity 时不要输出。
- 每条评价信息必须直接放在 recommendation_quote 中，并由对应 evidence 支持。
- recommendation_quote 必须是原文逐字片段，不得加入新的天气、楼层、路线、时间或规划推断。最终服务会以验证后的 recommendation_quote 重建 evidence summary。
- 每个 evidence_id 都必须有对应 evidence_support，并同时提供支持 identity 和 recommendation 的原文短片段。
- 不得为了增加可信度附加不相关 evidence；不得因为多篇笔记都提到同一目的地，就把全部 note_id 挂到所有 item。
- 无法找到对应 note_id 的候选不得输出。不要输出 unsupported item。
- 不得使用“大家都推荐”“小红书普遍认为”“热门必去”等共识或热度措辞。

**地理编码辅助字段说明:**
name_zh 和 name_en 将分别用于不同地图服务商(高德/Google)的地理定位，请务必准确填写！
- name_zh 必须是中文简体名称
- name_en 必须是英文名称，优先使用国际通用的官方英文名

游记杂文内容如下:
{combined_text}

"""
    try:
        from .llm_service import create_chat_completion
        with timed_stage("xhs_stage_timing", "research_llm_extraction"):
            response = create_chat_completion(
                stage="xhs_research",
                model=llm.model,
                messages=[{"role": "user", "content": extract_prompt}],
                llm_instance=llm,
                temperature=0.1,
                max_tokens=_XHS_EXTRACTION_MAX_TOKENS,
                stage_max_token_exposure=_XHS_EXTRACTION_MAX_TOKENS,
            )
        output_metadata = structured_output_metadata(
            response, _XHS_EXTRACTION_MAX_TOKENS,
        )
        if output_metadata["finish_reason"] == "length":
            log_structured_output_event(
                stage="xhs_extraction", category="output_limit_reached",
                metadata=output_metadata, success=False,
            )
            return XHSResearchResult(
                status="unavailable", verification_status="unavailable", degraded=True,
                reason="output_truncated", evidence=evidence, context="",
            )
        content = response.choices[0].message.content

        try:
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                extracted = json.loads(json_match.group())
            else:
                extracted = json.loads(content)
        except json.JSONDecodeError:
            log_structured_output_event(
                stage="xhs_extraction", category="json_decode_failed",
                metadata=output_metadata, success=False,
            )
            return XHSResearchResult(
                status="unavailable", verification_status="unavailable", degraded=True,
                reason="extraction_failed", evidence=evidence, context="",
            )

        evidence_lookup = {item.note_id: item for item in evidence}
        supported_items: List[XHSExtractedItem] = []
        extraction_candidates = []
        if isinstance(extracted, list):
            for raw_item in extracted:
                if not isinstance(raw_item, dict):
                    extraction_candidates.append(raw_item)
                    continue
                candidate = dict(raw_item)
                raw_support = candidate.get("evidence_support")
                if isinstance(raw_support, list) and not str(
                    candidate.get("recommendation") or candidate.get("reason") or ""
                ).strip():
                    candidate["recommendation"] = "；".join(
                        str(support.get("recommendation_quote") or "").strip()
                        for support in raw_support
                        if isinstance(support, dict)
                        and str(support.get("recommendation_quote") or "").strip()
                    )
                extraction_candidates.append(candidate)
        else:
            extraction_candidates = extracted
        validated_items = _validate_xhs_extracted_items(
            extraction_candidates, evidence, evidence_source_text,
        )
        for item in validated_items:
            name = item.get("name", "")
            valid_ids = item["evidence_ids"]
            # XHS Research owns only evidence-native facts. Never forward the
            # model's free-form recommendation: rebuild it deterministically
            # from source-verified excerpts. Planner inference remains a
            # separate, non-XHS provenance domain.
            evidence_summary = "；".join(dict.fromkeys(
                support["recommendation_quote"].strip()
                for support in item["evidence_support"]
                if support["recommendation_quote"].strip()
            ))
            if not evidence_summary or any(
                term in evidence_summary for term in _FABRICATED_CONSENSUS_TERMS
            ):
                continue
            # 获取中英文名称，用于精准地理编码（Google用英文，高德用中文）
            name_zh = item.get("name_zh", name)
            name_en = item.get("name_en", name)
            with timed_stage("xhs_stage_timing", "research_geocoding"):
                loc = geocode_amap(name, city, name_zh=name_zh, name_en=name_en)
            supported_items.append(XHSExtractedItem(
                name=name,
                identity_text=item["identity_text"],
                name_zh=name_zh,
                name_en=name_en,
                evidence_summary=evidence_summary,
                recommendation=evidence_summary,
                # These are planning interpretations unless separately
                # grounded. Keep them neutral; the exact source tip remains in
                # evidence_summary for Planner to reason about under its own provenance.
                duration=None,
                reservation_required=None,
                reservation_tips="",
                evidence_ids=valid_ids,
                evidence_support=[XHSEvidenceSupport(**support) for support in item["evidence_support"]],
                location=loc,
                location_status="available" if loc else "unavailable",
            ))

        if not supported_items:
            return XHSResearchResult(
                status="unavailable", verification_status="unavailable", degraded=True,
                reason="unsupported_extraction", evidence=evidence,
                extracted_items=[], context="",
            )

        final_result = "以下候选由指定的小红书笔记证据支持；坐标仅在地图服务成功时提供：\n"
        for item in supported_items:
            final_result += json.dumps(item.model_dump(), ensure_ascii=False) + "\n"
            sources = [evidence_lookup[note_id] for note_id in item.evidence_ids]
            final_result += "XHS evidence: " + "; ".join(
                f"{source.note_id} ({source.source_url})" for source in sources
            ) + "\n"

        print(f"✅ [XHS_SERVICE] 小红书数据挖掘完毕，已装载进上下文。")
        usable_evidence = [item for item in evidence if item.status != "detail_unavailable"]
        return XHSResearchResult(
            status="available" if usable_evidence else "degraded",
            verification_status="verified" if usable_evidence else "partial",
            degraded=not bool(usable_evidence),
            reason=None if usable_evidence else "detail_unavailable",
            evidence=evidence, extracted_items=supported_items,
            context=final_result,
        )

    except Exception:
        return XHSResearchResult(
            status="unavailable", verification_status="unavailable", degraded=True,
            reason="extraction_failed", evidence=evidence, context="",
        )


# ============ 景点搜图 ============

def _photo_error_category(exc: Exception) -> str:
    if isinstance(exc, XHSCookieExpiredError):
        return "risk_control"
    if isinstance(exc, XHSRequestError):
        return exc.reason if exc.reason in _XHS_RETRYABLE else "unexpected_error"
    if isinstance(exc, (requests.Timeout, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, (requests.ConnectionError, httpx.NetworkError)):
        return "network_error"
    if isinstance(exc, requests.RequestException):
        return "request_error"
    if isinstance(exc, (json.JSONDecodeError, ValueError, TypeError, KeyError, AttributeError)):
        return "malformed_response"
    return "unexpected_error"


def _photo_error_stage(category: str, default_stage: str) -> str:
    if category == "sign_error":
        return "sign"
    if category == "malformed_response":
        return "parse"
    return default_stage


def _require_mapping(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("malformed provider mapping")
    return value


def _require_list(value: Any) -> List[Any]:
    if not isinstance(value, list):
        raise ValueError("malformed provider list")
    return value


def _positive_dimension(value: Any) -> Optional[int]:
    """Accept only finite positive integer-like provider dimensions."""
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _image_dimensions(image: Dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    return _positive_dimension(image.get("width")), _positive_dimension(image.get("height"))


def _detail_image_url(image: Dict[str, Any]) -> str:
    info_list = _require_list(image.get("info_list", []))
    if len(info_list) > 1:
        url = _require_mapping(info_list[1]).get("url", "")
        if url:
            return url
    if info_list:
        url = _require_mapping(info_list[0]).get("url", "")
        if url:
            return url
    return image.get("url_default", "") or image.get("url_pre", "") or image.get("url", "")


def _ssr_image_url(image: Dict[str, Any]) -> str:
    return image.get("urlDefault", "") or image.get("urlPattern", "") or image.get("url", "")


def _select_cover_image(images: List[Any], url_getter) -> str:
    """Prefer a non-tall cover, then safely fall back to a usable portrait.

    Unknown-dimension and portrait candidates preserve Provider order. Orientation
    is only a ranking signal; a usable URL is never rejected solely for portrait
    dimensions.
    """
    return _select_cover_image_outcome(images, url_getter)[0]


def _select_cover_image_outcome(images: List[Any], url_getter) -> tuple[str, bool]:
    """Return ``(url, used_portrait_fallback)`` without inspecting image content."""
    balanced: List[str] = []
    unknown: List[str] = []
    portrait: List[str] = []
    for raw_image in images:
        image = _require_mapping(raw_image)
        url = url_getter(image)
        if not url:
            continue
        width, height = _image_dimensions(image)
        if width is None or height is None:
            unknown.append(url)
        elif width / height >= 0.8:
            balanced.append(url)
        else:
            portrait.append(url)
    if balanced:
        return balanced[0], False
    if unknown:
        return unknown[0], False
    if portrait:
        return portrait[0], True
    return "", False


def _cover_image_empty_category(images: List[Any], url_getter, *, stage: str) -> str:
    """Classify an already-empty cover selection without exposing image data."""
    usable_url_seen = False
    for raw_image in images:
        image = _require_mapping(raw_image)
        if url_getter(image):
            usable_url_seen = True
            break
    suffix = "image_rejected" if usable_url_seen else "url_missing"
    return f"xhs_{stage}_{suffix}"


_XHS_IMAGE_TERMINAL_CATEGORIES = {
    "xhs_search_empty",
    "xhs_no_eligible_note",
    "xhs_detail_url_missing",
    "xhs_detail_image_rejected",
    "xhs_detail_portrait_fallback",
    "xhs_ssr_state_missing",
    "xhs_ssr_image_empty",
    "xhs_ssr_url_missing",
    "xhs_ssr_image_rejected",
    "xhs_ssr_portrait_fallback",
    "xhs_ssr_portrait_fallback_after_detail_failed",
    "xhs_ssr_empty_after_detail_failed",
    "xhs_ssr_success_after_detail_failed",
}


def _log_xhs_image_event(
    *, request_id: str, stage: str, category: str, retryable: bool = False,
) -> None:
    """Emit bounded image-chain state without request or Provider content."""
    safe_request_id = request_id if re.fullmatch(r"[0-9a-f]{12}", request_id or "") else "unknown"
    safe_stage = stage if stage in {"search", "detail", "ssr"} else "ssr"
    safe_category = (
        category if category in _XHS_IMAGE_TERMINAL_CATEGORIES
        else "xhs_ssr_state_missing"
    )
    print(
        "XHS_IMAGE_EVENT "
        f"request_id={safe_request_id} stage={safe_stage} "
        f"category={safe_category} retryable={str(retryable).lower()}",
        flush=True,
    )


def _get_xhs_photo_sync_unbounded(keyword: str, *, request_id: str = "") -> str:
    """根据关键词从小红书搜索一张首图URL

    使用原生签名客户端搜索最新帖子，然后通过原生 API 或 SSR 抓取首张图片。
    """
    try:
        try:
            client = get_xhs_client()
        except Exception as exc:
            category = "missing_config" if isinstance(exc, XHSCookieExpiredError) else _photo_error_category(exc)
            _log_xhs_event(request_id=request_id, stage="configuration", category=category)
            raise

        # 搜图时强制按"最新"排序，避开综合高赞的含文字攻略图
        try:
            with timed_stage("xhs_stage_timing", "image_search"):
                res_json = client.search_notes(keyword=keyword, sort_type=0)
        except Exception as exc:
            category = _photo_error_category(exc)
            stage = _photo_error_stage(category, "search")
            retryable_override = (
                exc.retryable if isinstance(exc, XHSRequestError) else None
            )
            business_code = (
                exc.business_code if isinstance(exc, XHSRequestError) else None
            )
            http_status = (
                exc.http_status if isinstance(exc, XHSRequestError) else None
            )
            _log_xhs_event(
                request_id=request_id, stage=stage, category=category,
                retryable_override=retryable_override,
                business_code=business_code,
                http_status=http_status,
            )
            raise
        try:
            data = _require_mapping(_require_mapping(res_json).get("data", {}))
            items = _require_list(data.get("items", []))
        except Exception:
            _log_xhs_event(request_id=request_id, stage="parse", category="malformed_response")
            raise

        if not items:
            _log_xhs_event(request_id=request_id, stage="search", category="no_result")
            _log_xhs_image_event(
                request_id=request_id, stage="search", category="xhs_search_empty",
            )
            return ""

        target_note_id = None
        target_xsec_token = ""
        for note in items:
            if not isinstance(note, dict):
                _log_xhs_event(request_id=request_id, stage="parse", category="malformed_response")
                raise ValueError("malformed note item")
            if note.get("model_type") == "note":
                target_note_id = note.get("id")
                target_xsec_token = note.get("xsec_token", "")
                break

        if not target_note_id:
            _log_xhs_event(request_id=request_id, stage="search", category="no_result")
            _log_xhs_image_event(
                request_id=request_id, stage="search", category="xhs_no_eligible_note",
            )
            return ""

        # 方案 A: 通过原生 API 获取笔记详情和图片
        detail_failed = False
        try:
            with timed_stage("xhs_stage_timing", "image_detail"):
                detail_res = client.get_note_detail(target_note_id, target_xsec_token)
                detail_data = _require_mapping(_require_mapping(detail_res).get("data", {}))
                detail_items = _require_list(detail_data.get("items", []))
                if detail_items:
                    note_card = _require_mapping(_require_mapping(detail_items[0]).get("note_card", {}))
                    image_list = _require_list(note_card.get("image_list", []))
                    if image_list:
                        photo_url, portrait_fallback = _select_cover_image_outcome(
                            image_list, _detail_image_url,
                        )
                        if photo_url:
                            if portrait_fallback:
                                _log_xhs_image_event(
                                    request_id=request_id, stage="detail",
                                    category="xhs_detail_portrait_fallback",
                                )
                            return photo_url
                        _log_xhs_event(request_id=request_id, stage="detail", category="no_result")
                        _log_xhs_image_event(
                            request_id=request_id,
                            stage="detail",
                            category=_cover_image_empty_category(
                                image_list, _detail_image_url, stage="detail",
                            ),
                        )
                        return ""
        except Exception as exc:
            detail_failed = True
            category = _photo_error_category(exc)
            _log_xhs_event(
                request_id=request_id,
                stage=_photo_error_stage(category, "detail"),
                category=category,
                retryable_override=(
                    exc.retryable if isinstance(exc, XHSRequestError) else None
                ),
                business_code=(
                    exc.business_code if isinstance(exc, XHSRequestError) else None
                ),
                http_status=(
                    exc.http_status if isinstance(exc, XHSRequestError) else None
                ),
            )

        # 方案 B: 降级到 SSR 抓取
        url = f"https://www.xiaohongshu.com/explore/{target_note_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            with timed_stage("xhs_stage_timing", "image_ssr"):
                resp = httpx.get(url, headers=headers, timeout=10)
                match = re.search(r'window\.__INITIAL_STATE__=({.*?})</script>', resp.text)
                if match:
                    state_json_str = match.group(1).replace("undefined", "null")
                    state_json = _require_mapping(json.loads(state_json_str))
                    note = _require_mapping(state_json.get("note", {}))
                    detail_map = _require_mapping(note.get("noteDetailMap", {}))
                    note_entry = _require_mapping(detail_map.get(target_note_id, {}))
                    note_data = _require_mapping(note_entry.get("note", {}))
                    img_list = _require_list(note_data.get("imageList", []))
                    if img_list:
                        photo_url, portrait_fallback = _select_cover_image_outcome(
                            img_list, _ssr_image_url,
                        )
                        if photo_url:
                            if portrait_fallback:
                                category = (
                                    "xhs_ssr_portrait_fallback_after_detail_failed"
                                    if detail_failed else "xhs_ssr_portrait_fallback"
                                )
                                _log_xhs_image_event(
                                    request_id=request_id, stage="ssr", category=category,
                                )
                            elif detail_failed:
                                _log_xhs_image_event(
                                    request_id=request_id, stage="ssr",
                                    category="xhs_ssr_success_after_detail_failed",
                                )
                            return photo_url
                        terminal_category = _cover_image_empty_category(
                            img_list, _ssr_image_url, stage="ssr",
                        )
                    else:
                        terminal_category = "xhs_ssr_image_empty"
                else:
                    terminal_category = "xhs_ssr_state_missing"
        except Exception as exc:
            category = _photo_error_category(exc)
            _log_xhs_event(
                request_id=request_id,
                stage=_photo_error_stage(category, "ssr"), category=category,
            )
            raise

    except Exception:
        # The route owns stable failure classification. Never log the keyword,
        # cookie-related detail, or a raw provider response here.
        raise
    if detail_failed:
        terminal_category = "xhs_ssr_empty_after_detail_failed"
    _log_xhs_image_event(
        request_id=request_id, stage="ssr", category=terminal_category,
    )
    _log_xhs_event(request_id=request_id, stage="ssr", category="no_result")
    return ""


def get_xhs_photo_sync(keyword: str, *, request_id: str = "") -> str:
    """Run one complete XHS image fallback under a process-local permit.

    This is deliberately not a distributed/global Provider limiter. The
    production deployment currently uses one worker; every process owns its
    own bounded pair of permits.
    """
    with _XHS_IMAGE_FALLBACK_SEMAPHORE:
        return _get_xhs_photo_sync_unbounded(keyword, request_id=request_id)


async def get_photo_from_xhs(keyword: str, *, request_id: str = "") -> str:
    """供异步环境调用的小红书图片搜索API"""
    import asyncio
    return await asyncio.to_thread(get_xhs_photo_sync, keyword, request_id=request_id)
