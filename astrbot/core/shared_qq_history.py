"""QQ-backed conversation history owned by AstrBot, independent of plugins."""

import asyncio
import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.session_context import prepare_request_context


class SharedQQHistory:
    """Synchronize source messages and persist native summaries per conversation."""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.locks: dict[str, asyncio.Lock] = {}

    def settings(self, session_id: str) -> dict | None:
        """Get an explicitly configured QQ session policy.

        Args:
            session_id: Unified session origin.

        Returns:
            Session policy, or None for ordinary AstrBot history.
        """
        sessions = (
            self.context.get_config(umo=session_id)
            .get("shared_qq_history", {})
            .get("sessions", [])
        )
        return next(
            (policy for policy in sessions if policy.get("session_id") == session_id),
            None,
        )

    async def read_records(
        self,
        session_id: str,
        policy: dict,
        before_message_id: str | None = None,
        after_sequence: int | None = None,
    ) -> list[dict]:
        """Probe the newest record and read backwards only to the saved cursor.

        Args:
            session_id: QQ private session origin.
            policy: Inclusive first sequence to retain.
            before_message_id: Exclude an incoming turn and any later messages.
            after_sequence: Inclusive saved cursor, or None for initial import.

        Returns:
            Chronological QQ records without overlapping-page duplicates.

        Raises:
            ValueError: If the adapter or history response is invalid.
            TimeoutError: If a QQ history page times out.
        """
        platform_id, kind, user_id = session_id.split(":", 2)
        platform = self.context.get_platform_inst(platform_id)
        if kind != "FriendMessage" or platform.meta().name != "aiocqhttp":
            raise ValueError("Shared QQ history requires an aiocqhttp private session")
        client = platform.get_client()
        first_sequence = int(policy.get("start_sequence", 0))
        stop_sequence = max(first_sequence, after_sequence or 0)
        records: dict[str, dict] = {}
        cursor = ""
        visited: set[str] = set()
        while True:
            params = {
                "user_id": int(user_id),
                "count": 100 if cursor else 1,
                "reverse_order": True,
                "disable_get_url": True,
                "parse_mult_msg": False,
            }
            if cursor:
                params["message_seq"] = cursor
            try:
                response = await asyncio.wait_for(
                    client.call_action("get_friend_msg_history", **params), timeout=30
                )
            except Exception as error:
                if cursor and "消息" in str(error) and "不存在" in str(error):
                    break
                raise
            page = response.get("messages")
            if not isinstance(page, list):
                raise ValueError("QQ history response has no messages list")
            if not page:
                break
            page.sort(
                key=lambda m: (int(m.get("time") or 0), int(m.get("real_seq") or 0))
            )
            if not cursor:
                # Some adapters return cached extras even for count=1. Only the
                # latest record is a reliable starting point for pagination.
                page = page[-1:]
            for message in page:
                sequence = int(message.get("real_seq") or 0)
                if sequence >= first_sequence:
                    # NapCat short IDs can change; QQ sequence is stable within a chat.
                    key = str(sequence or message["message_id"])
                    records[key] = message
            if int(page[0].get("real_seq") or 0) <= stop_sequence:
                break
            next_cursor = str(page[0 if cursor else -1]["message_id"])
            if next_cursor == cursor or next_cursor in visited:
                break
            visited.add(next_cursor)
            cursor = next_cursor
        ordered = sorted(
            records.values(),
            key=lambda m: (int(m.get("time") or 0), int(m.get("real_seq") or 0)),
        )
        if before_message_id:
            for index, message in enumerate(ordered):
                if str(message["message_id"]) == str(before_message_id):
                    ordered = ordered[:index]
                    break
        return ordered

    def normalize_records(self, records: list[dict]) -> tuple[list[dict], list[str]]:
        """Convert QQ text, media markers, expressions and shared cards.

        Args:
            records: Chronological OneBot message records.

        Returns:
            Chat messages and stable source fingerprints for incremental import.
        """
        history, fingerprints = [], []
        placeholders = {
            "image": "[图片]",
            "record": "[语音]",
            "video": "[视频]",
            "reply": "[回复]",
            "file": "[文件]",
        }
        for message in records:
            text_parts = []
            parts = message.get("message") or []
            if isinstance(parts, str):
                text_parts.append(parts)
                parts = []
            for part in parts:
                kind, data = part.get("type"), part.get("data") or {}
                if kind == "text":
                    text_parts.append(str(data.get("text") or ""))
                elif kind == "face":
                    name = (data.get("raw") or {}).get("faceText") or data.get("id")
                    text_parts.append(f"[QQ表情:{name}]")
                elif kind == "json":
                    try:
                        card = json.loads(data.get("data") or "{}")
                        news = (card.get("meta") or {}).get("news") or {}
                        title = news.get("title") or card.get("prompt") or "分享卡片"
                        url = news.get("jumpUrl") or ""
                        if urlsplit(url).scheme not in {"http", "https"}:
                            url = ""
                        text_parts.append(f"[分享卡片] {title}\n{url}".rstrip())
                    except (ValueError, TypeError, AttributeError):
                        text_parts.append("[分享卡片]")
                elif kind in placeholders:
                    text_parts.append(placeholders[kind])
            text = "".join(text_parts).strip()
            if not text:
                continue
            sender = message.get("sender") or {}
            own = str(sender.get("user_id") or message.get("user_id")) == str(
                message.get("self_id")
            )
            entry = {"role": "assistant" if own else "user", "content": text}
            stable = [message.get("real_seq"), message.get("time"), entry]
            fingerprints.append(
                hashlib.sha256(
                    json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
            )
            sent_at = message.get("time")
            if type(sent_at) is int and sent_at > 0:
                entry["_qq_time"] = sent_at
                entry["_qq_sequence"] = int(message.get("real_seq") or 0)
            history.append(entry)
        return history, fingerprints

    @staticmethod
    def merge_source_timestamps(history: list[dict], source: list[dict]) -> list[dict]:
        """Attach source times to retained messages without replacing summaries.

        Args:
            history: Already retained native history, including possible summaries.
            source: Chronological normalized QQ records up to the saved cursor.

        Returns:
            Original history with time metadata on matching retained messages.
        """
        result = [dict(item) for item in history]
        positions: dict[tuple, list[int]] = {}
        for index, item in enumerate(source):
            if isinstance(item.get("content"), str):
                positions.setdefault((item["role"], item["content"]), []).append(index)
        before = len(source)
        for item in reversed(result):
            if (
                item.get("_no_save")
                or item.get("role") not in {"user", "assistant"}
                or not isinstance(item.get("content"), str)
            ):
                continue
            matches = positions.get((item["role"], item["content"]), [])
            while matches and matches[-1] >= before:
                matches.pop()
            if not matches:
                continue
            before = matches.pop()
            original = source[before]
            for key in ("_qq_time", "_qq_sequence"):
                if key in original:
                    item[key] = original[key]
        return result

    def record_cursor(self, record: dict) -> dict:
        """Identify a source position independently of QQ's temporary short IDs.

        Args:
            record: Last imported QQ record, including records without text.

        Returns:
            Stable sequence, timestamp and normalized content fingerprint.
        """
        _, fingerprints = self.normalize_records([record])
        return {
            "sequence": int(record.get("real_seq") or 0),
            "time": int(record.get("time") or 0),
            "fingerprints": fingerprints,
        }

    async def synchronize(
        self, session_id: str, conversation: Any, before_message_id: str | None = None
    ) -> Any:
        """Refresh a shared conversation and reuse its previously saved summary.

        Args:
            session_id: Unified private-session origin.
            conversation: Existing database conversation.
            before_message_id: Current incoming message, which the caller adds.

        Returns:
            Conversation containing the shared, length-managed QQ history.
        """
        policy = self.settings(session_id)
        if policy is None:
            return conversation
        cid = conversation.conversation_id
        async with self.locks.setdefault(cid, asyncio.Lock()):
            db = self.context.conversation_manager.db
            conversation = await db.get_conversation_by_id(cid=cid)
            preference = await db.get_preference(
                "conversation", cid, "shared_qq_history"
            )
            state = preference.value if preference else {}
            current = conversation.content or []
            digest = hashlib.sha256(
                json.dumps(current, sort_keys=True).encode()
            ).hexdigest()
            valid = (
                state.get("history_digest") == digest and state.get("policy") == policy
            )
            previous = state.get("fingerprints", []) if valid else []
            last_record = state.get("last_record") if valid else None
            needs_times = valid and state.get("timestamp_version") != 1
            records = await self.read_records(
                session_id,
                policy,
                before_message_id,
                after_sequence=last_record["sequence"]
                if last_record and not needs_times
                else None,
            )
            retained = current
            if needs_times:
                originals = [
                    record
                    for record in records
                    if not last_record
                    or int(record.get("real_seq") or 0) <= last_record["sequence"]
                ]
                retained = self.merge_source_timestamps(
                    current, self.normalize_records(originals)[0]
                )
            newest_record = self.record_cursor(records[-1]) if records else last_record
            if last_record:
                anchor = next(
                    (
                        record
                        for record in records
                        if int(record.get("real_seq") or 0) == last_record["sequence"]
                    ),
                    None,
                )
                if anchor and self.record_cursor(anchor) == last_record:
                    records = [
                        record
                        for record in records
                        if int(record.get("real_seq") or 0) > last_record["sequence"]
                    ]
                    if not records and not needs_times:
                        return conversation
                    history, fingerprints = self.normalize_records(records)
                    history = [*retained, *history]
                    fingerprints = [*previous, *fingerprints]
                else:
                    # A removed/changed cursor or an older incoming request
                    # needs reconciliation. Older edits are not polled on each
                    # turn: the normal path only checks the newest QQ record.
                    records = await self.read_records(
                        session_id, policy, before_message_id
                    )
                    newest_record = self.record_cursor(records[-1]) if records else None
                    history, fingerprints = self.normalize_records(records)
            else:
                history, fingerprints = self.normalize_records(records)
                # Upgrade the previous full-scan state without losing a summary.
                if valid and fingerprints[: len(previous)] == previous:
                    history = [*retained, *history[len(previous) :]]
            if history != current:
                if history != retained:
                    provider = await self.context.get_using_provider_async(
                        umo=session_id
                    )
                    req = ProviderRequest(prompt="", contexts=history, system_prompt="")
                    await prepare_request_context(
                        self.context,
                        session_id,
                        provider,
                        req,
                        preserve_history_metadata=True,
                    )
                    history = req.contexts
                await db.update_conversation(cid=cid, content=history, token_usage=0)
            await db.insert_preference_or_update(
                "conversation",
                cid,
                "shared_qq_history",
                {
                    "policy": policy,
                    "fingerprints": fingerprints,
                    "last_record": newest_record,
                    "timestamp_version": 1,
                    "history_digest": hashlib.sha256(
                        json.dumps(history, sort_keys=True).encode()
                    ).hexdigest(),
                },
            )
            conversation.content = history
            conversation.token_usage = 0
            return conversation
