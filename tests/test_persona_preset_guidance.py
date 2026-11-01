"""Keep preset style guidance scoped to examples and inside native budgeting."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.db.po import Persona
from astrbot.core.persona_mgr import (
    PRESET_DIALOGUE_STYLE_INSTRUCTION,
    PersonaManager,
)
from astrbot.core.persona_presets import PRESET_DIALOGUE_END_INSTRUCTION
from astrbot.core.session_context import make_context_manager, prepare_request_context


class PersonaPresetGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.persona = Persona(
            persona_id="example",
            system_prompt="Exact user persona",
            begin_dialogs=["Q", "A"],
        )
        self.manager = PersonaManager(Mock(), SimpleNamespace(default_conf={}))
        self.manager.personas = [self.persona]

    def test_valid_presets_receive_one_transient_system_guidance(self):
        original = copy.deepcopy(self.persona)
        for _ in range(2):
            _, personas, _ = self.manager.get_v3_persona_data()
            processed = personas[0]["_begin_dialogs_processed"]
            self.assertEqual(
                processed[0],
                {
                    "role": "system",
                    "content": PRESET_DIALOGUE_STYLE_INSTRUCTION,
                    "_no_save": True,
                },
            )
            self.assertEqual(
                [m["role"] for m in processed],
                ["system", "system", "user", "assistant", "system"],
            )
            self.assertEqual([m["content"] for m in processed[2:4]], ["Q", "A"])
            self.assertEqual(processed[-1]["content"], PRESET_DIALOGUE_END_INSTRUCTION)
            self.assertEqual(personas[0]["prompt"], "Exact user persona")
        self.assertEqual(self.persona, original)

    def test_empty_and_invalid_presets_do_not_add_guidance(self):
        for dialogs in ([], ["unpaired"]):
            with self.subTest(dialogs=dialogs):
                self.persona.begin_dialogs = dialogs
                _, personas, _ = self.manager.get_v3_persona_data()
                self.assertEqual(personas[0]["_begin_dialogs_processed"], [])
                self.assertEqual(personas[0]["prompt"], "Exact user persona")


class PresetGuidanceBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_guidance_is_counted_and_retained_as_unsaved_system_context(self):
        context = SimpleNamespace(
            get_config=lambda **_: {}, get_provider_by_id=Mock(return_value=None)
        )
        provider = SimpleNamespace(
            provider_config={"max_context_tokens": 128000},
            get_model=lambda: "fixture",
            text_chat=AsyncMock(
                return_value=LLMResponse(role="assistant", completion_text="summary")
            ),
        )
        manager = PersonaManager(Mock(), SimpleNamespace(default_conf={}))
        manager.personas = [
            Persona(
                persona_id="fixture", system_prompt="Persona", begin_dialogs=["Q", "A"]
            )
        ]
        _, personas, _ = manager.get_v3_persona_data()
        req = ProviderRequest(
            prompt="Current task",
            system_prompt="Persona",
            contexts=copy.deepcopy(personas[0]["_begin_dialogs_processed"]),
        )
        context_manager = await make_context_manager(context, "fixture", provider)
        counter = context_manager.token_counter
        with (
            patch(
                "astrbot.core.session_context.make_context_manager",
                AsyncMock(return_value=context_manager),
            ),
            patch.object(counter, "count_tokens", wraps=counter.count_tokens) as count,
        ):
            await prepare_request_context(context, "fixture", provider, req)
        self.assertTrue(
            any(
                any(
                    message.content == PRESET_DIALOGUE_STYLE_INSTRUCTION
                    for message in call.args[0]
                )
                for call in count.call_args_list
            )
        )
        self.assertEqual(
            req.contexts[0],
            {
                "role": "system",
                "content": PRESET_DIALOGUE_STYLE_INSTRUCTION,
                "_no_save": True,
            },
        )
        self.assertEqual(req.system_prompt, "Persona")
        provider.text_chat.assert_not_awaited()
