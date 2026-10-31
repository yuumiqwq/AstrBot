"""Verify source synchronization and native context management offline."""

import copy
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.message import Message
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.session_context import make_context_manager, prepare_request_context
from astrbot.core.shared_qq_history import SharedQQHistory


class NativeContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = {
            "max_turns": -1,
            "trim_turns": 1,
            "overflow_strategy": "llm_compress",
            "instruction": "Keep the conversation facts.",
            "keep_recent_ratio": 0.15,
            "provider_id": "",
            "fallback_max_tokens": 128000,
        }
        self.context = SimpleNamespace(
            get_config=lambda **_: {
                "agent_runner": {"config": {"compression": self.settings}}
            },
            get_provider_by_id=Mock(return_value=None),
        )
        self.provider = SimpleNamespace(
            provider_config={"max_context_tokens": 128000},
            get_model=lambda: "test-model",
            text_chat=AsyncMock(
                return_value=LLMResponse(
                    role="assistant", completion_text="A short summary."
                )
            ),
        )

    def request(self, turns=30, size=100):
        return ProviderRequest(
            prompt="Decide whether to contact the user.",
            system_prompt="Keep the requested personality.",
            contexts=[
                {"role": role, "content": f"round {i} {role}: " + "文" * size}
                for i in range(turns)
                for role in ("user", "assistant")
            ],
        )

    async def test_under_native_budget_is_not_cut_to_20_or_4000(self):
        req = self.request()
        original = list(req.contexts)
        await prepare_request_context(self.context, "test", self.provider, req)
        self.assertEqual(req.contexts, original)
        self.provider.text_chat.assert_not_awaited()

    async def test_resolves_session_settings_and_model_capacity(self):
        self.provider.provider_config = {}
        self.settings.update(
            max_turns=9,
            trim_turns=2,
            provider_id="summary-model",
            keep_recent_ratio=0.2,
        )
        summary_provider = SimpleNamespace()
        self.context.get_provider_by_id.return_value = summary_provider
        with patch.dict(
            "astrbot.core.session_context.LLM_METADATAS",
            {"test-model": {"limit": {"context": 1000000}}},
            clear=True,
        ):
            manager = await make_context_manager(self.context, "test", self.provider)
        self.assertEqual(manager.config.max_context_tokens, 1000000)
        self.assertEqual(manager.config.enforce_max_turns, 9)
        self.assertEqual(manager.config.truncate_turns, 2)
        self.assertEqual(manager.config.llm_compress_keep_recent_ratio, 0.2)
        self.assertIs(manager.config.llm_compress_provider, summary_provider)
        with patch.dict(
            "astrbot.core.session_context.LLM_METADATAS",
            {},
            clear=True,
        ):
            manager = await make_context_manager(self.context, "test", self.provider)
        self.assertEqual(manager.config.max_context_tokens, 128000)

    async def test_native_turn_limit(self):
        self.settings["max_turns"] = 3
        req = self.request(turns=20)
        latest = req.contexts[-1]
        await prepare_request_context(self.context, "test", self.provider, req)
        self.assertEqual(req.contexts[-1], latest)
        # The active proactive request also occupies one native user round.
        self.assertLessEqual(sum(m["role"] == "user" for m in req.contexts), 2)

    async def test_presets_checkpoints_and_output_protocol_survive_preparation(self):
        req = self.request(turns=1)
        req.contexts[0]["_no_save"] = True
        req.contexts.append({"role": "_checkpoint", "content": {"id": "turn-1"}})
        original = copy.deepcopy(req.contexts)
        await prepare_request_context(
            self.context, "test", self.provider, req, "Return only JSON."
        )
        self.assertEqual(req.contexts, original)
        self.assertTrue(req.system_prompt.endswith("Return only JSON."))
        self.assertNotIn("Return only JSON.", str(req.contexts))

    async def test_native_summary_for_large_archive_preserves_latest(self):
        self.provider.provider_config["max_context_tokens"] = 10000
        req = self.request(turns=80, size=180)
        latest = req.contexts[-2:]
        await prepare_request_context(self.context, "test", self.provider, req)
        self.assertGreater(self.provider.text_chat.await_count, 1)
        self.assertEqual(req.contexts[-2:], latest)
        self.assertTrue(
            any(
                "previous history conversation summary" in m["content"]
                for m in req.contexts
            )
        )
        counter = EstimateTokenCounter()
        for call in self.provider.text_chat.call_args_list:
            messages = [
                m if isinstance(m, Message) else Message.model_validate(m)
                for m in call.kwargs["contexts"]
            ]
            self.assertLess(counter.count_tokens(messages), 10000)

    async def test_summary_failure_uses_native_truncation(self):
        self.provider.provider_config["max_context_tokens"] = 3000
        self.provider.text_chat.side_effect = RuntimeError("unavailable")
        req = self.request(turns=20, size=250)
        latest = req.contexts[-2:]
        await prepare_request_context(self.context, "test", self.provider, req)
        self.assertEqual(req.contexts[-2:], latest)
        self.assertLess(len(req.contexts), 40)


