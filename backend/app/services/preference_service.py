"""Phase 1 用户旅行偏好解析服务。"""

import json
import re
from typing import Any, Dict, List

from ..models.schemas import (
    PreferenceConstraints,
    PreferenceParseRequest,
    PreferenceProfile,
)


ALLOWED_INTERESTS = [
    "历史文化", "自然风光", "美食", "购物", "艺术", "休闲",
    "拍照", "博物馆", "城市探索", "夜生活", "小众景点",
]


def _get_llm():
    """延迟导入，避免 schema/纯合并测试依赖 Agent 运行时。"""
    from .llm_service import get_llm
    return get_llm()


def _create_preference_completion(llm, prompt: str):
    from .llm_service import create_chat_completion
    return create_chat_completion(
        stage="preference",
        model=llm.model,
        messages=[{"role": "user", "content": prompt}],
        llm_instance=llm,
        temperature=0.0,
    )


def _unique_text(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    result: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _extract_json_object(content: str) -> Dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        text = match.group(0)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("偏好解析结果必须是 JSON 对象")
    return data


def _fallback_profile(request: PreferenceParseRequest, note: str = "") -> PreferenceProfile:
    notes = [note] if note else []
    return PreferenceProfile(
        party_type=request.party_type,
        party_size=request.party_size,
        budget_cny=request.budget_cny,
        pace=request.pace,
        interests=_unique_text(request.interests),
        special_requirements=request.special_requirements.strip(),
        constraints=PreferenceConstraints(),
        parsing_notes=notes,
    )


def _merge_inference(
    request: PreferenceParseRequest,
    inference: Dict[str, Any],
) -> PreferenceProfile:
    explicit_interests = _unique_text(request.interests)
    inferred_interests = [
        item for item in _unique_text(inference.get("inferred_interests"))
        if item in ALLOWED_INTERESTS and item not in explicit_interests
    ]

    avoid_early_start = inference.get("avoid_early_start") is True
    earliest_start_time = inference.get("earliest_start_time")
    if not isinstance(earliest_start_time, str) or not re.fullmatch(
        r"([01]\d|2[0-3]):[0-5]\d", earliest_start_time.strip()
    ):
        earliest_start_time = None
    else:
        earliest_start_time = earliest_start_time.strip()

    parsing_notes = _unique_text(inference.get("parsing_notes"))
    if avoid_early_start and earliest_start_time is None:
        prompt = "你提到不想早起，请选择一个具体的最早出发时间。"
        if prompt not in parsing_notes:
            parsing_notes.append(prompt)

    if request.pace == "relaxed":
        intensive_phrases = ("尽量多", "多去几个", "越多越好", "特种兵")
        if any(phrase in request.special_requirements for phrase in intensive_phrases):
            conflict_note = "特殊要求可能与已选择的松弛节奏冲突，将以你显式选择的松弛节奏为准。"
            if conflict_note not in parsing_notes:
                parsing_notes.append(conflict_note)

    constraints = PreferenceConstraints(
        avoid_early_start=avoid_early_start,
        earliest_start_time=earliest_start_time,
        mobility_notes=_unique_text(inference.get("mobility_notes")),
        food_notes=_unique_text(inference.get("food_notes")),
        other_notes=_unique_text(inference.get("other_notes")),
    )
    return PreferenceProfile(
        party_type=request.party_type,
        party_size=request.party_size,
        budget_cny=request.budget_cny,
        pace=request.pace,
        interests=explicit_interests,
        special_requirements=request.special_requirements.strip(),
        constraints=constraints,
        inferred_interests=inferred_interests,
        parsing_notes=parsing_notes,
    )


def parse_preference_profile(request: PreferenceParseRequest) -> tuple[PreferenceProfile, bool, str]:
    """解析自由文本。失败时返回显式字段构成的安全 Profile，不阻塞规划。"""
    special_requirements = request.special_requirements.strip()
    if not special_requirements:
        return _fallback_profile(request), False, "未填写特殊要求，已整理显式偏好。"

    prompt = f"""你是旅行需求结构化助手。只从用户的特殊要求中提取旅行约束，返回严格 JSON 对象。

用户已经明确选择的字段如下，仅用于判断冲突，绝对不能覆盖或改写：
- 同行类型: {request.party_type}
- 人数: {request.party_size}
- 目的地旅行期间当地消费总预算（不包含往返目的地的大交通）: {request.budget_cny if request.budget_cny is not None else '未设置'}
- 旅行节奏: {request.pace}
- 显式兴趣: {json.dumps(request.interests, ensure_ascii=False)}

特殊要求原文:
{special_requirements}

只输出以下 schema：
{{
  "avoid_early_start": false,
  "earliest_start_time": null,
  "mobility_notes": [],
  "food_notes": [],
  "other_notes": [],
  "inferred_interests": [],
  "parsing_notes": []
}}

规则：
1. 用户只说“不想早起”但没有具体时间时，avoid_early_start=true，earliest_start_time=null；禁止擅自填写时间。
2. 只有用户明确说出具体时间时，earliest_start_time 才填写 HH:MM。
3. 不做医学诊断，只提取对旅行安排有影响的行动需求。
4. inferred_interests 只能取自: {json.dumps(ALLOWED_INTERESTS, ensure_ascii=False)}。
5. 不扩写用户没有表达的限制；不确定时写入 parsing_notes。
6. 只输出 JSON，不要 Markdown 或解释文字。
"""
    try:
        llm = _get_llm()
        response = _create_preference_completion(llm, prompt)
        content = response.choices[0].message.content or ""
        inference = _extract_json_object(content)
        return _merge_inference(request, inference), True, "AI 已整理特殊要求，请确认。"
    except Exception as exc:
        print(f"⚠️ Preference Parser 失败，使用显式偏好继续: {exc}")
        profile = _fallback_profile(
            request,
            "AI 未能结构化解析特殊要求，已保留原文；你仍可继续生成行程。",
        )
        return profile, False, "偏好解析失败，已使用显式字段和原始要求。"
