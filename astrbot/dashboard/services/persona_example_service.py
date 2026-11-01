from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import desc, func, select, text

from astrbot.core.db.po import ConversationV2, Persona, PersonaExampleReview
from astrbot.core.persona_presets import (
    normalize_preset_segments,
    preserve_preset_segments,
    preset_revision,
)
from astrbot.dashboard.services.persona_preset_budget import preset_budget


class PersonaExampleService:
    def __init__(self, core_lifecycle, db):
        self.core_lifecycle = core_lifecycle
        self.persona_mgr = core_lifecycle.persona_mgr
        self.db = db

    async def presets(self, persona_id: str) -> dict:
        """Read current persona examples with a content revision.

        Args:
            persona_id: Persona selected in the dashboard.

        Returns:
            Current example strings and their revision.

        Raises:
            ValueError: The persona does not exist.
        """
        persona = await self.db.get_persona_by_id(persona_id)
        if persona is None:
            raise ValueError("人格不存在")
        dialogs = list(persona.begin_dialogs or [])
        segments = normalize_preset_segments(dialogs, persona.begin_dialog_segments)
        return {
            "begin_dialogs": dialogs,
            "segments": segments,
            "revision": preset_revision(dialogs, segments),
            "budget": await preset_budget(
                self.core_lifecycle, dialogs, segments=segments
            ),
        }

    async def update_presets(
        self,
        persona_id: str,
        revision: str,
        begin_dialogs: list[str],
        segments: list[dict] | None = None,
    ) -> dict:
        """Edit or remove existing examples without overwriting concurrent approval.

        Args:
            persona_id: Persona selected in the dashboard.
            revision: Revision observed before editing.
            begin_dialogs: Remaining edited pairs; an empty list removes all pairs.
            segments: Updated excerpt boundaries, including stable identifiers.

        Returns:
            Saved example strings and their new revision.

        Raises:
            ValueError: The persona is missing, changed, or has incomplete pairs.
        """
        if len(begin_dialogs) % 2:
            raise ValueError("预设对话数量必须为偶数")
        async with self.db.get_db() as session:
            if session.bind.dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            persona = (
                await session.execute(
                    select(Persona)
                    .where(Persona.persona_id == persona_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if persona is None:
                raise ValueError("人格不存在")
            current = list(persona.begin_dialogs or [])
            actual = preset_revision(current, persona.begin_dialog_segments)
            if actual != revision:
                raise ValueError("预设对话已更新，请刷新后重试")
            segments = (
                preserve_preset_segments(
                    current, persona.begin_dialog_segments, begin_dialogs
                )
                if segments is None
                else normalize_preset_segments(begin_dialogs, segments)
            )
            await preset_budget(
                self.core_lifecycle, begin_dialogs, segments=segments, enforce=True
            )
            persona.begin_dialogs = begin_dialogs
            persona.begin_dialog_segments = segments
            persona.updated_at = datetime.now(timezone.utc)
            session.add(persona)
            await session.commit()
        self.persona_mgr.personas = await self.persona_mgr.get_all_personas()
        self.persona_mgr.get_v3_persona_data()
        return await self.presets(persona_id)

    async def sources(self) -> list[dict]:
        async with self.db.get_db() as session:
            rows = await session.execute(
                select(
                    ConversationV2.conversation_id,
                    ConversationV2.title,
                    ConversationV2.user_id,
                    ConversationV2.persona_id,
                )
                .order_by(desc(ConversationV2.updated_at))
                .limit(100)
            )
            return [dict(row._mapping) for row in rows]

    async def list(self, persona_id: str, state: str, page: int) -> dict:
        async with self.db.get_db() as session:
            filters = [PersonaExampleReview.state == state]
            if persona_id:
                filters.append(PersonaExampleReview.persona_id == persona_id)
            total = (
                await session.execute(
                    select(func.count())
                    .select_from(PersonaExampleReview)
                    .where(*filters)
                )
            ).scalar_one()
            rows = (
                await session.execute(
                    select(PersonaExampleReview)
                    .where(*filters)
                    .order_by(
                        desc(PersonaExampleReview.created_at),
                        PersonaExampleReview.review_id,
                    )
                    .offset((page - 1) * 20)
                    .limit(20)
                )
            ).scalars()
            items = [row.model_dump() for row in rows]
            personas = {}
            for item in items:
                if item["persona_id"] not in personas:
                    personas[item["persona_id"]] = (
                        await session.execute(
                            select(Persona).where(
                                Persona.persona_id == item["persona_id"]
                            )
                        )
                    ).scalar_one_or_none()
                persona = personas[item["persona_id"]]
                matching = None
                if persona is not None and item["state"] == "approved":
                    dialogs = list(persona.begin_dialogs or [])
                    for segment in normalize_preset_segments(
                        dialogs, persona.begin_dialog_segments
                    ):
                        start = segment["start_pair"] * 2
                        end = start + segment["pair_count"] * 2
                        if segment["source_review_id"] == item["review_id"] or any(
                            dialogs[index : index + len(item["begin_dialogs"])]
                            == item["begin_dialogs"]
                            for index in range(
                                start, end - len(item["begin_dialogs"]) + 1, 2
                            )
                        ):
                            matching = segment["id"]
                            break
                item["live_segment_id"] = matching
                item["present"] = matching is not None
            return {"items": items, "total": total}

    @staticmethod
    def _source_snapshot(conversation) -> dict:
        """Extract selectable text while keeping each original history index.

        Args:
            conversation: Persisted native conversation read in the current transaction.

        Returns:
            Source messages and a revision covering the complete stored history.
        """
        messages = []
        for index, message in enumerate(conversation.content or []):
            if (
                not isinstance(message, dict)
                or message.get("_no_save")
                or message.get("tool_calls")
            ):
                continue
            role = message.get("role")
            if role not in ("user", "assistant"):
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    part["text"]
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                )
            if isinstance(content, str) and content.strip():
                messages.append({"index": index, "role": role, "content": content})
        return {
            "conversation_id": conversation.conversation_id,
            "title": conversation.title or conversation.user_id,
            "revision": hashlib.sha256(
                json.dumps(
                    conversation.content or [], ensure_ascii=False, sort_keys=True
                ).encode()
            ).hexdigest(),
            "messages": messages,
        }

    async def history(self, conversation_id: str) -> dict:
        async with self.db.get_db() as session:
            conversation = (
                await session.execute(
                    select(ConversationV2).where(
                        ConversationV2.conversation_id == conversation_id
                    )
                )
            ).scalar_one_or_none()
            if conversation is None:
                raise ValueError("会话不存在")
            return self._source_snapshot(conversation)

    async def create(self, persona_id: str, begin_dialogs: list[str]) -> dict:
        async with self.db.get_db() as session:
            persona = (
                await session.execute(
                    select(Persona).where(Persona.persona_id == persona_id)
                )
            ).scalar_one_or_none()
            if persona is None:
                raise ValueError("人格不存在")
            review = PersonaExampleReview(
                persona_id=persona_id,
                source_digest=str(uuid4()),
                begin_dialogs=begin_dialogs,
            )
            session.add(review)
            await session.commit()
            return review.model_dump()

    async def import_conversation(
        self,
        persona_id: str,
        conversation_id: str,
        context_rounds: int = 2,
        revision: str | None = None,
        ranges: list[dict] | None = None,
    ) -> dict:
        """Snapshot complete text exchanges without invoking a language model.

        Args:
            persona_id: Persona to receive examples after human approval.
            conversation_id: Existing AstrBot conversation selected by the operator.
            context_rounds: Number of preceding exchanges included in each candidate.
            revision: Observed source revision, required for selected ranges.
            ranges: Inclusive original message indices; each range is one candidate.

        Returns:
            Counts of newly captured and previously captured candidates.

        Raises:
            ValueError: The selected persona or conversation no longer exists.
        """
        async with self.db.get_db() as session:
            # Serialize import and approval writes, including concurrent HTTP requests.
            if session.bind.dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            persona = (
                await session.execute(
                    select(Persona).where(Persona.persona_id == persona_id)
                )
            ).scalar_one_or_none()
            conversation = (
                await session.execute(
                    select(ConversationV2).where(
                        ConversationV2.conversation_id == conversation_id
                    )
                )
            ).scalar_one_or_none()
            if persona is None or conversation is None:
                raise ValueError("人格或会话不存在")
            snapshot = self._source_snapshot(conversation)
            messages = snapshot["messages"]
            selections = []
            if ranges is not None:
                if revision != snapshot["revision"]:
                    raise ValueError("会话历史已更新，请重新读取后选择片段")
                if not ranges:
                    raise ValueError("请选择至少一个连续对话片段")
                by_index = {message["index"]: message for message in messages}
                previous_end = -1
                for selected in sorted(ranges, key=lambda item: item["start"]):
                    start, end = selected["start"], selected["end"]
                    if (
                        start > end
                        or start <= previous_end
                        or start not in by_index
                        or end not in by_index
                    ):
                        raise ValueError("所选片段范围无效或重叠，请重新选择")
                    if by_index[end]["role"] != "assistant":
                        raise ValueError("请选择到助手回复结束，以保留完整对话")
                    selections.append(
                        [
                            message
                            for message in messages
                            if start <= message["index"] <= end
                        ]
                    )
                    previous_end = end
            else:
                selections = [messages]
            candidates = []
            for selection in selections:
                turns: list[tuple[str, str]] = []
                if selection and selection[0]["role"] == "assistant":
                    turns.append(("user", ""))
                for message in selection:
                    role, content = message["role"], message["content"]
                    if turns and turns[-1][0] == role:
                        turns[-1] = (role, turns[-1][1] + "\n\n" + content)
                    else:
                        turns.append((role, content))
                if ranges is not None:
                    dialogs = [value for _, value in turns]
                    if sum(map(len, dialogs)) > 200000:
                        raise ValueError("候选对话过长，请缩短所选片段")
                    candidates.append(dialogs)
                else:
                    for end in range(1, len(turns), 2):
                        start = max(0, end - 1 - context_rounds * 2)
                        candidates.append(
                            [value for _, value in turns[start : end + 1]]
                        )
            existing = set(
                (
                    await session.execute(
                        select(PersonaExampleReview.source_digest).where(
                            PersonaExampleReview.persona_id == persona_id,
                            PersonaExampleReview.conversation_id == conversation_id,
                        )
                    )
                ).scalars()
            )
            added = skipped = 0
            for dialogs in candidates:
                digest = hashlib.sha256(
                    json.dumps(
                        [persona_id, conversation_id, dialogs], ensure_ascii=False
                    ).encode()
                ).hexdigest()
                if digest in existing:
                    skipped += 1
                    continue
                session.add(
                    PersonaExampleReview(
                        persona_id=persona_id,
                        conversation_id=conversation_id,
                        source_title=conversation.title or conversation.user_id,
                        source_digest=digest,
                        begin_dialogs=dialogs,
                    )
                )
                existing.add(digest)
                added += 1
            await session.commit()
            return {"added": added, "skipped": skipped}

    async def update(
        self,
        review_id: str,
        revision: int,
        begin_dialogs: list[str],
        action: str,
        username: str,
    ) -> dict:
        """Commit approval and persona examples together, retaining existing presets.

        Args:
            review_id: Persisted candidate identifier.
            revision: Revision displayed to the operator.
            begin_dialogs: Complete, editable user/assistant exchanges.
            action: Save, approve, or reject the pending candidate.
            username: Authenticated dashboard operator.

        Returns:
            The committed review, including its updated state and revision.

        Raises:
            ValueError: The candidate changed or its target persona disappeared.
        """
        async with self.db.get_db() as session:
            if session.bind.dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                await session.execute(
                    select(PersonaExampleReview)
                    .where(PersonaExampleReview.review_id == review_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise ValueError("候选不存在")
            final_state = {"approve": "approved", "reject": "rejected"}.get(action)
            if row.state != "pending":
                if row.state != final_state:
                    raise ValueError("候选已审核，请刷新")
            else:
                if row.revision != revision:
                    raise ValueError("候选已更新，请刷新后重试")
                if action == "approve":
                    persona = (
                        await session.execute(
                            select(Persona)
                            .where(Persona.persona_id == row.persona_id)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if persona is None:
                        raise ValueError("目标人格不存在")
                    existing = list(persona.begin_dialogs or [])
                    if len(existing) % 2:
                        raise ValueError("现有预设对话格式有误，请先修正")
                    segments = normalize_preset_segments(
                        existing, persona.begin_dialog_segments
                    )
                    already_present = any(
                        existing[i : i + len(begin_dialogs)] == begin_dialogs
                        for segment in segments
                        for i in range(
                            segment["start_pair"] * 2,
                            (segment["start_pair"] + segment["pair_count"]) * 2
                            - len(begin_dialogs)
                            + 1,
                            2,
                        )
                    )
                    if not already_present:
                        segments.append(
                            {
                                "id": str(uuid4()),
                                "start_pair": len(existing) // 2,
                                "pair_count": len(begin_dialogs) // 2,
                                "source_review_id": row.review_id,
                                "source_title": row.source_title,
                            }
                        )
                        await preset_budget(
                            self.core_lifecycle,
                            existing + begin_dialogs,
                            segments=segments,
                            enforce=True,
                        )
                        persona.begin_dialogs = existing + begin_dialogs
                        persona.begin_dialog_segments = segments
                        persona.updated_at = datetime.now(timezone.utc)
                        session.add(persona)
                row.begin_dialogs = begin_dialogs
                row.revision += 1
                row.updated_at = datetime.now(timezone.utc)
                if final_state:
                    row.state = final_state
                    row.reviewed_by = username
                session.add(row)
            await session.commit()
            result = row.model_dump()
        if action == "approve":
            self.persona_mgr.personas = await self.persona_mgr.get_all_personas()
            self.persona_mgr.get_v3_persona_data()
        return result
