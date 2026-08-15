"""
AI 行程问答服务
复用项目统一的 OpenAI-compatible Chat Completions 调用链，
将当前旅行计划作为上下文注入,实现针对行程的智能问答
"""

import asyncio
import json
from typing import List, Optional, Dict, Any
from ..config import get_settings
from .llm_service import _error_category, create_chat_completion, get_llm

# ============ System Prompt ============
SYSTEM_PROMPT = """你是一个专业且贴心的私人旅行管家「TripStar-AI」。

你当前正在为用户提供关于一份 **已生成的旅行计划** 的答疑服务。
用户可能会问你关于行程中的景点、酒店、餐饮、天气、交通、门票、费用等任何细节问题。

请根据下方提供的【当前旅行计划】JSON 上下文来回答用户的问题。
回答规则：
1. 如果行程数据中包含相关信息，请精确引用并给出详细回答。
2. 如果行程数据中没有明确信息，可以基于常识进行合理推断，但需说明"行程中未提供该信息，以下是建议"。
3. 回答要有温度、条理清晰，适当使用 emoji 增加亲切感 🌟。
4. 回答尽量简洁，控制在200字以内，除非用户要求详细展开。
5. 使用中文回答。"""


def _build_context_message(trip_plan_dict: Dict[str, Any]) -> str:
    """将旅行计划转化为上下文文本"""
    return f"【当前旅行计划】\n```json\n{json.dumps(trip_plan_dict, ensure_ascii=False, indent=2)}\n```"


async def chat_with_trip_context(
    message: str,
    trip_plan_dict: Dict[str, Any],
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    使用 LLM 回答关于当前行程的用户提问

    Args:
        message: 用户的提问
        trip_plan_dict: 当前旅行计划 (dict 格式)
        history: 可选的历史对话 [{"role": "user"/"assistant", "content": "..."}]

    Returns:
        AI 的回复文本
    """
    # 构造消息列表
    settings = get_settings()
    if not settings.openai_api_key:
        return "抱歉，AI 服务尚未配置 API Key，请先在设置页面中完成配置。"
    llm = get_llm()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_context_message(trip_plan_dict)},
    ]

    # 追加历史对话
    if history:
        for item in history:
            messages.append({
                "role": item.get("role", "user"),
                "content": item.get("content", ""),
            })

    # 追加本次用户提问
    messages.append({"role": "user", "content": message})

    try:
        response = await asyncio.to_thread(
            create_chat_completion,
            stage="chat",
            model=llm.model,
            messages=messages,
            llm_instance=llm,
            temperature=0.7,
            max_tokens=1024,
            stage_max_token_exposure=1024,
        )
        reply = response.choices[0].message.content
        return reply.strip()
    except Exception as exc:
        category, retryable = _error_category(exc)
        status = getattr(exc, "status_code", None)
        status_text = str(status) if isinstance(status, int) else "none"
        print(
            "❌ provider=llm endpoint=chat_completions "
            f"category={category} status={status_text} "
            f"retryable={str(retryable).lower()}"
        )
        if category == "transient_network":
            return "抱歉，AI 回复超时了，请稍后再试 ⏳"
        if isinstance(status, int):
            return f"抱歉，AI 服务暂时出现问题 (HTTP {status})，请稍后重试 🙏"
        return "抱歉，AI 出现了意外错误，请稍后重试 🙏"
