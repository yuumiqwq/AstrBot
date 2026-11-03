"""Preserve independent preset excerpts through storage and model assembly."""

import hashlib
import json

PRESET_DIALOGUE_STYLE_INSTRUCTION = "只参考预设对话中 assistant 一方的措辞与口癖，user 一方用于理解对话情境和对方的表达，不作为自己的风格范例。"
PRESET_DIALOGUE_END_INSTRUCTION = "预设对话示例结束。以下内容属于真实会话历史和当前请求，示例中的事件不代表刚刚发生的对话。"


def normalize_preset_segments(
    dialogs: list[str], segments: list[dict] | None = None
) -> list[dict]:
    """Validate excerpt coverage while treating legacy presets as one excerpt.

    Args:
        dialogs: Alternating user and assistant text stored by AstrBot.
        segments: Optional ordered excerpt metadata, with pair-based offsets.

    Returns:
        Independent canonical dictionaries covering all stored pairs.

    Raises:
        ValueError: The text or excerpt coverage is invalid.
    """
    if len(dialogs) % 2 or any(not isinstance(item, str) for item in dialogs):
        raise ValueError("预设对话须为成对的用户和助手文字")
    pair_count = len(dialogs) // 2
    if segments is None:
        return (
            [
                {
                    "id": "legacy",
                    "start_pair": 0,
                    "pair_count": pair_count,
                    "source_review_id": None,
                    "source_title": "",
                }
            ]
            if pair_count
            else []
        )
    result = []
    seen = set()
    offset = 0
    for segment in segments:
        identifier = segment.get("id")
        count = segment.get("pair_count")
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or identifier in seen
            or type(count) is not int
            or count <= 0
            or type(segment.get("start_pair")) is not int
            or segment["start_pair"] != offset
        ):
            raise ValueError("预设对话片段的编号或范围有误，请刷新后重试")
        title = segment.get("source_title", "")
        review_id = segment.get("source_review_id")
        if not isinstance(title, str) or (
            review_id is not None and not isinstance(review_id, str)
        ):
            raise ValueError("预设对话片段来源格式有误")
        result.append(
            {
                "id": identifier,
                "start_pair": offset,
                "pair_count": count,
                "source_review_id": review_id,
                "source_title": title,
            }
        )
        source = segment.get("source_conversation_id")
        origins = segment.get("source_messages")
        if source is not None or origins is not None:
            if (
                not isinstance(source, str)
                or not source
                or not isinstance(origins, list)
                or len(origins) != count
                or any(
                    not isinstance(pair, list)
                    or any(not isinstance(item, str) or not item for item in pair)
                    for pair in origins
                )
            ):
                raise ValueError("预设对话来源消息格式有误")
            result[-1].update(
                source_conversation_id=source,
                source_messages=[list(pair) for pair in origins],
            )
        seen.add(identifier)
        offset += count
    if offset != pair_count:
        raise ValueError("预设对话片段必须完整覆盖全部对话")
    return result


def preset_revision(dialogs: list[str], segments: list[dict] | None = None) -> str:
    """Hash both excerpt boundaries and their text for optimistic updates.

    Args:
        dialogs: Stored example text.
        segments: Stored or proposed excerpt metadata.

    Returns:
        Stable SHA-256 revision of the effective preset collection.
    """
    return hashlib.sha256(
        json.dumps(
            [dialogs, normalize_preset_segments(dialogs, segments)],
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def prepare_preset_messages(
    dialogs: list[str], segments: list[dict] | None = None
) -> list[dict]:
    """Assemble examples with transient style and independent-excerpt boundaries.

    Args:
        dialogs: Alternating user and assistant example strings.
        segments: Optional excerpt metadata; legacy text is one excerpt.

    Returns:
        Messages shared by native requests and native token accounting.
    """
    excerpts = normalize_preset_segments(dialogs, segments)
    if not excerpts:
        return []
    messages = [
        {
            "role": "system",
            "content": PRESET_DIALOGUE_STYLE_INSTRUCTION,
            "_no_save": True,
        }
    ]
    for number, segment in enumerate(excerpts, 1):
        messages.append(
            {
                "role": "system",
                "content": f"预设对话片段 {number} 开始。此片段内部的对话连续，与其他片段互不承接。",
                "_no_save": True,
            }
        )
        start = segment["start_pair"] * 2
        end = start + segment["pair_count"] * 2
        messages.extend(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": content,
                "_no_save": True,
            }
            for index, content in enumerate(dialogs[start:end])
        )
    messages.append(
        {"role": "system", "content": PRESET_DIALOGUE_END_INSTRUCTION, "_no_save": True}
    )
    return messages


def preserve_preset_segments(
    old_dialogs: list[str], old_segments: list[dict] | None, dialogs: list[str]
) -> list[dict]:
    """Preserve boundaries for legacy callers that do not submit metadata.

    Args:
        old_dialogs: Current stored example strings.
        old_segments: Current stored excerpt metadata.
        dialogs: Proposed replacement strings.

    Returns:
        Existing metadata when pair positions survive, or an adjusted single excerpt.

    Raises:
        ValueError: A multi-excerpt structural edit omitted its new boundaries.
    """
    segments = normalize_preset_segments(old_dialogs, old_segments)
    if not dialogs:
        return []
    if len(old_dialogs) == len(dialogs):
        return normalize_preset_segments(dialogs, segments)
    if len(segments) > 1:
        raise ValueError("修改多段预设的条数时，请同时提交片段范围")
    if segments:
        segments[0]["pair_count"] = len(dialogs) // 2
        segments[0].pop("source_conversation_id", None)
        segments[0].pop("source_messages", None)
        return normalize_preset_segments(dialogs, segments)
    return normalize_preset_segments(dialogs)