def qq_record(sequence, text=None, own=False, parts=None):
    """Build a QQ source record for synchronization tests."""
    return {
        "message_id": sequence * 7,
        "real_seq": str(sequence),
        "time": sequence,
        "self_id": 200,
        "sender": {"user_id": 200 if own else 100},
        "message": parts
        if parts is not None
        else [{"type": "text", "data": {"text": text or f"message {sequence}"}}],
    }


class MemoryDatabase:
    """Keep database reads detached, as with AstrBot's SQLModel database."""

    def __init__(self):
        now = datetime.now(timezone.utc)
        self.conversation = SimpleNamespace(
            conversation_id="conversation",
            user_id="test:FriendMessage:100",
            content=[],
            token_usage=0,
            persona_id="fish",
            title="Test",
            platform_id="test",
            created_at=now,
            updated_at=now,
        )
        self.state = None

    async def get_conversation_by_id(self, cid):
        return copy.deepcopy(self.conversation)

    async def get_preference(self, *args):
        return SimpleNamespace(value=copy.deepcopy(self.state)) if self.state else None

    async def insert_preference_or_update(self, scope, cid, key, value):
        self.state = copy.deepcopy(value)

    async def update_conversation(self, cid, **kwargs):
        for key, value in kwargs.items():
            if value is not None:
                setattr(self.conversation, key, copy.deepcopy(value))


class SharedHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.umo = "test:FriendMessage:100"
        self.policy = {"session_id": self.umo, "start_sequence": 40}
        self.records = [qq_record(i, own=i % 2 == 0) for i in range(1, 130)]
        self.calls = []

        async def api(action, **params):
            self.calls.append(params)
            if "message_seq" not in params:
                if params["count"] == 1:
                    return {"messages": copy.deepcopy(self.records[-1:])}
                return {
                    "messages": [copy.deepcopy(self.records[i]) for i in (0, 70, -1)]
                }
            end = next(
                i
                for i, record in enumerate(self.records)
                if str(record["message_id"]) == params["message_seq"]
            )
            return {"messages": copy.deepcopy(self.records[max(0, end - 36) : end + 1])}

        client = SimpleNamespace(call_action=AsyncMock(side_effect=api))
        self.platform = SimpleNamespace(
            meta=lambda: SimpleNamespace(name="aiocqhttp"), get_client=lambda: client
        )
        self.provider = SimpleNamespace(
            provider_config={"max_context_tokens": 128000},
            get_model=lambda: "unit-model",
            text_chat=AsyncMock(
                return_value=LLMResponse(role="assistant", completion_text="Summary.")
            ),
        )
        self.db = MemoryDatabase()
        self.manager = ConversationManager(self.db)
        self.manager.get_curr_conversation_id = AsyncMock(return_value="conversation")
        self.context = SimpleNamespace(
            get_config=lambda **_: {
                "shared_qq_history": {"sessions": [self.policy]},
                "agent_runner": {
                    "config": {
                        "compression": {
                            "max_turns": -1,
                            "overflow_strategy": "llm_compress",
                            "keep_recent_ratio": 0.15,
                        }
                    }
                },
            },
            get_platform_inst=lambda _: self.platform,
            get_provider_by_id=lambda _: None,
            get_using_provider_async=AsyncMock(return_value=self.provider),
            conversation_manager=self.manager,
        )
        self.service = SharedQQHistory(self.context)
        self.manager.shared_history = self.service

    async def test_boundary_sparse_pages_and_plugin_independent_reads(self):
        first_plugin = await self.manager.get_conversation(self.umo, "conversation")
        second_plugin = await self.manager.get_conversation(self.umo, "conversation")
        first = json.loads(first_plugin.history)
        self.assertEqual(first_plugin.history, second_plugin.history)
        self.assertEqual(first[0]["content"], "message 40")
        self.assertEqual(len(first), 90)
        self.assertEqual(self.calls[1]["message_seq"], str(129 * 7))
        self.provider.text_chat.assert_not_awaited()

    async def test_ordinary_request_excludes_current_message(self):
        from astrbot.core.astr_main_agent import _get_session_conv

        event = SimpleNamespace(
            unified_msg_origin=self.umo, message_obj=SimpleNamespace(message_id=129 * 7)
        )
        conversation = await _get_session_conv(event, self.context)
        history = json.loads(conversation.history)
        self.assertEqual(history[-1]["content"], "message 128")
        # A later plugin read includes the current message once from QQ.
        conversation = await self.manager.get_conversation(self.umo, "conversation")
        history = json.loads(conversation.history)
        self.assertEqual(sum(m["content"] == "message 129" for m in history), 1)

    async def test_generated_prompts_and_unsent_responses_are_not_imported(self):
        await self.manager.get_conversation(self.umo, "conversation")
        await self.manager.update_conversation(
            self.umo,
            "conversation",
            history=[
                {"role": "user", "content": "Artificial proactive prompt"},
                {"role": "assistant", "content": "Unsent response"},
            ],
        )
        await self.manager.add_message_pair(
            "conversation",
            {"role": "user", "content": "Artificial prompt"},
            {"role": "assistant", "content": "Unsent response"},
        )
        history = json.loads(
            (await self.manager.get_conversation(self.umo, "conversation")).history
        )
        self.assertEqual(len(history), 90)
        self.assertNotIn("Unsent response", str(history))
        # Real delivery appears once when QQ reports it, including split bubbles.
        self.records.extend(
            [
                qq_record(130, "Sent paragraph one", True),
                qq_record(131, "Sent paragraph two", True),
            ]
        )
        history = json.loads(
            (await self.manager.get_conversation(self.umo, "conversation")).history
        )
        self.assertEqual(
            [m["content"] for m in history[-2:]],
            ["Sent paragraph one", "Sent paragraph two"],
        )

    async def test_cursor_deletion_and_policy_change_rebuild_shared_history(self):
        await self.manager.get_conversation(self.umo, "conversation")
        self.records = [m for m in self.records if m["real_seq"] != "129"]
        history = json.loads(
            (await self.manager.get_conversation(self.umo, "conversation")).history
        )
        self.assertFalse(any(m["content"] == "message 129" for m in history))
        self.policy["start_sequence"] = 60
        history = json.loads(
            (await self.manager.get_conversation(self.umo, "conversation")).history
        )
        self.assertEqual(history[0]["content"], "message 60")

    async def test_unchanged_head_only_reads_one_record_without_writes(self):
        first = await self.manager.get_conversation(self.umo, "conversation")
        self.calls.clear()
        with (
            patch.object(
                self.db, "update_conversation", wraps=self.db.update_conversation
            ) as update,
            patch.object(
                self.db,
                "insert_preference_or_update",
                wraps=self.db.insert_preference_or_update,
            ) as save,
        ):
            second = await self.manager.get_conversation(self.umo, "conversation")
        self.assertEqual(second.history, first.history)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["count"], 1)
        self.assertNotIn("message_seq", self.calls[0])
        update.assert_not_awaited()
        save.assert_not_awaited()

    async def test_new_records_paginate_only_to_previous_cursor(self):
        await self.manager.get_conversation(self.umo, "conversation")
        self.records.extend(qq_record(i) for i in range(130, 230))
        self.calls.clear()
        history = json.loads(
            (await self.manager.get_conversation(self.umo, "conversation")).history
        )
        self.assertEqual(
            [m["content"] for m in history], [f"message {i}" for i in range(40, 230)]
        )
        self.assertEqual(len(self.calls), 4)
        self.assertEqual([c["count"] for c in self.calls], [1, 100, 100, 100])
        self.assertEqual(self.db.state["last_record"]["sequence"], 229)

    async def test_cursor_persists_across_restart_and_short_id_changes(self):
        first = await self.manager.get_conversation(self.umo, "conversation")
        self.manager.shared_history = SharedQQHistory(self.context)
        self.records[-1]["message_id"] = 999999
        self.calls.clear()
        second = await self.manager.get_conversation(self.umo, "conversation")
        self.assertEqual(first.history, second.history)
        self.assertEqual(len(self.calls), 1)

    async def test_new_message_with_same_text_is_not_skipped(self):
        await self.manager.get_conversation(self.umo, "conversation")
        self.records.append(qq_record(130, "message 129"))
        history = json.loads(
            (await self.manager.get_conversation(self.umo, "conversation")).history
        )
        self.assertEqual(len(history), 91)
        self.assertEqual([m["content"] for m in history[-2:]], ["message 129"] * 2)

    async def test_empty_records_advance_cursor_without_changing_context(self):
        first = await self.manager.get_conversation(self.umo, "conversation")
        self.records.append(qq_record(130, parts=[]))
        second = await self.manager.get_conversation(self.umo, "conversation")
        self.assertEqual(first.history, second.history)
        self.assertEqual(self.db.state["last_record"]["sequence"], 130)
        self.calls.clear()
        await self.manager.get_conversation(self.umo, "conversation")
        self.assertEqual(len(self.calls), 1)

    async def test_older_edits_are_not_rescanned_when_head_matches(self):
        first = await self.manager.get_conversation(self.umo, "conversation")
        self.records = [m for m in self.records if m["real_seq"] != "50"]
        self.calls.clear()
        second = await self.manager.get_conversation(self.umo, "conversation")
        self.assertEqual(first.history, second.history)
        self.assertEqual(len(self.calls), 1)

    async def test_changed_cursor_reconciles_history(self):
        await self.manager.get_conversation(self.umo, "conversation")
        self.records[-1]["message"][0]["data"]["text"] = "edited last record"
        history = json.loads(
            (await self.manager.get_conversation(self.umo, "conversation")).history
        )
        self.assertEqual(len(history), 90)
        self.assertEqual(history[-1]["content"], "edited last record")

    async def test_current_message_is_not_committed_as_synced_until_included(self):
        await self.manager.get_conversation(self.umo, "conversation")
        self.records.append(qq_record(130))
        for _ in range(2):
            history = json.loads(
                (
                    await self.manager.get_conversation(
                        self.umo, "conversation", before_message_id=str(130 * 7)
                    )
                ).history
            )
            self.assertEqual(history[-1]["content"], "message 129")
            self.assertEqual(self.db.state["last_record"]["sequence"], 129)
        history = json.loads(
            (await self.manager.get_conversation(self.umo, "conversation")).history
        )
        self.assertEqual(sum(m["content"] == "message 130" for m in history), 1)
        self.assertEqual(self.db.state["last_record"]["sequence"], 130)

    async def test_legacy_sync_state_upgrades_without_replacing_summary(self):
        self.provider.provider_config["max_context_tokens"] = 10000
        self.records = [qq_record(i, "文" * 300) for i in range(1, 130)]
        first = await self.manager.get_conversation(self.umo, "conversation")
        self.db.state.pop("last_record")
        calls = self.provider.text_chat.await_count
        second = await self.manager.get_conversation(self.umo, "conversation")
        self.assertEqual(first.history, second.history)
        self.assertEqual(calls, self.provider.text_chat.await_count)
        self.assertEqual(self.db.state["last_record"]["sequence"], 129)

    async def test_failed_incremental_fetch_does_not_advance_cursor(self):
        await self.manager.get_conversation(self.umo, "conversation")
        original = copy.deepcopy(self.db.state)
        self.platform.get_client().call_action.side_effect = TimeoutError(
            "QQ unavailable"
        )
        with self.assertRaises(TimeoutError):
            await self.manager.get_conversation(self.umo, "conversation")
        self.assertEqual(self.db.state, original)

    async def test_saved_summary_is_reused_and_new_records_append(self):
        self.provider.provider_config["max_context_tokens"] = 10000
        self.records = [qq_record(i, "文" * 300, i % 2 == 0) for i in range(1, 130)]
        history = json.loads(
            (await self.manager.get_conversation(self.umo, "conversation")).history
        )
        self.assertIn("previous history conversation summary", str(history))
        calls = self.provider.text_chat.await_count
        await self.manager.get_conversation(self.umo, "conversation")
        self.assertEqual(self.provider.text_chat.await_count, calls)
        self.records.append(qq_record(130, "new source message", True))
        history = json.loads(
            (await self.manager.get_conversation(self.umo, "conversation")).history
        )
        self.assertEqual(history[-1]["content"], "new source message")
        self.assertEqual(self.provider.text_chat.await_count, calls)

    async def test_partial_state_write_recovers_without_duplicate_import(self):
        await self.manager.get_conversation(self.umo, "conversation")
        self.db.conversation.content.append(
            {"role": "assistant", "content": "unexpected external write"}
        )
        history = json.loads(
            (await self.manager.get_conversation(self.umo, "conversation")).history
        )
        self.assertEqual(len(history), 90)
        self.assertNotIn("unexpected external write", str(history))

    async def test_new_plugin_can_use_public_session_llm_api(self):
        from astrbot.core.provider.provider import Provider
        from astrbot.core.star.context import Context

        provider = Mock(spec=Provider)
        provider.provider_config = {"max_context_tokens": 128000}
        provider.text_chat = AsyncMock(
            return_value=LLMResponse(role="assistant", completion_text="response")
        )
        self.context.provider_manager = SimpleNamespace(
            get_provider_by_id=AsyncMock(return_value=provider)
        )
        self.context.prepare_session_request = AsyncMock(
            side_effect=lambda *args, **kwargs: None
        )
        await Context.llm_generate(
            self.context,
            chat_provider_id="unit",
            prompt="Next turn",
            session_id=self.umo,
        )
        sent = provider.text_chat.call_args.kwargs
        self.assertEqual(sent["contexts"][0]["content"], "message 40")
        self.assertEqual(len(sent["contexts"]), 90)
        self.context.prepare_session_request.assert_awaited_once()

    def test_cards_expressions_whitespace_and_empty_records(self):
        card = {
            "meta": {
                "news": {
                    "title": "Shared title",
                    "jumpUrl": "https://www.xiaohongshu.com/discovery/item/example",
                }
            }
        }
        records = [
            qq_record(40, "one\n\ntwo"),
            qq_record(41, parts=[{"type": "json", "data": {"data": json.dumps(card)}}]),
            qq_record(
                42,
                parts=[
                    {
                        "type": "face",
                        "data": {"id": "461", "raw": {"faceText": "/宕机"}},
                    }
                ],
            ),
            qq_record(43, parts=[]),
        ]
        history, _ = self.service.normalize_records(records)
        self.assertEqual(history[0]["content"], "one\n\ntwo")
        self.assertIn(
            "Shared title\nhttps://www.xiaohongshu.com/", history[1]["content"]
        )
        self.assertEqual(history[2]["content"], "[QQ表情:/宕机]")
        self.assertEqual(len(history), 3)


def test_session_policy_survives_config_normalization(tmp_path):
    from astrbot.core.config.astrbot_config import AstrBotConfig
    from astrbot.core.config.default import DEFAULT_CONFIG

    config_path = tmp_path / "cmd_config.json"
    policy = {"session_id": "fish:FriendMessage:100", "start_sequence": 40}
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["shared_qq_history"] = {"sessions": [policy]}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    first = AstrBotConfig(config_path=str(config_path))
    first.save_config()
    second = AstrBotConfig(config_path=str(config_path))
    service = SharedQQHistory(SimpleNamespace(get_config=lambda **_: second))
    assert service.settings(policy["session_id"]) == policy
    assert service.settings("unconfigured:FriendMessage:200") is None
