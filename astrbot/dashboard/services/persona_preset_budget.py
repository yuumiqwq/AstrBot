"""Apply the shared preset allowance using the host's token estimator."""

from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.message import Message
from astrbot.core.config.agent_runner import resolve_context_compression_config
from astrbot.core.persona_presets import prepare_preset_messages
from astrbot.core.utils.llm_metadata import LLM_METADATAS


async def preset_budget(
    core_lifecycle,
    dialogs: list[str],
    *,
    segments: list[dict] | None = None,
    enforce: bool = False,
):
    """Estimate preset usage against the current default chat context capacity.

    Args:
        core_lifecycle: Active host, including its default provider and settings.
        dialogs: Alternating native user and assistant preset text.
        segments: Optional independent excerpt boundaries.
        enforce: Raise when a proposed save exceeds the fifteen-percent allowance.

    Returns:
        Context capacity, estimated usage, percentage, and remaining allowance.

    Raises:
        ValueError: A preset contains non-text content or exceeds its allowance.
    """
    if any(not isinstance(item, str) for item in dialogs):
        raise ValueError("预设对话必须为文字")
    context = getattr(core_lifecycle, "star_context", None)
    provider = await context.get_using_provider_async() if context is not None else None
    config = getattr(core_lifecycle, "astrbot_config", {})
    if not isinstance(config, dict):
        config = {}
    compression = (
        config.get("agent_runner", {}).get("config", {}).get("compression", {})
    )
    options = resolve_context_compression_config(compression)
    capacity = options["fallback_max_context_tokens"]
    model = ""
    if provider is not None:
        model = provider.get_model()
        metadata = LLM_METADATAS.get(model)
        capacity = provider.provider_config.get("max_context_tokens", 0)
        if capacity <= 0:
            capacity = (
                metadata["limit"]["context"]
                if metadata
                else options["fallback_max_context_tokens"]
            )
    processed = prepare_preset_messages(dialogs, segments)
    counter = EstimateTokenCounter()
    tokens = counter.count_tokens(
        [
            Message(role=message["role"], content=message["content"])
            for message in processed
        ]
    )
    overhead = counter.count_tokens(
        [
            Message(role="system", content=message["content"])
            for message in processed
            if message["role"] == "system"
        ]
    )
    limit = int(capacity * 0.15)
    percent = tokens * 100 / capacity
    if enforce and tokens > limit:
        raise ValueError(
            f"预设对话预计占用 {percent:.2f}%，超过上下文容量的 15% 上限；"
            f"当前约 {tokens} tokens，上限 {limit} tokens，请减少后保存"
        )
    return {
        "model": model,
        "context_tokens": capacity,
        "estimated_tokens": tokens,
        "overhead_tokens": overhead,
        "used_percent": round(percent, 2),
        "limit_percent": 15,
        "limit_tokens": limit,
        "remaining_tokens": max(0, limit - tokens),
    }
