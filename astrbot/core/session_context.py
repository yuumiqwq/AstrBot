"""Shared session context processing for ordinary turns and plugin requests."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from astrbot.core.agent.context.config import ContextConfig
from astrbot.core.agent.context.manager import ContextManager
from astrbot.core.agent.context.round_utils import split_into_rounds
from astrbot.core.agent.message import (
    CheckpointMessageSegment,
    Message,
    TextPart,
    bind_checkpoint_messages,
)
from astrbot.core.config.agent_runner import resolve_context_compression_config
from astrbot.core.utils.llm_metadata import LLM_METADATAS


async def make_context_manager(context: Any, session_id: str, provider: Any):
    """Resolve the same session settings used by the main agent.

    Args:
        context: AstrBot service context.
        session_id: Session whose compression settings apply.
        provider: Chat provider used by the request.

    Returns:
        Configured native context manager.
    """
    config = context.get_config(umo=session_id)
    compression = (
        config.get("agent_runner", {}).get("config", {}).get("compression", {})
    )
    options = resolve_context_compression_config(compression)
    max_tokens = provider.provider_config.get("max_context_tokens", 0)
    if max_tokens <= 0:
        metadata = LLM_METADATAS.get(provider.get_model())
        max_tokens = (
            metadata["limit"]["context"]
            if metadata
            else options["fallback_max_context_tokens"]
        )
    compression_provider = None
    if options["context_limit_reached_strategy"] == "llm_compress":
        provider_id = options["llm_compress_provider_id"]
        if provider_id:
            compression_provider = context.get_provider_by_id(provider_id)
        if compression_provider is None:
            compression_provider = provider
    return ContextManager(
        ContextConfig(
            max_context_tokens=max_tokens,
            enforce_max_turns=options["max_context_length"],
            truncate_turns=options["dequeue_context_length"],
            llm_compress_instruction=options["llm_compress_instruction"],
            llm_compress_keep_recent_ratio=options["llm_compress_keep_recent_ratio"],
            llm_compress_provider=compression_provider,
        )
    )


async def prepare_request_context(
    context: Any,
    session_id: str,
    provider: Any,
    req: Any,
    response_instruction: str = "",
    *,
    preserve_history_metadata: bool = False,
):
    """Manage a full request using AstrBot's native token and turn policies.

    Args:
        context: AstrBot service context.
        session_id: Session whose compression settings apply.
        provider: Chat provider used by the request.
        req: Provider request to update in place.
        response_instruction: Optional output protocol applied after summarization.
        preserve_history_metadata: Restore original source text and QQ metadata
            after length processing when persisting synchronized history.

    Raises:
        ValueError: If the active request is removed by context processing.
    """
    manager = await make_context_manager(context, session_id, provider)
    source_history = req.contexts or []
    history = bind_checkpoint_messages(source_history)
    source_data = {}
    source_offsets = set()
    current = Message.model_validate(await req.assemble_context())
    try:
        local_zone = ZoneInfo(
            context.get_config(umo=session_id).get("timezone") or "Asia/Shanghai"
        )
    except (ValueError, ZoneInfoNotFoundError):
        local_zone = ZoneInfo("Asia/Shanghai")
    entries = (item for item in source_history if item.get("role") != "_checkpoint")
    for message, original in zip(history, entries):
        sent_at = original.get("_qq_time")
        if (
            original.get("_no_save")
            or message.role not in {"user", "assistant"}
            or type(sent_at) is not int
            or sent_at <= 0
        ):
            continue
        try:
            local_time = datetime.fromtimestamp(sent_at, timezone.utc).astimezone(
                local_zone
            )
        except (ValueError, OverflowError, OSError):
            continue
        source_data[id(message)] = original
        source_offsets.add(local_time.utcoffset())
        annotation = f"[QQ消息发送时间：{local_time:%Y-%m-%d %H:%M:%S}]\n"
        if isinstance(message.content, str):
            message.content = annotation + message.content
        elif isinstance(message.content, list):
            message.content = [TextPart(text=annotation), *message.content]
    timezone_note = f"QQ历史消息的发送时间统一使用 {local_zone.key} 时区。"
    timezone_header = next(
        (
            message
            for message in history
            if message.role == "system"
            and message._no_save
            and message.content == timezone_note
        ),
        None,
    )
    if source_data and timezone_header is None:
        # Only current instructions/materials declare the time zone. Historical
        # dialogue and preset examples may mention unrelated places and times.
        current_text = (
            current.content
            if isinstance(current.content, str)
            else "\n".join(
                part.text
                for part in (current.content or [])
                if isinstance(part, TextPart)
            )
        )
        current_materials = "\n".join(
            (req.system_prompt or "", current_text, response_instruction)
        )
        timezone_present = local_zone.key in current_materials
        for timestamp in re.findall(
            r"(?:当前时间(?:是)?|current\s+time|current_time|current_local_time|local_now)"
            r"\s*[\"\']?\s*[:：]\s*[\"\']?"
            r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2}))",
            current_materials,
            re.IGNORECASE,
        ):
            try:
                timezone_present |= source_offsets == {
                    datetime.fromisoformat(timestamp.replace("Z", "+00:00")).utcoffset()
                }
            except ValueError:
                continue
        if not timezone_present:
            timezone_header = Message(role="system", content=timezone_note)
            timezone_header._no_save = True
            history.insert(0, timezone_header)
    # Count response protocol tokens without imposing that protocol on summaries.
    if isinstance(current.content, str):
        current.content = [TextPart(text=current.content)]
    if response_instruction:
        current.content.append(TextPart(text=response_instruction))
    system = (
        Message(role="system", content=req.system_prompt) if req.system_prompt else None
    )
    messages = [system] if system else []
    counter = manager.token_counter
    tokens = counter.count_tokens([*messages, current])
    rounds = split_into_rounds(history)
    for index, round_messages in enumerate(rounds):
        messages.extend(round_messages)
        tokens += counter.count_tokens(round_messages)
        # Replaying a large QQ archive incrementally keeps summary requests near
        # the native compression threshold, as during an ordinary conversation.
        if index < len(rounds) - 1 and manager.compressor.should_compress(
            messages, tokens, manager.config.max_context_tokens
        ):
            processed = await manager.process([*messages, current])
            messages = [item for item in processed if item is not current]
            tokens = counter.count_tokens([*messages, current])
    processed = await manager.process([*messages, current])
    if current not in processed:
        raise ValueError("AstrBot context management removed the current request")
    req.contexts = []
    for message in processed:
        if message is system or message is current:
            continue
        if preserve_history_metadata and message is timezone_header:
            continue
        data = message.model_dump()
        if isinstance(message.content, list):
            data["content"] = [
                part.model_dump_for_context() for part in message.content
            ]
        if preserve_history_metadata and id(message) in source_data:
            original = source_data[id(message)]
            data["content"] = copy.deepcopy(original["content"])
            data["_qq_time"] = original["_qq_time"]
            if "_qq_sequence" in original:
                data["_qq_sequence"] = original["_qq_sequence"]
        if message._no_save:
            data["_no_save"] = True
        req.contexts.append(data)
        if message._checkpoint_after is not None:
            req.contexts.append(
                CheckpointMessageSegment(content=message._checkpoint_after).model_dump()
            )

    if response_instruction:
        req.system_prompt = (
            f"{req.system_prompt or ''}\n\n{response_instruction}".strip()
        )
