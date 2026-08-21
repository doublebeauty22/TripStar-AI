"""LLM service, per-execution cost guardrails, and safe retry policy."""

import contextvars
import threading
import time
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from hello_agents import HelloAgentsLLM
from ..config import get_settings

# 全局LLM实例
_llm_instance = None
MAX_LLM_CALLS_PER_TRIP = int(os.getenv("MAX_LLM_CALLS_PER_TRIP", "5"))
MAX_TRANSIENT_LLM_RETRIES = min(
    1, max(0, int(os.getenv("MAX_TRANSIENT_LLM_RETRIES", "1")))
)
GENERATION_USAGE_TTL_SECONDS = max(
    60, int(os.getenv("GENERATION_USAGE_TTL_SECONDS", "3600"))
)


class LLMCallBudgetExceeded(RuntimeError):
    """Raised before a request that would exceed the current trip budget."""

    def __init__(self, message: str, *, snapshot: dict[str, Any] | None = None,
                 failed_before_stage: str | None = None,
                 failed_after_stage: str | None = None):
        super().__init__(message)
        self.snapshot = snapshot or {}
        self.failed_before_stage = failed_before_stage
        self.failed_after_stage = failed_after_stage


class StructuredOutputLimitReached(RuntimeError):
    """Raised when a Provider confirms that structured output hit its limit."""


_STRUCTURED_STAGES = {"planner", "json_repair", "schema_repair", "xhs_research"}
_STRUCTURED_EVENT_STAGES = {
    "planner_parse", "json_repair", "schema_repair", "xhs_extraction",
}
_STRUCTURED_EVENT_CATEGORIES = {
    "json_decode_failed", "schema_validation_failed",
    "output_limit_reached", "repair_exhausted",
}


@dataclass
class LLMExecutionUsage:
    execution_id: str
    generation_id: str = ""
    task_id: str = ""
    max_calls: int = MAX_LLM_CALLS_PER_TRIP
    max_total_tokens: Optional[int] = None
    budget_exceeded: bool = False
    admission_events: list[dict[str, Any]] = field(default_factory=list)
    logical_llm_calls: int = 0
    llm_stage: str = ""
    model: str = ""
    retry_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    updated_at: float = field(default_factory=time.monotonic)
    on_update: Optional[Callable[["LLMExecutionUsage"], None]] = None
    stage_calls: dict[str, int] = field(default_factory=dict)
    structured_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "logical_llm_calls": self.logical_llm_calls,
                "llm_stage": self.llm_stage,
                "model": self.model,
                "retry_count": self.retry_count,
                "generation_id": self.generation_id or self.execution_id,
                "task_id": self.task_id,
                "stage_calls": dict(self.stage_calls),
                "structured_outputs": {
                    key: dict(value) for key, value in self.structured_outputs.items()
                },
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "max_calls": self.max_calls,
                "max_total_tokens": self.max_total_tokens,
                "budget_exceeded": self.budget_exceeded,
                "admission_events": [dict(item) for item in self.admission_events],
            }

    def notify(self) -> None:
        if self.on_update:
            self.on_update(self)


_llm_execution: contextvars.ContextVar[LLMExecutionUsage | None] = contextvars.ContextVar(
    "tripstar_llm_execution", default=None
)
_generation_usage_registry: dict[str, LLMExecutionUsage] = {}
_generation_registry_lock = threading.RLock()


def _prune_generation_registry(now: float | None = None) -> None:
    current = now if now is not None else time.monotonic()
    expired = [
        generation_id
        for generation_id, usage in _generation_usage_registry.items()
        if current - usage.updated_at > GENERATION_USAGE_TTL_SECONDS
    ]
    for generation_id in expired:
        _generation_usage_registry.pop(generation_id, None)


def get_or_create_generation_usage(
    generation_id: str,
    *,
    task_id: str = "",
    max_calls: int = MAX_LLM_CALLS_PER_TRIP,
) -> LLMExecutionUsage:
    """Return the shared usage budget for one full user-generation flow."""
    normalized = (generation_id or "").strip()
    if not normalized:
        raise ValueError("generation_id is required")
    with _generation_registry_lock:
        _prune_generation_registry()
        usage = _generation_usage_registry.get(normalized)
        if usage is None:
            usage = LLMExecutionUsage(
                execution_id=task_id or normalized,
                generation_id=normalized,
                task_id=task_id,
                max_calls=max_calls,
            )
            _generation_usage_registry[normalized] = usage
        elif task_id:
            usage.task_id = task_id
            usage.execution_id = task_id
        usage.updated_at = time.monotonic()
        return usage


def get_generation_usage(generation_id: str) -> LLMExecutionUsage | None:
    with _generation_registry_lock:
        _prune_generation_registry()
        return _generation_usage_registry.get((generation_id or "").strip())


def release_generation_usage(generation_id: str) -> LLMExecutionUsage | None:
    with _generation_registry_lock:
        return _generation_usage_registry.pop((generation_id or "").strip(), None)


@contextmanager
def llm_execution(
    execution_id: str,
    *,
    max_calls: int = MAX_LLM_CALLS_PER_TRIP,
    max_total_tokens: int | None = None,
    on_update: Optional[Callable[[LLMExecutionUsage], None]] = None,
) -> Iterator[LLMExecutionUsage]:
    """Bind an isolated, in-memory LLM budget to one execution/task."""
    usage = LLMExecutionUsage(
        execution_id,
        generation_id=execution_id,
        task_id=execution_id,
        max_calls=max_calls,
        max_total_tokens=max_total_tokens,
        on_update=on_update,
    )
    token = _llm_execution.set(usage)
    usage.notify()
    try:
        yield usage
    finally:
        usage.notify()
        _llm_execution.reset(token)


@contextmanager
def generation_llm_execution(
    generation_id: str,
    *,
    task_id: str = "",
    max_calls: int = MAX_LLM_CALLS_PER_TRIP,
    on_update: Optional[Callable[[LLMExecutionUsage], None]] = None,
) -> Iterator[LLMExecutionUsage]:
    """Bind the shared Preference-to-Trip budget to the current request/task."""
    usage = get_or_create_generation_usage(
        generation_id,
        task_id=task_id,
        max_calls=max_calls,
    )
    previous_callback = usage.on_update
    if on_update is not None:
        usage.on_update = on_update
    token = _llm_execution.set(usage)
    usage.notify()
    try:
        yield usage
    finally:
        usage.updated_at = time.monotonic()
        usage.notify()
        usage.on_update = previous_callback
        _llm_execution.reset(token)


def get_current_llm_usage() -> LLMExecutionUsage | None:
    return _llm_execution.get()


def record_application_retry() -> None:
    """Include a higher-level retry (for example planner timeout) in task metrics."""
    usage = get_current_llm_usage()
    if usage is not None:
        with usage.lock:
            usage.retry_count += 1
            usage.updated_at = time.monotonic()
        usage.notify()


def _error_category(exc: Exception) -> tuple[str, bool]:
    """Return a log-safe category and whether one bounded retry is allowed."""
    status = getattr(exc, "status_code", None)
    body = str(exc).lower()
    if any(value in body for value in (
        "insufficient_quota", "credit_balance_exhausted", "no credits remaining",
    )):
        return "quota", False
    if status in (400, 401, 403, 404, 422) or any(value in body for value in (
        "authentication", "invalid_api_key", "invalid request", "invalid_request_error",
    )):
        return "non_retryable_request", False
    if status == 429:
        return "rate_limit", True
    if status in (408, 409) or (isinstance(status, int) and status >= 500):
        return "transient_http", True
    if any(value in body for value in (
        "timeout", "timed out", "connection", "temporarily unavailable",
    )):
        return "transient_network", True
    return "unknown", False


def _usage_value(response: Any, name: str) -> Any:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    value = getattr(usage, name, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(name)
    return value


def normalize_finish_reason(response: Any) -> str:
    """Normalize Provider termination metadata without inspecting generated text."""
    choices = getattr(response, "choices", None)
    if not choices:
        return "missing"
    raw = getattr(choices[0], "finish_reason", None)
    if raw is None and isinstance(choices[0], dict):
        raw = choices[0].get("finish_reason")
    normalized = str(raw or "").strip().lower()
    if not normalized:
        return "missing"
    if normalized == "stop":
        return "stop"
    if normalized in {"length", "max_tokens", "max_completion_tokens"}:
        return "length"
    return "other"


def structured_output_metadata(response: Any, configured_output_limit: Any) -> dict[str, Any]:
    """Return bounded structured-output metadata; never include generated content."""
    completion_tokens = _usage_value(response, "completion_tokens")
    if completion_tokens is None:
        completion_tokens = _usage_value(response, "output_tokens")
    try:
        safe_tokens = max(0, int(completion_tokens)) if completion_tokens is not None else None
    except (TypeError, ValueError, OverflowError):
        safe_tokens = None
    try:
        safe_limit = max(0, int(configured_output_limit))
    except (TypeError, ValueError, OverflowError):
        safe_limit = 0
    return {
        "finish_reason": normalize_finish_reason(response),
        "configured_output_limit": safe_limit,
        "completion_tokens": safe_tokens,
        "limit_observed": bool(
            safe_limit and safe_tokens is not None and safe_tokens == safe_limit
        ),
    }


def get_last_structured_output(stage: str) -> dict[str, Any]:
    usage = get_current_llm_usage()
    if usage is None:
        return {
            "finish_reason": "missing", "configured_output_limit": 0,
            "completion_tokens": None, "limit_observed": False,
        }
    with usage.lock:
        return dict(usage.structured_outputs.get(stage, {
            "finish_reason": "missing", "configured_output_limit": 0,
            "completion_tokens": None, "limit_observed": False,
        }))


def log_structured_output_event(
    *, stage: str, category: str, metadata: dict[str, Any], success: bool,
) -> None:
    """Emit only bounded parser state; never prompts, output, or exceptions."""
    safe_stage = stage if stage in _STRUCTURED_EVENT_STAGES else "planner_parse"
    safe_category = category if category in _STRUCTURED_EVENT_CATEGORIES else "repair_exhausted"
    finish_reason = metadata.get("finish_reason")
    if finish_reason not in {"stop", "length", "other", "missing"}:
        finish_reason = "other"
    limit = metadata.get("configured_output_limit")
    limit = limit if isinstance(limit, int) and limit >= 0 else 0
    tokens = metadata.get("completion_tokens")
    tokens_text = str(tokens) if isinstance(tokens, int) and tokens >= 0 else "unknown"
    print(
        "event=structured_output_event "
        f"stage={safe_stage} category={safe_category} "
        f"finish_reason={finish_reason} configured_output_limit={limit} "
        f"completion_tokens={tokens_text} success={str(success).lower()}",
        flush=True,
    )


def create_chat_completion(
    *,
    stage: str,
    model: str,
    messages: list[dict[str, Any]],
    llm_instance: Any = None,
    **kwargs: Any,
) -> Any:
    """Make one budgeted logical invocation with at most one transient retry."""
    usage = get_current_llm_usage()
    stage_max_token_exposure = kwargs.pop("stage_max_token_exposure", None)
    configured_output_limit = kwargs.get("max_completion_tokens", kwargs.get("max_tokens", 0))
    if usage is not None:
        with usage.lock:
            event = {
                "stage": stage,
                "calls_used": usage.logical_llm_calls,
                "calls_remaining": max(0, usage.max_calls - usage.logical_llm_calls),
                "tokens_used": usage.total_tokens,
                "max_total_tokens": usage.max_total_tokens,
                "next_stage_max_token_exposure": stage_max_token_exposure,
                "admission_certainty": "known" if stage_max_token_exposure is not None else "unknown",
                "admitted": True,
            }
            if usage.budget_exceeded:
                event["admitted"] = False
                event["reason"] = "budget_already_exceeded"
                usage.admission_events.append(event)
                raise LLMCallBudgetExceeded(
                    "LLM token budget was already exceeded", snapshot=usage.snapshot(),
                    failed_before_stage=stage,
                )
            if usage.logical_llm_calls >= usage.max_calls:
                event["admitted"] = False
                event["reason"] = "max_llm_calls_exceeded"
                usage.admission_events.append(event)
                raise LLMCallBudgetExceeded(
                    f"LLM call budget exceeded ({usage.logical_llm_calls}/{usage.max_calls})",
                    snapshot=usage.snapshot(), failed_before_stage=stage,
                )
            if (usage.max_total_tokens is not None and stage_max_token_exposure is not None
                    and usage.total_tokens + int(stage_max_token_exposure) > usage.max_total_tokens):
                event["admitted"] = False
                event["reason"] = "known_stage_exposure_exceeds_remaining_budget"
                usage.admission_events.append(event)
                raise LLMCallBudgetExceeded(
                    "known stage token exposure exceeds remaining budget",
                    snapshot=usage.snapshot(), failed_before_stage=stage,
                )
            usage.admission_events.append(event)
            usage.logical_llm_calls += 1
            usage.llm_stage = stage
            usage.model = model
            usage.stage_calls[stage] = usage.stage_calls.get(stage, 0) + 1
            usage.updated_at = time.monotonic()
            logical_number = usage.logical_llm_calls
        usage.notify()
        execution_id = usage.execution_id
    else:
        execution_id = "standalone"
        logical_number = 1

    llm = llm_instance or get_llm()
    attempts = MAX_TRANSIENT_LLM_RETRIES + 1
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            response = llm._client.chat.completions.create(
                model=model,
                messages=messages,
                **kwargs,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            prompt_tokens = _usage_value(response, 'prompt_tokens')
            if prompt_tokens is None:
                prompt_tokens = _usage_value(response, 'input_tokens')
            completion_tokens = _usage_value(response, 'completion_tokens')
            if completion_tokens is None:
                completion_tokens = _usage_value(response, 'output_tokens')
            total_tokens = _usage_value(response, 'total_tokens')
            if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
                total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
            structured_metadata = (
                structured_output_metadata(response, configured_output_limit)
                if stage in _STRUCTURED_STAGES else None
            )
            if usage is not None:
                with usage.lock:
                    usage.prompt_tokens += int(prompt_tokens or 0)
                    usage.completion_tokens += int(completion_tokens or 0)
                    usage.total_tokens += int(total_tokens or 0)
                    usage.updated_at = time.monotonic()
                    exceeded = (
                        usage.max_total_tokens is not None
                        and usage.total_tokens > usage.max_total_tokens
                    )
                    if exceeded:
                        usage.budget_exceeded = True
                    if stage in _STRUCTURED_STAGES:
                        usage.structured_outputs[stage] = dict(structured_metadata)
                usage.notify()
            print(
                "LLM_CALL "
                f"execution_id={execution_id} stage={stage} model={model} "
                f"logical_call={logical_number} retry_attempt={attempt - 1} "
                f"duration_ms={duration_ms} success=true "
                f"prompt_tokens={prompt_tokens} "
                f"completion_tokens={completion_tokens} "
                f"total_tokens={total_tokens}"
                + (
                    f" finish_reason={structured_metadata['finish_reason']} "
                    f"configured_output_limit={structured_metadata['configured_output_limit']} "
                    f"limit_observed={str(structured_metadata['limit_observed']).lower()}"
                    if structured_metadata is not None else ""
                )
            )
            if usage is not None and exceeded:
                raise LLMCallBudgetExceeded(
                    "LLM actual token usage exceeded the evaluation ceiling",
                    snapshot=usage.snapshot(), failed_after_stage=stage,
                )
            return response
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            if isinstance(exc, LLMCallBudgetExceeded):
                raise
            category, retryable = _error_category(exc)
            will_retry = retryable and attempt < attempts
            print(
                "LLM_CALL "
                f"execution_id={execution_id} stage={stage} model={model} "
                f"logical_call={logical_number} retry_attempt={attempt - 1} "
                f"duration_ms={duration_ms} success=false "
                f"error_category={category} will_retry={str(will_retry).lower()}"
            )
            if not will_retry:
                raise
            if usage is not None:
                with usage.lock:
                    usage.retry_count += 1
                    usage.updated_at = time.monotonic()
                usage.notify()
    raise RuntimeError("unreachable LLM retry state")


class TaskScopedLLM:
    """HelloAgents-compatible facade that routes invokes through the guardrail."""

    def __init__(self, llm: HelloAgentsLLM, stage: str):
        self._llm = llm
        self.stage = stage
        self.model = llm.model
        self.temperature = llm.temperature
        self.max_tokens = llm.max_tokens

    def invoke(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        response = create_chat_completion(
            stage=self.stage,
            model=self.model,
            messages=messages,
            llm_instance=self._llm,
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            **{k: v for k, v in kwargs.items() if k not in ("temperature", "max_tokens")},
        )
        return response.choices[0].message.content


def _uses_max_completion_tokens(model: str | None) -> bool:
    """Return whether an OpenAI GPT-5 family model uses the newer token field."""
    model_name = (model or "").lower().rsplit("/", 1)[-1]
    return model_name == "gpt-5" or model_name.startswith("gpt-5-") or model_name.startswith("gpt-5.")


def _sanitize_chat_completion_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Remove unset optional values and normalize the output-token parameter.

    HelloAgents 0.2.x always supplies ``max_tokens`` even when its value is
    ``None``. Some OpenAI-compatible endpoints reject the resulting JSON null.
    GPT-5 family chat-completion models use ``max_completion_tokens`` instead;
    when both names are supplied, the newer explicit value wins.
    """
    sanitized = {key: value for key, value in kwargs.items() if value is not None}

    uses_gpt5_parameters = _uses_max_completion_tokens(sanitized.get("model"))

    if "max_completion_tokens" in sanitized:
        sanitized.pop("max_tokens", None)
    elif "max_tokens" in sanitized and uses_gpt5_parameters:
        sanitized["max_completion_tokens"] = sanitized.pop("max_tokens")

    # GPT-5 chat-completion models currently accept only the default
    # temperature. HelloAgents defaults to 0.7, so omit that unsupported value.
    if uses_gpt5_parameters and sanitized.get("temperature", 1) != 1:
        sanitized.pop("temperature", None)

    return sanitized


class _CompatibleCompletions:
    def __init__(self, completions: Any):
        self._completions = completions

    def create(self, *args: Any, **kwargs: Any) -> Any:
        return self._completions.create(
            *args,
            **_sanitize_chat_completion_kwargs(kwargs),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._completions, name)


class _CompatibleChat:
    def __init__(self, chat: Any):
        self._chat = chat
        self.completions = _CompatibleCompletions(chat.completions)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chat, name)


class OpenAICompatibilityClient:
    """Transparent OpenAI client adapter for HelloAgents request compatibility."""

    def __init__(self, client: Any):
        self._client = client
        self.chat = _CompatibleChat(client.chat)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def get_llm() -> HelloAgentsLLM:
    """
    获取LLM实例(单例模式)
    
    Returns:
        HelloAgentsLLM实例
    """
    global _llm_instance
    
    if _llm_instance is None:
        settings = get_settings()

        api_key = (
            settings.openai_api_key
            or os.getenv("LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        base_url = (
            settings.openai_base_url
            or os.getenv("LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        model = (
            settings.openai_model
            or os.getenv("LLM_MODEL_ID")
            or os.getenv("OPENAI_MODEL")
            or "gpt-4"
        )
        timeout = int(os.getenv("LLM_TIMEOUT", "60"))

        _llm_instance = HelloAgentsLLM(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        
        # 【关键修复】：针对第三方中转API可能开启了 Cloudflare/WAF 拦截 Python 默认爬虫特征的情况
        # 我们手动覆盖底层的 OpenAI client，加入伪装的浏览器 User-Agent
        from openai import OpenAI
        _llm_instance._client = OpenAICompatibilityClient(
            OpenAI(
                api_key=_llm_instance.api_key,
                base_url=_llm_instance.base_url,
                timeout=_llm_instance.timeout,
                # Retries are classified and bounded by create_chat_completion.
                max_retries=0,
                default_headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
            )
        )
        
        print(f"✅ LLM服务初始化成功")
        print(f"   提供商: {_llm_instance.provider}")
        print(f"   模型: {_llm_instance.model}")
    
    return _llm_instance


def reset_llm():
    """重置LLM实例(用于测试或重新配置)"""
    global _llm_instance
    _llm_instance = None
